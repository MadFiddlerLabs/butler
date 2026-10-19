"""Fetches recent headlines from the RSS feeds in settings.yaml."""

import calendar
import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser

USER_AGENT = "butler/1.0 (personal morning brief; https://github.com/MadFiddlerLabs/butler)"


@dataclass
class Story:
    ref: str
    region: str
    source: str
    title: str
    summary: str
    link: str
    published: datetime | None


def _clean(text: str, limit: int) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def fetch_stories(settings: dict, now: datetime) -> tuple[list[Story], list[str]]:
    """Returns (stories, problems). A dead feed is a problem, not a failure."""
    cfg = settings["news"]
    cutoff = now - timedelta(hours=cfg["max_age_hours"])
    stories: list[Story] = []
    problems: list[str] = []

    for region in cfg["regions"]:
        count = 0
        for feed in region["feeds"]:
            parsed = feedparser.parse(feed["url"], agent=USER_AGENT)
            if parsed.bozo and not parsed.entries:
                problems.append(f"{feed['name']} feed unavailable")
                continue
            for entry in parsed.entries[:40]:
                stamp = entry.get("published_parsed") or entry.get("updated_parsed")
                published = (
                    datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc) if stamp else None
                )
                if published and published < cutoff:
                    continue
                count += 1
                stories.append(Story(
                    ref=f"{region['key']}-{count}",
                    region=region["key"],
                    source=feed["name"],
                    title=_clean(entry.get("title", ""), 200),
                    summary=_clean(entry.get("summary", ""), 400),
                    link=entry.get("link", ""),
                    published=published,
                ))
    return stories, problems
