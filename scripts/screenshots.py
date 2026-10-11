"""The README's screenshots, made from the invented example household (the Johnsons).

    pip install -r requirements-screenshots.txt && python -m playwright install chromium
    python scripts/screenshots.py [--out docs/images]

It builds the morning brief (`daily --dry-run --no-llm`) and the Sunday letter
(`weekly --dry-run`) in a throwaway install with every outside service faked (tests/fakes),
then serves that install's portal on 127.0.0.1 and photographs the pages with headless Chromium.
Nothing is sent and nothing leaves the machine.

Pinned, so the images change only when Butler's rendering does:
- the clock: the letter on Sunday 18 October 2026 at 17:00, the brief and the portal on Monday
  19 October at 07:00, Lisbon time (time-machine, inside the Butler processes only);
- the data: the test fixtures (calendar, Reminders, Lunch Money, weather) with a money overlay
  (cash withdrawals, a café, daily balances), invented news from invented outlets, and a fixed
  Sunday-letter text written from the letter's own numbers;
- the fonts: Inter (scripts/fonts, OFL), with DejaVu Sans from matplotlib for other symbols;
- the browser: light mode, fixed viewports, Playwright's Chromium at the pinned version.

.github/workflows/screenshots.yml runs this on main and opens a pull request when an image changes.
"""

import argparse
import base64
import copy
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
FONTS = Path(__file__).resolve().parent / "fonts"
ZONE = ZoneInfo("Europe/Lisbon")
LETTER_AT = datetime(2026, 10, 18, 17, 0, tzinfo=ZONE)     # a Sunday, when the letter goes out
BRIEF_AT = datetime(2026, 10, 19, 7, 0, tzinfo=ZONE)       # the next morning's brief

EMAIL = {"width": 672, "height": 900}      # the emails' 640px column, as a mail app shows it
DESKTOP = {"width": 960, "height": 900}
PHONE = {"width": 390, "height": 844}

# (file, what to load, viewport, device scale, most height kept in CSS pixels)
SHOTS = [
    ("brief.png", "brief", EMAIL, 1, 2200),
    ("weekly-letter.png", "letter", EMAIL, 1, 2000),
    ("portal-projects.png", "/projects", DESKTOP, 1, 1090),
    ("portal-money.png", "/money", DESKTOP, 1, 1150),
    ("portal-phone.png", "/", PHONE, 2, 844),
]

# Invented outlets and stories. Real outlets' names next to invented headlines would put
# words in their mouths, so the screenshot install reads these instead of the example feeds.
NEWS = {
    "local": ("Lisbon", "Lisbon Example Daily", [
        ("Green metro line to run until 2am on weekends from November",
         "The city's invented trial runs for six months, with trains every 15 minutes after midnight."),
        ("Roadworks close one lane of Avenida Exemplo for three weeks",
         "Buses on two routes are diverted from Monday. The works finish before the school holidays."),
    ]),
    "europe": ("Europe", "Europe Example Wire", [
        ("Rail operators agree a single ticket for cross-border night trains",
         "From next spring, passengers can book a whole journey in one go, the operators said."),
        ("Heating costs expected to ease through the winter",
         "Analysts point to full storage and a mild forecast for the first months."),
    ]),
    "world": ("World", "World Example Service", [
        ("Shipping rates fall to a two-year low",
         "Freight companies cite new capacity and steadier demand ahead of the holiday season."),
        ("Scientists map a deep-sea reef off the Atlantic coast",
         "The survey found hundreds of species, several of them thought to be new to science."),
    ]),
}


# The test fixtures' money, with three changes so the pictures read like a real month: Thursday
# cash withdrawals that cover Pat's Friday pay (the fixtures have none, so the drawer estimate
# goes negative), an ordinary café in place of the fixtures' unexplained payee, and account
# balances that move a little from day to day.
CAFE = ("Pastelaria Exemplo", 14.60, "Restaurants")
ATM = ("ATM WITHDRAWAL", 200.00, "", "Everyday Checking")     # the example cash_drawer's top-up
WEEK_BEFORE = date(2026, 10, 11)                               # an older net worth snapshot
BALANCES = {   # day: (Everyday Checking in EUR, Brokerage in USD, EUR per USD)
    WEEK_BEFORE: (4630.10, 51400.00, 0.8590),
    LETTER_AT.date(): (4268.35, 52000.00, 0.8600),
    BRIEF_AT.date(): (4253.75, 52000.00, 0.8600),
}


def _money_overlay(fixtures) -> None:
    """Serves the transactions above, dated from the brief's day whatever window is asked for."""
    day = BRIEF_AT.date()
    rows = [(day - timedelta(days=back), *(CAFE if payee.startswith("Mystery") else (payee, amount, category)),
             account) for back, payee, amount, category, account in fixtures.TRANSACTIONS]
    thursday = date(2026, 9, 3)       # the week before Pat's first Friday (cash_drawer.recurring.since)
    while thursday < day:
        rows.append((thursday, *ATM))
        thursday += timedelta(days=7)
    serve = fixtures.transactions

    def transactions(start: date, end: date) -> list[dict]:
        fixtures.TRANSACTIONS = [((end - d).days, payee, amount, category, account)
                                 for d, payee, amount, category, account in rows]
        return serve(start, end)
    fixtures.transactions = transactions


def _balances(fixtures, day: date) -> None:
    checking, usd, rate = BALANCES[day]
    accounts = copy.deepcopy(fixtures.PLAID_ACCOUNTS)
    for a in accounts:
        if a["name"] == "Everyday Checking":
            a["balance"], a["to_base"] = f"{checking:.2f}", checking
        elif a["currency"] == "usd":
            a["balance"], a["to_base"] = f"{usd:.2f}", round(usd * rate, 2)
    fixtures.PLAID_ACCOUNTS = accounts


# ---------- inside the throwaway install ----------

def _feed(url: str, **kwargs):
    key = re.search(r"/(\w+)\.rss$", url).group(1)
    stamp = (datetime.now(ZONE) - timedelta(hours=3)).utctimetuple()
    return SimpleNamespace(bozo=0, entries=[
        {"title": t, "summary": s, "link": f"https://news.example.test/{key}/{i}", "published_parsed": stamp}
        for i, (t, s) in enumerate(NEWS[key][2], start=1)])


def _letter_words(prompt: str) -> dict:
    """The Sunday letter's Claude-written parts, written here from the letter's own numbers so
    the text is fixed and every figure in it is true."""
    from butler.fmt import money
    n = json.loads(re.search(r"Numbers \(computed in Python\):\n(.*)\n", prompt).group(1))
    week, typical = n["daily_spending_this_week"], n["daily_spending_typical_week"]
    compared = "less than" if week < typical else "more than" if week > typical else "the same as"
    return {
        "headline": (f"Daily spending this week was {money(week)}, {compared} a typical week's "
                     f"{money(typical)}. Fixed monthly expenses are at {money(n['fixed_monthly_spent'])} of "
                     f"the {money(n['fixed_monthly_plan'])} plan, and daily spending is at "
                     f"{money(n['daily_spending_month'])} against a pace of {money(n['daily_spending_pace'])}. "
                     "The one thing worth doing this week is gathering the residence card documents."),
        "focus": [{"who": "Alex", "what": "Gather the residence card renewal documents."},
                  {"who": "Both", "what": "Visit the two shortlisted schools."},
                  {"who": "Sam", "what": "Book the yearly boiler service before 2 November."}],
        "ideas": [{"title": "Switch the streaming subscription to a yearly plan",
                   "detail": "Paying once a year usually costs about two months less.", "per_month": 2,
                   "kind": "restructure"},
                  {"title": "Shop once a week from a list",
                   "detail": "One planned shop a week usually trims the extras.",
                   "per_month": None, "kind": "cut"}],
        "project_notes": [
            {"project": "Home", "note": "The boiler service falls due on 2 November; nothing booked yet."},
            {"project": "Residency", "note": "The documents come first. The health centre is due back on 20 October."},
            {"project": "Mia: school", "note": "The school visits and the vaccination record are both open."}],
    }


def stage(name: str, port: int) -> None:
    """One step, run inside the throwaway install (cwd), with the test fakes and a frozen clock."""
    import time_machine
    sys.path.insert(0, str(REPO / "tests" / "fakes"))
    import feedparser
    import harness         # the network guard and the Gmail, Lunch Money and weather fakes
    harness.install_fakes()
    feedparser.parse = _feed
    canned = harness._canned
    harness._canned = lambda kind, prompt: _letter_words(prompt) if kind == "weekly" else canned(kind, prompt)
    import fixtures        # the same module the fakes answer from
    _money_overlay(fixtures)
    if name == "letter":
        from butler import networth
        _balances(fixtures, WEEK_BEFORE)
        networth.snapshot(WEEK_BEFORE)
    at = LETTER_AT if name == "letter" else BRIEF_AT
    _balances(fixtures, at.date())
    time_machine.travel(at, tick=False).start()
    if name == "letter":
        from butler import weekly
        sys.argv = ["weekly", "--dry-run"]
        weekly.main()
    elif name == "brief":
        from butler import daily
        sys.argv = ["daily", "--dry-run", "--no-llm"]
        daily.main()
    elif name == "portal":
        from waitress import serve
        from butler import portal
        serve(portal.app, host="127.0.0.1", port=port, threads=2)
    else:
        raise SystemExit(f"unknown stage {name}")


# ---------- the throwaway install ----------

def make_home():
    """The test install (tests/fakes/home.py) with the invented news feeds and pinned dates."""
    import yaml
    sys.path.insert(0, str(REPO / "tests"))
    from fakes import fixtures
    from fakes.home import Home
    home = Home(reference=False)
    settings = yaml.safe_load((REPO / "config" / "settings.example.yaml").read_text())
    settings["news"]["stories_per_region"] = 2
    settings["news"]["regions"] = [
        {"key": key, "label": label, "feeds": [{"name": outlet, "url": f"https://news.example.test/{key}.rss"}]}
        for key, (label, outlet, _) in NEWS.items()]
    (home.root / "config" / "settings.yaml").write_text(yaml.safe_dump(settings, sort_keys=False, allow_unicode=True))
    (home.root / "projects" / "reference.local.yaml").write_text(fixtures.reference_yaml(BRIEF_AT.date()))
    (home.root / "data" / "feedback.md").unlink()
    return home


def run_stage(home, name: str) -> None:
    proc = subprocess.run([sys.executable, __file__, "--stage", name], cwd=home.root, env=home.env(),
                          capture_output=True, text=True, timeout=300)
    if proc.returncode:
        raise SystemExit(f"{name} failed:\n{proc.stdout}\n{proc.stderr}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(port: int, proc: subprocess.Popen) -> None:
    for _ in range(300):
        if proc.poll() is not None:
            raise SystemExit(f"the portal stopped:\n{proc.stderr.read()}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("the portal didn't start")


# ---------- photographs ----------

def font_css() -> str:
    """Inter for Latin text, DejaVu Sans for any other symbol, so no glyph comes from the OS."""
    import matplotlib
    dejavu = Path(matplotlib.get_data_path()) / "fonts" / "ttf"

    def face(family: str, path: Path, fmt: str, weight: str, extra: str = "") -> str:
        data = base64.b64encode(path.read_bytes()).decode()
        return (f"@font-face{{font-family:'{family}';src:url(data:font/{fmt};base64,{data}) format('{fmt}');"
                f"font-weight:{weight};{extra}}}")
    latin = ("unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,"
             "U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD")
    return (face("Pinned", FONTS / "Inter-latin.woff2", "woff2", "100 900", latin)
            + face("Pinned symbols", dejavu / "DejaVuSans.ttf", "truetype", "100 500")
            + face("Pinned symbols", dejavu / "DejaVuSans-Bold.ttf", "truetype", "600 900")
            + "*{font-family:'Pinned','Pinned symbols',sans-serif!important}")


# The page's full height when it fits. Otherwise the top of the first block that doesn't fit,
# so a long page ends between paragraphs, not through a line of text.
CROP = """most => {
  const full = document.documentElement.scrollHeight;
  if (full <= most) return full;
  const tops = [...document.querySelectorAll("h1,h2,p,li,img,tr,details,.grp")]
    .map(e => e.getBoundingClientRect()).filter(r => r.bottom + window.scrollY > most)
    .map(r => r.top + window.scrollY).filter(t => t > most / 2);
  return Math.floor(Math.min(most, ...tops));
}"""


def photograph(home, port: int, out: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright
    preview = home.root / "out" / "preview"
    pages = {"brief": (preview / f"brief-{BRIEF_AT.date()}.html").as_uri(),
             "letter": next(preview.glob("weekly-*.html")).as_uri()}
    css, written = font_css(), []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, what, size, scale, most in SHOTS:
            context = browser.new_context(viewport=size, device_scale_factor=scale, color_scheme="light",
                                          locale="en-GB", timezone_id="Europe/Lisbon", reduced_motion="reduce")
            # The emails are files; the portal is the throwaway install's, on this Mac only.
            context.route(re.compile(r"^https?://(?!127\.0\.0\.1[:/]).*"), lambda route: route.abort())
            page = context.new_page()
            page.goto(pages.get(what) or f"http://127.0.0.1:{port}{what}", wait_until="networkidle")
            page.add_style_tag(content=css)
            page.evaluate("document.fonts.ready.then(() => true)")
            page.wait_for_function("[...document.images].every(i => i.complete && i.naturalWidth)")
            height = page.evaluate(CROP, most)
            png = page.screenshot(full_page=True, clip={"x": 0, "y": 0, "width": size["width"], "height": height},
                                  animations="disabled", caret="hide")
            written.append(save(png, out / name))
            context.close()
        browser.close()
    return written


def save(png: bytes, path: Path) -> Path:
    """A 256-color PNG without dithering: sharp text, a fraction of the size, the same bytes
    for the same pixels."""
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert("RGB")
    small = image.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    small.save(path, optimize=True)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="The README screenshots, from the invented example household")
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "images")
    parser.add_argument("--stage", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.stage:
        return stage(args.stage, args.port)

    args.out.mkdir(parents=True, exist_ok=True)
    home = make_home()
    portal = None
    try:
        run_stage(home, "letter")
        run_stage(home, "brief")
        # The portal shows what was sent: put the previews where sent emails are kept.
        for f in (home.root / "out" / "preview").iterdir():
            shutil.copy(f, home.root / "out" / f.name)
        port = free_port()
        portal = subprocess.Popen([sys.executable, __file__, "--stage", "portal", "--port", str(port)],
                                  cwd=home.root, env=home.env(), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, text=True)
        wait_for(port, portal)
        for path in photograph(home, port, args.out):
            print(f"{path.relative_to(REPO) if path.is_relative_to(REPO) else path}  {path.stat().st_size // 1024} KB")
    finally:
        if portal:
            portal.terminate()
            portal.wait(timeout=10)
        home.close()


if __name__ == "__main__":
    os.environ.setdefault("TZ", "Europe/Lisbon")
    main()
