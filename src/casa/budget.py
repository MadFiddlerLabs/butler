"""Reads the plan from budget/budget.xlsx (start from budget/budget.example.xlsx).

Only input cells are read (Expenses A–I, Assumptions C5–C6), and monthly figures in the
primary currency are computed here, so this works even when formula cells have no cached values.

Two currencies: the primary one (what Lunch Money reports in) and an optional second one that
some lines are priced in. Assumptions!C5 holds how much one unit of the second currency is
worth in the primary currency.
"""

import shutil
from dataclasses import dataclass

import openpyxl

from .config import ROOT
from .fmt import second_code

BUDGET_FILE = ROOT / "budget" / "budget.xlsx"
EXAMPLE_FILE = ROOT / "budget" / "budget.example.xlsx"


def budget_file():
    return BUDGET_FILE if BUDGET_FILE.exists() else EXAMPLE_FILE


@dataclass
class Line:
    category: str
    item: str
    currency: str | None
    amount: float
    frequency: str  # Monthly | Annual | 5-Year
    due_month: int | None
    note: str = ""

    def amount_primary(self, fx_rate: float) -> float:
        return self.amount * (fx_rate if self.currency == second_code() else 1.0)


@dataclass
class Plan:
    fx_rate: float   # primary-currency units per unit of the second currency
    lines: list[Line]

    def monthly_by_category(self, exclude_items: set[str] = frozenset()) -> dict[str, float]:
        """Recurring monthly lines only; annual and 5-year items are handled as lumps."""
        out: dict[str, float] = {}
        for l in self.lines:
            if l.frequency == "Monthly" and l.item not in exclude_items:
                out[l.category] = out.get(l.category, 0.0) + l.amount_primary(self.fx_rate)
        return out

    def lumps_due(self, month: int) -> list[Line]:
        return [l for l in self.lines if l.frequency == "Annual" and l.due_month == month]


def load_plan() -> Plan:
    wb = openpyxl.load_workbook(budget_file(), read_only=True)
    fx = float(wb["Assumptions"]["C5"].value or 1)
    lines = []
    for row in wb["Expenses"].iter_rows(min_row=5, max_row=60, max_col=9, values_only=True):
        category, item, currency, amount, frequency, due, _, _, note = row
        if not item or amount in (None, ""):
            continue
        lines.append(Line(category, item, currency, float(amount), frequency,
                          int(due) if due else None, note or ""))
    return Plan(fx_rate=fx, lines=lines)


def update_fx(rate: float, today) -> tuple[float, float] | None:
    """Sets Assumptions!C5 to today's rate when it has moved at least 0.1%, snapshotting the
    workbook first. Returns (old, new), or None if nothing changed. Never touches the example."""
    if not BUDGET_FILE.exists():
        return None
    wb = openpyxl.load_workbook(BUDGET_FILE)
    old = float(wb["Assumptions"]["C5"].value)
    new = round(rate, 4)
    if abs(new / old - 1) < 0.001:
        return None
    history = ROOT / "budget" / "history"
    history.mkdir(exist_ok=True)
    shutil.copy(BUDGET_FILE, history / f"budget_{today}_fx.xlsx")
    wb["Assumptions"]["C5"] = new
    wb.save(BUDGET_FILE)
    return old, new


def load_income() -> tuple[float, list[dict]]:
    """(management fee, income rows) from the Income sheet, with net values computed here.
    A deduction cell holding text (like "fee") means "the management fee in Assumptions!C6"."""
    wb = openpyxl.load_workbook(budget_file(), read_only=True)
    a = wb["Assumptions"]
    fx, fee = float(a["C5"].value or 1), float(a["C6"].value or 0)
    rows = []
    for source, currency, gross, deduction, *_rest in wb["Income"].iter_rows(
            min_row=5, max_row=20, max_col=7, values_only=True):
        if not source or gross in (None, ""):
            continue
        d = fee if isinstance(deduction, str) else float(deduction or 0)
        net = float(gross) * (1 - d)
        rows.append({"source": source, "currency": currency, "gross": float(gross), "deduction": d,
                     "net": net, "net_primary": net * (fx if currency == second_code() else 1)})
    return fee, rows
