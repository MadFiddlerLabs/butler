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

from .config import KEYCHAIN_GMAIL, keychain


@dataclass
class Reply:
    uid: str
    message_id: str
    sender: str
    subject: str
    body: str
    trusted: bool
    why_untrusted: str


def _text_body(msg: email.message.Message) -> str:
    part = next((p for p in msg.walk() if p.get_content_type() == "text/plain"), None)
    if part is None:
        return ""
    text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
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
    return any(re.match(r"\s*dkim=pass\b", r, re.I) and signed_by.search(r)
               for r in results.split(";"))


def _login(settings: dict) -> imaplib.IMAP4_SSL:
    password = keychain(KEYCHAIN_GMAIL)
    if not password:
        raise RuntimeError(f"no Gmail app password in Keychain (service '{KEYCHAIN_GMAIL}')")
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
        raise RuntimeError(f"no Gmail app password in Keychain (service '{KEYCHAIN_GMAIL}')")
    trusted = {a.lower() for a in cfg["trusted_senders"]}

    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        imap.login(cfg["sender"], password)
        imap.select("INBOX", readonly=not mark_read)
        _, data = imap.uid("search", None, "UNSEEN")
        replies = []
        for uid in data[0].split():
            _, parts = imap.uid("fetch", uid, "(BODY.PEEK[])" if not mark_read else "(RFC822)")
            msg = email.message_from_bytes(parts[0][1])
            sender = parseaddr(msg.get("From", ""))[1].lower()
            reason = ""
            if sender not in trusted:
                reason = "sender not on the trusted list"
            elif not _dkim_passes(msg, sender):
                reason = "DKIM signature missing or failed"
            replies.append(Reply(
                uid=uid.decode(), message_id=msg.get("Message-ID", ""), sender=sender,
                subject=str(make_header(decode_header(msg.get("Subject", "")))),
                body=_text_body(msg), trusted=not reason, why_untrusted=reason,
            ))
        return replies
