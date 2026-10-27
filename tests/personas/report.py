"""One report from every persona run (tests/personas/run_persona.py writes a run.json each): the
findings grouped by lesson, most-reported first, what each run reached, Butler's own checks, and
what the round cost. Plain Python: no model reads it, so it costs nothing.

    python tests/personas/report.py <folder of run folders> > report.md
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

SEVERITY = {"blocker": 0, "major": 1, "minor": 2, "polish": 3}


def load(folder: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(folder.rglob("run.json"))]


def render(runs: list[dict]) -> str:
    if not runs:
        return "# Persona tests\n\nNo run finished: check the jobs' logs.\n"
    total = sum(r["cost_usd"]["total"] for r in runs)
    out = [f"# Persona tests: {len(runs)} runs, ${total:.2f}", ""]
    out += ["| Persona | Computer | Run | Outcome | Reached | Signed in | First brief | Claude Code | Cost | Minutes |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(runs, key=lambda r: (r["persona"], r["platform"], r["seed"])):
        c = r["checks"]
        out.append(f"| {r['name']} | {r['platform']} | {r['seed']} | {r['outcome']['outcome']} | {r['reached']} "
                   f"({r['lessons_read']}/{r['lessons_total']}) | {'yes' if c['signed_in'] else 'no'} | "
                   f"{'yes' if c['first_brief'] else 'no'} | {'yes' if c['claude_code_opened'] else 'no'} | "
                   f"${r['cost_usd']['total']:.2f} | {r['minutes']} |")
    crashes = [r for r in runs if r["checks"]["tracebacks"]]
    if crashes:
        out += ["", f"**Python tracebacks on screen in {len(crashes)} runs:** "
                + ", ".join(f"{r['name']}/{r['platform']}/{r['seed']}" for r in crashes)]

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
    for lesson, items in sorted(by_lesson.items(), key=lambda kv: (-len({(r['persona'], r['platform'], r['seed']) for r, _ in kv[1]}),
                                                                  min(SEVERITY.get(f.get('severity'), 3) for _, f in kv[1]))):
        who = {(r["name"], r["platform"]) for r, _ in items}
        out += [f"### {lesson} ({len(items)} findings, from {', '.join(f'{n}/{p}' for n, p in sorted(who))})", ""]
        for r, f in sorted(items, key=lambda x: SEVERITY.get(x[1].get("severity"), 3)):
            line = f"- **{f.get('severity', '?')}** ({r['name']}, {r['platform']}, run {r['seed']}"
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
        out.append(f"- **{r['name']}** ({r['platform']}, run {r['seed']}; {'; '.join(r['cards'])}): "
                   f"{r['report'].get('how_it_felt', '')}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render(load(Path(sys.argv[1]))))
