"""Rebuilds a day's brief page in the portal without sending anything.

    python -m casa.rebuild               # today
    python -m casa.rebuild --date 2026-10-09

The first rebuild of a day keeps the version that was emailed as brief-DAY.sent.html, which
the portal links to ("See the email as it was sent").
"""

import argparse
import logging
import shutil
from datetime import date, datetime

from . import daily
from .config import ROOT, load_settings, tz
from .render import to_html, to_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild a brief page without sending")
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings()
    day = args.date or datetime.now(tz(settings)).date()
    out = ROOT / "out"
    page, sent = out / f"brief-{day}.html", out / f"brief-{day}.sent.html"
    if page.exists() and not sent.exists() and (out / f"sent-{day}").exists():
        shutil.copy(page, sent)
        shutil.copy(out / f"brief-{day}.txt", out / f"brief-{day}.sent.txt")
    b = daily.build(settings, day, use_llm=True)
    b.portal_url = settings.get("portal", {}).get("url", "")
    b.banner = f"Rebuilt at {datetime.now(tz(settings)):%H:%M} with the latest changes. Not sent."
    for name, png in b.charts.items():
        (out / f"chart-{day}-{name}.png").write_bytes(png)
    page.write_text(to_html(b, image_src=lambda n: f"chart-{day}-{n}.png"))
    (out / f"brief-{day}.txt").write_text(to_text(b))
    print(f"rebuilt {page.name}; problems: {b.problems or 'none'}")


if __name__ == "__main__":
    main()
