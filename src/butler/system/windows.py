"""Windows: secrets in Credential Manager, the schedule in Task Scheduler, Narrator or NVDA.

Credential Manager is called through ctypes (advapi32's CredReadW and CredWriteW), so Butler
needs no extra package for it. Each secret is a generic credential whose target name is the
service name (`butler-anthropic`), with the user name `butler`, as on the Mac."""
import contextlib
import ctypes
import msvcrt
import os
import subprocess
from ctypes import wintypes

from ..schedule import FOLDER

NAME = "windows"
TASK_TICK = FOLDER + "tick"

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
# Windows refuses a credential blob over 5 * 512 bytes.
CRED_MAX_BLOB = 2560
SCREEN_READERS = ("narrator.exe", "nvda.exe")
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _advapi32():
    lib = ctypes.WinDLL("advapi32", use_last_error=True)
    lib.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    lib.CredReadW.restype = wintypes.BOOL
    lib.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    lib.CredWriteW.restype = wintypes.BOOL
    lib.CredFree.argtypes = [ctypes.c_void_p]
    lib.CredFree.restype = None
    return lib


def _read(service: str) -> bytes | None:
    lib = _advapi32()
    found = ctypes.POINTER(_CREDENTIAL)()
    if not lib.CredReadW(service, CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise OSError(error, f"Credential Manager couldn't read {service}")
    try:
        cred = found.contents
        return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        lib.CredFree(found)


def get_secret(service: str) -> str | None:
    try:
        blob = _read(service)
    except OSError:
        return None
    return (blob.decode().strip() or None) if blob is not None else None


def has_secret(service: str) -> bool:
    try:
        return _read(service) is not None
    except OSError:
        return False


def store_secret(service: str, secret: str) -> None:
    blob = secret.encode()
    if len(blob) > CRED_MAX_BLOB:
        raise ValueError(f"too long for Credential Manager ({len(blob)} bytes)")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=service, CredentialBlobSize=len(blob),
                       CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                       Persist=CRED_PERSIST_LOCAL_MACHINE, UserName="butler")
    if not _advapi32().CredWriteW(ctypes.byref(cred), 0):
        raise OSError(ctypes.get_last_error(), f"Credential Manager couldn't save {service}")


def scheduled() -> bool:
    try:
        return subprocess.run(["schtasks", "/Query", "/TN", TASK_TICK],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def screen_reader_on() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             timeout=10).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return any(f'"{name}"' in out for name in SCREEN_READERS)


def open_file(path: str) -> None:
    os.startfile(path)


@contextlib.contextmanager
def keep_awake():
    """SetThreadExecutionState holds off sleep until this thread says otherwise, or ends."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def lock_exclusive(f) -> None:
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
