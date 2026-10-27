"""bin/butler-ek on a persona-test Mac. The first time, like the real helper, it makes macOS ask
for Calendar and then Reminders access: the persona answers those windows. Allowed, it answers
from tests/fakes/fixtures.py (tests/fakes/ek_stub.py); refused, it fails as the real one does."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "fakes"))
import events  # noqa: E402
import ek_stub  # noqa: E402

GRANTS = events.FAKES / "ek-grants.json"
ASKS = {"calendar": ("Calendar", "“butler-ek” would like full access to your Calendar.",
                     "This lets butler-ek read and add events in all your calendars."),
        "reminders": ("Reminders", "“butler-ek” would like full access to your Reminders.",
                      "This lets butler-ek read and add reminders in all your lists.")}


def main() -> int:
    if sys.argv[1:2] and sys.argv[1] in ek_stub.COMMANDS:
        grants = json.loads(GRANTS.read_text()) if GRANTS.exists() else {}
        for kind, (what, title, text) in ASKS.items():
            if kind not in grants:
                answer = events.ask("dialog", {"title": title, "text": text, "box": False, "hidden": False,
                                               "buttons": ["Don't Allow", "Allow Full Access"]})
                grants[kind] = bool(answer) and answer.get("button", "").lower().startswith("allow")
                GRANTS.write_text(json.dumps(grants))
            if not grants[kind]:
                print(f"butler-ek: no {what} access. Grant it in System Settings > Privacy & Security > {what}.",
                      file=sys.stderr)
                return 1
    ek_stub.HERE = events.FAKES      # its call log, which the persona's Reminders and Calendar show
    return ek_stub.main()


if __name__ == "__main__":
    sys.exit(main())
