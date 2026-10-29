"""`butler claude` for real, in a real console, as a household's account meets it (#40): Claude Code
isn't there yet, so Butler runs Anthropic's own installer in the same window, finds `claude`
without a new window, and opens it. Run by windows-e2e.yml's standard-user job as that account
(tests/e2e/run_as.py), after the installer; not part of the suite.

No key and no sign-in: it stops at Claude Code's first screen (its welcome, theme choice or
sign-in), so it costs nothing. BUTLER_CLAUDE_DRY_RUN is never set.

  python tests/e2e/windows_claude.py "%USERPROFILE%\\Butler"
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from windows_console import Console, check, new_window_environment  # noqa: E402

# What Claude Code shows first, before any key or sign-in: its welcome, the theme choice, or
# the sign-in choice. Any one of them means it started and is waiting for the student.
FIRST_SCREEN = ("Welcome to Claude Code", "Choose the text style", "Select login method",
                "Claude account with subscription", "Let's get started")


def first_screen(screen: str) -> bool:
    """Compared without spaces: the console reads some of Claude Code's lines with the spaces
    gone ("Let'sgetstarted", 28 Oct 2026), drawn as cursor moves rather than characters."""
    flat = "".join(screen.split())
    return any("".join(s.split()) in flat for s in FIRST_SCREEN)


def main(butler: Path) -> None:
    env = new_window_environment()
    env.pop("BUTLER_CLAUDE_DRY_RUN", None)
    home = str(Path.home())
    local = Path(home) / ".local" / "bin" / "claude.exe"
    check("Claude Code isn't installed yet, as on a new computer", not local.exists())
    cmd = [str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"), "/d", "/k"]
    console = Console(cmd, home, env)
    try:
        console.type("butler claude")
        console.wait_for("Install Claude Code now? Press Enter for yes, or type no: ")
        console.type("")
        # Anthropic's installer downloads Claude Code; then Butler asks how it should work.
        console.wait_for("Type 1 or 2, or press Enter for Ask first: ", seconds=600)
        console.type("")
        console.wait_for("Opening Claude Code in your Butler folder.")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and not first_screen(console.text()):
            time.sleep(1)
        screen = console.text()
        print(screen[-4000:], flush=True)
    finally:
        console.close()
    check("Anthropic's installer put claude.exe in ~/.local/bin", local.exists())
    check("Butler found claude in the same window, without asking for a new one",
          "I can't find it yet" not in screen)
    check("the installer didn't fail", "didn't finish" not in screen)
    check("Claude Code's first screen opened", first_screen(screen), screen[-3000:])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
