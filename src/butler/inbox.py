"""Reads replies sent to the household mailbox over IMAP.

Only mail from a trusted sender with a passing DKIM signature for that sender's
domain is treated as a command. Everything else is reported, never acted on.
"""

import email
import email.message
import imaplib
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parseaddr

from . import system
from .config import KEYCHAIN_GMAIL, NotSetUp, keychain


@dataclass
class Reply:
    uid: str
    message_id: str
    sender: str
    subject: str
    body: str
    trusted: bool
    why_untrusted: str


def _decode(data: bytes, charset: str | None) -> str:
    """Bytes in the charset the mail names. A charset Python doesn't know falls back to UTF-8
    with replacements: anyone can send any charset, and one odd mail mustn't stop the inbox."""
    try:
        return data.decode(charset or "utf-8", "replace")
    except (LookupError, UnicodeError):
        return data.decode("utf-8", "replace")


def _header(value: str) -> str:
    """A header such as Subject as plain text, whatever charsets its encoded words name."""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return "".join(chunk if isinstance(chunk, str) else _decode(chunk, charset)
                       for chunk, charset in decode_header(value))


def _text_body(msg: email.message.Message) -> str:
    part = next((p for p in msg.walk() if p.get_content_type() == "text/plain"), None)
    if part is None:
        return ""
    text = _decode(part.get_payload(decode=True) or b"", part.get_content_charset())
    # Keep only what they wrote, not the quoted brief below it.
    text = re.split(r"\n(?:On .+wrote:|El .+escribió:|-----Original Message-----)", text, maxsplit=1)[0]
    return "\n".join(l for l in text.splitlines() if not l.startswith(">")).strip()


GMAIL_AUTHSERV = "mx.google.com"


def _dkim_passes(msg: email.message.Message, sender: str) -> bool:
    """True when Gmail's own Authentication-Results header shows a DKIM pass for exactly the
    sender's domain. Only the topmost header counts: Gmail adds it on arrival, and any below
    it came with the message, so the sender could have written them."""
    domain = sender.rsplit("@", 1)[-1].lower()
    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return False
    authserv, _, results = " ".join(str(headers[0]).split()).partition(";")
    if authserv.strip().lower() != GMAIL_AUTHSERV:
        return False
    # The domain must end where the property ends, so "gmail.com.attacker.example" fails.
    signed_by = re.compile(rf"\bheader\.(?:i=@|d=){re.escape(domain)}(?=[\s;)]|$)", re.I)
    return any(re.match(r"\s*dkim=pass(?=[\s(]|$)", r, re.I) and signed_by.search(r)
               for r in results.split(";"))


def _login(settings: dict) -> imaplib.IMAP4_SSL:
    password = keychain(KEYCHAIN_GMAIL)
    if not password:
        raise NotSetUp(f"no Gmail app password in {system.PASSWORD_STORE} yet (service '{KEYCHAIN_GMAIL}')")
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    imap.login(settings["email"]["sender"], password)
    return imap


def mark_seen(settings: dict, uids: list[str]) -> None:
    if not uids:
        return
    with _login(settings) as imap:
        imap.select("INBOX")
        for uid in uids:
            imap.uid("store", uid, "+FLAGS", "(\\Seen)")


def fetch_unread(settings: dict, mark_read: bool = False) -> list[Reply]:
    cfg = settings["email"]
    password = keychain(KEYCHAIN_GMAIL)
    if not password:
        raise NotSetUp(f"no Gmail app password in {system.PASSWORD_STORE} yet (service '{KEYCHAIN_GMAIL}')")
    trusted = {a.lower() for a in cfg["trusted_senders"]}

    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        imap.login(cfg["sender"], password)
        imap.select("INBOX", readonly=not mark_read)
        _, data = imap.uid("search", None, "UNSEEN")
        replies = []
        for uid in data[0].split():
            _, parts = imap.uid("fetch", uid, "(BODY.PEEK[])" if not mark_read else "(RFC822)")
            replies.append(_parse(uid.decode(), parts, trusted))
        return replies


def _parse(uid: str, parts, trusted: set[str]) -> Reply:
    """One fetched message as a Reply. A message that can't be read comes back untrusted, so
    replies.process marks it read and moves on instead of failing on it every tick."""
    sender = ""
    try:
        msg = email.message_from_bytes(parts[0][1])
        sender = parseaddr(str(msg.get("From", "")))[1].lower()
        reason = ""
        if sender not in trusted:
            reason = "sender not on the trusted list"
        elif not _dkim_passes(msg, sender):
            reason = "DKIM signature missing or failed"
        return Reply(uid=uid, message_id=str(msg.get("Message-ID", "")), sender=sender,
                     subject=_header(str(msg.get("Subject", ""))), body=_text_body(msg),
                     trusted=not reason, why_untrusted=reason)
    except Exception as e:
        return Reply(uid=uid, message_id="", sender=sender or "an unknown sender", subject="", body="",
                     trusted=False, why_untrusted=f"couldn't read the message ({type(e).__name__})")
