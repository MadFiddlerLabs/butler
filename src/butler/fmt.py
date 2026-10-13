"""Money formatting in the household's primary currency (Lunch Money's), from settings."""

from functools import lru_cache

SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CAD": "CA$", "AUD": "A$", "CHF": "CHF ", "MXN": "MX$",
           "JPY": "¥", "SEK": "kr ", "NOK": "kr ", "DKK": "kr "}


@lru_cache(maxsize=1)
def _symbols() -> tuple[str, str, str]:
    """(primary symbol, primary code, second-currency code)."""
    from .config import load_settings
    s = load_settings()
    primary = s.get("currency", {}).get("primary", "EUR").upper()
    second = s.get("currency", {}).get("second", "USD").upper()
    return SYMBOLS.get(primary, primary + " "), primary, second


# Roughly how many units of each currency make one euro or dollar. Butler's thresholds ("100
# over pace", "right on pace within 25") are written in euro-sized units and scaled by this, so
# a yen household isn't told about a ¥100 overrun. Not an exchange rate: an order of magnitude.
SCALES = {"JPY": 150, "SEK": 10, "NOK": 10, "DKK": 7, "MXN": 20}


@lru_cache(maxsize=1)
def scale() -> float:
    """What one euro-sized unit is in the primary currency: currency.scale if set, else SCALES."""
    from .config import load_settings
    cur = load_settings().get("currency", {})
    return float(cur.get("scale") or SCALES.get(str(cur.get("primary", "EUR")).upper(), 1))


def money(x: float) -> str:
    """'€1,234' or '−€56' in the primary currency."""
    sym = _symbols()[0]
    return f"−{sym}{-x:,.0f}" if x < -0.5 else f"{sym}{abs(x):,.0f}"


def second_money(x: float) -> str:
    """'$1,234' or '−$56' in the second currency (the one the budget converts with its FX rate)."""
    code = _symbols()[2]
    sym = SYMBOLS.get(code, code + " ")
    return f"−{sym}{-x:,.0f}" if x < -0.5 else f"{sym}{abs(x):,.0f}"


NAMES = {"EUR": "euro", "USD": "dollar", "GBP": "pound", "CAD": "Canadian dollar", "AUD": "Australian dollar",
         "CHF": "franc", "MXN": "peso", "JPY": "yen", "SEK": "krona", "NOK": "krone", "DKK": "krone"}


def name(code: str) -> str:
    """'dollar' for USD; the code itself when unknown."""
    return NAMES.get(code.upper(), code.upper())


def primary_code() -> str:
    return _symbols()[1]


def second_code() -> str:
    return _symbols()[2]
