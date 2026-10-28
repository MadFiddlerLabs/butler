"""The Windows installer as a household meets it (#40): downloaded by a browser, double-clicked,
and clicked through. Run by windows-e2e.yml's standard-user job, as that account
(tests/e2e/run_as.py), on the runner's desktop; not part of the suite.

  - The download lands in Downloads as a browser names a second copy, "Mr Pickles (1).exe", with
    the Mark of the Web (a Zone.Identifier saying it came from the internet), as Edge and Chrome
    write it.
  - It opens the way a double-click does (ShellExecute), so Windows' own checks for a download
    run. A security prompt (the "Open File" warning, SmartScreen) is answered as a person would,
    and named in the output.
  - Then the wizard: each page it shows is printed, and its buttons are pressed as a person would
    (Next, Install, Finish). No /VERYSILENT.

Windows are found by their titles with plain Win32 calls, which never wait on a busy window, and
the windows on screen are printed whenever they change, so a stuck run shows what it was looking
at. Inno Setup's pages and the "Open File" warning are ordinary Win32 controls, pressed with
pywinauto's win32 backend; SmartScreen's prompt is XAML, pressed through UI Automation.

Exits 0 once the wizard has finished, 1 otherwise.

  python tests/e2e/windows_wizard.py <Butler-Setup.exe> ["Mr Pickles (1).exe"]
"""
import ctypes
import os
import re
import shutil
import sys
import time
from ctypes import wintypes
from pathlib import Path

from pywinauto import Application

WIZARD = re.compile(r"^Setup - Butler")
BUTTONS = ("Finish", "Install", "Next")          # pressed in this order of preference
OPEN_FILE = "Open File - Security Warning"
SMARTSCREEN = ("Windows protected your PC", "Microsoft Defender SmartScreen", "Windows Defender SmartScreen")

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows() -> list[tuple[int, str, int]]:
    """Visible top-level windows with a title: (handle, title, process id)."""
    found = []

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append((hwnd, buffer.value, pid.value))
        return True
    user32.EnumWindows(WNDENUMPROC(each), 0)
    return found


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


def win32_window(hwnd: int):
    return Application(backend="win32").connect(handle=hwnd, timeout=5).window(handle=hwnd)


def labels(window) -> list[str]:
    seen = []
    for child in window.descendants():
        try:
            text = child.window_text().strip()
        except Exception:  # noqa: BLE001 - a control can go as the page changes
            continue
        if text and text not in seen:
            seen.append(text)
    return seen


def press(window, label: str) -> bool:
    """Presses the enabled, visible button with this label (ignoring its & accelerator)."""
    for child in window.descendants(class_name_re="(?i).*button.*"):
        try:
            if (child.window_text().replace("&", "").strip() == label
                    and child.is_enabled() and child.is_visible()):
                child.click()        # BM_CLICK: no need for the window to be in front
                print(f"  pressed {label}", flush=True)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def smartscreen(hwnd: int) -> None:
    """SmartScreen's prompt: More info, then Run anyway, as a person would."""
    window = Application(backend="uia").connect(handle=hwnd, timeout=5).window(handle=hwnd)
    for label in ("More info", "Run anyway"):
        for item in window.descendants():
            try:
                if item.window_text().strip() == label:
                    item.invoke()
                    print(f"  pressed {label}", flush=True)
                    time.sleep(1)
                    break
            except Exception:  # noqa: BLE001
                continue


def main(installer: Path, name: str) -> int:
    exe = download(installer, name)
    print(f"downloaded to {exe}, with the Mark of the Web", flush=True)
    os.startfile(str(exe))      # what a double-click does
    print("opened it, as a double-click does", flush=True)
    deadline = time.monotonic() + 20 * 60
    on_screen: set[str] = set()
    last_page: list[str] = []
    finished = False
    while time.monotonic() < deadline:
        time.sleep(1)
        now = windows()
        titles = {f"{title!r} (pid {pid})" for _, title, pid in now}
        if titles != on_screen:
            print(f"on screen: {sorted(titles)}", flush=True)
            on_screen = titles
        wizard_open = False
        for hwnd, title, _ in now:
            try:
                if title == OPEN_FILE:
                    window = win32_window(hwnd)
                    print(f"security prompt {title!r}: {labels(window)}", flush=True)
                    press(window, "Run")
                elif any(s in title for s in SMARTSCREEN):
                    print(f"security prompt {title!r}", flush=True)
                    smartscreen(hwnd)
                elif WIZARD.match(title):
                    wizard_open = True
                    window = win32_window(hwnd)
                    page = labels(window)
                    if page != last_page:
                        print(f"wizard page: {page}", flush=True)
                        last_page = page
                    for label in BUTTONS:
                        if press(window, label):
                            finished = finished or label == "Finish"
                            break
                elif title in ("Setup", "Butler", "Error"):
                    # A message box from Setup: an error, or "couldn't get its Python".
                    print(f"message box {title!r}: {labels(win32_window(hwnd))}", flush=True)
                    return 1
            except Exception as e:  # noqa: BLE001 - the window went while we looked; look again
                print(f"  ({title!r}: {type(e).__name__}: {e})", flush=True)
        if finished and not wizard_open:
            print("the wizard finished", flush=True)
            return 0
    print(f"the wizard didn't finish in 20 minutes; its last page: {last_page}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "Mr Pickles (1).exe"))
