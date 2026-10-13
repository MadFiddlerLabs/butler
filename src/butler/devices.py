"""The browsers that may open the portal, each paired once with a one-time code.

The portal listens on 127.0.0.1, which every account and process on the Mac can reach, and
there a local process can also forge the headers `tailscale serve` and Caddy add. So a login
header alone doesn't prove a visitor: every request also needs a device key, a random cookie
whose hash is kept in data/portal_devices.json (readable by this Mac account only).

A browser gets its key by trading a pairing code: `butler portal` pairs this Mac's own browser,
and the portal's Devices page makes a code for a phone. A code works once, for 10 minutes, and
five wrong tries cancel every open code. Only hashes are stored, never a key or a code.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta

from .config import ROOT

FILE = ROOT / "data" / "portal_devices.json"
COOKIE = "butler_device"
CODE_MINUTES = 10
MAX_MISSES = 5
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no I, O, 0 or 1 to misread
_lock = threading.Lock()


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _load() -> dict:
    if not FILE.exists():
        return {"devices": [], "codes": [], "misses": 0}
    return json.loads(FILE.read_text())


def _save(data: dict) -> None:
    """Owner-only from the first byte: the file is created 0600, then swapped in."""
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, FILE)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now().astimezone()


def _open_codes(data: dict, now: datetime) -> list[dict]:
    return [c for c in data["codes"] if datetime.fromisoformat(c["expires"]) > now]


def new_code(now: datetime | None = None) -> str:
    """A one-time pairing code, like "K7QM-3XPA"."""
    now = _now(now)
    code = "".join(secrets.choice(ALPHABET) for _ in range(8))
    with _lock:
        data = _load()
        data["codes"] = _open_codes(data, now) + [
            {"hash": _hash(code), "expires": (now + timedelta(minutes=CODE_MINUTES)).isoformat()}]
        _save(data)
    return f"{code[:4]}-{code[4:]}"


def pair(code: str, label: str, now: datetime | None = None) -> str | None:
    """Trades a code for a new device key, once. None for a wrong or expired code."""
    now = _now(now)
    typed = _hash(re.sub(r"[^A-Z0-9]", "", code.upper()))
    with _lock:
        data = _load()
        codes = _open_codes(data, now)
        match = next((c for c in codes if hmac.compare_digest(c["hash"], typed)), None)
        if match is None:
            data["misses"] = data.get("misses", 0) + 1
            if data["misses"] >= MAX_MISSES:          # someone is guessing: cancel every open code
                codes, data["misses"] = [], 0
            data["codes"] = codes
            _save(data)
            return None
        key = secrets.token_urlsafe(32)
        data["codes"] = [c for c in codes if c is not match]
        data["misses"] = 0
        data["devices"].append({"id": secrets.token_hex(4), "hash": _hash(key), "label": label[:60],
                                "added": now.date().isoformat(), "seen": now.date().isoformat()})
        _save(data)
    return key


def known(key: str | None, now: datetime | None = None) -> dict | None:
    """The paired device this key belongs to, or None. Notes the day it was last seen."""
    if not key:
        return None
    typed = _hash(key)
    with _lock:
        data = _load()
        device = next((d for d in data["devices"] if hmac.compare_digest(d["hash"], typed)), None)
        today = _now(now).date().isoformat()
        if device and device["seen"] != today:
            device["seen"] = today
            _save(data)
    return device


def listing() -> list[dict]:
    """Every paired device, without its hash."""
    return [{k: v for k, v in d.items() if k != "hash"} for d in _load()["devices"]]


def forget(device_id: str) -> bool:
    with _lock:
        data = _load()
        kept = [d for d in data["devices"] if d["id"] != device_id]
        if len(kept) == len(data["devices"]):
            return False
        data["devices"] = kept
        _save(data)
    return True
