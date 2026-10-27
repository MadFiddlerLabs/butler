"""osascript on a persona-test Mac: a `display dialog` with a box (scripts/store-secret.sh's
window for a key or password) goes to the persona, who types into it and clicks a button.
Anything else goes to the real osascript."""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import events  # noqa: E402

script = " ".join(a for a in sys.argv[1:] if a != "-e")
dialog = re.search(r'display dialog "([^"]*)"', script)
if not dialog or "default answer" not in script:
    os.execv("/usr/bin/osascript", ["/usr/bin/osascript", *sys.argv[1:]])
title = re.search(r'with title "([^"]*)"', script)
buttons = re.findall(r'"([^"]*)"', re.search(r"buttons \{([^}]*)\}", script)[1]) if "buttons {" in script else ["Cancel", "OK"]
answer = events.ask("dialog", {"title": title[1] if title else "", "text": dialog[1], "box": True,
                               "hidden": "hidden answer" in script, "buttons": buttons})
if not answer or answer.get("button", "").lower() in ("cancel", ""):
    print("execution error: User canceled. (-128)", file=sys.stderr)
    sys.exit(1)
print(answer.get("text", ""))
