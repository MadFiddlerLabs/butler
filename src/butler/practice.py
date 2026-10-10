"""A score tracker for one person's practice tests (a driving-theory exam, a language test),
logged by reply ("27/30"). Configured in settings.yaml under score_tracker; off when absent."""

import json
from datetime import date, timedelta

from .config import ROOT, load_settings

SCORES = ROOT / "data" / "practice_scores.jsonl"


def _cfg() -> dict:
    return load_settings().get("score_tracker") or {}


def log(day: date, who: str, score: int, total: int) -> None:
    SCORES.parent.mkdir(exist_ok=True)
    with SCORES.open("a") as f:
        f.write(json.dumps({"date": day.isoformat(), "who": who, "score": score, "total": total}) + "\n")


def summary(today: date) -> str:
    """One line on the trend toward the pass mark, or "" when no tracker is configured."""
    cfg = _cfg()
    if not cfg:
        return ""
    out_of, pass_score = int(cfg["out_of"]), int(cfg["pass_score"])
    mark = f"{pass_score}/{out_of}"
    name = cfg["name"][0].upper() + cfg["name"][1:]
    if not SCORES.exists():
        return (f"{name}: no scores yet. Reply with them (\"{cfg['person']}: {mark}\") to see the "
                f"trend toward the {mark} pass mark.")
    rows = [json.loads(l) for l in SCORES.read_text().splitlines() if l.strip()]
    week = [r for r in rows if r["date"] >= (today - timedelta(days=6)).isoformat()]
    last10 = rows[-10:]
    passes = sum(1 for r in last10 if r["score"] / r["total"] >= pass_score / out_of)
    avg = sum(r["score"] / r["total"] for r in week) / len(week) * out_of if week else None
    out = f"{name}: "
    out += f"{len(week)} tests this week, averaging {avg:.1f}/{out_of}. " if week else "no tests in the last 7 days. "
    out += f"{passes} of the last {len(last10)} at the {mark} pass mark."
    if len(last10) >= 10 and passes >= 8:
        out += " Consistently passing: time to book the exam."
    return out
