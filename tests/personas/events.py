"""The persona tests' channel from a stub to the harness: a window that appears on screen (a Mac
dialog, a Calendar permission prompt) or a browser page that opens. The stub writes an event file
and waits for the harness's answer file; the harness asks the persona and writes the answer.
Standard library only: Butler's Python and the stubs both import it.

Everything lives in ~/persona-fakes, on a persona-test runner only (the free tests point
BUTLER_PERSONA_FAKES at a folder of their own; the processes launchd starts have no such variable).
"""
import json
import os
import time
from pathlib import Path

FAKES = Path(os.environ.get("BUTLER_PERSONA_FAKES") or Path.home() / "persona-fakes")
EVENTS = FAKES / "events"


def config() -> dict:
    """The run's fake outside world: addresses of the harness's fakes, and the secrets the persona
    was shown (fake ones, made up for this run). Read fresh each time: the persona makes them
    as the course goes on."""
    try:
        return json.loads((FAKES / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def ask(kind: str, payload: dict, timeout: float = 900) -> dict | None:
    """Shows something to the persona, and with a timeout, waits for its answer (None if none
    comes: a person who walked away)."""
    EVENTS.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{os.getpid()}"
    _write(EVENTS / f"{name}.json", {"kind": kind, **payload})
    answer = EVENTS / f"{name}.answer"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if answer.exists():
            return json.loads(answer.read_text(encoding="utf-8"))
        time.sleep(0.3)
    return None


def waiting(seen: set[str]) -> list[tuple[str, dict]]:
    """Events the harness hasn't taken yet, oldest first."""
    if not EVENTS.exists():
        return []
    out = []
    for path in sorted(EVENTS.glob("*.json")):
        if path.stem not in seen and not path.with_suffix(".answer").exists():
            seen.add(path.stem)
            out.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))
    return out


def reply(name: str, answer: dict) -> None:
    _write(EVENTS / f"{name}.answer", answer)
