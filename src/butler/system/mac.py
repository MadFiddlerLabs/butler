"""macOS: secrets in the login Keychain, the schedule in launchd, VoiceOver."""
import contextlib
import fcntl
import os
import plistlib
import subprocess
from pathlib import Path
from xml.parsers.expat import ExpatError

NAME = "mac"
PASSWORD_STORE = "Keychain"
LABEL = "com.buildyourbutler.butler"
APP_ID = "com.buildyourbutler.butler-app"


def get_secret(service: str) -> str | None:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def has_secret(service: str) -> bool:
    try:
        return subprocess.run(["security", "find-generic-password", "-s", service],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def store_secret(service: str, secret: str) -> None:
    """`security -i` reads its command from stdin, and the secret travels in it hex-encoded (-X),
    so no character in it can break the command. scripts/lib/keychain.sh does the same."""
    command = f"add-generic-password -U -s {service} -a butler -X {secret.encode().hex()}\n"
    subprocess.run(["security", "-i"], input=command, text=True, check=True, capture_output=True)


def scheduled() -> bool:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split("\t")[-1] == LABEL for line in out.splitlines())


def screen_reader_on() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "VoiceOver"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def open_file(path: str) -> None:
    subprocess.run(["open", path], check=False)


@contextlib.contextmanager
def keep_awake():
    """caffeinate -i holds off idle sleep, and -w lets go if this process dies first."""
    try:
        proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except OSError:
        proc = None
    try:
        yield
    finally:
        if proc:
            proc.terminate()


def add_to_path(bin_dir: str) -> bool:
    """One line in ~/.zprofile, as add_to_path in scripts/lib/install.sh writes it."""
    profile = Path.home() / ".zprofile"
    line = f'export PATH="{bin_dir}:$PATH"'
    try:
        if profile.exists() and line in profile.read_text(errors="replace"):
            return False
        with open(profile, "a") as f:
            f.write(f"\n# Added by Butler: lets you type `butler` in Terminal.\n{line}\n")
    except OSError:
        return False
    return True


def butler_app() -> str | None:
    """/Applications first, if the app there is ours, then ~/Applications: app_location in
    scripts/lib/install.sh. BUTLER_SYSTEM_APPS stands in for /Applications in tests."""
    system_app = Path(os.environ.get("BUTLER_SYSTEM_APPS", "/Applications")) / "Butler.app"
    try:
        with open(system_app / "Contents" / "Info.plist", "rb") as f:
            if plistlib.load(f).get("CFBundleIdentifier") == APP_ID:
                return str(system_app)
    except (OSError, ValueError, ExpatError, AttributeError):
        pass
    home_app = Path.home() / "Applications" / "Butler.app"
    return str(home_app) if home_app.is_dir() else None


def lock_exclusive(f) -> None:
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
