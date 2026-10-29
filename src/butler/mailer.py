"""Sends mail from the household Gmail account over SMTP with an app password.

When Gmail refuses a message (a revoked app password, an outage), send_or_queue keeps the built
message in data/outbox/ and a later tick re-sends it. Nothing is rebuilt, so a retry never pays
for Claude again. After a few refusals in a row, the outbox is tried at most once an hour.
"""

import email
import email.policy
import json
import logging
import re
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import privacy, system
from .config import KEYCHAIN_GMAIL, ROOT, NotSetUp, keychain

log = logging.getLogger("butler")
OUTBOX = ROOT / "data" / "outbox"
FAST_TRIES = 3              # refusals in a row before backing off
BACKOFF = 60 * 60           # seconds between tries after that
KEEP = 3 * 24 * 60 * 60     # a queued message older than this is dropped: it's no longer news


def send(settings: dict, subject: str, text: str, html: str | None = None,
         to: list[str] | None = None, in_reply_to: str = "",
         images: dict[str, bytes] | None = None) -> None:
    """Sends to the configured recipients, or to `to` (used for replies). `images` are PNGs
    the HTML references as cid:<name>. ID numbers are withheld from every part (privacy.py)."""
    _deliver(settings, _message(settings, subject, text, html, to, in_reply_to, images))


def send_or_queue(settings: dict, key: str, subject: str, text: str, html: str | None = None,
                  to: list[str] | None = None, in_reply_to: str = "",
                  images: dict[str, bytes] | None = None) -> bool:
    """Like send, but a message Gmail refuses goes to the outbox under `key` instead of raising.
    Returns True when it went out now. While Gmail keeps refusing, it doesn't even try: the
    message waits for the next hourly flush."""
    msg = _message(settings, subject, text, html, to, in_reply_to, images)
    if not _backing_off():
        try:
            _deliver(settings, msg)
            _record(ok=True)
            return True
        except Exception as e:
            _record(ok=False)
            log.warning("send failed, queued %s for a retry (%s)", key, _why(e))
    _queue(key, msg)
    return False


def flush(settings: dict) -> int:
    """Re-sends queued messages, oldest first, unless Gmail has been refusing and the hour isn't
    up. Drops any older than KEEP. Returns how many went out."""
    when = _queued()
    paths = [p for p in OUTBOX.glob("*.eml")] if OUTBOX.exists() else []
    for path in paths:
        when.setdefault(path.name, time.time())    # a file with no record: count from now
    fresh = []
    for path in sorted(paths, key=lambda p: when[p.name]):
        if time.time() - when[path.name] > KEEP:
            log.warning("outbox: dropped %s, unsent for three days", path.name)
            path.unlink()
        else:
            fresh.append(path)
    sent = 0
    if fresh and not _backing_off():
        for path in fresh:
            try:
                _deliver(settings, email.message_from_bytes(path.read_bytes(), policy=email.policy.default))
            except Exception as e:
                _record(ok=False)
                log.warning("outbox: %s still not sent (%s)", path.name, _why(e))
                break
            _record(ok=True)
            path.unlink()
            log.info("outbox: sent %s", path.name)
            sent += 1
    _save_queued({p.name: when[p.name] for p in paths if p.exists()})
    return sent


def _message(settings, subject, text, html, to, in_reply_to, images) -> EmailMessage:
    cfg = settings["email"]
    subject, text, html = privacy.withhold(subject, text, html)
    msg = EmailMessage()
    msg["From"] = formataddr((cfg["sender_name"], cfg["sender"]))
    msg["To"] = ", ".join(to or cfg["recipients"])
    msg["Reply-To"] = cfg["sender"]
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="butler.local")
    if in_reply_to:
        msg["In-Reply-To"] = msg["References"] = in_reply_to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
        html_part = msg.get_payload()[1]
        for name, png in (images or {}).items():
            html_part.add_related(png, "image", "png", cid=f"<{name}>", filename=f"{name}.png")
    return msg


def _deliver(settings: dict, msg: EmailMessage) -> None:
    cfg = settings["email"]
    password = keychain(KEYCHAIN_GMAIL)
    if not password:
        raise NotSetUp(f"no Gmail app password in {system.PASSWORD_STORE} yet (service '{KEYCHAIN_GMAIL}')")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(cfg["sender"], password)
        smtp.send_message(msg)


def _why(e: Exception) -> str:
    return str(e).splitlines()[0][:160] if str(e) else type(e).__name__


def _queue(key: str, msg: EmailMessage) -> None:
    OUTBOX.mkdir(parents=True, exist_ok=True)
    path = OUTBOX / (re.sub(r"[^\w.-]", "-", key) + ".eml")
    path.write_bytes(msg.as_bytes())
    _save_queued({**_queued(), path.name: time.time()})


def _queued() -> dict[str, float]:
    """When each queued message was queued, by file name. Kept on time.time(), the clock the
    backoff uses, not in file times: those come from the file system's own clock, which a
    test's (or libfaketime's) clock doesn't move."""
    try:
        return json.loads((OUTBOX / "queued.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_queued(when: dict[str, float]) -> None:
    path = OUTBOX / "queued.json"
    if when:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(when))
    elif path.exists():
        path.unlink()


def _state() -> dict:
    try:
        return json.loads((OUTBOX / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def _record(ok: bool) -> None:
    """Counts refusals in a row; a message that goes out resets the count."""
    state = {} if ok else {"refusals": _state().get("refusals", 0) + 1, "last_try": time.time()}
    path = OUTBOX / "state.json"
    if state:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    elif path.exists():
        path.unlink()


def _backing_off() -> bool:
    state = _state()
    return state.get("refusals", 0) >= FAST_TRIES and time.time() - state.get("last_try", 0) < BACKOFF
