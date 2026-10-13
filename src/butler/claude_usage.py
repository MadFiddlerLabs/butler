"""What Butler spends on Claude: the price table, and a month-to-date estimate from the token
counts each response reports. The portal's Money page shows it ("Claude this month: about $1.20
of your $5 limit"). It's an estimate: the Anthropic Console has the bill.

data/claude_usage.jsonl holds one line per billed attempt: the day, the model and four token
counts. Never a prompt, an answer or anything else from the household. scripts/model_check.py
prices its runs with the same table.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from .config import ROOT, tz

log = logging.getLogger("butler")
USAGE = ROOT / "data" / "claude_usage.jsonl"
TOKENS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

# ---------- prices ----------

PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICES_CHECKED = "2026-10-13"


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""
    input: float
    output: float
    cache_write: float    # 5-minute cache writes
    cache_read: float


# From PRICES_SOURCE, checked PRICES_CHECKED. Butler sets no cache_control, so the cache prices
# should never apply; they're here so a cache hit can't be priced as zero.
PRICES = {
    "claude-opus-5-5": Price(4.00, 20.00, 5.00, 0.20),
    "claude-opus-5": Price(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-8": Price(5.00, 25.00, 6.25, 0.50),     # where a refused request can fall back
    "claude-sonnet-5-5": Price(2.00, 10.00, 2.50, 0.10),
    "claude-sonnet-5": Price(2.00, 10.00, 2.50, 0.20),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.125, 0.01),    # prompts up to 100,000 tokens
    "claude-haiku-4-5-20251001": Price(1.00, 5.00, 1.25, 0.10),
}
# Haiku 5.5 charges more when a prompt passes 100,000 tokens (input, cache reads and writes counted).
LONG_PROMPT = {"claude-haiku-5-5": (100_000, Price(0.50, 2.50, 0.625, 0.05))}
HIGHEST = max(PRICES.values(), key=lambda p: (p.output, p.input))


def price_of(model: str, prompt_tokens: int = 0) -> tuple[Price, bool]:
    """The price for `model`, and whether it's known. An unknown model is priced at the highest,
    so an estimate errs high."""
    if model in LONG_PROMPT and prompt_tokens > LONG_PROMPT[model][0]:
        return LONG_PROMPT[model][1], True
    return (PRICES[model], True) if model in PRICES else (HIGHEST, False)


def _n(usage, name: str) -> int:
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
    return int(value or 0)


def usage_cost(model: str, usage) -> float:
    """Dollars for one usage record (an object or a dict): input, output, cache writes and reads."""
    tokens_in, tokens_out = _n(usage, "input_tokens"), _n(usage, "output_tokens")
    written, read = _n(usage, "cache_creation_input_tokens"), _n(usage, "cache_read_input_tokens")
    p, _ = price_of(model, tokens_in + written + read)
    return (tokens_in * p.input + tokens_out * p.output + written * p.cache_write + read * p.cache_read) / 1_000_000


def attempts(requested: str, response) -> list:
    """Each billed attempt in one response, with its own model. With a server-side fallback,
    usage.iterations has one entry per attempt; top-level usage covers only the last."""
    usage = response.usage
    served = getattr(response, "model", None) or requested
    found = [a for a in (getattr(usage, "iterations", None) or [])
             if getattr(a, "type", "") in ("message", "fallback_message")]
    if found:
        return [SimpleNamespace(model=getattr(a, "model", None) or requested, **{k: _n(a, k) for k in TOKENS})
                for a in found]
    return [SimpleNamespace(model=served, **{k: _n(usage, k) for k in TOKENS})]


# ---------- this household's spend ----------

def record(settings: dict, response, path: Path | None = None) -> None:
    """Appends each attempt's model and token counts, dated in the household's timezone. Never
    raises: a full disk mustn't stop a brief, it only makes the estimate low."""
    try:
        requested = settings["llm"]["model"]
        day = datetime.now(tz(settings)).date()
        path = path or USAGE
        path.parent.mkdir(exist_ok=True)
        with path.open("a") as f:
            for a in attempts(requested, response):
                f.write(json.dumps({"day": day.isoformat(), "model": a.model,
                                    **{k: getattr(a, k) for k in TOKENS}}) + "\n")
    except Exception as e:
        log.warning("claude usage: not recorded (%s)", type(e).__name__)


def month_spend(today: date, path: Path | None = None) -> float | None:
    """Estimated dollars spent on Claude in `today`'s calendar month, or None before the first
    recorded call. A line that can't be read is skipped."""
    path = path or USAGE
    if not path.exists():
        return None
    month = today.strftime("%Y-%m")
    total = 0.0
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
            if row["day"].startswith(month):
                total += usage_cost(row["model"], row)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return total


def dollars(amount: float) -> str:
    """$1.23, $5, or "less than $0.01"."""
    if 0 < amount < 0.005:
        return "less than $0.01"
    return f"${amount:,.0f}" if amount == int(amount) and amount >= 1 else f"${amount:,.2f}"


def summary(settings: dict, today: date, path: Path | None = None) -> str | None:
    """'Claude this month: about $1.20 of your $5 limit.' Only the spend when llm.monthly_limit
    isn't set. None before Butler has made a call."""
    spent = month_spend(today, path)
    if spent is None:
        return None
    limit = (settings.get("llm") or {}).get("monthly_limit")
    try:
        limit = float(limit) if limit not in (None, "") else None
    except (TypeError, ValueError):
        limit = None
    amount = dollars(spent)
    text = f"Claude this month: {'' if amount.startswith('less') else 'about '}{amount}"
    return text + (f" of your {dollars(limit)} limit." if limit else ".")
