"""Per-module coverage floors for the code where a bug costs the most: money, replies, the DKIM
check, the portal gate, updates, the course account, the first run and the Google calendar. The total floor is `fail_under` in
.coveragerc; these are on top of it.

    python -m coverage json -q && python scripts/check_coverage.py coverage.json

Prints a Markdown table (the test job appends it to its summary) and exits 1 when a module is
below its floor. When you add tests to a module, raise its floor to just under the new figure.
Coverage counts lines and branches (branch = true), measured on Linux in CI.
"""

import json
import sys
from pathlib import Path

FLOORS = {
    "finance.py": 98,
    "budget_changes.py": 96,
    "replies.py": 92,
    "inbox.py": 90,
    "portal.py": 85,
    "devices.py": 93,
    "updates.py": 86,
    "progress.py": 94,
    "account.py": 92,
    "privacy.py": 99,
    "networth.py": 93,
    "fmt.py": 99,
    "claude_usage.py": 95,
    "gcal.py": 93,
    "calendar_source.py": 99,
    "first_run.py": 93,
}


def main(path: str) -> int:
    files = json.loads(Path(path).read_text())["files"]
    by_module = {Path(name).name: f["summary"]["percent_covered"] for name, f in files.items()}
    rows, failed = [], []
    for module, floor in FLOORS.items():
        got = by_module.get(module)
        ok = got is not None and got >= floor
        rows.append(f"| `{module}` | {'missing' if got is None else f'{got:.1f}%'} | {floor}% | "
                    f"{'ok' if ok else '**below the floor**'} |")
        if not ok:
            failed.append(module)
    print("### Coverage floors for the high-risk modules\n")
    print("| Module | Covered | Floor | |\n|---|---|---|---|")
    print("\n".join(rows))
    if failed:
        print(f"\nBelow the floor: {', '.join(failed)}. Add tests, or explain in the pull request why not.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
