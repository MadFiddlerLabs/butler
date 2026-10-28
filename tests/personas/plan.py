"""The persona round's runs, for personas.yml's matrix: each persona on each computer asked for,
`runs` times, and the round's ceiling. Reads RUNS, PLATFORMS, BUDGET and ONLY from the
environment (the workflow's inputs) and prints the job outputs.

    RUNS=1 PLATFORMS=all BUDGET=6 ONLY= python3 tests/personas/plan.py
"""
import json
import os
import sys
from pathlib import Path

import yaml

# Each computer: the lessons it reads (mac or windows) and its runner. windows-arm is Windows 11
# on ARM, as the owner's test PC is.
COMPUTERS = {"mac": ("mac", "macos-latest"), "windows": ("windows", "windows-2025"),
             "windows-arm": ("windows", "windows-11-arm")}
ALIASES = {"both": ["mac", "windows"], "all": list(COMPUTERS)}
CEILING = 400


def plan(personas: list[str], only: str, platforms: str, runs: int, budget: float) -> list[dict]:
    wanted = only.split()
    if set(wanted) - set(personas):
        raise SystemExit(f"no such persona: {sorted(set(wanted) - set(personas))}; there are {personas}")
    chosen = [p for p in personas if not wanted or p in wanted]
    asked = ALIASES.get(platforms.strip(), platforms.split())
    if not asked or set(asked) - set(COMPUTERS):
        raise SystemExit(f"platforms: {platforms!r}; choose from {list(COMPUTERS)}, both or all")
    include = [{"persona": p, "platform": COMPUTERS[c][0], "computer": c, "seed": s, "os": COMPUTERS[c][1]}
               for p in chosen for c in asked for s in range(1, runs + 1)]
    if len(include) * budget > CEILING:
        raise SystemExit(f"{len(include)} runs at ${budget} could spend over ${CEILING}: lower runs or budget")
    return include


def main() -> None:
    personas = list(yaml.safe_load((Path(__file__).parent / "personas.yaml").read_text())["personas"])
    include = plan(personas, os.environ.get("ONLY", ""), os.environ.get("PLATFORMS", "all"),
                   int(os.environ.get("RUNS", "1")), float(os.environ.get("BUDGET", "6")))
    print("matrix=" + json.dumps({"include": include}))
    print("windows=" + json.dumps(any(r["platform"] == "windows" for r in include)))


if __name__ == "__main__":
    sys.exit(main())
