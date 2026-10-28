"""What a persona sees and does on a Windows screen, outside a terminal the harness opened itself:
the windows Butler's installer shows (the security prompt, the setup wizard, its message boxes),
and the console window the installer opens at the end, which the persona then works in.

- Windows on screen are read and answered with plain Win32 calls: EnumWindows, WM_GETTEXT and a
  button's own WM_COMMAND, so nothing waits on a busy window and nothing needs the window in front.
  SmartScreen's prompt is XAML: it's read and pressed through UI Automation (pywinauto).
- The installer's console is a real console window, not one the harness made. ConsoleWindow reads
  what it shows (ReadConsoleOutputCharacterW) and types into it (WriteConsoleInputW) from a small
  helper process attached to it (`python windows_screen.py serve <pid>`), as a person at the
  screen reads and types. It has the harness's Terminal methods, so the persona can't tell them
  apart.

Windows only, and CI only, like the rest of the harness.
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from screens import console_keys, window_form  # noqa: E402
from terminal import Terminal  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
WM_GETTEXT, WM_GETTEXTLENGTH, WM_COMMAND, BN_CLICKED, BM_GETCHECK = 0x000D, 0x000E, 0x0111, 0, 0x00F0
SMTO_ABORTIFHUNG = 0x0002
SMARTSCREEN = ("Windows protected your PC", "SmartScreen")
DETACHED_PROCESS = 0x00000008


# ---------- windows on screen ----------
def _text(hwnd) -> str:
    """A window's or control's text, also from another process (GetWindowText can't read those)."""
    length = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(hwnd, WM_GETTEXTLENGTH, 0, 0, SMTO_ABORTIFHUNG, 2000, ctypes.byref(length)):
        return ""
    buffer = ctypes.create_unicode_buffer(length.value + 1)
    got = ctypes.c_size_t()
    user32.SendMessageTimeoutW(hwnd, WM_GETTEXT, length.value + 1, ctypes.cast(buffer, ctypes.c_void_p).value,
                               SMTO_ABORTIFHUNG, 2000, ctypes.byref(got))
    return buffer.value


def _class(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def top_windows() -> list[tuple[int, str, int]]:
    """(handle, title, process id) of each visible top-level window with a title."""
    found = []

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = _text(hwnd)
            if title:
                found.append((hwnd, title, _pid(hwnd)))
        return True
    user32.EnumWindows(WNDENUMPROC(each), 0)
    return found


def _children(hwnd) -> list[int]:
    found = []

    def each(child, _):
        found.append(child)
        return True
    user32.EnumChildWindows(hwnd, WNDENUMPROC(each), 0)
    return found


def form(hwnd: int, title: str) -> dict:
    """The window as the persona sees it (screens.window_form): its text, buttons and boxes."""
    if any(s in title for s in SMARTSCREEN):
        return _uia_form(hwnd, title)
    controls = [(_kind(c), _text(c), bool(user32.IsWindowEnabled(c)), bool(user32.IsWindowVisible(c)))
                for c in _children(hwnd)]
    return window_form(title, controls)


def _kind(control: int) -> str:
    """A control's class, with a plain Win32 "Button" told apart by its style: a check box, a radio
    button and a group box are all of class Button."""
    cls = _class(control)
    if cls == "Button":
        style = user32.GetWindowLongW(control, -16) & 0xF     # GWL_STYLE, the BS_ type
        if style in (2, 3, 5, 6):
            return "CheckBox"
        if style in (4, 9):
            return "RadioButton"
        if style == 7:
            return "GroupBox"
    return cls


def answer(hwnd: int, title: str, choice: dict, attempt: int = 1) -> bool:
    """Ticks the boxes the persona ticked and clicks its button. False if there's no such button.
    A click that didn't take is tried again another way (`attempt` 2 and 3)."""
    if any(s in title for s in SMARTSCREEN):
        return _uia_press(hwnd, choice.get("button", ""))
    controls = [(c, _kind(c), _text(c).replace("&", "").strip().lower()) for c in _children(hwnd)]
    wanted = {t.strip().lower() for t in choice.get("ticked") or []}
    for c, kind, label in controls:
        if label in wanted and ("CheckBox" in kind or "RadioButton" in kind):
            checked = ctypes.c_size_t()
            user32.SendMessageTimeoutW(c, BM_GETCHECK, 0, 0, SMTO_ABORTIFHUNG, 2000, ctypes.byref(checked))
            if not checked.value:
                _click(hwnd, c, attempt)
    button = (choice.get("button") or "").replace("&", "").strip().lower()
    for c, kind, label in controls:
        if ("Button" in kind and "CheckBox" not in kind and "RadioButton" not in kind and label == button
                and user32.IsWindowEnabled(c) and user32.IsWindowVisible(c)):
            _click(hwnd, c, attempt)
            return True
    return False


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _click(window: int, control: int, attempt: int) -> None:
    """A mouse click on a control, as messages, so nothing needs the window in front: first the
    button down and up a real click sends (what pywinauto does); then BM_CLICK; then the
    WM_COMMAND a click ends in, to the window itself. All posted, so a dialog that opens from the
    click can't block the harness."""
    if attempt <= 1:
        rect = RECT()
        user32.GetClientRect(control, ctypes.byref(rect))
        where = ((rect.bottom // 2) << 16) | (rect.right // 2 & 0xFFFF)
        user32.PostMessageW(control, 0x0201, 0x0001, where)   # WM_LBUTTONDOWN, MK_LBUTTON
        user32.PostMessageW(control, 0x0202, 0, where)        # WM_LBUTTONUP
    elif attempt == 2:
        user32.PostMessageW(control, 0x00F5, 0, 0)            # BM_CLICK
    else:
        control_id = user32.GetDlgCtrlID(control)
        user32.PostMessageW(window, WM_COMMAND, (BN_CLICKED << 16) | (control_id & 0xFFFF), control)


def _uia(hwnd):
    from pywinauto import Application
    return Application(backend="uia").connect(handle=hwnd, timeout=5).window(handle=hwnd)


def _uia_form(hwnd: int, title: str) -> dict:
    window = _uia(hwnd)
    controls = []
    for item in window.descendants():
        try:
            kind = item.element_info.control_type or ""
            controls.append(("Button" if kind in ("Button", "Hyperlink") else kind, item.window_text(), True, True))
        except Exception:  # noqa: BLE001 - an element can go as the prompt changes
            continue
    return window_form(title, controls)


def _uia_press(hwnd: int, label: str) -> bool:
    for item in _uia(hwnd).descendants():
        try:
            if item.window_text().strip().lower() == label.strip().lower():
                item.invoke()
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


# ---------- processes ----------
def image(pid: int) -> str:
    """The program a process runs, or "" if this account can't see it."""
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def pids() -> list[int]:
    array = (wintypes.DWORD * 8192)()
    needed = wintypes.DWORD()
    ctypes.WinDLL("psapi").EnumProcesses(array, ctypes.sizeof(array), ctypes.byref(needed))
    return [p for p in array[: needed.value // ctypes.sizeof(wintypes.DWORD)] if p]


def setup_pids(download: Path) -> set[int]:
    """The installer's processes: the downloaded .exe, and the copy of itself Inno Setup runs from
    a temporary folder (is-XXXXX.tmp\\<name>.tmp)."""
    want = os.path.normcase(str(download))
    found = set()
    for pid in pids():
        path = os.path.normcase(image(pid))
        if path == want or (path.endswith(".tmp") and "\\is-" in path):
            found.add(pid)
    return found


def processes(name: str) -> list[dict]:
    """{pid, parent, command} of this account's processes called `name`, newest last."""
    script = (f"Get-CimInstance Win32_Process -Filter \"Name = '{name}'\" | Sort-Object CreationDate | "
              "ForEach-Object { [pscustomobject]@{ pid = $_.ProcessId; parent = $_.ParentProcessId; command = $_.CommandLine } } "
              "| ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    if not out:
        return []
    found = json.loads(out)
    return [p for p in (found if isinstance(found, list) else [found]) if p.get("command")]


def kill(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def mark_of_the_web(path: Path, page: str) -> None:
    """The Zone.Identifier a browser writes on a download from the internet, which makes Windows
    check the file when it's opened."""
    with open(f"{path}:Zone.Identifier", "w", encoding="utf-8") as f:
        f.write(f"[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl={page}\r\nHostUrl={page}\r\n")


def task_info(name: str) -> dict | None:
    """Task Scheduler's record of one of this account's tasks: {LastRunTime (ms), LastTaskResult,
    State}, or None if there's no such task."""
    folder, _, task = name.rpartition("\\")
    script = (f"$t = Get-ScheduledTask -TaskPath '{folder}\\' -TaskName '{task}' -ErrorAction SilentlyContinue; if ($t) {{ "
              "$i = $t | Get-ScheduledTaskInfo; $a = $t.Actions[0]; "
              "[pscustomobject]@{ last = [long]($i.LastRunTime.ToUniversalTime() - [datetime]'1970-01-01').TotalSeconds; "
              "result = $i.LastTaskResult; state = [string]$t.State; command = $a.Execute; arguments = $a.Arguments; "
              "folder = $a.WorkingDirectory } | ConvertTo-Json -Compress }")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    return json.loads(out) if out else None


# ---------- the installer's console window ----------
class ConsoleWindow:
    """A console window someone else opened (the installer, the Start menu's Butler), read and typed
    into through a helper attached to it. The same methods as terminal.Terminal."""

    since = Terminal.since

    def __init__(self, pid: int):
        self.pid = pid
        self._proc = subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__)), "serve", str(pid)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, creationflags=DETACHED_PROCESS)
        self._lock = threading.Lock()
        first = self._call(None)
        if not first.get("ok"):
            raise RuntimeError(f"couldn't reach the console of process {pid}: {first.get('error')}")
        self._lines: list[str] = []
        self._shown: list[str] = []
        self._changed = time.monotonic()
        self._alive = True
        threading.Thread(target=self._poll, daemon=True).start()

    def _call(self, request: dict | None) -> dict:
        with self._lock:
            if request is not None:
                self._proc.stdin.write((json.dumps(request) + "\n").encode())
                self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        return json.loads(line) if line else {"ok": False, "error": "the helper ended", "alive": False}

    def _poll(self) -> None:
        while self._alive:
            got = self._call({"op": "read"})
            lines = got.get("lines", self._lines)
            if lines != self._lines:
                self._lines, self._changed = lines, time.monotonic()
            self._alive = got.get("alive", False)
            time.sleep(0.5)

    @property
    def screen(self) -> str:
        return "\n".join(self._lines)

    @property
    def open(self) -> bool:
        return self._alive

    def type(self, line: str) -> None:
        self.keys(line + "\r")

    def keys(self, text: str) -> None:
        if self._alive:
            self._call({"op": "keys", "text": text})

    def read(self, quiet: float = 2.0, limit: float = 120.0) -> str:
        start = time.monotonic()
        time.sleep(0.6)   # one poll, so what was just typed has shown
        while time.monotonic() - start < limit and self._alive and time.monotonic() - self._changed < quiet:
            time.sleep(0.1)
        return self.since(list(self._lines))

    def wait_new(self, limit: float = 30.0) -> bool:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline and self._alive:
            if self._lines != self._shown:
                return True
            time.sleep(0.2)
        return False

    def close(self) -> None:
        if self._alive:
            self._call({"op": "close"})
            self._alive = False


# The helper's side: one process, attached to the window's console for as long as it's open.
class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short), ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD), ("uChar", ctypes.c_wchar), ("dwControlKeyState", wintypes.DWORD)]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_size", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]


def serve(pid: int) -> int:
    out, inp = sys.stdout.buffer, sys.stdin.buffer

    def reply(data: dict) -> None:
        out.write((json.dumps(data) + "\n").encode())
        out.flush()

    if not kernel32.AttachConsole(pid):
        reply({"ok": False, "error": f"AttachConsole failed ({ctypes.get_last_error()})"})
        return 1
    kernel32.SetConsoleCtrlHandler(None, True)    # Ctrl+C typed into the window isn't for this helper
    kernel32.CreateFileW.restype = wintypes.HANDLE
    share, rw = 0x1 | 0x2, 0x80000000 | 0x40000000
    conout = kernel32.CreateFileW("CONOUT$", rw, share, None, 3, 0, None)
    conin = kernel32.CreateFileW("CONIN$", rw, share, None, 3, 0, None)
    user32.VkKeyScanW.restype = ctypes.c_short
    # A wide buffer, as the harness's own windows have (BUTLER_COLUMNS), so a long line reads as
    # one line rather than wrapped mid-word. A console in a pseudo-console may refuse: then it wraps.
    info = CONSOLE_SCREEN_BUFFER_INFO()
    if kernel32.GetConsoleScreenBufferInfo(conout, ctypes.byref(info)) and info.dwSize.X < 1000:
        kernel32.SetConsoleScreenBufferSize(conout, COORD(1000, max(info.dwSize.Y, 3000)))

    def count() -> int:
        listed = (wintypes.DWORD * 64)()
        return kernel32.GetConsoleProcessList(listed, 64)

    def lines() -> list[str]:
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not kernel32.GetConsoleScreenBufferInfo(conout, ctypes.byref(info)):
            return []
        width = info.dwSize.X
        rows = max(info.dwCursorPosition.Y, info.srWindow.Bottom) + 1
        buffer = ctypes.create_unicode_buffer(width * rows)
        got = wintypes.DWORD()
        kernel32.ReadConsoleOutputCharacterW(conout, buffer, width * rows, COORD(0, 0), ctypes.byref(got))
        text = buffer[: got.value]
        shown = [text[i * width:(i + 1) * width].rstrip() for i in range(rows)]
        while shown and not shown[-1]:
            shown.pop()
        return shown

    def write(text: str) -> None:
        records = []
        for key in console_keys(text):
            if key == "ctrl_c":
                if records:
                    _write_records(conin, records)
                    records = []
                kernel32.GenerateConsoleCtrlEvent(0, 0)
                continue
            vk, char = key
            shift = 0
            if vk is None:
                scan = user32.VkKeyScanW(char)
                vk, shift = (scan & 0xFF, (scan >> 8) & 1) if scan != -1 else (0, 0)
            code = user32.MapVirtualKeyW(vk, 0) if vk else 0
            for down in (True, False):
                record = INPUT_RECORD()
                record.EventType = 1   # KEY_EVENT
                k = record.Event.KeyEvent
                k.bKeyDown, k.wRepeatCount, k.wVirtualKeyCode, k.wVirtualScanCode = down, 1, vk, code
                k.uChar, k.dwControlKeyState = char, 0x0010 if shift else 0
                records.append(record)
        if records:
            _write_records(conin, records)

    reply({"ok": True})
    for raw in inp:
        request = json.loads(raw)
        if request["op"] == "read":
            alive = count() > 1
            reply({"lines": lines(), "alive": alive})
            if not alive:          # everything in the window has ended: let it close, as it would
                kernel32.FreeConsole()
                return 0
        elif request["op"] == "keys":
            write(request["text"])
            reply({"ok": True})
        elif request["op"] == "close":    # the window's close button: everything in it ends
            listed = (wintypes.DWORD * 64)()
            for i in range(kernel32.GetConsoleProcessList(listed, 64)):
                if listed[i] != os.getpid():
                    kill(listed[i])
            reply({"ok": True})
            return 0
    return 0


def _write_records(conin, records: list) -> None:
    array = (INPUT_RECORD * len(records))(*records)
    written = wintypes.DWORD()
    kernel32.WriteConsoleInputW(conin, array, len(records), ctypes.byref(written))


if __name__ == "__main__":
    if sys.argv[1:2] != ["serve"]:
        sys.exit("usage: windows_screen.py serve <pid>")
    sys.exit(serve(int(sys.argv[2])))
