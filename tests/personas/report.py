"""One report from every persona run (tests/personas/run_persona.py writes a run.json each): the
big picture first (the end-of-run interview, the videos, the diaries), then the findings grouped by
lesson, most-reported first, what each run reached, Butler's own checks, and what the round cost.
Plain Python: no model reads it, so it costs nothing.

    python tests/personas/report.py <folder of run folders> > report.md
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

SEVERITY = {"blocker": 0, "major": 1, "minor": 2, "polish": 3}
# The end-of-run interview's answers (run_persona.INTERVIEW), in the report's words and order.
QUESTIONS = {"installing": "Installing", "first_setup": "The first setup, in the installer's window",
             "the_window": "The window they typed into", "videos": "The videos",
             "when_stuck": "When they didn't know what to do next", "in_my_words": "Butler, in their own words",
             "carry_on_why": "Would they carry on tomorrow?"}


def where(run: dict) -> str:
    """The computer a run was on: mac, windows or windows-arm (older runs say only the platform)."""
    return run.get("computer") or run["platform"]


def load(folder: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(folder.rglob("run.json"))]


def render(runs: list[dict]) -> str:
    if not runs:
        return "# Persona tests\n\nNo run finished: check the jobs' logs.\n"
    total = sum(r["cost_usd"]["total"] for r in runs)
    out = [f"# Persona tests: {len(runs)} runs, ${total:.2f}", ""]
    out += ["| Persona | Computer | Run | Outcome | Reached | Signed in | First brief | Claude Code | Cost | Minutes |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(runs, key=lambda r: (r["persona"], where(r), r["seed"])):
        c = r["checks"]
        out.append(f"| {r['name']} | {where(r)} | {r['seed']} | {r['outcome']['outcome']} | {r['reached']} "
                   f"({r['lessons_read']}/{r['lessons_total']}) | {'yes' if c['signed_in'] else 'no'} | "
                   f"{'yes' if c['first_brief'] else 'no'} | {'yes' if c['claude_code_opened'] else 'no'} | "
                   f"${r['cost_usd']['total']:.2f} | {r['minutes']} |")
    guessed = [r for r in runs if r["checks"].get("harness_answered")]
    if guessed:
        out += ["", "**The harness answered for the persona** (asking it failed) in: "
                + ", ".join(f"{r['name']}/{where(r)}/{r['seed']} ({r['checks']['harness_answered']})" for r in guessed)
                + ". Findings about those answers are the harness's, not the course's."]
    crashes = [r for r in runs if r["checks"]["tracebacks"]]
    if crashes:
        out += ["", f"**Python tracebacks on screen in {len(crashes)} runs:** "
                + ", ".join(f"{r['name']}/{where(r)}/{r['seed']}" for r in crashes)]
    # Where a run's path wasn't a real student's (a hosted runner's limits), so a finding there
    # can be weighed: it may be the stand-in's, not the course's.
    differences: dict[str, set[str]] = defaultdict(set)
    for r in runs:
        for d in r.get("differences") or []:
            differences[d].add(where(r))
    if differences:
        out += ["", "**Where the runs' path differed from a student's:**"]
        out += [f"- ({', '.join(sorted(computers))}) {d}" for d, computers in differences.items()]

    out += big_picture(runs)

    by_lesson: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for r in runs:
        for f in r["report"].get("findings", []):
            by_lesson[f.get("lesson") or "(no lesson)"].append((r, f))
        for n in r["notes"]:
            if n["kind"] in ("confused", "broken", "mismatch", "unsupported"):
                by_lesson[n.get("lesson") or n.get("at") or "(no lesson)"].append(
                    (r, {"severity": "minor" if n["kind"] == "confused" else "major", "what_happened": n["text"],
                         "expected": "", "suggestion": "", "note": n["kind"]}))
    out += ["", "## Findings, by lesson (most-reported first)", ""]
    for lesson, items in sorted(by_lesson.items(), key=lambda kv: (-len({(r['persona'], where(r), r['seed']) for r, _ in kv[1]}),
                                                                  min(SEVERITY.get(f.get('severity'), 3) for _, f in kv[1]))):
        who = {(r["name"], where(r)) for r, _ in items}
        out += [f"### {lesson} ({len(items)} findings, from {', '.join(f'{n}/{p}' for n, p in sorted(who))})", ""]
        for r, f in sorted(items, key=lambda x: SEVERITY.get(x[1].get("severity"), 3)):
            line = f"- **{f.get('severity', '?')}** ({r['name']}, {where(r)}, run {r['seed']}"
            line += f", {f['note']} note" if f.get("note") else ""
            line += f"): {f.get('what_happened', '')}"
            if f.get("expected"):
                line += f" *Expected:* {f['expected']}"
            if f.get("suggestion"):
                line += f" *Suggestion:* {f['suggestion']}"
            out.append(line)
        out.append("")
    out += ["## How it felt", ""]
    for r in runs:
        out.append(f"- **{r['name']}** ({where(r)}, run {r['seed']}; {'; '.join(r['cards'])}): "
                   f"{r['report'].get('how_it_felt', '')}")
    return "\n".join(out) + "\n"


def big_picture(runs: list[dict]) -> list[str]:
    """How it felt, from 10,000 feet: each persona's interview answers side by side, what they
    thought of each video, and their diaries as they went. Runs from before the interview have none."""
    asked = [r for r in runs if r.get("interview") and not r["interview"].get("failed")]
    if not asked and not any(r.get("diary") for r in runs):
        return []
    who = lambda r: f"**{r['name']}** ({where(r)}, run {r['seed']})"  # noqa: E731
    out = ["", "## The big picture", "",
           "| Persona | Computer | Run | Carry on? | Videos watched | Opened, not watched |", "|---|---|---|---|---|---|"]
    for r in sorted(runs, key=lambda r: (r["persona"], where(r), r["seed"])):
        c = r["checks"]
        watched, opened = c.get("videos_watched") or [], c.get("videos_opened") or []
        skipped = sorted(set(opened) - set(watched))
        out.append(f"| {r['name']} | {where(r)} | {r['seed']} | {(r.get('interview') or {}).get('carry_on', '–')} | "
                   f"{', '.join(map(str, watched)) or 'none'} | {', '.join(map(str, skipped)) or 'none'} |")
    for key, label in QUESTIONS.items():
        answers = [(r, r["interview"].get(key)) for r in asked if r["interview"].get(key)]
        if answers:
            out += ["", f"### {label}", ""] + [f"- {who(r)}: {a}" for r, a in answers]
    changes = [(r, r["interview"].get("top_changes") or []) for r in asked]
    if any(c for _, c in changes):
        out += ["", "### The changes they'd make first", ""]
        out += [f"- {who(r)}: " + " ".join(f"{i}. {c}" for i, c in enumerate(cs, 1)) for r, cs in changes if cs]
    videos = sorted(((d.get("module"), r, d) for r in runs for d in r.get("diary") or [] if d.get("kind") == "video"),
                    key=lambda x: (x[0] if x[0] is not None else -1, x[1]["persona"]))
    if videos:
        out += ["", "### Each video, just after watching it", "",
                "| Module | Persona | Helpful? | It got them ready for | They'd change |", "|---|---|---|---|---|"]
        cell = lambda t: str(t or "").replace("|", "/").replace("\n", " ")  # noqa: E731
        out += [f"| {m} | {r['name']} ({where(r)}) | {d.get('helpful', '')} | {cell(d.get('it_prepared_me_for'))} | "
                f"{cell(d.get('i_would_change'))} |" for m, r, d in videos]
    diaries = [(r, [d for d in r.get("diary") or [] if d.get("kind") == "diary"]) for r in runs]
    if any(d for _, d in diaries):
        out += ["", "### Their diaries, as they went", ""]
        for r, lines in diaries:
            if lines:
                out += [f"{who(r)}:", ""] + [f"- *{d.get('moment')}* ({d.get('mood')}): {d.get('text')}" for d in lines] + [""]
    return out


if __name__ == "__main__":
    sys.stdout.write(render(load(Path(sys.argv[1]))))
