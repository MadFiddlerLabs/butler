"""Sends mail from the household Gmail account over SMTP with an app password."""

import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import KEYCHAIN_GMAIL, keychain


def send(settings: dict, subject: str, text: str, html: str | None = None,
         to: list[str] | None = None, in_reply_to: str = "",
         images: dict[str, bytes] | None = None) -> None:
    """Sends to the configured recipients, or to `to` (used for replies). `images` are PNGs
    the HTML references as cid:<name>."""
    cfg = settings["email"]
    password = keychain(KEYCHAIN_GMAIL)
    if not password:
        raise RuntimeError(f"no Gmail app password in Keychain (service '{KEYCHAIN_GMAIL}')")

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

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(cfg["sender"], password)
        smtp.send_message(msg)
