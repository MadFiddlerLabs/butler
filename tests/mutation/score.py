"""The mutation score, and a floor under it. Run after `mutmut run` and `mutmut export-cicd-stats`:

    python tests/mutation/score.py mutants/mutmut-cicd-stats.json

The score is the share of mutants some test caught (killed or timed out) among those that ran.
"No tests" counts against it: a function no selected test reaches is a gap too. Prints Markdown
(the weekly workflow appends it to its summary) and exits 1 below FLOOR. Raise FLOOR when tests
kill more mutants; docs/TESTING.md says how to read a survivor.
"""

import json
import sys
from pathlib import Path

FLOOR = 95.0


def score(stats: dict) -> float:
    caught = stats["killed"] + stats["timeout"]
    ran = caught + stats["survived"] + stats["no_tests"] + stats.get("suspicious", 0)
    return 100.0 * caught / ran if ran else 0.0


def main(path: str) -> int:
    stats = json.loads(Path(path).read_text())
    got = score(stats)
    print("### Mutation testing\n")
    print("| Killed | Timed out | Survived | No tests | Score | Floor |\n|---|---|---|---|---|---|")
    print(f"| {stats['killed']} | {stats['timeout']} | {stats['survived']} | {stats['no_tests']} | "
          f"{got:.1f}% | {FLOOR:.0f}% |")
    if got < FLOOR:
        print("\nBelow the floor. `mutmut results` lists the survivors; `mutmut show <name>` shows one.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "mutants/mutmut-cicd-stats.json"))
