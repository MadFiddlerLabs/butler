"""A fresh, just-downloaded Butler install for testing the `butler` command and the first-run
setup: the repo's tracked files in a temp folder, a .venv whose python is this test's Python, a
temp HOME, stubs for `security` (a JSON file), `open` and `launchctl`, and a `curl` that logs its
command line before running the real one. Nothing touches the real Keychain, browser or home
folder.

    home = SetupHome(api)           # api: a started FakeAccountAPI, or None for offline
    proc = home.butler("town", "Lisbon")
    proc = home.first_run(["sam@example.com", "butler town Lisbon", ...])
    home.close()
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from . import timeout

REPO = Path(__file__).resolve().parents[2]

SECURITY = """import json, os, shlex, sys
path = os.environ["BUTLER_FAKE_KEYCHAIN"]
store = json.load(open(path)) if os.path.exists(path) else {}
with open(os.environ["BUTLER_FAKE_ARGV"], "a") as log:
    log.write(json.dumps(["security", *sys.argv[1:]]) + "\\n")
def run(args):
    def opt(flag):
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None
    if args[:1] == ["add-generic-password"]:
        value = bytes.fromhex(opt("-X")).decode() if "-X" in args else opt("-w")
        if value is None:
            return 1          # the prompting form (-w last, no value): never scripted
        store[opt("-s")] = value
        json.dump(store, open(path, "w"))
        return 0
    if args[:1] == ["find-generic-password"]:
        service = opt("-s")
        if service in store:
            if "-w" in args:
                print(store[service])
            return 0
        return 44
    return 0
args = sys.argv[1:]
if args == ["-i"]:            # commands on stdin, as the real security -i reads them
    code = 0
    for line in sys.stdin:
        if line.strip():
            code = run(shlex.split(line))
    sys.exit(code)
sys.exit(run(args))
"""

# Every curl the scripts run, logged, then the real one.
CURL = """import json, os, sys
with open(os.environ["BUTLER_FAKE_ARGV"], "a") as log:
    log.write(json.dumps(["curl", *sys.argv[1:]]) + "\\n")
os.execv("/usr/bin/curl", ["curl", *sys.argv[1:]])
"""


def _script(path: Path, body: str, interpreter: str = sys.executable) -> None:
    path.write_text(f"#!{interpreter}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO, capture_output=True, text=True)
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.split()
    return [str(p.relative_to(REPO)) for p in REPO.rglob("*")
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts]


class SetupHome:
    def __init__(self, api=None, butler_name: str | None = "Bento"):
        self.tmp = Path(tempfile.mkdtemp(prefix="butler-setup-"))
        self.root = self.tmp / "Butler"
        self.home = self.tmp / "home"
        self.home.mkdir()
        for rel in tracked_files():
            src = REPO / rel
            if src.is_file():
                dst = self.root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        # Only the files a package installs: never this checkout's own config or data.
        for f in (self.root / "config").glob("*.yaml"):
            if ".example." not in f.name:
                f.unlink()
        (self.root / ".butler-version").write_text("v0.0.0-test\n")
        if butler_name is not None:
            (self.root / ".butler-name").write_text(butler_name + "\n")
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        _script(fakebin / "security", SECURITY)
        _script(fakebin / "curl", CURL)
        _script(fakebin / "open", "import sys\nopen(__import__('os').environ['BUTLER_FAKE_OPENED'], 'a')"
                                  ".write(' '.join(sys.argv[1:]) + '\\n')\n")
        _script(fakebin / "launchctl", "print('PID\\tStatus\\tLabel')\n")
        # No login records, so `butler doctor` never reports this machine's own restarts.
        _script(fakebin / "last", "")
        self.keychain = self.tmp / "keychain.json"
        self.argv_log = self.tmp / "argv.jsonl"
        self.opened = self.tmp / "opened.txt"
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            # Only the system's tools, so whatever this machine has installed (Claude Code, uv) stays out.
            "PATH": os.pathsep.join([str(fakebin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
            "BUTLER_FAKE_KEYCHAIN": str(self.keychain),
            "BUTLER_FAKE_ARGV": str(self.argv_log),
            "BUTLER_FAKE_OPENED": str(self.opened),
            # A wrapper, not a symlink: it must keep this test run's packages (yaml, flask, ...).
            "BUTLER_BOOTSTRAP": (f"mkdir -p .venv/bin && printf '#!/bin/sh\\nexec {sys.executable} \"$@\"\\n'"
                                 " > .venv/bin/python && chmod +x .venv/bin/python"),
            "BUTLER_POLL_SECONDS": "1",
            "BUTLER_NO_OPEN": "1",
            "BUTLER_CLAUDE_DRY_RUN": "1",
            "MPLBACKEND": "Agg",
            "BUTLER_COLUMNS": "1000",     # no wrapping, so tests match whole sentences; test_wrap checks 80
        }
        self.env.pop("PYTHONPATH", None)
        if api is not None:
            self.env["BUTLER_API"] = api.url + "/api"
            self.env["BUTLER_GEOCODE_URL"] = api.url + "/geocode"
        else:
            self.env["BUTLER_API"] = "http://127.0.0.1:9/api"          # nothing listens: offline
            self.env["BUTLER_GEOCODE_URL"] = "http://127.0.0.1:9/geocode"

    def bootstrap(self) -> None:
        subprocess.run(["bash", "-c", self.env["BUTLER_BOOTSTRAP"]], cwd=self.root, check=True)

    def butler(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([str(self.root / "bin" / "butler"), *args], cwd=self.home, env=self.env,
                              input=stdin, capture_output=True, text=True, timeout=timeout(300))

    def first_run(self, lines: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.root / "scripts" / "first-run.sh")], cwd=self.home, env=self.env,
                              input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=timeout(600))

    def secrets(self) -> dict:
        return json.loads(self.keychain.read_text()) if self.keychain.exists() else {}

    def command_lines(self) -> list[list[str]]:
        """The argv of every `security` and `curl` the install ran: what the process list showed."""
        if not self.argv_log.exists():
            return []
        return [json.loads(line) for line in self.argv_log.read_text().splitlines()]

    def settings(self) -> dict:
        import yaml
        return yaml.safe_load((self.root / "config" / "settings.yaml").read_text())

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
