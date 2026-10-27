"""The computer Butler runs on. Everything that differs between macOS and Windows goes through
here, so the rest of Butler reads the same on both: secrets, the schedule, opening a file, the
screen reader and the lock that keeps two updates apart. macOS is in mac.py, Windows in
windows.py; each has the same functions, with the same meaning.

Callers import the module, not the names (`from . import system`, then `system.get_secret(...)`),
so a test can patch one function in one place."""
import sys

if sys.platform == "win32":
    from . import windows as _impl
else:
    from . import mac as _impl

NAME = _impl.NAME


def get_secret(service: str) -> str | None:
    """A secret from the computer's own password store, or None if it isn't there."""
    return _impl.get_secret(service)


def has_secret(service: str) -> bool:
    """Whether the password store holds this entry, without reading its value."""
    return _impl.has_secret(service)


def store_secret(service: str, secret: str) -> None:
    """Saves a secret in the password store, replacing any old one. The secret never goes on a
    command line, where any account on the computer can read it in the process list."""
    _impl.store_secret(service, secret)


def scheduled() -> bool:
    """Whether Butler's every-few-minutes job is installed."""
    return _impl.scheduled()


def screen_reader_on() -> bool:
    """Whether a screen reader is running now: VoiceOver on a Mac, Narrator or NVDA on Windows."""
    return _impl.screen_reader_on()


def open_file(path: str) -> None:
    """Opens a file the way double-clicking it would: a page in the default browser."""
    _impl.open_file(path)


def lock_exclusive(f) -> None:
    """Takes a lock on an open file without waiting. Raises OSError if something else holds it.
    The lock goes when the file is closed or the process ends."""
    _impl.lock_exclusive(f)
