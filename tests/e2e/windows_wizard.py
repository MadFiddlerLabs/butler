"""The Windows installer as a household meets it (#40): downloaded by a browser, double-clicked,
and clicked through. Run by windows-e2e.yml's standard-user job, as the runner's own account on
its desktop: a second account has no desktop of its own on a hosted runner, and a double-click
(ShellExecute) as BYB Test hung with no window. Not part of the suite.

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
import threading
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
user32.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_wchar_p,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))


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


def box_text(hwnd: int) -> list[str]:
    """A message box's text, read from each control with WM_GETTEXT (GetWindowText doesn't read
    another process's controls). Read again for a few seconds: a box just opened can be empty."""
    WM_GETTEXT, SMTO_ABORTIFHUNG = 0x000D, 0x0002
    for _ in range(10):
        texts = []

        def each(child, _):
            buffer = ctypes.create_unicode_buffer(4096)
            result = ctypes.c_size_t()
            if user32.SendMessageTimeoutW(child, WM_GETTEXT, 4096, buffer, SMTO_ABORTIFHUNG, 1000,
                                          ctypes.byref(result)) and buffer.value.strip():
                texts.append(buffer.value.strip())
            return True
        user32.EnumChildWindows(hwnd, WNDENUMPROC(each), 0)
        if texts:
            return texts
        time.sleep(0.5)
    return []


def uia_text(hwnd: int) -> list[str]:
    """Text as UI Automation reads it: a task dialog draws its text without child windows."""
    try:
        window = Application(backend="uia").connect(handle=hwnd, timeout=5).window(handle=hwnd)
        return [t for t in dict.fromkeys(i.window_text().strip() for i in window.descendants()) if t]
    except Exception as e:  # noqa: BLE001
        return [f"(UI Automation: {type(e).__name__}: {e})"]


def screenshot(name: str) -> None:
    """The whole screen, kept beside the job's other files (windows-e2e.yml uploads C:\\byb\\*.png)."""
    try:
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(Path(r"C:\byb") / name)
        print(f"  screenshot: C:\\byb\\{name}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  (no screenshot: {type(e).__name__}: {e})", flush=True)


def class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


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


def answer_open_file(hwnd: int, window, tries: int) -> None:
    """Run, on the "Open File" warning. A BM_CLICK didn't always take on ARM (28 Oct 2026: the
    warning stayed up for 20 minutes), so it's pressed through UI Automation next, then with
    Alt+R from the keyboard, as a person might."""
    if tries <= 2:
        press(window, "Run")
    elif tries <= 4:
        uia = Application(backend="uia").connect(handle=hwnd, timeout=5).window(handle=hwnd)
        for item in uia.descendants(control_type="Button"):
            if item.window_text().replace("&", "").strip() == "Run":
                item.invoke()
                print("  pressed Run (UI Automation)", flush=True)
                return
    elif tries <= 6:
        window.set_focus()
        window.type_keys("%r")
        print("  pressed Alt+R", flush=True)
    elif tries == 7:
        screenshot("open-file-warning.png")


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
    # What a double-click does. On its own thread: for a file from the internet, Windows asks
    # "Open File - Security Warning" inside this call and waits there for the answer, which the
    # loop below gives (the run of 28 Oct 2026 hung here for an hour).
    threading.Thread(target=os.startfile, args=(str(exe),), daemon=True).start()
    print("opened it, as a double-click does", flush=True)
    deadline = time.monotonic() + 20 * 60
    on_screen: set[str] = set()
    last_page: list[str] = []
    tries: dict[int, int] = {}
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
                    tries[hwnd] = tries.get(hwnd, 0) + 1
                    window = win32_window(hwnd)
                    if tries[hwnd] == 1:
                        print(f"security prompt {title!r}: {labels(window)}", flush=True)
                    answer_open_file(hwnd, window, tries[hwnd])
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
                elif title in ("Setup", "Butler", "Error") and class_name(hwnd) == "#32770":
                    # A dialog's class: Inno's own main window is also titled "Setup", and is
                    # there all through the install (28 Oct 2026, the screenshot showed no box).
                    # A message box from Setup: an error, or "couldn't get its Python".
                    print(f"message box {title!r}: {box_text(hwnd)} {uia_text(hwnd)}", flush=True)
                    screenshot("message-box.png")
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
