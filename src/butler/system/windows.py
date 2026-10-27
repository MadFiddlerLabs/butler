"""Windows: secrets in Credential Manager, the schedule in Task Scheduler, Narrator or NVDA.

Credential Manager is called through ctypes (advapi32's CredReadW and CredWriteW), so Butler
needs no extra package for it. Each secret is a generic credential whose target name is the
service name (`butler-anthropic`), with the user name `butler`, as on the Mac. An update's
installer is checked with WinVerifyTrust (wintrust.dll), Authenticode as Windows itself does it."""
import contextlib
import ctypes
import msvcrt
import os
import subprocess
import winreg
from ctypes import wintypes

from ..schedule import FOLDER

NAME = "windows"
PASSWORD_STORE = "Credential Manager"
INSTALLER = "Butler-Setup.exe"
# The user's own environment in the registry: what a new window's PATH starts from.
ENVIRONMENT = "Environment"
TASK_TICK = FOLDER + "tick"

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
# Windows refuses a credential blob over 5 * 512 bytes.
CRED_MAX_BLOB = 2560
SCREEN_READERS = ("narrator.exe", "nvda.exe")
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
WTD_UI_NONE = 2
WTD_REVOKE_WHOLECHAIN = 1
WTD_CHOICE_FILE = 1
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2
WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT = 0x80
CERT_NAME_ATTR_TYPE = 3
OID_COMMON_NAME = b"2.5.4.3"
OID_ORGANIZATION = b"2.5.4.10"


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


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("pcwszFilePath", wintypes.LPCWSTR),
                ("hFile", wintypes.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]


class _WINTRUST_DATA(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
                ("pFile", ctypes.POINTER(_WINTRUST_FILE_INFO)), ("dwStateAction", wintypes.DWORD),
                ("hWVTStateData", wintypes.HANDLE), ("pwszURLReference", wintypes.LPCWSTR),
                ("dwProvFlags", wintypes.DWORD), ("dwUIContext", wintypes.DWORD),
                ("pSignatureSettings", ctypes.c_void_p)]


class _CRYPT_PROVIDER_CERT(ctypes.Structure):
    # Only the start of the structure: the signer's own certificate is all that's read.
    _fields_ = [("cbStruct", wintypes.DWORD), ("pCert", ctypes.c_void_p)]


class _CRYPT_PROVIDER_SGNR(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("sftVerifyAsOf", wintypes.FILETIME),
                ("csCertChain", wintypes.DWORD), ("pasCertChain", ctypes.POINTER(_CRYPT_PROVIDER_CERT))]


# WINTRUST_ACTION_GENERIC_VERIFY_V2: Authenticode, the policy Windows applies to a download.
_VERIFY_V2 = ((0x00AAC56B, 0xCD44, 0x11D0), (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))


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


def add_to_path(bin_dir: str) -> bool:
    """Adds the folder to the user's PATH in the registry (no admin rights needed), then tells
    open programs, so a window opened from now on finds `butler`. The window it runs in keeps
    its old PATH."""
    wanted = os.path.normcase(os.path.normpath(bin_dir))
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, ENVIRONMENT, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                value, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                value, kind = "", winreg.REG_EXPAND_SZ
            parts = [p for p in str(value).split(";") if p]
            if any(os.path.normcase(os.path.normpath(os.path.expandvars(p))) == wanted for p in parts):
                return False
            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                kind = winreg.REG_EXPAND_SZ
            winreg.SetValueEx(key, "Path", 0, kind, ";".join([*parts, bin_dir]))
    except OSError:
        return False
    # WM_SETTINGCHANGE with "Environment", as System Properties sends it, to every top window.
    result = wintypes.DWORD()
    ctypes.WinDLL("user32").SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000,
                                                ctypes.byref(result))
    return True


def butler_app() -> str | None:
    """Windows has no Butler app yet: the way back is `butler` in a new Terminal window."""
    return None


def python_command(root) -> list[str]:
    return [str(root / ".venv" / "Scripts" / "python.exe"), "-X", "utf8"]


def _powershell() -> str:
    # The 64-bit Windows PowerShell, which every Windows 10 and 11 has.
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                        "powershell.exe")


def bootstrap_command(root) -> list[str]:
    return [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts" / "bootstrap.ps1")]


def install_command(installer: str, log: str) -> list[str]:
    """Inno Setup, silently, into the same %USERPROFILE%\\Butler: no admin password, no prompt,
    and butler.installed opens nothing. /LOG keeps the installer's own account of it."""
    return [installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}"]


def portal_restart() -> tuple[list[str], list[list[str]]]:
    task = FOLDER + "portal"
    return ["schtasks", "/Query", "/TN", task], [["schtasks", "/End", "/TN", task], ["schtasks", "/Run", "/TN", task]]


def _name(crypt32, cert, oid: bytes) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    crypt32.CertGetNameStringW(cert, CERT_NAME_ATTR_TYPE, 0, oid, buffer, len(buffer))
    return buffer.value


def installer_signer(path: str) -> dict | None:
    """WinVerifyTrust with the Authenticode policy and no UI: a signature that is intact, made by
    a certificate whose chain Windows trusts, for code signing, not revoked (every certificate but
    the root is checked online) and in date or timestamped while it was. Then the signer's
    certificate's common name and organization."""
    wintrust = ctypes.WinDLL("wintrust")
    crypt32 = ctypes.WinDLL("crypt32")
    wintrust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(_GUID), ctypes.POINTER(_WINTRUST_DATA)]
    wintrust.WinVerifyTrust.restype = wintypes.LONG
    wintrust.WTHelperProvDataFromStateData.argtypes = [wintypes.HANDLE]
    wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
    wintrust.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.POINTER(_CRYPT_PROVIDER_SGNR)
    crypt32.CertGetNameStringW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_char_p,
                                           wintypes.LPWSTR, wintypes.DWORD]
    crypt32.CertGetNameStringW.restype = wintypes.DWORD

    (d1, d2, d3), d4 = _VERIFY_V2
    action = _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))
    file_info = _WINTRUST_FILE_INFO(ctypes.sizeof(_WINTRUST_FILE_INFO), str(path), None, None)
    data = _WINTRUST_DATA(cbStruct=ctypes.sizeof(_WINTRUST_DATA), dwUIChoice=WTD_UI_NONE,
                          fdwRevocationChecks=WTD_REVOKE_WHOLECHAIN, dwUnionChoice=WTD_CHOICE_FILE,
                          pFile=ctypes.pointer(file_info), dwStateAction=WTD_STATEACTION_VERIFY,
                          dwProvFlags=WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT)
    trusted = wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data)) == 0
    try:
        if not trusted:
            return None
        provider = wintrust.WTHelperProvDataFromStateData(data.hWVTStateData)
        signer = wintrust.WTHelperGetProvSignerFromChain(provider, 0, False, 0) if provider else None
        if not signer or not signer.contents.csCertChain:
            return None
        cert = signer.contents.pasCertChain[0].pCert
        return {"name": _name(crypt32, cert, OID_COMMON_NAME), "organization": _name(crypt32, cert, OID_ORGANIZATION)}
    finally:
        data.dwStateAction = WTD_STATEACTION_CLOSE
        wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))


def lock_exclusive(f) -> None:
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
