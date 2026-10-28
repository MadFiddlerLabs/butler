"""The Windows installer as a household meets it (#40): downloaded by a browser, double-clicked,
and clicked through. Run by windows-e2e.yml's standard-user job, as that account
(tests/e2e/run_as.py), on the runner's desktop; not part of the suite.

  - The download lands in Downloads as a browser names a second copy, "Mr Pickles (1).exe", with
    the Mark of the Web (a Zone.Identifier saying it came from the internet), as Edge and Chrome
    write it.
  - It opens the way a double-click does (ShellExecute), so Windows' own checks for a download
    run. Any security prompt (the "Open File" warning, SmartScreen) is answered as a person would,
    and named in the output.
  - Then the wizard, through UI Automation: every page it shows is printed, and its buttons are
    pressed as a person would (Next, Install, Finish). No /VERYSILENT.

Prints each window and page it saw, and exits 0 once the wizard has finished, 1 otherwise.

  python tests/e2e/windows_wizard.py <Butler-Setup.exe> ["Mr Pickles (1).exe"]
"""
import os
import re
import shutil
import sys
import time
from pathlib import Path

from pywinauto import Desktop

WIZARD = re.compile(r"^Setup - Butler")
BUTTONS = ("Finish", "Install", "Next")          # pressed in this order of preference
WARNINGS = {
    "Open File - Security Warning": ("Run",),
    "Windows protected your PC": ("More info", "Run anyway"),
}


def download(installer: Path, name: str) -> Path:
    """A copy in Downloads, carrying the Mark of the Web as a browser leaves it."""
    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    target = downloads / name
    shutil.copyfile(installer, target)
    with open(f"{target}:Zone.Identifier", "w", encoding="utf-8") as f:
        f.write("[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://buildyourbutler.com/download/\r\n"
                "HostUrl=https://buildyourbutler.com/files/Butler-latest.exe\r\n")
    return target


def texts(window) -> list[str]:
    seen = []
    for item in window.descendants():
        try:
            text = item.window_text().strip()
        except Exception:  # noqa: BLE001 - a control can go as the page changes
            continue
        if text and text not in seen:
            seen.append(text)
    return seen


def button(window, label: str):
    for item in window.descendants(control_type="Button"):
        try:
            if item.window_text().replace("&", "").strip() == label and item.is_enabled() and item.is_visible():
                return item
        except Exception:  # noqa: BLE001
            continue
    return None


def press(item) -> None:
    try:
        item.invoke()
    except Exception:  # noqa: BLE001 - not every button has the invoke pattern
        item.click_input()


def main(installer: Path, name: str) -> int:
    exe = download(installer, name)
    print(f"downloaded to {exe}, with the Mark of the Web", flush=True)
    os.startfile(str(exe))      # what a double-click does
    desktop = Desktop(backend="uia")
    deadline = time.monotonic() + 20 * 60
    last_page: list[str] = []
    finished = False
    while time.monotonic() < deadline:
        time.sleep(1)
        for window in desktop.windows():
            try:
                title = window.window_text()
            except Exception:  # noqa: BLE001
                continue
            for warning, steps in WARNINGS.items():
                if warning in title or warning in " ".join(texts(window)[:5]):
                    print(f"security prompt: {title!r}: {texts(window)}", flush=True)
                    for label in steps:
                        found = button(window, label)
                        if found:
                            print(f"  pressed {label}", flush=True)
                            press(found)
                            time.sleep(1)
            if not WIZARD.match(title):
                continue
            page = texts(window)
            if page != last_page:
                print(f"wizard page: {page}", flush=True)
                last_page = page
            for label in BUTTONS:
                found = button(window, label)
                if found:
                    print(f"  pressed {label}", flush=True)
                    press(found)
                    if label == "Finish":
                        finished = True
                    break
        # A message box from Setup (an error, or "couldn't get its Python") is a failure.
        for window in desktop.windows():
            try:
                title = window.window_text()
            except Exception:  # noqa: BLE001
                continue
            if title in ("Setup", "Butler") or title.startswith("Error"):
                print(f"message box {title!r}: {texts(window)}", flush=True)
                return 1
        if finished:
            # The wizard closes once Finish is pressed.
            if not any(WIZARD.match(w.window_text()) for w in desktop.windows()):
                print("the wizard finished", flush=True)
                return 0
    print(f"the wizard didn't finish in 20 minutes; its last page: {last_page}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "Mr Pickles (1).exe"))
