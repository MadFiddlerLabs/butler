"""Over-the-air updates: Butler keeps itself current, safely.

    butler update --check      # is there a new version?
    butler update [--yes]      # install it now

Once a day, at `update_hour`, the tick reads a small manifest on buildyourbutler.com (version,
package URL, checksum, notes). Patch and minor versions install on their own (`updates: auto`);
a major version, or any version under `updates: notify`, waits for a "Reply 'update'" from the
household. `updates: off` never checks.

An update is the same signed, notarized Butler.pkg the website serves. Before it installs:
- its SHA-256 must match the manifest,
- pkgutil must show the Developer ID Installer chain for Team ID VB9CHJM4AN,
- Gatekeeper (spctl) must accept it, and stapler must validate it when Xcode tools exist,
- and the package of the version installed now is kept (downloaded if need be), for rollback.
Then Installer puts it in ~/Butler (no admin password; personal files are never in a package),
the NEW code runs its migrations and a health check, and a failure reinstalls the kept package.
The next brief says what happened, in a line.

The website's manifest is the kill switch: a held version is never offered. See docs/UPDATES.md.
"""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

log = logging.getLogger("butler")

TEAM_ID = "VB9CHJM4AN"
MANIFEST_URL = "https://buildyourbutler.com/updates/stable.json"
RELEASES_URL = "https://github.com/nerolabs/butler/releases/download"
UPDATES = ROOT / "data" / "updates"
STATE = UPDATES / "state.json"
INSTALL_ID = UPDATES / "install-id"
VERSION_FILE = ROOT / ".butler-version"
# Written into unsigned test packages only (packaging/macos/build-pkg.sh), holding that build's
# version. Only then, and only with BUTLER_UPDATE_ALLOW_UNSIGNED=1, is the signature check
# relaxed. A signed package never carries it, and a stale one from an older build doesn't match
# the installed version, so a real install can't relax its checks.
UNSIGNED_MARKER = ROOT / ".butler-unsigned-build"
PORTAL_LABEL = "com.buildyourbutler.portal"


class UpdateError(RuntimeError):
    """Something stopped an update. The message is plain words for the household."""


# ---------- versions ----------

def parse_version(text: str | None) -> tuple[int, int, int, int] | None:
    """'v1.2.3' → (1, 2, 3, 1). A pre-release ('v1.2.3-rc1', 'v0.0.0-d78f91e') sorts below its
    release: (1, 2, 3, 0). Anything else → None."""
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?", text.strip())
    if not m:
        return None
    return int(m[1]), int(m[2]), int(m[3]), 0 if m[4] else 1


def installed_version() -> str | None:
    return VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else None


def is_major(new: str, old: str) -> bool:
    n, o = parse_version(new), parse_version(old)
    return bool(n and o and n[0] > o[0])


# ---------- state ----------

def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    UPDATES.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE)


def install_id() -> str:
    """A random id for this install, made once. Only used to place it in a staged rollout."""
    if INSTALL_ID.exists():
        return INSTALL_ID.read_text().strip()
    UPDATES.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(16)
    INSTALL_ID.write_text(value + "\n")
    return value


def in_rollout(percent, ident: str, version: str) -> bool:
    """Whether this install is in the first `percent`% of a staged rollout of `version`."""
    if percent is None:
        return True
    try:
        percent = float(percent)
    except (TypeError, ValueError):
        return True
    bucket = int(hashlib.sha256(f"{ident}:{version}".encode()).hexdigest()[:8], 16) % 100
    return bucket < percent


# ---------- the manifest ----------

def fetch_manifest() -> dict:
    """The manifest from the website, or from BUTLER_UPDATE_MANIFEST (a URL or a file path)."""
    source = os.environ.get("BUTLER_UPDATE_MANIFEST") or MANIFEST_URL
    try:
        if "://" not in source:
            return json.loads(Path(source).read_text())
        req = urllib.request.Request(source, headers={"User-Agent": f"butler/{installed_version()}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except (OSError, ValueError) as e:
        raise UpdateError(f"couldn't read the update list ({e})") from None


@dataclass
class Decision:
    action: str          # "none" | "install" | "ask" | "reinstall"
    reason: str
    entry: dict | None = None


def decide(settings: dict, manifest: dict, installed: str, ident: str) -> Decision:
    """What to do about the manifest, under the household's `updates` setting."""
    mode = settings.get("updates", "auto")
    if mode == "off":
        return Decision("none", "updates are off")
    if not isinstance(manifest, dict):
        return Decision("none", "the update list isn't readable")
    new = manifest.get("version")
    if not manifest.get("available", True) or not new:
        return Decision("none", "no update is available")
    n, o = parse_version(new), parse_version(installed)
    if not n or not o:
        return Decision("none", f"can't compare versions ({new!r} vs {installed!r})")
    if n <= o:
        return Decision("none", f"up to date ({installed})")
    if not in_rollout(manifest.get("rollout_percent"), ident, new):
        return Decision("none", f"{new} is rolling out gradually; not here yet")
    minimum = parse_version(manifest.get("min_version"))
    if minimum and o < minimum:
        return Decision("reinstall", f"{new} needs a fresh download (this is {installed})", manifest)
    if mode == "notify" or is_major(new, installed) or manifest.get("major"):
        return Decision("ask", f"{new} is ready and waits for a yes", manifest)
    return Decision("install", f"{new} will install", manifest)


# ---------- checks on the package ----------

def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unsigned_allowed() -> bool:
    """True only for a test build (the marker matches the installed version) AND the test flag."""
    if os.environ.get("BUTLER_UPDATE_ALLOW_UNSIGNED") != "1":
        return False
    try:
        return UNSIGNED_MARKER.read_text().strip() == (installed_version() or "\0")
    except OSError:
        return False


def verify_signature(pkg: Path) -> None:
    """Refuses a package not signed by our Developer ID, or one Gatekeeper wouldn't open. The
    signing certificate (the chain's first line) must end in our Team ID: any Developer ID passes
    Gatekeeper, so this line is what ties the package to Butler's publisher."""
    if unsigned_allowed():
        log.warning("update: signature checks relaxed for a test build")
        return
    sig = _run(["pkgutil", "--check-signature", str(pkg)])
    out = sig.stdout + sig.stderr
    if (sig.returncode != 0 or "signed by a developer certificate issued by Apple" not in out
            or not re.search(rf"(?m)^\s*1\. Developer ID Installer: [^\n]*\({TEAM_ID}\)\s*$", out)):
        raise UpdateError("the new version isn't signed by Butler's publisher, so I didn't install it")
    gate = _run(["spctl", "--assess", "-vv", "--type", "install", str(pkg)])
    if gate.returncode != 0 or "accepted" not in (gate.stdout + gate.stderr):
        raise UpdateError("macOS didn't approve the new version, so I didn't install it")
    if shutil.which("xcode-select") and _run(["xcode-select", "-p"]).returncode == 0:
        staple = _run(["xcrun", "stapler", "validate", str(pkg)])
        if staple.returncode != 0:
            raise UpdateError("the new version's notarization ticket didn't check out, so I didn't install it")


def download(url: str, dest: Path, sha256: str | None = None, size: int | None = None) -> Path:
    """Downloads to dest (via a .part file) and checks the checksum. A mismatch deletes it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        if "://" not in url:
            shutil.copyfile(url, part)
        else:
            req = urllib.request.Request(url, headers={"User-Agent": f"butler/{installed_version()}"})
            with urllib.request.urlopen(req, timeout=300) as r, part.open("wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"couldn't download the new version ({e})") from None
    if size and part.stat().st_size != int(size):
        part.unlink(missing_ok=True)
        raise UpdateError("the download was the wrong size, so I deleted it")
    if sha256 and sha256_of(part).lower() != sha256.lower():
        part.unlink(missing_ok=True)
        raise UpdateError("the download didn't match its checksum, so I deleted it")
    part.replace(dest)
    return dest


def kept_package(version: str) -> Path:
    return UPDATES / f"Butler-{version}.pkg"


def ensure_rollback_package(version: str) -> Path:
    """The package of the version installed now, for rollback: kept from the last update, or
    fetched from that version's GitHub Release (BUTLER_RELEASES_URL overrides the base)."""
    path = kept_package(version)
    if not path.exists():
        base = os.environ.get("BUTLER_RELEASES_URL") or RELEASES_URL
        url = f"{base.rstrip('/')}/{version}/Butler.pkg"
        try:
            download(url, path)
        except UpdateError as e:
            raise UpdateError(f"I couldn't keep a copy of {version} to go back to ({e}), "
                              "so I'll try again tomorrow") from None
    verify_signature(path)
    return path


def prune(keep: set[str]) -> None:
    for pkg in UPDATES.glob("Butler-*.pkg"):
        if pkg.name not in keep:
            pkg.unlink(missing_ok=True)


# ---------- installing ----------

def can_update() -> str | None:
    """Why this install can't update itself, or None."""
    if (ROOT / ".git").exists():
        return "this is a git checkout; update it with: git pull"
    if not installed_version():
        return "this install has no version file; download Butler again from buildyourbutler.com"
    if ROOT.resolve() != (Path.home() / "Butler").resolve() and not os.environ.get("BUTLER_UPDATE_ANY_ROOT"):
        return f"updates install into ~/Butler, but this copy lives in {ROOT}"
    return None


def install_package(pkg: Path) -> None:
    """Installer, into the current user's home: no admin password, no prompts. postinstall sees
    an existing household and opens nothing."""
    result = _run(["installer", "-pkg", str(pkg), "-target", "CurrentUserHomeDirectory"], timeout=900)
    if result.returncode != 0:
        raise UpdateError(f"Installer didn't finish ({(result.stderr or result.stdout).strip()[-200:]})")


def post_install(previous: str) -> dict:
    """Runs the NEW code in a fresh process: dependencies, migrations, restarts, health check."""
    python = ROOT / ".venv" / "bin" / "python"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run([str(python), "-m", "butler.updates", "--post-install", previous],
                            capture_output=True, text=True, env=env, cwd=ROOT, timeout=900)
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "problem": (result.stderr or result.stdout).strip()[-300:] or "no answer"}


def apply(entry: dict, now: datetime | None = None) -> dict:
    """Installs the manifest entry, or rolls back. Returns the result recorded in the state."""
    now = now or datetime.now(timezone.utc)
    why_not = can_update()
    if why_not:
        raise UpdateError(why_not)
    previous = installed_version()
    new = entry["version"]
    UPDATES.mkdir(parents=True, exist_ok=True)
    with (UPDATES / "lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise UpdateError("another update is already running") from None
        rollback_pkg = ensure_rollback_package(previous)
        pkg = download(entry["pkg_url"], kept_package(new), entry.get("sha256"), entry.get("size"))
        verify_signature(pkg)
        log.info("update: installing %s over %s", new, previous)
        install_package(pkg)
        outcome = post_install(previous)
        if outcome.get("ok") and installed_version() == new:
            result = {"status": "updated", "from": previous, "to": new,
                      "notes": _short_notes(entry.get("notes", "")), "at": now.isoformat()}
            prune({pkg.name, rollback_pkg.name})
            log.info("update: now on %s", new)
        else:
            problem = outcome.get("problem") or f"the version file says {installed_version()}"
            log.error("update to %s failed its check (%s); going back to %s", new, problem, previous)
            install_package(rollback_pkg)
            back = post_install(previous)
            result = {"status": "rolled_back", "from": previous, "to": new, "problem": problem,
                      "restored": installed_version(), "restored_ok": bool(back.get("ok")),
                      "at": now.isoformat()}
            kept_package(new).unlink(missing_ok=True)
    state = load_state()
    state.update(last_result=result, announced=False)
    state.pop("waiting", None)
    state.pop("approved", None)
    save_state(state)
    return result


def _short_notes(notes: str, limit: int = 200) -> str:
    text = " ".join(notes.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


# ---------- the daily tick, the reply, the brief ----------

def tick(settings: dict, now: datetime) -> None:
    """Called by the tick every 5 minutes; checks at most once a day, at update_hour, and installs
    an approved update straight away."""
    if can_update():
        return
    state = load_state()
    waiting = state.get("waiting")
    if waiting and state.get("approved") == waiting.get("version"):
        _apply_logged(waiting, now)
        return
    if settings.get("updates", "auto") == "off":
        return
    hour = int(settings.get("update_hour", 3))
    today = now.date().isoformat()
    if now.hour != hour or state.get("checked") == today:
        return
    state["checked"] = today
    save_state(state)
    try:
        decision = decide(settings, fetch_manifest(), installed_version(), install_id())
    except UpdateError as e:
        log.info("update check: %s", e)
        return
    log.info("update check: %s", decision.reason)
    if decision.action == "install":
        _apply_logged(decision.entry, now)
    elif decision.action in ("ask", "reinstall"):
        state = load_state()
        state["waiting"] = {**decision.entry, "kind": decision.action}
        state.pop("announced_waiting", None)
        save_state(state)


def _apply_logged(entry: dict, now: datetime) -> None:
    try:
        apply(entry, now)
    except UpdateError as e:
        log.warning("update to %s didn't happen: %s", entry.get("version"), e)
        state = load_state()
        state["last_error"] = {"to": entry.get("version"), "problem": str(e), "at": now.isoformat()}
        save_state(state)


def approve() -> str:
    """The `update` reply: approves the waiting version; the next tick installs it."""
    state = load_state()
    waiting = state.get("waiting")
    if not waiting:
        return "There's no update waiting. Everything is current."
    if waiting.get("kind") == "reinstall":
        return (f"Version {waiting['version']} needs a fresh download: get it from "
                "buildyourbutler.com and open it. Your settings and data stay as they are.")
    state["approved"] = waiting["version"]
    save_state(state)
    return f"Installing version {waiting['version']} in the next few minutes. The next brief confirms it."


def brief_lines(name: str) -> list[str]:
    """One line for the next brief about what happened, or what's waiting."""
    state = load_state()
    result, lines = state.get("last_result"), []
    if result and not state.get("announced"):
        if result["status"] == "updated":
            notes = f" What's new: {result['notes']}" if result.get("notes") else ""
            lines.append(f"{name} updated overnight to {result['to']}.{notes}")
        else:
            lines.append(f"{name} tried to update to {result['to']} overnight, but its check didn't pass, "
                         f"so it went back to {result.get('restored') or result['from']}. Nothing changed for you.")
    waiting = state.get("waiting")
    if waiting:
        if waiting.get("kind") == "reinstall":
            lines.append(f"Version {waiting['version']} of {name} needs a fresh download from buildyourbutler.com.")
        else:
            lines.append(f"Version {waiting['version']} of {name} is ready. Reply \"update\" to install it.")
    return lines


def mark_announced() -> None:
    """After a brief really went out with the update line."""
    state = load_state()
    if state.get("last_result") and not state.get("announced"):
        state["announced"] = True
        save_state(state)


# ---------- the new code's side of an update ----------

HEALTH_MODULES = ("butler.config", "butler.daily", "butler.tick", "butler.replies", "butler.render",
                  "butler.finance", "butler.portal", "butler.cli", "butler.updates", "butler.migrations")


def health() -> list[str]:
    """Problems that should stop an update: run by the new code, after migrations."""
    import importlib
    problems = []
    if sys.version_info < (3, 12):
        problems.append("Python is older than 3.12")
    for name in HEALTH_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001 - any import failure is a broken update
            problems.append(f"{name} doesn't load ({type(e).__name__}: {str(e).splitlines()[0][:120]})")
    try:
        from .config import load_settings
        settings = load_settings()
        for key in ("timezone", "email", "household"):
            if key not in settings:
                problems.append(f"settings lost '{key}'")
    except Exception as e:  # noqa: BLE001
        problems.append(f"settings don't load ({str(e).splitlines()[0][:120]})")
    return problems


def _restart_jobs() -> None:
    uid = os.getuid()
    if shutil.which("launchctl") and _run(["launchctl", "print", f"gui/{uid}/{PORTAL_LABEL}"]).returncode == 0:
        _run(["launchctl", "kickstart", "-k", f"gui/{uid}/{PORTAL_LABEL}"])


def _sync_dependencies() -> str | None:
    """New versions may need new packages: bootstrap.sh is safe to run again."""
    if os.environ.get("BUTLER_UPDATE_SKIP_BOOTSTRAP"):
        return None
    result = _run(["bash", str(ROOT / "scripts" / "bootstrap.sh")], timeout=900)
    return None if result.returncode == 0 else f"dependencies didn't install ({result.stderr.strip()[-200:]})"


def run_post_install() -> dict:
    from . import migrations
    problem = _sync_dependencies()
    if problem:
        return {"ok": False, "problem": problem}
    try:
        applied = migrations.run_pending()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "problem": f"a settings migration failed ({e})"}
    problems = health()
    if problems:
        return {"ok": False, "problem": "; ".join(problems)}
    _restart_jobs()
    return {"ok": True, "migrations": applied}


# ---------- `butler update` ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="butler update")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--post-install", metavar="PREVIOUS")
    args = parser.parse_args(argv)
    if args.post_install:
        print(json.dumps(run_post_install()), flush=True)
        return 0
    return command(args.check, args.yes)


def command(check: bool, yes: bool, say=print) -> int:
    """`butler update`: plain words for a person at a Terminal."""
    (ROOT / "logs").mkdir(exist_ok=True)
    why_not = can_update()
    if why_not:
        say(f"I can't update myself: {why_not}.")
        return 1
    current = installed_version()
    try:
        manifest = fetch_manifest()
    except UpdateError as e:
        say(f"I {e}. Check your internet connection and try again.")
        return 1
    new = manifest.get("version")
    if not manifest.get("available", True) or not new or not parse_version(new) \
            or parse_version(new) <= parse_version(current):
        say(f"You're up to date: version {current}.")
        return 0
    minimum = parse_version(manifest.get("min_version"))
    if minimum and parse_version(current) < minimum:
        say(f"Version {new} needs a fresh download from buildyourbutler.com. Your settings and data stay.")
        return 0
    notes = _short_notes(manifest.get("notes", ""), 400)
    say(f"Version {new} is ready (you have {current}).{' What is new: ' + notes if notes else ''}")
    if check:
        say("To install it, type: butler update")
        return 0
    if not yes:
        answer = input("Install it now? Press Enter for yes, or type no: ").strip().lower()
        if answer in {"n", "no"}:
            say("No problem. Type butler update whenever you like.")
            return 0
    say("Downloading and checking it. This takes a minute or two…")
    try:
        result = apply(manifest)
    except UpdateError as e:
        say(f"I didn't update: {e}.")
        return 1
    if result["status"] == "updated":
        say(f"Done. You're on version {result['to']}.")
        return 0
    say(f"The new version didn't pass its check, so I went back to {result.get('restored')}. "
        "Nothing changed for you. Details are in logs/butler.log.")
    return 1


if __name__ == "__main__":
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / "logs" / "butler.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
