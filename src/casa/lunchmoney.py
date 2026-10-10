"""Read-only Lunch Money client. Raw responses are cached in data/raw/YYYY-MM-DD/."""

import json
import urllib.parse
import urllib.request
from datetime import date

from .config import ROOT, keychain

KEYCHAIN_LUNCHMONEY = "casa-lunchmoney"
BASE = "https://dev.lunchmoney.app/v1"


def get(path: str, **params) -> dict:
    token = keychain(KEYCHAIN_LUNCHMONEY)
    if not token:
        raise RuntimeError(f"no Lunch Money token in Keychain (service '{KEYCHAIN_LUNCHMONEY}')")
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(
        f"{BASE}/{path}{query}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "casa/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def cache(name: str, payload: dict, day: date | None = None) -> None:
    folder = ROOT / "data" / "raw" / str(day or date.today())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))


def transactions(start: date, end: date) -> list[dict]:
    """All transactions in [start, end], following pagination."""
    out, offset = [], 0
    while True:
        page = get("transactions", start_date=start.isoformat(), end_date=end.isoformat(),
                   limit=500, offset=offset)
        out += page.get("transactions", [])
        if not page.get("has_more"):
            return out
        offset += 500
