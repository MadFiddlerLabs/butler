"""launchctl, for a persona's standard Mac account that isn't the one at the screen (a hosted
runner's console is its own admin). Such an account has no GUI launchd domain (gui/<uid>), so
Butler's launchd/install.sh, which loads its jobs there, would fail where a student's works. This
does what tests/e2e/standard-user.sh does: the same jobs, from the same plists, go into the
account's own background domain (user/<uid>), with one added line, LimitLoadToSessionType
Background, since launchd loads an agent there only if it says so. `launchctl list` also shows
that domain's jobs, as a student's shows their GUI domain's. Every call is logged to
~/persona-fakes/launchctl.log. Everything else goes to the real launchctl unchanged.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import events  # noqa: E402

REAL = "/bin/launchctl"


def mapped(args: list[str], uid: int) -> list[str]:
    gui, user = f"gui/{uid}", f"user/{uid}"
    return [user + a[len(gui):] if a == gui or a.startswith(gui + "/") else a for a in args]


def services(printed: str) -> list[tuple[str, str, str]]:
    """(pid, last exit status, label) for each job in `launchctl print user/<uid>`'s services block,
    as `launchctl list` writes them: "-" for a job that isn't running."""
    block = re.search(r"\n\s*services = \{\n(.*?)\n\s*\}", printed, re.S)
    rows = []
    for line in (block[1].splitlines() if block else []):
        parts = line.split()
        if len(parts) == 3:
            pid, status, label = parts
            rows.append(("-" if pid == "0" else pid, status if status.lstrip("-").isdigit() else "0", label))
    return rows


def main(args: list[str]) -> int:
    uid = os.getuid()
    with open(events.FAKES / "launchctl.log", "a", encoding="utf-8") as log:
        log.write(" ".join(args) + "\n")
    if args[:1] == ["bootstrap"] and len(args) >= 3 and args[1] == f"gui/{uid}":
        for plist in args[2:]:
            if subprocess.run(["/usr/bin/plutil", "-extract", "LimitLoadToSessionType", "raw", plist],
                              capture_output=True).returncode != 0:
                subprocess.run(["/usr/bin/plutil", "-insert", "LimitLoadToSessionType", "-string", "Background", plist])
    if args == ["list"]:
        listed = subprocess.run([REAL, "list"], capture_output=True, text=True).stdout
        sys.stdout.write(listed)
        have = {line.split("\t")[-1] for line in listed.splitlines()}
        printed = subprocess.run([REAL, "print", f"user/{uid}"], capture_output=True, text=True).stdout
        for pid, status, label in services(printed):
            if label not in have:
                print(f"{pid}\t{status}\t{label}")
        return 0
    return subprocess.run([REAL, *mapped(args, uid)]).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
