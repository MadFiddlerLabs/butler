"""Writes budget/budget.example.xlsx: the budget template with the invented example household.

    .venv/bin/python scripts/make_budget_example.py

Butler reads only the input cells (Assumptions C5–C6, Income A–D, Expenses A–F and I), so you can
add sheets, formatting and formulas of your own. Keep the columns and the first data row (5).
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

OUT = Path(__file__).resolve().parents[1] / "budget" / "budget.example.xlsx"
INPUT = PatternFill("solid", fgColor="FFF6D5")
BLUE = Font(color="1F4E9E")
BOLD = Font(bold=True)
LAST_EXPENSE_ROW = 60

EXPENSES = [  # category, item, currency, amount, frequency, due month, note
    ("Housing", "Rent", "EUR", 1600, "Monthly", None, "Paid around the 25th for the next month"),
    ("Utilities", "Electricity", "EUR", 90, "Monthly", None, ""),
    ("Utilities", "Internet and phones", "EUR", 45, "Monthly", None, ""),
    ("Food", "Groceries", "EUR", 700, "Monthly", None, ""),
    ("Food", "Restaurants", "EUR", 250, "Monthly", None, ""),
    ("Childcare", "Nanny", "EUR", 780, "Monthly", None, "180 every Friday in cash (52 × 180 ÷ 12)"),
    ("Childcare", "Swimming lessons", "EUR", 60, "Monthly", None, ""),
    ("Transport", "Transit and taxis", "EUR", 120, "Monthly", None, ""),
    ("Personal", "Clothes and haircuts", "EUR", 150, "Monthly", None, ""),
    ("Lifestyle", "Subscriptions and outings", "EUR", 120, "Monthly", None, ""),
    ("Household", "Household supplies", "EUR", 150, "Monthly", None, ""),
    ("Health", "Health insurance", "USD", 320, "Monthly", None, "Priced in USD"),
    ("Health", "Dentist", "EUR", 400, "Annual", 3, ""),
    ("Housing", "Home insurance", "EUR", 350, "Annual", 1, ""),
    ("Travel", "Summer trip", "EUR", 3000, "Annual", 7, ""),
    ("Household", "Replace appliances", "EUR", 4000, "5-Year", None, "Smoothed at 1/60 a month"),
]
INCOME = [  # source, currency, gross, deduction, note
    ("Salary (Sam)", "USD", 7000, 0.25, "After US tax withholding"),
    ("Freelance (Alex)", "EUR", 400, 0, ""),
]


def header(ws, row: int, cols: list[str]) -> None:
    for i, text in enumerate(cols, start=1):
        ws.cell(row, i, text).font = BOLD


def main() -> None:
    wb = Workbook()
    a = wb.active
    a.title = "Assumptions"
    a["A1"] = "Assumptions"
    a["A1"].font = Font(bold=True, size=14)
    a["A2"] = "Yellow cells with blue text are inputs. Butler updates C5 each morning from Lunch Money."
    header(a, 4, ["Assumption", "", "Value", "Notes"])
    rows = [("Exchange rate: primary-currency units per 1 unit of the second currency", 0.86,
             "e.g. EUR per USD. Every second-currency line converts with this."),
            ("Management fee", 0.10, "Used by Income rows whose Deduction says \"fee\"."),
            ("Second currency", "USD", "The code in the Currency column that converts with C5.")]
    for i, (label, value, note) in enumerate(rows, start=5):
        a.cell(i, 1, label)
        c = a.cell(i, 3, value)
        c.fill, c.font = INPUT, BLUE
        a.cell(i, 4, note)
    a.column_dimensions["A"].width = 70
    a.column_dimensions["D"].width = 60

    inc = wb.create_sheet("Income")
    inc["A1"] = "Income (monthly)"
    inc["A1"].font = Font(bold=True, size=14)
    inc["A2"] = "Gross per month. Deduction is a fraction (0.25) or the word fee (Assumptions C6)."
    header(inc, 4, ["Source", "Currency", "Gross / month", "Deduction", "Net / month", "Net primary / month", "Notes"])
    for r in range(5, 21):
        inc.cell(r, 5, f'=IF(A{r}="","",C{r}*(1-IF(ISTEXT(D{r}),Assumptions!$C$6,N(D{r}))))')
        inc.cell(r, 6, f'=IF(A{r}="",0,E{r}*IF(B{r}=Assumptions!$C$7,Assumptions!$C$5,1))')
        for col in range(1, 5):
            inc.cell(r, col).fill = INPUT
            inc.cell(r, col).font = BLUE
    for r, (source, cur, gross, ded, note) in enumerate(INCOME, start=5):
        for col, v in enumerate((source, cur, gross, ded), start=1):
            inc.cell(r, col, v)
        inc.cell(r, 7, note)
    inc["A22"], inc["F22"] = "Total", "=SUM(F5:F20)"
    inc["A22"].font = inc["F22"].font = BOLD
    for col, w in zip("ABCDEFG", (28, 10, 14, 11, 14, 18, 40)):
        inc.column_dimensions[col].width = w

    ex = wb.create_sheet("Expenses")
    ex["A1"] = "Expenses: the master list"
    ex["A1"].font = Font(bold=True, size=14)
    ex["A2"] = ("One row per expense. Monthly lines are fixed or daily (see category_map.yaml); "
                "Annual lines land in their due month; 5-Year lines are smoothed over 60 months.")
    header(ex, 4, ["Category", "Item", "Currency", "Amount", "Frequency", "Due month (1–12)",
                   "Monthly primary equiv.", "Annual primary", "Notes"])
    freq = DataValidation(type="list", formula1='"Monthly,Annual,5-Year"', allow_blank=True)
    ex.add_data_validation(freq)
    for r in range(5, LAST_EXPENSE_ROW + 1):
        ex.cell(r, 7, f'=IF(B{r}="",0,D{r}*IF(C{r}=Assumptions!$C$7,Assumptions!$C$5,1)'
                      f'/IF(E{r}="Monthly",1,IF(E{r}="Annual",12,60)))')
        ex.cell(r, 8, f"=G{r}*12")
        for col in (1, 2, 3, 4, 5, 6, 9):
            ex.cell(r, col).fill = INPUT
            ex.cell(r, col).font = BLUE
        freq.add(f"E{r}")
    for r, (cat, item, cur, amount, fq, due, note) in enumerate(EXPENSES, start=5):
        for col, v in enumerate((cat, item, cur, amount, fq, due), start=1):
            ex.cell(r, col, v)
        ex.cell(r, 9, note or None)
    total = LAST_EXPENSE_ROW + 2
    ex.cell(total, 1, "Monthly lines").font = BOLD
    ex.cell(total, 7, f'=SUMIFS(G5:G{LAST_EXPENSE_ROW},E5:E{LAST_EXPENSE_ROW},"Monthly")').font = BOLD
    ex.cell(total + 1, 1, "Set-aside for annual and five-year lines").font = BOLD
    ex.cell(total + 1, 7, f'=SUM(G5:G{LAST_EXPENSE_ROW})-G{total}').font = BOLD
    for col, w in zip("ABCDEFGHI", (16, 28, 10, 10, 11, 10, 18, 14, 48)):
        ex.column_dimensions[col].width = w

    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
