"""Budget suggestions from the monthly look-back, applied by reply.

The look-back saves its suggestions to data/budget_suggestions.json when it's sent. A reply
like "apply the Transport suggestion" or "Transport 160" becomes a `budget_change` action.
Claude only says which suggestion and copies any figure as written; everything else happens
here, in Python: the figure is parsed and must appear in the reply itself, the new budget must
stay within bounds, and one input cell (a Monthly line's Amount) changes after a dated backup.
Anything unclear changes nothing and asks.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .budget import BUDGET_FILE, Line, load_plan, set_monthly_amount
from .config import ROOT
from .fmt import SYMBOLS, money, primary_code, second_code, second_money

log = logging.getLogger("butler")
SUGGESTIONS = ROOT / "data" / "budget_suggestions.json"
OPEN_DAYS = 40          # suggestions stay open until the next look-back replaces them
LOW, HIGH = 0.5, 2.0    # by reply, a category's budget stays between half the lower and
                        # double the higher of its current budget and its trend


def usable(settings: dict) -> bool:
    """True when a reply can change the budget: the household has its own workbook (the
    example is never edited) and replies are trusted from someone."""
    return BUDGET_FILE.exists() and bool(settings.get("email", {}).get("trusted_senders"))


def save(month: date, suggestions: list, sent: date) -> None:
    """Keeps the look-back's suggestions (category -> budget, trend, suggested) for replies."""
    SUGGESTIONS.parent.mkdir(exist_ok=True)
    SUGGESTIONS.write_text(json.dumps({
        "month": f"{month:%Y-%m}", "sent": sent.isoformat(),
        "suggestions": {s.category: {"budget": round(s.budget, 2), "trend": round(s.trend, 2),
                                     "suggested": s.suggested} for s in suggestions}},
        indent=1, ensure_ascii=False))


def pending(today: date) -> dict[str, dict]:
    """The latest look-back's suggestions, while they're still open."""
    if not SUGGESTIONS.exists():
        return {}
    data = json.loads(SUGGESTIONS.read_text())
    if not timedelta(0) <= today - date.fromisoformat(data["sent"]) <= timedelta(days=OPEN_DAYS):
        return {}
    return data["suggestions"]


def daily_lines(category: str) -> list[Line]:
    """The Monthly lines that make up a category's daily-spending budget: the same lines the
    numbers use, so fixed, off-book and netted lines, and the cash drawer's line, are left out."""
    from .finance import load_rules
    rules = load_rules()
    r = rules["rules"]
    drawer = ((rules.get("cash_drawer") or {}).get("recurring") or {}).get("budget_line")
    skip = (set(r.get("fixed_lines", [])) | set(r.get("netted_from_rent", []))
            | {o["item"] for o in rules.get("off_book") or []} | ({drawer} if drawer else set()))
    return [l for l in load_plan().lines
            if l.category == category and l.frequency == "Monthly" and l.item not in skip]


# ---------- amounts ----------

_TOKEN = re.compile(r"\d[\d.,  ]*")


def _number(token: str) -> float | None:
    """'160', '1,050', '1.050', '1,050.50', '1.050,50', '87.5' -> a number; None if unclear."""
    t = token.rstrip(".,\u00a0\u202f")          # "160 €" ends its figure with a no-break space
    # A no-break space groups thousands too ('1\u00a0050'), so each group after one is three digits.
    head, *groups = re.split("[\u00a0\u202f]", t)
    if groups and not (re.fullmatch(r"\d{1,3}", head) and all(re.match(r"\d{3}(?!\d)", g) for g in groups)):
        return None
    t = "".join([head, *groups])
    if re.fullmatch(r"\d+", t):
        return float(t)
    m = re.fullmatch(r"\d{1,3}(([.,])\d{3})(?:\2\d{3})*", t)
    if m:
        return float(re.sub(r"[.,]", "", t))
    m = re.fullmatch(r"(\d+|\d{1,3}(?:([.,])\d{3})(?:\2\d{3})*)([.,])(\d{1,2})", t)
    if m and m.group(2) != m.group(3):
        return float(re.sub(r"[.,]", "", m.group(1)) + "." + m.group(4))
    return None


def parse_amounts(text: str) -> list[float | None]:
    """Every figure in the text, in order. None marks one that can't be read safely."""
    return [_number(t) for t in _TOKEN.findall(text or "")]


def _other_currency(text: str) -> bool:
    """True when the figure names a currency other than the primary one ('$350', '350 USD')."""
    primary = primary_code()
    for code, symbol in SYMBOLS.items():
        if code == primary:
            continue
        if re.search(rf"\b{code}\b", text, re.I):
            return True
        if symbol.strip() in ("$", "£", "¥", "€") and symbol.strip() in text \
                and symbol.strip() not in SYMBOLS.get(primary, ""):
            return True
    return False


def within_bounds(total: float, budget: float, trend: float) -> bool:
    """A category's new monthly budget is positive and between half the lower and double the
    higher of its current budget and its trend."""
    return total > 0 and LOW * min(budget, trend) <= total <= HIGH * max(budget, trend)


# ---------- planning and applying ----------

@dataclass
class Change:
    category: str
    item: str
    currency: str | None
    old: float          # the line's amount, in its own currency
    new: float
    total_old: float    # the category's daily-spending budget, primary currency
    total_new: float
    several_lines: bool


def plan(action: dict, said: str, today: date) -> Change | str:
    """Works out the change, or returns what to ask when it isn't clear and safe."""
    open_ = pending(today)
    if not open_:
        return "There are no open budget suggestions. They come with the monthly look-back."
    category = action.get("category") or ""
    if category not in open_:
        return f"I couldn't tell which suggestion you meant. Open ones: {', '.join(open_)}."
    lines = daily_lines(category)
    words = said.lower()
    if len(open_) > 1 and category.lower() not in words \
            and not any(l.item.lower() in words for l in lines):
        return f"I couldn't tell which suggestion you meant. Open ones: {', '.join(open_)}."
    if not lines:
        return f"{category} has no monthly line I can change by reply. Edit the budget workbook instead."
    if len(lines) == 1:
        line = lines[0]
    else:
        named = [l for l in lines if l.item.lower() in words]
        if len(named) != 1:
            names = ", ".join(l.item for l in lines)
            return (f"{category} has more than one line ({names}). Reply naming the one to change, "
                    f"like “{category}: {lines[0].item}”.")
        line = named[0]

    fx = load_plan().fx_rate
    total_old = sum(l.amount_primary(fx) for l in lines)
    others = total_old - line.amount_primary(fx)
    raw = (action.get("amount") or "").strip()
    if raw:
        figures = parse_amounts(raw)
        if len(figures) != 1 or figures[0] is None or _other_currency(raw):
            return f"I couldn't read “{raw}” as one amount in {primary_code()}."
        if not any(f is not None and abs(f - figures[0]) < 0.005 for f in parse_amounts(said)):
            return f"I couldn't find “{raw}” in your reply, so I left it alone."
        line_primary = figures[0]
    else:
        line_primary = open_[category]["suggested"] - others
    total_new = others + line_primary
    if line_primary <= 0 or not within_bounds(total_new, total_old, open_[category]["trend"]):
        lo = LOW * min(total_old, open_[category]["trend"])
        hi = HIGH * max(total_old, open_[category]["trend"])
        return (f"{money(total_new)} a month for {category} is outside what I'll change by reply "
                f"({money(lo)} to {money(hi)}). Edit the budget workbook if that's what you want.")
    if _in_second(line.currency):
        new = float(round(line_primary / fx))      # second-currency lines stay in whole units
        total_new = others + new * fx
    else:
        new = round(line_primary, 2)
    if abs(new - line.amount) < 0.005:
        return f"{line.item} is already {_amount(line.currency, new)} a month, so nothing changed."
    return Change(category, line.item, line.currency, line.amount, new, total_old, total_new, len(lines) > 1)


def _in_second(currency: str | None) -> bool:
    return currency == second_code() and second_code() != primary_code()


def _amount(currency: str | None, x: float) -> str:
    return second_money(x) if _in_second(currency) else money(x)


def apply(settings: dict, action: dict, said: str, today: date) -> str:
    """Applies one budget_change, or changes nothing and says what to clarify. A sentence for
    the confirmation email either way."""
    if not usable(settings):
        return ("I didn't change the budget: replies can only change your own budget/budget.xlsx, "
                "and there isn't one yet.")
    change = plan(action, said, today)
    if isinstance(change, str):
        return "I didn't change the budget. " + change
    c = change
    backup = set_monthly_amount(c.category, c.item, c.old, c.new, today)
    log.info("budget: %s (%s) %s -> %s by reply; backup %s", c.item, c.category, c.old, c.new, backup.name)
    text = (f"Budget changed: {c.item} ({c.category}) from {_amount(c.currency, c.old)} to "
            f"{_amount(c.currency, c.new)} a month")
    if c.several_lines or _in_second(c.currency):
        text += f", so {c.category} goes from {money(c.total_old)} to {money(c.total_new)}"
    return text + f". The workbook as it was is saved as {backup.relative_to(ROOT)}."
