"""Two small PNG charts for the Money section, embedded in the email as inline images.

Email clients strip SVG and scripts, so these are static images: no hover, light surface
baked in, text labels on the marks so nothing depends on color alone. Colors are the
dataviz reference palette (validated): series blue, neutral grays for context.
"""

import io
from itertools import accumulate

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .finance import Summary  # noqa: E402
from .fmt import money

# Light is for email (always light); dark is for the portal in dark mode. Both are steps
# of the same validated reference palette.
THEMES = {
    "light": dict(SURFACE="#fcfcfb", INK="#0b0b0b", INK_2="#52514e", MUTED="#a3a29c",
                  GRID="#e8e7e3", SERIES="#2a78d6", TRACK="#e8e7e3"),
    "dark": dict(SURFACE="#1a1a19", INK="#ffffff", INK_2="#c3c2b7", MUTED="#8f8e86",
                 GRID="#3a3936", SERIES="#3987e5", TRACK="#3a3936"),
}
WIDTH_IN, DPI = 6.0, 200   # shown at 600px wide; 2x for sharp screens


def _style(ax, t: dict) -> None:
    ax.set_facecolor(t["SURFACE"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["GRID"])
    ax.tick_params(colors=t["INK_2"], labelsize=8, length=0)
    ax.grid(axis="y", color=t["GRID"], linewidth=0.6)
    ax.set_axisbelow(True)


def _png(fig, t: dict) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, facecolor=t["SURFACE"], bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return buf.getvalue()




def month_to_date(m: Summary, theme: str = "light") -> bytes:
    """Cumulative spending this month vs the budget pace line and last month."""
    t = THEMES[theme]
    SURFACE, INK, INK_2, MUTED, SERIES = t["SURFACE"], t["INK"], t["INK_2"], t["MUTED"], t["SERIES"]
    label_box = {"boxstyle": "round,pad=0.2", "facecolor": SURFACE, "edgecolor": "none"}
    fig, ax = plt.subplots(figsize=(WIDTH_IN, 2.6))
    fig.patch.set_facecolor(SURFACE)
    _style(ax, t)

    days = list(range(1, m.days_in_month + 1))
    pace = [m.budget_month * d / m.days_in_month for d in days]
    last = list(accumulate(m.daily_last_month.get(d, 0.0) for d in days))
    this_days = list(range(1, m.today.day + 1))
    this = list(accumulate(m.daily_this_month.get(d, 0.0) for d in this_days))

    ax.plot(days, pace, color=INK_2, linewidth=1, linestyle=(0, (4, 3)))
    ax.plot(days, last, color=MUTED, linewidth=1.5)
    ax.plot(this_days, this, color=SERIES, linewidth=2.2)
    ax.plot([this_days[-1]], [this[-1]], "o", color=SERIES, markersize=6,
            markeredgecolor=SURFACE, markeredgewidth=1.5)

    # Direct labels at the line ends, in text ink; the legend is the labels themselves.
    ax.annotate(f"{m.today:%B} {money(this[-1])}", (this_days[-1], this[-1]),
                xytext=(8, 10), textcoords="offset points", color=INK, fontsize=8.5, weight="bold",
                bbox=label_box)
    # Put the higher line's label above it and the lower one's below, so they never stack.
    above, below = (("Last month", last[-1]), ("Budget pace", pace[-1]))
    if pace[-1] > last[-1]:
        above, below = below, above
    labels = {"Last month": f"Last month {money(last[-1])}",
              "Budget pace": f"Budget pace {money(m.budget_month)}"}
    ax.annotate(labels[above[0]], (days[-1], above[1]), xytext=(-4, 6),
                textcoords="offset points", ha="right", color=INK_2, fontsize=8, bbox=label_box)
    ax.annotate(labels[below[0]], (days[-1], below[1]), xytext=(-4, -16),
                textcoords="offset points", ha="right", color=INK_2, fontsize=8, bbox=label_box)

    ax.set_xlim(1, m.days_in_month)
    ax.set_ylim(0, max(pace[-1], last[-1], this[-1]) * 1.12)
    ax.set_xticks([1, 8, 15, 22, m.days_in_month])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: money(v)))
    starred = "*" if (m.one_offs_mtd or m.one_offs_last_month) else ""
    ax.set_title(f"Spending so far this month (tracked categories, one-offs excluded{starred})", loc="left",
                 color=INK, fontsize=9.5, pad=8)
    return _png(fig, t)


def categories(m: Summary, day_to_day: list[str], theme: str = "light") -> bytes | None:
    """Each daily-spending category: spent vs its monthly budget, with a tick for today's pace."""
    cats = [c for c in day_to_day
            if m.budget_by_category.get(c, 0) > 0 or m.mtd_by_category.get(c, 0) > 0]
    if not cats:
        return None
    cats.sort(key=lambda c: -(m.mtd_by_category.get(c, 0) / (m.budget_by_category.get(c) or 1)))

    t = THEMES[theme]
    SURFACE, INK, INK_2, SERIES, TRACK = t["SURFACE"], t["INK"], t["INK_2"], t["SERIES"], t["TRACK"]
    fig, ax = plt.subplots(figsize=(WIDTH_IN, 0.42 * len(cats) + 0.7))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(left=False, bottom=False, labelbottom=False, labelsize=9, colors=INK)

    top = max(max(m.budget_by_category.get(c, 0), m.mtd_by_category.get(c, 0)) for c in cats)
    for i, c in enumerate(reversed(cats)):
        budget, spent = m.budget_by_category.get(c, 0.0), m.mtd_by_category.get(c, 0.0)
        if budget:
            ax.barh(i, budget, height=0.5, color=TRACK)                   # the month's budget
        ax.barh(i, spent, height=0.5, color=SERIES)                       # spent so far
        if budget:
            x = budget * m.pace_fraction                                  # where it "should" be
            ax.plot([x, x], [i - 0.36, i + 0.36], color=INK, linewidth=1.3)
        label = f"{money(spent)} of {money(budget)}" if budget else f"{money(spent)}, no budget"
        new = m.new_by_category.get(c)
        if new:
            label += f"   +{money(new)} new"
        ax.text(max(budget, spent) + top * 0.02, i, label, va="center", fontsize=8.5,
                color=INK if new else INK_2, weight="bold" if new else "normal")

    ax.set_yticks(range(len(cats)), list(reversed(cats)))
    ax.set_xlim(0, top * 1.55)
    ax.set_title("Daily spending by category: spent vs month budget   ▏= today's pace",
                 loc="left", color=INK, fontsize=9.5, pad=8)
    return _png(fig, t)
