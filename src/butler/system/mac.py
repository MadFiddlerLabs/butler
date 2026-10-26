"""macOS: secrets in the login Keychain, the schedule in launchd, VoiceOver."""
import fcntl
import subprocess

NAME = "mac"
LABEL = "com.buildyourbutler.butler"


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


def lock_exclusive(f) -> None:
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
