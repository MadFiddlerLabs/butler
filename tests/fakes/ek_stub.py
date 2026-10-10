"""Stands in for bin/butler-ek in the integration tests. It answers from fixtures.py, logs every
call to bin/ek-calls.jsonl, and refuses any command the real helper doesn't have, so a test
fails if the code ever asks to edit or delete something.

The test home installs it as bin/butler-ek with a shebang for the running Python.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))   # fixtures.py sits next to the stub

import fixtures  # noqa: E402

COMMANDS = {"calendars", "events", "reminder-lists", "reminders", "ensure-list", "add-reminder",
            "complete-reminder", "completed", "add-event"}


def option(args: list[str], name: str) -> str | None:
    flag = f"--{name}"
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def main() -> int:
    command, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    with open(HERE / "ek-calls.jsonl", "a") as log:
        log.write(json.dumps({"command": command, "args": args}) + "\n")
    if command not in COMMANDS:
        print(f"butler-ek: unknown command '{command}'", file=sys.stderr)
        return 1
    if command == "calendars":
        out = fixtures.CALENDARS
    elif command == "events":
        out = fixtures.events(datetime.fromisoformat(option(args, "start")),
                              datetime.fromisoformat(option(args, "end")))
    elif command == "reminder-lists":
        out = [{"title": t, "source": "iCloud", "id": t, "writable": True} for t in fixtures.REMINDERS]
    elif command == "reminders":
        name = option(args, "list")
        if name not in fixtures.REMINDERS:
            print(f"butler-ek: no Reminders list named '{name}'", file=sys.stderr)
            return 1
        out = fixtures.REMINDERS[name]
    elif command == "completed":
        out = [{"title": "Return the library books", "completed": option(args, "start") + "T10:00:00Z"}]
    elif command == "ensure-list":
        out = {"title": option(args, "list"), "source": "iCloud", "id": option(args, "list"), "writable": True}
    elif command == "add-reminder":
        out = {"id": "new-reminder", "list": option(args, "list"), "title": option(args, "title")}
    elif command == "complete-reminder":
        out = {"completed": option(args, "id"), "title": "Done"}
    else:   # add-event
        out = {"event_id": "new-event", "calendar": option(args, "calendar"), "title": option(args, "title")}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
