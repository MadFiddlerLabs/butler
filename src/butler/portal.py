"""The household web portal. Listens on 127.0.0.1 only; `tailscale serve` puts HTTPS in
front of it for devices on the tailnet.

    python -m butler.portal            # serve on 127.0.0.1:8787

Who's allowed, in order:
- The Host must be one the portal serves, so a web page can't reach it by DNS rebinding.
- Requests through `tailscale serve` carry a Tailscale-User-Login header, checked against
  portal.allowed_logins. A request without a login must come straight from this Mac to
  127.0.0.1 or localhost, with no proxy in between.
- Every browser also needs its device key (devices.py), a cookie it got by pairing once. Any
  account or process on the Mac can reach 127.0.0.1 and forge the login headers there, so the
  headers alone prove nothing. Only the pairing page, the web app manifest and the icons are
  open without a key.
Changes go through the same code paths as email replies (projects.update_step, butler-ek),
so nothing here can delete anything.
"""

import json
import re
import subprocess
import time as _time
from datetime import date, datetime, timedelta
from html import escape

from flask import Flask, Response, abort, g, redirect, request, send_from_directory

from urllib.parse import quote, urlparse

from . import charts, devices, finance, networth, projects, replies
from .config import BUTLER_EK, ROOT, brand, load_settings, people, tz
from .render import money_lines, one_off_note, readable
from .fmt import SYMBOLS, money, primary_code, second_code

OUT = ROOT / "out"
app = Flask(__name__)
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, seconds: int, make):
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < seconds:
        return hit[1]
    value = make()
    _cache[key] = (_time.time(), value)
    return value


TAILSCALE = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def _whois(ip: str) -> str | None:
    """The tailnet login behind an IP, via `tailscale whois` (cached for 10 minutes)."""
    def look():
        out = subprocess.run([TAILSCALE, "whois", "--json", ip], capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout).get("UserProfile", {}).get("LoginName")
    return _cached(f"whois-{ip}", 600, look)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _caddy() -> bool:
    """True when the portal sits behind Caddy: launchd/install.sh --domain wrote caddy/domain.env."""
    return (ROOT / "caddy" / "domain.env").exists()


def _login() -> str | None:
    """Who's visiting. Through `tailscale serve` in HTTPS mode: the Tailscale-User-Login header.
    Through your own domain (Caddy, see caddy/): the peer address Caddy passes on, looked up with
    whois. Each header counts only in its own setup, so a client can't send the other one."""
    if _caddy():
        peer = request.headers.get("X-Butler-Peer")
        return (_whois(peer) or "unknown") if peer else None
    return request.headers.get("Tailscale-User-Login")


def _hostname(netloc: str) -> str:
    return re.sub(r":\d+$", "", netloc).lower()


def _served_hosts(settings: dict) -> set[str]:
    """The names the portal answers to: this Mac, the host in portal.url, and portal.hosts."""
    portal = settings["portal"]
    names = {urlparse(portal.get("url", "")).hostname or ""}
    names |= {h.lower() for h in portal.get("hosts", [])}
    return {n for n in names if n} | LOCAL_HOSTS


@app.before_request
def _gate():
    settings = load_settings()
    host = _hostname(request.host)
    if host not in _served_hosts(settings):
        abort(403)
    login = _login()
    if login is None:
        if host not in LOCAL_HOSTS or request.headers.get("X-Forwarded-For"):
            abort(403)
    elif login.lower() not in {l.lower() for l in settings["portal"]["allowed_logins"]}:
        abort(403)
    if request.method == "POST":
        origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
        # `butler portal` pairs this Mac's browser from a local file, which posts as Origin "null".
        from_file = request.path == "/pair" and origin == "null" and host in LOCAL_HOSTS
        if urlparse(origin).netloc.lower() != request.host.lower() and not from_file:
            abort(403)
    if request.path in OPEN_PATHS or request.path.startswith("/static/"):
        return None
    g.device = devices.known(request.cookies.get(devices.COOKIE))
    if g.device is None:
        if request.method == "GET":
            return redirect("/pair", 303)
        abort(403)
    return None


OPEN_PATHS = {"/pair", "/manifest.json"}
KEY_DAYS = 400          # the longest a browser keeps a cookie; each visit renews it


def _set_key(resp: Response, key: str) -> None:
    resp.set_cookie(devices.COOKIE, key, max_age=KEY_DAYS * 86400, path="/", httponly=True,
                    samesite="Lax", secure=_hostname(request.host) not in LOCAL_HOSTS)


@app.after_request
def _renew_key(resp: Response) -> Response:
    if g.get("device") and request.cookies.get(devices.COOKIE):
        _set_key(resp, request.cookies[devices.COOKIE])
    return resp


@app.after_request
def _no_framing(resp: Response) -> Response:
    """No other page may frame the portal, so it can't trick a click into a post from here
    (clickjacking); and a file is never read as anything but its declared type."""
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ---------- layout ----------

CSS = """
/* Text tokens reach 4.5:1 (WCAG AA) on --bg and --card in both modes; --chipblue carries white. */
:root{--bg:#fcfcfb;--card:#ffffff;--ink:#1f2328;--ink2:#52514e;--muted:#6e6d68;--rule:#e8e7e3;
--accent:#9a3412;--blue:#1f66c1;--chipblue:#1f66c1;--chip:#f1f0ec}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1a1a19;--card:#232321;
--ink:#f2f1ec;--ink2:#c3c2b7;--muted:#8f8e86;--rule:#3a3936;--accent:#f0a070;--blue:#4f94ea;
--chipblue:#2a6fc4;--chip:#2e2d2a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--rule);z-index:2}
nav{max-width:760px;margin:0 auto;padding:10px 16px;display:flex;gap:14px;overflow-x:auto;white-space:nowrap}
nav a{color:var(--ink2);text-decoration:none;font-weight:600}
nav a[aria-current=page]{color:var(--accent);text-decoration:underline;text-underline-offset:5px;text-decoration-thickness:2px}
.vh{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
main{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:4px 0 2px}h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;
color:var(--accent);margin:28px 0 8px;border-bottom:1px solid var(--rule);padding-bottom:6px}
.sub{color:var(--muted)}.card{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:12px 14px;margin:10px 0}
table{border-collapse:collapse;width:100%}td,th{padding:6px 8px 6px 0;text-align:left;vertical-align:top;border-bottom:1px solid var(--rule)}
th{font-size:12px;color:var(--muted);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
img.chart{width:100%;max-width:600px;height:auto;display:block;margin:8px 0;border-radius:6px}
.chip{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px;background:var(--chip);color:var(--ink2);margin-right:4px}
.who{font-weight:600}
form.inline{display:inline}button{font:inherit;font-size:13px;padding:3px 10px;border-radius:6px;
border:1px solid var(--rule);background:var(--card);color:var(--ink);cursor:pointer;margin:2px 4px 2px 0}
button:hover{border-color:var(--ink2)}input[type=text]{font:inherit;padding:6px 8px;border-radius:6px;
border:1px solid var(--rule);background:var(--card);color:var(--ink);width:min(100%,360px)}
.step{padding:8px 0;border-bottom:1px solid var(--rule)}.gate{font-size:13px;color:var(--muted);margin:3px 0}
details.step>summary{cursor:pointer;list-style:none}details.step>summary::-webkit-details-marker{display:none}
details.step>summary::before{content:"▸";color:var(--muted);display:inline-block;width:14px}
details.step[open]>summary::before{content:"▾"}
details.step.later>summary,details.step.paused>summary,details.step.done>summary{color:var(--muted)}
details.step.done>summary{text-decoration:line-through}
details.step .body{margin:6px 0 4px 14px}details.step ul{margin:2px 0 6px 18px;padding:0}
.lbl{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-top:6px}
details.parked{margin:8px 0;padding:6px 0;border-bottom:1px solid var(--rule)}
details.parked>summary{cursor:pointer}
.grp{font-size:12px;font-weight:600;color:var(--muted);margin:14px 0 2px}
.chip.c-now{background:var(--chipblue);color:#fff}.acts{margin-top:6px}.upd{margin-top:4px}
textarea{font:inherit;font-size:14px;width:100%;min-height:54px;padding:6px 8px;border-radius:6px;
border:1px solid var(--rule);background:var(--card);color:var(--ink);margin-top:4px}
details.step>summary{color:var(--ink)}
.flash{background:var(--chip);border-radius:8px;padding:10px 12px;margin:10px 0;white-space:pre-line}
.log{font-size:13px;color:var(--ink2);margin-top:2px}
.brief img{max-width:100%;height:auto;border-radius:8px}a{color:var(--blue)}
/* Tables here are full width; keep the emails' time column as narrow as its text, so each
   event's title starts right after its time instead of drifting toward the middle. */
.brief td[style*="white-space:nowrap"]{width:1%}
/* The emails use fixed inline colors (render.py); map them onto this page's light/dark tokens. */
.brief>div{background:transparent!important;padding:0!important}
.brief [style*="color:#1f2328"]{color:var(--ink)!important}
.brief [style*="color:#656d76"]{color:var(--muted)!important}
.brief [style*="color:#9a3412"]{color:var(--accent)!important}
.brief [style*="#e5e7eb"]{border-color:var(--rule)!important}
.brief [style*="background:#fdf3e7"]{background:var(--chip)!important}
/* The emails' "Open" button points here; hide it inside the portal itself. */
.brief p:has(> a[style*="display:inline-block"]){display:none}
"""

def _people_css() -> str:
    """Each person's color (settings.people[].color, optional color_dark) as .who-<Name>, and the
    emails' inline person colors remapped for dark mode."""
    light, dark, rules = [], [], []
    for i, p in enumerate(load_settings().get("people", [])):
        if not p.get("color"):
            continue
        color = readable(p["color"])          # as the emails draw it: 4.5:1 on the light page
        light.append(f"--p{i}:{color}")
        dark.append(f"--p{i}:{p.get('color_dark', p['color'])}")
        cls = re.sub(r"\W", "", p["name"])
        rules.append(f".who-{cls}{{color:var(--p{i})}}"
                     f'.brief [style*="color:{color}"]{{color:var(--p{i})!important}}')
    if not light:
        return ""
    return (f":root{{{';'.join(light)}}}@media (prefers-color-scheme:dark){{:root:not([data-theme=\"light\"])"
            f"{{{';'.join(dark)}}}}}" + "".join(rules))


NAV = [("/", "Today"), ("/week", "Week"), ("/month", "Last month"), ("/money", "Money"),
       ("/budget", "Budget"), ("/projects", "Projects"), ("/lists", "Lists"), ("/info", "Info"),
       ("/devices", "Devices")]


def page(title: str, body: str, active: str | None) -> str:
    """A portal page. With active=None (the pairing page, seen before a browser is paired):
    no navigation and no household colors, nothing beyond the butler's name."""
    links = "".join(f'<a href="{h}"' + (' aria-current="page"' if h == active else "") + f'>{n}</a>'
                    for h, n in NAV) if active is not None else ""
    people_css = _people_css() if active is not None else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)} · {escape(brand())}</title>'
            f'<link rel="apple-touch-icon" href="/static/icon-180.png">'
            f'<link rel="icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.json">'
            f'<meta name="apple-mobile-web-app-title" content="{escape(brand())}">'
            f'<meta name="theme-color" content="#1a1a19" media="(prefers-color-scheme: dark)">'
            f'<meta name="theme-color" content="#fcfcfb" media="(prefers-color-scheme: light)">'
            f'<style>{CSS}{people_css}</style></head>'
            f'<body><header><nav>{links}</nav></header><main>{body}</main></body></html>')


def _today() -> date:
    return datetime.now(tz(load_settings())).date()


# ---------- today / week (the emails, as pages) ----------

def _email_body(path, prefix: str) -> str:
    html = path.read_text()
    html = re.sub(r'src="((?:chart|weekly|monthly)-[^"]+\.png)"', rf'src="{prefix}\1"', html)
    html = re.sub(r"<!doctype html>.*?<body[^>]*>|</body></html>", "", html, flags=re.S | re.I)
    return f'<div class="brief">{html}</div>'


BRIEF_FILE = re.compile(r"brief-\d{4}-\d{2}-\d{2}\.html")


def _briefs() -> list:
    """Brief pages, newest first (not the kept copies such as brief-…​.sent.html)."""
    return sorted((f for f in OUT.glob("brief-*.html") if BRIEF_FILE.fullmatch(f.name)), reverse=True)


def _archive(pattern: str, link) -> str:
    files = _briefs() if pattern.startswith("brief") else sorted(OUT.glob(pattern), reverse=True)
    return " · ".join(link(f) for f in files[:30])


@app.get("/")
def today():
    briefs = _briefs()
    if not briefs:
        return page("Today", "<p>No brief yet.</p>", "/")
    return redirect(f"/brief/{briefs[0].stem.removeprefix('brief-')}")


@app.get("/brief/<day>")
def brief(day: str):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        abort(404)
    sent_copy = OUT / f"brief-{day}.sent.html"
    show_sent = request.args.get("version") == "sent" and sent_copy.exists()
    path = sent_copy if show_sent else OUT / f"brief-{day}.html"
    if not path.exists():
        abort(404)
    note = ""
    if sent_copy.exists():
        note = (f'<p class=sub><a href="/brief/{day}">See the rebuilt version</a></p>' if show_sent else
                f'<p class=sub><a href="/brief/{day}?version=sent">See the email as it was sent</a></p>')
    archive = _archive("brief-*.html", lambda f: f'<a href="/brief/{f.stem[6:]}">{f.stem[6:]}</a>')
    return page("Today", note + _email_body(path, "/out/") + f"<h2>Earlier briefs</h2><p>{archive}</p>", "/")


@app.get("/week")
def week():
    letters = sorted(OUT.glob("weekly-*.html"), reverse=True)
    if not letters:
        from .weekly import letter_day_name
        settings = load_settings()
        hour = settings["email"].get("weekly_hour", 17)
        day = letter_day_name(settings)
        return page("Week", "<h1>Your week</h1><p class=sub>The first weekly letter arrives "
                    f"{day} at {hour}:00.</p>", "/week")
    stem = request.args.get("w", letters[0].stem)
    path = OUT / f"{stem}.html"
    if not re.fullmatch(r"weekly-\d{4}-\d{2}", stem) or not path.exists():
        abort(404)
    archive = _archive("weekly-*.html", lambda f: f'<a href="/week?w={f.stem}">{f.stem[7:]}</a>')
    return page("Week", _email_body(path, "/out/") + f"<h2>Earlier letters</h2><p>{archive}</p>", "/week")


@app.get("/manifest.json")
def manifest():
    return {"name": brand(), "short_name": brand()[:12], "start_url": "/", "display": "standalone",
            "background_color": "#1a1a19", "theme_color": "#9a3412",
            "icons": [{"src": "/static/icon-180.png", "sizes": "180x180", "type": "image/png"},
                      {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]}


@app.get("/month")
def month():
    looks = sorted(OUT.glob("monthly-*.html"), reverse=True)
    if not looks:
        return page("Last month", "<h1>Last month</h1><p class=sub>The first monthly look-back arrives on "
                    "the 1st of next month.</p>", "/month")
    stem = request.args.get("m", looks[0].stem)
    path = OUT / f"{stem}.html"
    if not re.fullmatch(r"monthly-\d{4}-\d{2}", stem) or not path.exists():
        abort(404)
    archive = " · ".join(f'<a href="/month?m={f.stem}">{f.stem[8:]}</a>' for f in looks)
    return page("Last month", _email_body(path, "/out/") + f"<h2>Other months</h2><p>{archive}</p>", "/month")


@app.get("/out/<name>")
def out_file(name: str):
    if not re.fullmatch(r"(chart|weekly|monthly)-[\w-]+\.png", name):
        abort(404)
    return send_from_directory(OUT, name, max_age=300)


# ---------- money ----------

def _money():
    day = _today()
    return _cached("money", 600, lambda: finance.build(day, datetime.combine(
        day - timedelta(days=1), datetime.min.time(), tz(load_settings()))))


@app.get("/chart/<name>.png")
def chart(name: str):
    m = _money()
    theme = "dark" if request.args.get("theme") == "dark" else "light"
    if name == "mtd":
        png = _cached(f"chart-mtd-{theme}", 600, lambda: charts.month_to_date(m, theme))
    elif name == "categories":
        png = _cached(f"chart-cats-{theme}", 600, lambda: charts.categories(
            m, finance.load_rules()["rules"]["day_to_day"], theme))
    else:
        abort(404)
    return Response(png, mimetype="image/png")


def _chart_img(name: str, alt: str) -> str:
    """A chart that follows the viewer's light/dark setting."""
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="/chart/{name}.png?theme=dark">'
            f'<img class=chart src="/chart/{name}.png" alt="{alt}"></picture>')


def _subscriptions_html(m) -> str:
    subs = finance.subscriptions(m.txs, m.today)
    if not subs:
        return ""
    rows = "".join(f"<tr><td>{escape(x['payee'])}</td><td>{escape(x['kind'])}</td>"
                   f"<td class=num>{money(x['monthly'])}</td><td class=num>{money(x['monthly'] * 12)}</td>"
                   f"<td>{x['last']:%-d %b}</td></tr>" for x in subs)
    total = sum(x["monthly"] for x in subs)
    return (f"<h2>Subscriptions and monthly charges</h2><p class=sub>Detected from charges about a month "
            f"apart: {money(total)} a month, {money(total * 12)} a year.</p><table><tr><th>Payee</th><th>Counts as</th><th class=num>Monthly</th>"
            f"<th class=num>Yearly</th><th>Last</th></tr>{rows}</table>")


@app.get("/money")
def money_page():
    try:
        m = _money()
    except Exception as e:
        return page("Money", f"<h1>Money</h1><p>Couldn't load Lunch Money: {escape(str(e))}</p>", "/money")
    lines = "".join(f"<p>{escape(l)}</p>" for l in money_lines(m))
    snaps = networth.load()
    if snaps:
        cfg = load_settings().get("networth", {})
        ch = networth.compare(snaps[-1], snaps[-2]) if len(snaps) > 1 else None
        nw = networth.line(snaps[-1], ch, f"since {ch.since:%-d %B}" if ch else "",
                           cfg.get("notable_daily", 2500), cfg.get("labels"))
        lines = f"<p><strong>{escape(nw)}</strong></p>" + lines
    note = one_off_note(m)
    rows = []
    spent_by = m.against_budget_by_category        # one-offs are named above, not set against budgets
    for cat in sorted(set(m.budget_by_category) | set(spent_by), key=lambda c: -spent_by.get(c, 0)):
        b, s = m.budget_by_category.get(cat, 0), spent_by.get(cat, 0)
        rows.append(f"<tr><td>{escape(cat)}</td><td class=num>{money(s)}</td><td class=num>{money(b)}</td>"
                    f"<td class=num>{money(b - s) if b else '—'}</td></tr>")
    month_start = m.today.replace(day=1)
    txs = sorted((t for t in m.txs if t.book_day >= month_start and t.kind in (finance.SPEND, finance.LEDGER)),
                 key=lambda t: (t.day, t.amount), reverse=True)
    tx_rows = "".join(
        f"<tr><td>{t.day:%-d %b}</td><td>{escape(t.payee)}"
        + ("".join(f' <span class=chip>{c}</span>' for c in
                   (["one-off"] if t.one_off else []) + ([escape(t.ledger)] if t.kind == finance.LEDGER else [])
                   + ([f"for {t.book_day:%B}"] if t.book_day != t.day else [])
                   + (["unmapped"] if t.unmapped else [])))
        + f"</td><td>{escape(t.category or '—')}</td><td class=num>{money(t.amount)}</td></tr>" for t in txs)
    body = (f"<h1>Money</h1><div class=sub>{m.today:%B %Y}, from Lunch Money (refreshed every 10 minutes)</div>"
            f"<h2>So far this month</h2>{lines}"
            + _chart_img("mtd", "Spending so far this month against budget pace and last month")
            + (f'<p class=sub>{escape(note)}</p>' if note else "")
            + _chart_img("categories", "Day-to-day categories against budget")
            + "<h2>By category</h2><p class=sub>One-offs are left out here; they're named above.</p>"
            f"<table><tr><th>Category</th><th class=num>Spent</th>"
            f"<th class=num>Budget</th><th class=num>Left</th></tr>{''.join(rows)}</table>"
            + _subscriptions_html(m)
            + f"<h2>This month's transactions</h2><table><tr><th>Date</th><th>Payee</th><th>Category</th>"
            f"<th class=num>{primary_code()}</th></tr>{tx_rows}</table>")
    return page("Money", body, "/money")


# ---------- budget (the spreadsheet) ----------

@app.get("/budget")
def budget_page():
    from .budget import budget_file, load_income, load_plan
    plan = load_plan()
    fee, income = load_income()
    rules = finance.load_rules()["rules"]
    daily, fixed_lines = set(rules["day_to_day"]), set(rules.get("fixed_lines", []))
    netted = set(rules.get("netted_from_rent", []))
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def kind(l) -> str:
        if l.frequency != "Monthly":
            return "set-aside"
        if l.item in netted:
            return "paid from rent"
        return "daily" if l.category in daily and l.item not in fixed_lines else "fixed"

    rows, total_monthly, total_smoothed = [], 0.0, 0.0
    for cat in sorted({l.category for l in plan.lines}):
        for l in [l for l in plan.lines if l.category == cat]:
            per = {"Monthly": 1, "Annual": 12, "5-Year": 60}.get(l.frequency, 1)
            monthly = l.amount_primary(plan.fx_rate) / per
            total_smoothed += monthly
            total_monthly += monthly if l.frequency == "Monthly" else 0
            amount = f"{_sym(l.currency)}{l.amount:,.0f}"
            due = f" · {months[l.due_month]}" if l.due_month else ""
            rows.append(f"<tr><td>{escape(cat)}</td><td>{escape(l.item)}"
                        + (f'<div class=gate>{escape(l.note)}</div>' if l.note else "")
                        + f"</td><td class=num>{amount}</td><td>{escape(l.frequency)}{due}</td>"
                        f"<td><span class=chip>{kind(l)}</span></td><td class=num>{money(monthly)}</td></tr>")
    inc_rows = "".join(
        f"<tr><td>{escape(r['source'])}</td><td class=num>{_sym(r['currency'])}{r['gross']:,.0f}</td>"
        f"<td class=num>{r['deduction']:.0%}</td><td class=num>{money(r['net_primary'])}</td></tr>" for r in income)
    income_total = sum(r["net_primary"] for r in income)
    fee_label = load_settings().get("budget", {}).get("fee_label", "Management fee")
    body = (
        "<h1>Budget</h1><div class=sub>The plan in the spreadsheet, as the service reads it. "
        f'<a href="/budget/{budget_file().name}">Download the spreadsheet</a> to edit it; '
        "or ask in a reply and it gets edited for you (with a dated copy kept first).</div>"
        f"<h2>Assumptions</h2><p>Exchange rate: 1 {second_code()} = {plan.fx_rate:.4f} {primary_code()}, "
        f"updated each morning from Lunch Money. {escape(fee_label)}: {fee:.0%}.</p>"
        f"<h2>Income per month</h2><table><tr><th>Source</th><th class=num>Gross</th><th class=num>Fee</th>"
        f"<th class=num>Net {primary_code()}</th></tr>{inc_rows}<tr><td><strong>Total</strong></td><td></td><td></td>"
        f"<td class=num><strong>{money(income_total)}</strong></td></tr></table>"
        f"<h2>Spending plan</h2><p>Monthly lines: {money(total_monthly)}. With annual and five-year costs "
        f"smoothed in: {money(total_smoothed)} a month, so the plan runs {money(total_smoothed - income_total)} a month "
        f"{'more than' if total_smoothed > income_total else 'less than'} income.</p>"
        f"<table><tr><th>Category</th><th>Item</th><th class=num>Amount</th><th>How often</th><th>Kind</th>"
        f"<th class=num>{primary_code()} / month</th></tr>{''.join(rows)}</table>"
        '<p class=sub>"fixed" and "daily" are how the brief treats each line; "set-aside" lines are annual or '
        'five-year costs spread across months; "paid from rent" is taken out of rental income before the payout.</p>')
    return page("Budget", body, "/budget")


def _sym(currency: str | None) -> str:
    code = (currency or primary_code()).upper()
    return SYMBOLS.get(code, code + " ")


@app.get("/budget/<name>")
def budget_download(name: str):
    from .budget import budget_file
    f = budget_file()
    if name != f.name:
        abort(404)
    return send_from_directory(f.parent, f.name, as_attachment=True)


# ---------- projects ----------

def _buttons(s: projects.Step) -> str:
    def b(action: str, label: str) -> str:
        return (f'<form class=inline method=post action="/projects/{s.id}">'
                f'<input type=hidden name=action value="{action}">'
                f'<button aria-label="{escape(label, quote=True)}: {escape(s.do, quote=True)}">{label}</button></form>')
    if s.status == "done":
        return b("todo", "Reopen")
    out = b("done", "Done")
    if s.every:
        out += b("checked", "Checked today")
    out += b("todo", "Resume") if s.status in ("paused", "waiting") else b("paused", "Pause")
    return out


def _who() -> str:
    login = (_login() or "").lower()
    settings = load_settings()
    return settings["portal"].get("people", {}).get(login) or (people(settings) or ["Someone"])[0]


def _update_form(s: projects.Step) -> str:
    return (f'<form method=post action="/projects/{s.id}/update" class=upd>'
            f'<label class=vh for="upd-{s.id}">Send an update on {escape(s.do)}</label>'
            f'<textarea id="upd-{s.id}" name=text required placeholder="Send an update: what\'s happening? e.g. '
            f'\'Asked the office again, nothing to do but wait\' or \'booked for 12 Nov\'"></textarea>'
            f'<button>Send update</button></form>')


GROUP_LABEL = {"now": "Now", "waiting": "Waiting", "later": "Later", "paused": "Paused", "done": "Done"}
EXPAND_JS = """<script>
function setAll(open){document.querySelectorAll('details.step,details.parked').forEach(d=>d.open=open)}
addEventListener('DOMContentLoaded',()=>{const id=location.hash.slice(1);
 const d=id&&document.getElementById(id);if(d){d.open=true;d.scrollIntoView({block:'center'})}});
</script>"""


def _step_html(s: projects.Step, steps: dict, today: date) -> str:
    group, chip = projects.state(s, steps, today)
    who = f'<span class="who who-{re.sub(r"\W", "", s.who)}">{escape(s.who)}</span>'
    parts = [f'<details class="step {group}" id="{s.id}"><summary>{who} · {escape(s.do)} '
             f'<span class="chip c-{group}">{escape(chip)}</span></summary><div class=body>']
    if s.after:
        needs = "".join(f'<li>{escape(steps[a].label)} <span class=chip>'
                        f'{escape(projects.state(steps[a], steps, today)[1])}</span></li>' for a in s.after)
        parts.append(f'<div class=lbl>Needs</div><ul>{needs}</ul>')
    waves = projects.unlocks(s.id, steps)
    if waves:
        chain = "".join(f'<li>{"Then" if i == 0 else "After that"}: '
                        + "; ".join(f'<a href="#{d.id}">{escape(d.do)}</a>' for d in wave) + "</li>"
                        for i, wave in enumerate(waves))
        parts.append(f'<div class=lbl>Unlocks</div><ul>{chain}</ul>')
    if s.status == "waiting" and s.waiting_on:
        parts.append(f'<div class=gate>Waiting on {escape(s.waiting_on)}'
                     + (f'; back in the brief on {s.chase_after:%-d %B}' if s.chase_after else "") + "</div>")
    if s.every:
        parts.append(f'<div class=gate>Repeats every {s.every} days'
                     + (f'; last done {s.last_done:%-d %b}' if s.last_done else "") + "</div>")
    if s.not_before:
        parts.append(f'<div class=gate>Not before {s.not_before:%-d %B %Y}</div>')
    if s.notes:
        parts.append(f'<div class=gate>{escape(s.notes)}</div>')
    log = projects.updates(s.id)
    if log:
        parts.append('<div class=lbl>Updates</div>' + "".join(
            f'<div class=log>{u["date"]} · {escape(u["who"])}: {escape(u["text"])}</div>' for u in log))
    parts.append(f'<div class=acts>{_buttons(s)}</div>{_update_form(s)}</div></details>')
    return "".join(parts)


@app.get("/projects")
def projects_page():
    ps = projects.load()
    steps = {s.id: s for p in ps for s in p.steps}
    today = _today()
    body = ['<h1>Projects</h1><div class=sub>Tap a step for details, what it needs and what it unlocks. '
            'Tick things off here or by replying to any email.</div>'
            '<div class=acts style="margin-top:8px"><button onclick="setAll(true)">Expand all</button>'
            '<button onclick="setAll(false)">Collapse all</button></div>']
    if request.args.get("msg"):
        body.append(f'<div class=flash>{escape(request.args["msg"])}</div>')
    # Rank projects by what can be done now (high-priority steps count more); projects with
    # nothing to do now go to a collapsed "Parked" section at the bottom.
    weight = {"high": 3, "normal": 2, "low": 1}

    def now_steps(p):
        return [s for s in p.steps if projects.state(s, steps, today)[0] == "now"]

    def render(p) -> list[str]:
        out = []
        grouped = {group: [] for group in projects.GROUPS}
        for s in p.steps:
            grouped[projects.state(s, steps, today)[0]].append(s)
        for group in projects.GROUPS:
            if grouped[group]:
                out.append(f'<div class=grp>{GROUP_LABEL[group]} · {len(grouped[group])}</div>')
                out += [_step_html(s, steps, today) for s in grouped[group]]
        out += [f'<p class=gate>Rule: {escape(r)}</p>' for r in p.rules]
        return out

    active = sorted((p for p in ps if now_steps(p)),
                    key=lambda p: -sum(weight.get(s.priority, 2) for s in now_steps(p)))
    parked = [p for p in ps if not now_steps(p)]
    for p in active:
        n = len(now_steps(p))
        body.append(f"<h2>{escape(p.name)} <span class=sub>· {n} to do now</span></h2>"
                    f"<p class=sub>{escape(p.summary)}</p>")
        body += render(p)
    if parked:
        body.append("<h2>Parked <span class=sub>· nothing to do right now</span></h2>")
        for p in parked:
            # The most telling reason it's parked: something being waited on, then a start date,
            # then a dependency.
            chips = [projects.state(s, steps, today) for s in p.steps]
            rank = lambda c: (0 if c[0] == "waiting" else 1 if c[1].startswith(("from", "paused until")) else 2)
            pending = sorted((c for c in chips if c[0] in ("waiting", "later", "paused")), key=rank)
            waiting = pending[0][1] if pending else ""
            body.append(f'<details class=parked><summary><strong>{escape(p.name)}</strong> '
                        f'<span class=sub>· {escape(waiting)}</span></summary>'
                        f'<p class=sub>{escape(p.summary)}</p>{"".join(render(p))}</details>')
    return page("Projects", "".join(body) + EXPAND_JS, "/projects")


@app.post("/projects/<step_id>/update")
def project_update(step_id: str):
    text = (request.form.get("text") or "").strip()
    steps = {s.id: s for p in projects.load() for s in p.steps}
    if step_id not in steps or not text:
        abort(400)
    settings, who, today = load_settings(), _who(), _today()
    results = []
    for a in replies.plan_step_update(settings, steps[step_id], text, who, today):
        try:
            results.append(replies.perform(settings, a, who, today, said=text))
        except Exception as e:
            results.append(f"Couldn't do this ({e}): {a['text']}")
    msg = "\n".join(results) or "Thanks. Nothing to change."
    return redirect(f"/projects?msg={quote(msg)}#{step_id}", code=303)


@app.post("/projects/<step_id>")
def project_action(step_id: str):
    action = request.form.get("action")
    today = _today()
    if action == "done":
        projects.update_step(step_id, status="done", last_done=today)
    elif action == "checked":
        projects.update_step(step_id, last_done=today)
    elif action in ("todo", "paused", "waiting"):
        projects.update_step(step_id, status=action)
    else:
        abort(400)
    return redirect(f"/projects#{step_id}", code=303)


# ---------- useful information ----------

@app.get("/info")
def info_page():
    from . import reference
    data, today = reference.load(), _today()
    if not data:
        return page("Info", "<h1>Useful information</h1><p>No reference file yet. Copy "
                    "<code>examples/reference.local.example.yaml</code> to "
                    "<code>projects/reference.local.yaml</code> and fill it in.</p>", "/info")

    def cell(v) -> str:
        return escape(str(v)) if v not in (None, "") else '<span class=sub>—</span>'

    body = ["<h1>Useful information</h1><div class=sub>Only on this page: never emailed, never "
            "sent to Claude, not cached by your browser. Edit <code>projects/reference.local.yaml</code> "
            "on the Mac, or ask Claude.</div>"]
    body.append("<h2>People</h2><table><tr><th>Who</th><th>Phone</th><th>Email</th><th>IDs</th></tr>")
    for p in data.get("people", []):
        ids = "<br>".join(f"{escape(k)}: <code>{escape(str(v))}</code>" for k, v in (p.get("ids") or {}).items())
        phone = p.get("phone")
        tel = f'<a href="tel:{escape(phone)}">{escape(phone)}</a>' if phone else cell("")
        body.append(f"<tr><td>{escape(p['name'])}</td><td>{tel}</td><td>{cell(p.get('email'))}</td><td>{ids or cell('')}</td></tr>")
    body.append("</table>")
    if data.get("contacts"):
        body.append("<h2>Contacts</h2><table><tr><th>Who</th><th>Phone</th><th>Notes</th></tr>")
        for c in data["contacts"]:
            phone = c.get("phone")
            tel = f'<a href="tel:{escape(phone)}">{escape(phone)}</a>' if phone else cell("")
            body.append(f"<tr><td>{escape(c['name'])}</td><td>{tel}</td><td>{cell(c.get('notes'))}</td></tr>")
        body.append("</table>")
    if data.get("addresses"):
        body.append("<h2>Addresses</h2><table>" + "".join(
            f"<tr><td>{escape(a['label'])}</td><td>{escape(a['value'])}</td></tr>" for a in data["addresses"]) + "</table>")
    body.append("<h2>Documents and expiry</h2><table><tr><th>Holder</th><th>Document</th>"
                "<th>Expires</th><th>Kept</th></tr>")
    for d in sorted(data.get("documents", []), key=lambda d: str(d.get("expires") or "9999")):
        exp = d.get("expires")
        if exp:
            exp_d = exp if isinstance(exp, date) else date.fromisoformat(str(exp))
            days = (exp_d - today).days
            flag = (' <span class="chip c-now">expired</span>' if days < 0 else
                    f' <span class="chip c-now">in {days} days</span>' if days <= 90 else
                    f' <span class=chip>in {days // 365} yr</span>' if days > 365 else "")
            exp_html = f"{exp_d:%-d %b %Y}{flag}"
        else:
            exp_html = '<span class=sub>add date</span>'
        body.append(f"<tr><td>{escape(d['holder'])}</td><td>{escape(d['document'])}</td>"
                    f"<td>{exp_html}</td><td>{cell(d.get('kept'))}</td></tr>")
    body.append("</table>")
    settings = load_settings()
    name, mailbox = brand(settings), settings["email"]["sender"]
    body.append(
        f"<h2>Tell {escape(name)} with Siri</h2><ol>"
        "<li>On your iPhone, open <strong>Shortcuts</strong> and tap <strong>+</strong>.</li>"
        "<li>Add <strong>Dictate Text</strong>, then <strong>Send Email</strong>.</li>"
        f"<li>In Send Email: recipient <code>{escape(mailbox)}</code>, subject <code>{escape(name)}</code>, "
        "message: the Dictated Text. Turn <strong>Show Compose Sheet</strong> off.</li>"
        f"<li>Name the shortcut <strong>Tell {escape(name)}</strong>.</li></ol>"
        f"<p class=sub>Then: \"Hey Siri, Tell {escape(name)}\", and say \"add milk to shopping\" or "
        "\"what's on Thursday?\". A confirmation (or answer) comes back by email.</p>")
    resp = Response(page("Info", "".join(body), "/info"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- devices: pairing a browser, and the ones already paired ----------

def _device_label(agent: str) -> str:
    """A name a person recognises, from the browser's User-Agent: "iPhone, Safari"."""
    kind = next((k for k, pattern in [("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                      ("Mac", "Macintosh"), ("Windows", "Windows"), ("Linux", "Linux")]
                 if pattern in agent), "A browser")
    browser = next((b for b, pattern in [("Edge", "Edg/"), ("Firefox", "Firefox/"), ("Chrome", "Chrome/"),
                                         ("Chrome", "CriOS/"), ("Safari", "Safari/")] if pattern in agent), "")
    return f"{kind}, {browser}" if browser else kind


PAIR_FORM = ('<form method=post action="/pair"><p><label for=code>Pairing code</label><br>'
             '<input type=text id=code name=code autocomplete=one-time-code autocapitalize=characters '
             'spellcheck=false placeholder="K7QM-3XPA" required> <button>Pair</button></p></form>')


def _pair_page(message: str = "") -> str:
    name = escape(brand())
    return page("Pair", f"<h1>Pair this browser with {name}</h1>{message}"
                f"<p>{name} opens only on browsers you've paired. Type the code from a paired device's "
                "<strong>Devices</strong> page. On the Mac itself, run <code>butler portal</code> "
                f"instead.</p>{PAIR_FORM}", None)


@app.get("/pair")
def pair_page():
    if devices.known(request.cookies.get(devices.COOKIE)):
        return redirect("/", 303)
    return _pair_page()


@app.post("/pair")
def pair():
    key = devices.pair(request.form.get("code", ""), _device_label(request.headers.get("User-Agent", "")))
    if not key:
        return _pair_page("<p class=flash>That code didn't work. A code works once, for "
                          f"{devices.CODE_MINUTES} minutes. Make a new one and try again.</p>"), 403
    resp = redirect("/", 303)
    _set_key(resp, key)
    return resp


@app.get("/devices")
def devices_page(code: str = ""):
    this = g.device["id"]
    rows = "".join(
        f"<tr><td>{escape(d['label'])}{' <span class=chip>this one</span>' if d['id'] == this else ''}</td>"
        f"<td>{escape(d['added'])}</td><td>{escape(d['seen'])}</td><td>"
        f'<form class=inline method=post action="/devices/{escape(d["id"])}/forget">'
        f'<button aria-label="Forget {escape(d["label"])}, paired {escape(d["added"])}">Forget</button>'
        "</form></td></tr>" for d in devices.listing())
    address = load_settings()["portal"].get("url") or "this portal's address"
    if code:
        add = (f"<div class=flash>On the phone or browser to add, open <strong>{escape(address)}</strong> "
               f"and type <strong><code>{escape(code)}</code></strong>. It works once, within "
               f"{devices.CODE_MINUTES} minutes. On an iPhone, open it from the Home Screen icon if you "
               "use one: the icon keeps its own pairing, apart from Safari.</div>")
    else:
        add = ('<form method=post action="/devices/code"><button>Add a phone or browser</button></form>')
    body = (f"<h1>Devices</h1><div class=sub>The browsers that can open {escape(brand())}. Each was "
            "paired once with a one-time code.</div><table><tr><th>Device</th><th>Paired</th>"
            f"<th>Last seen</th><th></th></tr>{rows}</table><h2>Add one</h2>{add}")
    resp = Response(page("Devices", body, "/devices"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/devices/code")
def devices_code():
    return devices_page(devices.new_code())


@app.post("/devices/<device_id>/forget")
def devices_forget(device_id: str):
    devices.forget(device_id)
    return redirect("/devices", 303)


# ---------- lists (Apple Reminders) ----------

def _ek(*args: str):
    out = subprocess.run([str(BUTLER_EK), *args], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return json.loads(out.stdout)


@app.get("/lists")
def lists():
    rem = load_settings()["reminders"]
    body = ["<h1>Lists</h1><div class=sub>These are your Apple Reminders lists; changes sync to your phones.</div>"]
    for key, label in (("todo_list", "To do now"), ("shopping_list", "Shopping")):
        name = rem[key]
        body.append(f"<h2>{label} <span class=sub>({escape(name)})</span></h2>")
        try:
            items = _ek("reminders", "--list", name)
        except Exception as e:
            body.append(f"<p>Couldn't read Reminders: {escape(str(e))}</p>")
            continue
        for it in items:
            due = f' <span class=chip>due {it["due"]}</span>' if it.get("due") else ""
            body.append(f'<div class=step><form class=inline method=post action="/lists/complete">'
                        f'<input type=hidden name=id value="{escape(it["id"])}">'
                        f'<button aria-label="Done: {escape(it["title"], quote=True)}">✓</button></form> '
                        f'{escape(it["title"])}{due}</div>')
        if not items:
            body.append("<p class=sub>Nothing here.</p>")
        body.append(f'<form method=post action="/lists/add" style="margin-top:8px">'
                    f'<input type=hidden name=list value="{escape(name)}">'
                    f'<label class=vh for="add-{key}">Add to {escape(name)}</label>'
                    f'<input type=text id="add-{key}" name=title placeholder="Add to {escape(name)}" required> '
                    f'<button>Add</button></form>')
    return page("Lists", "".join(body), "/lists")


@app.post("/lists/add")
def lists_add():
    rem = load_settings()["reminders"]
    name, title = request.form.get("list"), (request.form.get("title") or "").strip()
    if name not in (rem["todo_list"], rem["shopping_list"]) or not title:
        abort(400)
    _ek("add-reminder", "--list", name, "--title", title)
    return redirect("/lists", code=303)


@app.post("/lists/complete")
def lists_complete():
    _ek("complete-reminder", "--id", request.form.get("id", ""))
    return redirect("/lists", code=303)


def main() -> None:
    from waitress import serve
    port = load_settings()["portal"]["port"]
    serve(app, host="127.0.0.1", port=port, threads=4)


if __name__ == "__main__":
    main()
