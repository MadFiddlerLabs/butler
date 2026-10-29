"""Read-only Lunch Money client, on the v2 API. Raw responses are cached in data/raw/YYYY-MM-DD/.

v2 gives a transaction's category and account only as ids. `transactions()` looks them up once
per fetch and adds the names Butler classifies by (`category_name`, `is_income`,
`exclude_from_totals`, `plaid_account_name`, `asset_name`, `institution_name`), so finance.py
stays pure arithmetic and the names in category_map.yaml keep matching.
"""

import json
import urllib.parse
import urllib.request
from datetime import date

from . import system
from .config import ROOT, NotSetUp, keychain

KEYCHAIN_LUNCHMONEY = "butler-lunchmoney"
BASE = "https://api.lunchmoney.dev/v2"
PAGE = 1000          # v2's default page size; it allows up to 2000


def get(path: str, **params) -> dict:
    token = keychain(KEYCHAIN_LUNCHMONEY)
    if not token:
        raise NotSetUp(f"no Lunch Money token in {system.PASSWORD_STORE} (service '{KEYCHAIN_LUNCHMONEY}')")
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(
        f"{BASE}/{path}{query}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "butler/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def cache(name: str, payload: dict, day: date | None = None) -> None:
    folder = ROOT / "data" / "raw" / str(day or date.today())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))


def categories() -> list[dict]:
    """Every category, grouped ones included at the top level. Groups carry `is_group`."""
    return get("categories", format="flattened")["categories"]


def manual_accounts() -> list[dict]:
    """Accounts kept by hand (v1 called them assets)."""
    return get("manual_accounts")["manual_accounts"]


def plaid_accounts() -> list[dict]:
    """Accounts synced from a bank."""
    return get("plaid_accounts")["plaid_accounts"]


def transactions(start: date, end: date) -> list[dict]:
    """All transactions in [start, end], following pagination, each with its names added."""
    out, offset = [], 0
    while True:
        page = get("transactions", start_date=start.isoformat(), end_date=end.isoformat(),
                   limit=PAGE, offset=offset)
        out += page.get("transactions", [])
        if not page.get("has_more"):
            break
        offset += PAGE
    cats = {c["id"]: c for c in categories()}
    plaid = {p["id"]: p for p in plaid_accounts()}
    manual = {m["id"]: m for m in manual_accounts()}
    return [named(t, cats, plaid, manual) for t in out]


def named(t: dict, cats: dict, plaid: dict, manual: dict) -> dict:
    """One v2 transaction with the category and account names v1 used to include."""
    category = cats.get(t.get("category_id")) or {}
    synced = plaid.get(t.get("plaid_account_id"))
    by_hand = manual.get(t.get("manual_account_id"))
    account = synced or by_hand or {}
    return {**t,
            "category_name": category.get("name") or "",
            "is_income": bool(category.get("is_income")),
            "exclude_from_totals": bool(category.get("exclude_from_totals")),
            "plaid_account_name": synced["name"] if synced else None,
            "asset_name": by_hand["name"] if by_hand else None,
            "institution_name": account.get("institution_name") or ""}
