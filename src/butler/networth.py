"""Net worth from Lunch Money balances, snapshotted once a day so changes can be explained.

Every account is valued in the primary currency at Lunch Money's rate (`value`). A change between
two snapshots is split per account into an exchange-rate effect (the old balance in the second
currency revalued at the new rate) and a
real move (the change in the account's own currency, at the new rate). That's the "why": the
dollar moved, or money actually moved, and in which account.
"""

import json
from dataclasses import dataclass
from datetime import date

from . import lunchmoney
from .config import ROOT
from .fmt import money, name, primary_code, second_code, second_money

SNAPSHOTS = ROOT / "data" / "networth.jsonl"
LIABILITY_TYPES = {"credit", "loan", "other liability"}
NEW, GONE = "newly added to Lunch Money", "not in Lunch Money today"
KIND = {"investment": "investments", "depository": "cash", "cash": "cash", "credit": "card balance",
        "real estate": "property value", "other asset": "asset value"}


@dataclass
class Move:
    account: str
    kind: str
    real: float        # change in the account's own currency, valued at today's rate
    fx: float          # change from the exchange rate alone


@dataclass
class Change:
    total: float
    previous: float
    since: date
    fx: float
    rate_change: float      # fractional change in primary units per second-currency unit
    moves: list[Move]       # real moves, biggest first


def _accounts() -> list[dict]:
    rows = []
    for a in lunchmoney.manual_accounts():
        if a.get("closed_on") or a.get("status") == "closed":
            continue
        rows.append({"name": a["name"], "type": a.get("type") or "", "currency": a["currency"],
                     "balance": float(a["balance"]), "value": float(a.get("to_base") or 0)})
    plaid = [p for p in lunchmoney.plaid_accounts() if p.get("status") == "active"]
    names = [f'{p.get("institution_name") or ""} {p["name"]}'.strip() for p in plaid]
    for p, label in zip(plaid, names):
        if names.count(label) > 1:         # e.g. two "Joint" accounts at one broker
            label = f'{label} ··{p.get("mask") or p["id"]}'
        rows.append({"name": label, "type": p.get("type") or "", "currency": p["currency"],
                     "balance": float(p["balance"]), "value": float(p.get("to_base") or 0)})
    for r in rows:
        sign = -1 if r["type"] in LIABILITY_TYPES else 1
        r["balance"] *= sign
        r["value"] *= sign
    return rows


def snapshot(today: date) -> dict:
    """Takes today's snapshot (replacing any earlier one from today) and returns it."""
    rows = _accounts()
    snap = {"date": today.isoformat(), "total": round(sum(r["value"] for r in rows), 2), "accounts": rows}
    history = [s for s in load() if s["date"] != snap["date"]]
    SNAPSHOTS.parent.mkdir(exist_ok=True)
    SNAPSHOTS.write_text("".join(json.dumps(s) + "\n" for s in history + [snap]))
    return snap


def load() -> list[dict]:
    if not SNAPSHOTS.exists():
        return []
    return [json.loads(l) for l in SNAPSHOTS.read_text().splitlines() if l.strip()]


def _rate(account: dict) -> float | None:
    return account["value"] / account["balance"] if account["balance"] else None


def compare(now: dict, before: dict) -> Change:
    prev = {a["name"]: a for a in before["accounts"]}
    usd, primary = second_code().lower(), primary_code().lower()
    rates_now = [_rate(a) for a in now["accounts"] if a["currency"] == usd and _rate(a)]
    rates_then = [_rate(a) for a in before["accounts"] if a["currency"] == usd and _rate(a)]
    usd_now = max(rates_now) if rates_now else None
    usd_then = max(rates_then) if rates_then else None
    moves, fx_total = [], 0.0
    for a in now["accounts"]:
        new = a["name"] not in prev
        p = prev.get(a["name"], {"balance": 0.0, "value": 0.0, "currency": a["currency"]})
        rate = _rate(a) or (usd_now if a["currency"] == usd else 1.0) or 1.0
        prate = _rate(p) or (usd_then if a["currency"] == usd else 1.0) or rate
        fx = p["balance"] * (rate - prate) if a["currency"] != primary else 0.0
        real = (a["balance"] - p["balance"]) * rate
        fx_total += fx
        if abs(real) >= 1:
            kind = NEW if new else KIND.get(a["type"], a["type"] or "account")
            moves.append(Move(a["name"], kind, real, fx))
    # An account that's gone (closed, or its bank link broke) takes its whole value with it.
    current = {a["name"] for a in now["accounts"]}
    for account, p in prev.items():
        if account not in current and abs(p["value"]) >= 1:
            moves.append(Move(account, GONE, -p["value"], 0.0))
    moves.sort(key=lambda m: -abs(m.real))
    change = (usd_now / usd_then - 1) if usd_now and usd_then else 0.0
    return Change(total=now["total"], previous=before["total"], since=date.fromisoformat(before["date"]),
                  fx=fx_total, rate_change=change, moves=moves)


def second_rate(snap: dict) -> float | None:
    """Primary units per second-currency unit in a snapshot, from its largest account in the
    second currency (Lunch Money's daily rate)."""
    held = [a for a in snap["accounts"] if a["currency"] == second_code().lower() and a["balance"] > 0]
    if not held:
        return None
    biggest = max(held, key=lambda a: a["balance"])
    return biggest["value"] / biggest["balance"]


def fx_line(now: dict, before: dict | None) -> str:
    rate = second_rate(now)
    if rate is None:
        return ""
    out = (f"{name(second_code()).capitalize()}: 1 {second_code()} = {rate:.4f} {primary_code()} "
           "(Lunch Money's rate)")
    prev = second_rate(before) if before else None
    if not prev:
        return out + "."
    change = rate / prev - 1
    if abs(change) < 0.0005:
        return out + f", unchanged since {before['date'][8:].lstrip('0')} {date.fromisoformat(before['date']):%B}."
    stronger = f"a stronger {name(second_code()) if change > 0 else name(primary_code())}"
    return out + f", {abs(change):.1%} {'up' if change > 0 else 'down'} since the last brief: {stronger}."


def previous(today: date) -> dict | None:
    older = [s for s in load() if s["date"] < today.isoformat()]
    return older[-1] if older else None


def change_since(today: date, days: int) -> tuple[dict, Change | None]:
    """Today's snapshot, compared with the latest snapshot at least `days` old (or the oldest)."""
    now = snapshot(today)
    older = [s for s in load() if s["date"] < today.isoformat()]
    if not older:
        return now, None
    target = [s for s in older if (today - date.fromisoformat(s["date"])).days >= days]
    return now, compare(now, (target[-1] if target else older[0]))


REASON = {"savings_draw": "drawn from savings for spending", "rental_income": "rent received",
          "spend": "spending", "ledger": "side-ledger spending", "transfer": "transfers between accounts",
          "cash_funding": "moved to the cash account", "cash_topup": "cash withdrawn", "income": "income",
          "managed_fee": "advisor fees"}


def reasons_from(txs, since: date, until: date) -> dict[str, str]:
    """For each account, the kind of transaction that moved the most money in the period."""
    flows: dict[str, dict[str, float]] = {}
    for t in txs:
        if since < t.day <= until and t.kind != "ignored":
            f = flows.setdefault(t.account, {})
            f[t.kind] = f.get(t.kind, 0.0) + abs(t.amount)
    return {acct: REASON.get(max(f, key=f.get), max(f, key=f.get)) for acct, f in flows.items() if f}


def line(now: dict, ch: Change | None, period: str, notable: float,
         labels: dict[str, str] | None = None, reasons: dict[str, str] | None = None) -> str:
    """One line; when the change is notable, what moved and why. `reasons` maps an account to
    what its transactions say happened ("drawn from savings for spending")."""
    labels, reasons = labels or {}, reasons or {}
    rate = second_rate(now)
    total = money(now["total"]) + (f" ({second_money(now['total'] / rate)})" if rate else "")
    if ch is None:
        return f"Net worth: {total}. Tracking starts today, so tomorrow's brief can show the change."
    delta = ch.total - ch.previous
    direction = "up" if delta >= 0 else "down"
    out = f"Net worth: {total}, {direction} {money(abs(delta))} {period}."
    if abs(delta) < notable:
        return out
    why = []
    if abs(ch.fx) >= notable / 4:
        moved = "strengthened" if ch.rate_change > 0 else "weakened"
        why.append(f"the {name(second_code())} {moved} {abs(ch.rate_change):.1%} against the {name(primary_code())} "
                   f"({'+' if ch.fx >= 0 else ''}{money(ch.fx)} on your {second_code()} holdings)")
    for m in ch.moves[:3]:
        if abs(m.real) >= notable / 4:
            reason = m.kind if m.kind in (NEW, GONE) else reasons.get(m.account, m.kind)
            why.append(f"{labels.get(m.account, m.account)} {'+' if m.real >= 0 else ''}"
                       f"{money(m.real)} ({reason})")
    return out + (" What changed: " + "; ".join(why) + "." if why else "")
