"""Runs a command as another Windows account, the way that person would have it: their own
profile, their own environment (USERPROFILE, PATH, APPDATA from their registry), their own
Credential Manager. windows-e2e.yml's standard-user job uses it to install and start Butler as
a household's non-admin account, "BYB Test", as on the owner's test PC.

PowerShell's Start-Process -Credential runs as the account but hands it the caller's
environment, so %USERPROFILE% was the admin's. CreateProcessWithLogonW with no environment
builds the account's own. The password comes from an environment variable, never the command
line.

  python run_as.py --user "BYB Test" --password-env BYB_PASSWORD --out C:\\byb\\out.txt -- <command line>

The command line goes to the account's cmd.exe, so %USERPROFILE% and the like are the account's,
and its output goes to --out (which the account must be able to write). Prints that output and
exits with the command's exit code; 124 if it ran past --timeout."""
import argparse
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

LOGON_WITH_PROFILE = 0x1
CREATE_UNICODE_ENVIRONMENT = 0x400
CREATE_NEW_CONSOLE = 0x10
WAIT_TIMEOUT = 0x102


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def run_as(user: str, password: str, command: str, cwd: str, timeout: float) -> int:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.CreateProcessWithLogonW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPWSTR,
        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    line = ctypes.create_unicode_buffer(f'"{cmd}" /d /c "{command}"')
    startup = STARTUPINFOW(cb=ctypes.sizeof(STARTUPINFOW))
    info = PROCESS_INFORMATION()
    if not advapi32.CreateProcessWithLogonW(user, ".", password, LOGON_WITH_PROFILE, None, line,
                                            CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_CONSOLE, None, cwd,
                                            ctypes.byref(startup), ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), f"couldn't start a process as {user}")
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000)) == WAIT_TIMEOUT:
            return 124
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hThread)
        kernel32.CloseHandle(info.hProcess)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--password-env", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    a = parser.parse_args()
    words = a.command[1:] if a.command[:1] == ["--"] else a.command
    out = Path(a.out)
    out.unlink(missing_ok=True)
    # In brackets, so `a & b` sends both to the file, not only b.
    command = f'({" ".join(words)}) > "{out}" 2>&1'
    code = run_as(a.user, os.environ[a.password_env], command, a.cwd or str(out.parent), a.timeout)
    if out.exists():
        # The runner's console is cp1252: Claude Code's installer prints "‼" (28 Oct 2026).
        sys.stdout.reconfigure(errors="replace")
        sys.stdout.write(out.read_text(encoding="utf-8", errors="replace"))
    print(f"[run_as: exit {code}{' (timed out)' if code == 124 else ''}]", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
