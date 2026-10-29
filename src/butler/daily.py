"""Builds and sends the morning brief.

    python -m butler.daily              # build and send
    python -m butler.daily --dry-run    # build only; writes out/brief-YYYY-MM-DD.{html,txt}
    python -m butler.daily --no-llm     # skip Claude: raw headlines, no prep notes
    python -m butler.daily --scheduled  # launchd: send once per day, after send_hour in your timezone

brief_check() builds today's brief for an update's health check (updates.py), sending nothing.

Each section fails on its own. A broken section becomes a line in the footer, and
if the whole run breaks, a short failure email still goes out.
"""

import argparse
import logging
import socket
import traceback
from datetime import date, datetime, time, timedelta

from . import calendar_source, charts, finance, headsup, llm, mailer, networth, practice, projects, updates, weather
from .agenda import load_agenda
from .config import ROOT, NotSetUp, brand, load_settings, tz
from .fmt import primary_code
from .news import fetch_stories
from .render import Brief, to_html, to_text

log = logging.getLogger("butler")


def _short(e: Exception) -> str:
    """One readable line for the email footer; the full error goes to the log."""
    if words := llm.trouble(e):
        return words
    return str(e).splitlines()[0][:120] if str(e) else type(e).__name__


def _not_connected(not_set_up: list[str], part: str) -> None:
    if part not in not_set_up:
        not_set_up.append(part)


def build(settings: dict, day: date, use_llm: bool, preview_projects: bool = False,
          update_budget: bool = False, snapshot: bool = True) -> Brief:
    zone = tz(settings)
    now = datetime.now(zone)
    problems: list[str] = []
    not_set_up: list[str] = []
    labels = {r["key"]: r["label"] for r in settings["news"]["regions"]}

    agenda = None
    try:
        agenda = load_agenda(settings, day, zone)
        log.info("calendar: %d today, %d upcoming", len(agenda.today), len(agenda.upcoming))
    except NotSetUp as e:
        log.info("calendar: %s", e)
        not_set_up.append("calendar")
    except Exception as e:
        log.exception("calendar failed")
        problems.append(f"calendar unavailable ({_short(e)})")

    prep: list[dict] = []
    phrase = None
    if agenda and use_llm:
        try:
            prep, phrase = llm.flag_prep(settings, agenda)
            log.info("prep: %d flagged", len(prep))
        except NotSetUp as e:
            log.info("prep: %s", e)
            _not_connected(not_set_up, "Claude")
        except Exception as e:
            llm.log_failure("prep", e)
            problems.append(f"couldn't flag events to prepare for ({_short(e)})")

    news: list[dict] = []
    try:
        stories, feed_problems = fetch_stories(settings, now)
        problems += feed_problems
        log.info("news: %d candidate stories", len(stories))
        if use_llm and stories:
            try:
                news = _fill_empty_regions(settings, stories, llm.pick_news(settings, stories, day), day)
            except NotSetUp as e:
                log.info("news selection: %s", e)
                _not_connected(not_set_up, "Claude")   # the headlines go out unedited, as lesson 4.5 expects
            except Exception as e:
                llm.log_failure("news selection", e)
                problems.append(f"news shown unedited ({_short(e)})")
        if not news:
            news = _raw_headlines(settings, stories)
    except Exception as e:
        log.exception("news failed")
        problems.append(f"news unavailable ({_short(e)})")

    money, chart_pngs = None, {}
    try:
        money = finance.build(day, _last_brief_time(day, zone))
        log.info("money: %d transactions, %d new", money.tx_count, len(money.new))
        try:
            chart_pngs = {"mtd": charts.month_to_date(money)}
            cats = charts.categories(money, finance.load_rules()["rules"]["day_to_day"])
            if cats:
                chart_pngs["categories"] = cats
        except Exception as e:
            log.exception("charts failed")
            problems.append(f"charts unavailable ({_short(e)})")
    except NotSetUp as e:
        log.info("money: %s", e)
        not_set_up.append("money")
    except Exception as e:
        log.exception("money failed")
        problems.append(f"money section unavailable ({_short(e)})")

    nw_line = ""
    # Snapshots are real history: only take one for today, never for a --date preview or a check.
    if snapshot and money and day == datetime.now(zone).date():
        try:
            cfg = settings.get("networth", {})
            now, ch = networth.change_since(day, 1)
            reasons = networth.reasons_from(money.txs, ch.since, day) if ch else {}
            reasons.update(cfg.get("reasons") or {})
            nw_line = networth.line(now, ch, "since the last brief", cfg.get("notable_daily", 2500),
                                    cfg.get("labels"), reasons)
            fx = networth.fx_line(now, networth.previous(day))
            rate = networth.second_rate(now)
            if update_budget and rate:
                from .budget import update_fx
                changed = update_fx(rate, day)
                if changed:
                    fx += f" The budget now plans at this rate (was {changed[0]:.4f} {primary_code()})."
            nw_line = f"{nw_line}\n{fx}" if fx else nw_line
        except Exception as e:
            log.exception("net worth failed")
            problems.append(f"net worth unavailable ({_short(e)})")
        if not networth.in_emails(settings):
            nw_line = ""     # the snapshot and the budget's exchange rate above still happen

    todos: list[dict] = []
    try:
        found = calendar_source.ask(["reminders", "--list", settings["reminders"]["todo_list"]],
                                    timeout=60, settings=settings)
        # Overdue and dated items first, then the rest in list order.
        todos = sorted(found, key=lambda t: (t.get("due") is None, t.get("due") or ""))
    except calendar_source.Failed as e:
        log.warning("reminders: %s", e)
        problems.append("to-dos unavailable")
    except NotSetUp as e:
        log.info("reminders: %s", e)
        not_set_up.append("to-dos")   # the lessons' word: Reminders on a Mac, Google Tasks on Google
    except Exception as e:
        log.exception("reminders failed")
        problems.append(f"to-dos unavailable ({_short(e)})")

    next_up = None
    if settings.get("projects", {}).get("in_brief") or preview_projects:
        try:
            loaded = projects.load()
            next_up = projects.today_view(loaded, day) if loaded else None
        except Exception as e:
            log.exception("projects failed")
            problems.append(f"projects unavailable ({_short(e)})")

    weather_line = ""
    try:
        weather_line = weather.today_line()
    except Exception:
        log.exception("weather failed")
    heads = [headsup.line(i) for i in headsup.items(day, 14)]
    try:
        update_lines = updates.brief_lines(brand(settings))
    except Exception:
        log.exception("update line failed")
        update_lines = []

    return Brief(day=day, agenda=agenda, prep=prep, news=news, region_labels=labels,
                 headsup=heads, weather=weather_line, phrase=phrase,
                 phrase_label=llm.phrase_label(settings),
                 practice=practice.summary(day),
                 people=settings.get("people", []), money=money, next_up=next_up,
                 charts=chart_pngs, networth=nw_line, todos=todos,
                 lists_app=calendar_source.lists_app(settings),
                 portal_url=settings.get("portal", {}).get("url", ""),
                 problems=problems, not_set_up=not_set_up, updates=update_lines)


# Errors only code makes. A section that can't reach its service (a timeout, a refused
# connection, a bad answer) fails with something else: that's the world's problem, not a release's.
CODE_ERRORS = (AttributeError, ImportError, KeyError, NameError, SyntaxError, TypeError)


class _Failures(logging.Handler):
    """Every section logs its exception before the brief moves on; this keeps those records."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_info and record.exc_info[1] is not None:
            self.records.append(record)


def brief_check() -> dict:
    """Builds and renders today's brief from the household's own settings and services, for an
    update's health check (updates.py). No Claude, nothing sent, no files in out/, no net worth
    snapshot, no budget change; Lunch Money's answers are cached in data/raw/ as on any brief.
    Returns {"failures": [{"where", "error", "detail"}]}: the brief itself breaking, or a section
    failing with one of CODE_ERRORS."""
    caught = _Failures()
    log.addHandler(caught)
    failures = []
    try:
        settings = load_settings()
        day = datetime.now(tz(settings)).date()
        brief = build(settings, day, use_llm=False, preview_projects=True, snapshot=False)
        to_text(brief)
        to_html(brief)
        to_html(brief, image_src=lambda name: f"chart-{day}-{name}.png")
    except Exception as e:  # noqa: BLE001 - whatever breaks the brief breaks the update
        log.exception("brief check failed")
        failures.append(_failure("the brief", e))
    finally:
        log.removeHandler(caught)
    failures += [_failure(r.getMessage(), r.exc_info[1]) for r in caught.records
                 if r.getMessage() != "brief check failed" and isinstance(r.exc_info[1], CODE_ERRORS)]
    return {"failures": failures}


def _failure(where: str, e: BaseException) -> dict:
    return {"where": where, "error": type(e).__name__, "detail": _short(e)}


def _last_brief_time(day: date, zone) -> datetime:
    """When the previous brief went out, so 'new spending' means new since then."""
    earlier = sorted(p for p in (ROOT / "out").glob("sent-*") if p.name < f"sent-{day}")
    if earlier:
        return datetime.fromtimestamp(earlier[-1].stat().st_mtime, zone)
    return datetime.combine(day - timedelta(days=1), time(7), zone)


def _fill_empty_regions(settings: dict, stories, news: list[dict], day) -> list[dict]:
    """Claude sometimes picks nothing for a region, or picks refs that match no story, and the
    region vanished from the brief (a persona's Europe and World, 28 Oct 2026). A region with new
    stories but no pick shows its feed's own first headlines instead, as when Claude is down.
    Stories already in the brief or sent recently stay out, so an empty region can stay empty."""
    covered = {c["story"].region for c in news}
    shown = {c["story"].link for c in news if c["story"].link}
    sent = {r["link"] for r in llm.recent_news(day)}
    rest = [s for s in stories if s.region not in covered and not (s.link and (s.link in shown or s.link in sent))]
    extra = _raw_headlines(settings, rest)
    if not extra:
        return news
    log.info("news: Claude picked nothing for %s; showing the feeds' own headlines",
             ", ".join(sorted({c["story"].region for c in extra})))
    order = [r["key"] for r in settings["news"]["regions"]]
    return sorted(news + extra, key=lambda c: order.index(c["story"].region) if c["story"].region in order else len(order))


def _raw_headlines(settings: dict, stories) -> list[dict]:
    """Each region's first stories, as the feeds give them, before Claude is connected. A story
    in two regions' feeds (BBC's World feed carries its Europe stories) shows once, in the first
    region; the next story takes its place."""
    n = settings["news"]["stories_per_region"]
    out, seen = [], set()
    for region in settings["news"]["regions"]:
        picks = []
        for s in stories:
            key = s.link or " ".join(s.title.lower().split())
            if s.region == region["key"] and key not in seen and len(picks) < n:
                seen.add(key)
                picks.append(s)
        out += [{"story": s, "headline": s.title, "summary": s.summary[:200]} for s in picks]
    return out


def _online() -> bool:
    try:
        socket.create_connection(("smtp.gmail.com", 465), timeout=5).close()
        return True
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="The morning brief")
    parser.add_argument("--dry-run", action="store_true", help="build but don't send")
    parser.add_argument("--no-llm", action="store_true", help="skip Claude")
    parser.add_argument("--date", type=date.fromisoformat, help="brief for this day (YYYY-MM-DD)")
    parser.add_argument("--scheduled", action="store_true",
                        help="send only if past send_hour and not yet sent today")
    args = parser.parse_args()

    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "out").mkdir(exist_ok=True)
    logging.basicConfig(
        filename=ROOT / "logs" / "butler.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger().addHandler(logging.StreamHandler())

    settings = load_settings()
    now = datetime.now(tz(settings))
    day = args.date or now.date()

    # launchd ticks every few minutes. Keying on the household's timezone, not the Mac's
    # clock, keeps the brief at send_hour local time when the laptop travels.
    sent_marker = ROOT / "out" / f"sent-{day}"
    if args.scheduled:
        if now.hour < settings["email"]["send_hour"] or sent_marker.exists():
            return
        if not _online():
            log.info("offline; will retry on the next tick")
            return
    log.info("brief for %s (dry_run=%s, llm=%s)", day, args.dry_run, not args.no_llm)

    try:
        brief = build(settings, day, use_llm=not args.no_llm, preview_projects=args.dry_run,
                      update_budget=not args.dry_run)
        html, text = to_html(brief), to_text(brief)
        # Sent briefs are the archive the portal shows; dry runs go to out/preview/.
        folder = ROOT / "out" / ("preview" if args.dry_run else "")
        folder.mkdir(parents=True, exist_ok=True)
        for name, png in brief.charts.items():
            (folder / f"chart-{day}-{name}.png").write_bytes(png)
        preview = to_html(brief, image_src=lambda name: f"chart-{day}-{name}.png")
        (folder / f"brief-{day}.html").write_text(preview)
        (folder / f"brief-{day}.txt").write_text(text)
        if args.dry_run:
            log.info("dry run: wrote out/preview/brief-%s.html", day)
            return
        # The brief is built once a day. If Gmail refuses it, it waits in the outbox and a
        # later tick re-sends it as built (mailer.flush), with no new Claude calls.
        sent = mailer.send_or_queue(settings, f"brief-{day}", brief.subject, text, html, images=brief.charts)
        sent_marker.touch()
        if brief.updates:
            updates.mark_announced()
        llm.record_news(day, [n for n in brief.news if n.get("summary") is not None])
        if sent:
            log.info("sent to %s", ", ".join(settings["email"]["recipients"]))
        else:
            log.info("brief queued; it goes out when Gmail takes it")
    except Exception:
        log.exception("brief failed")
        if args.dry_run:
            raise
        # Silence is the worst failure: tell them something broke.
        mailer.send_or_queue(settings, f"brief-failed-{day}", f"{brand(settings)} · brief failed {day}",
                             "This morning's brief failed to build.\n\n" + traceback.format_exc())
        sent_marker.touch()
        raise


if __name__ == "__main__":
    main()
