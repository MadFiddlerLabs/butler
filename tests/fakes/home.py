"""A throwaway Butler install for integration tests: a copy of the code with the invented Riveras'
settings, projects, budget and reference file, a stub butler-ek and a fake Keychain.

    home = Home()                  # everything connected
    record = home.run("daily")     # what went out: mails, prompts, URLs, errors
    home.close()

Flows run in a subprocess (harness.py), so ROOT is the test home and nothing touches the repo.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

from . import fixtures

REPO = Path(__file__).resolve().parents[2]
FAKES = Path(__file__).resolve().parent
ALL_SECRETS = tuple(fixtures.SECRETS)


def _script(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


SECURITY = """import json, os, sys
args = sys.argv[1:]
secrets = json.loads(open(os.environ["BUTLER_FAKE_SECRETS"]).read())
if args[:1] == ["find-generic-password"] and "-s" in args:
    service = args[args.index("-s") + 1]
    if service in secrets:
        if "-w" in args:
            print(secrets[service])
        sys.exit(0)
    print("security: SecKeychainSearchCopyNext: The specified item could not be found.", file=sys.stderr)
    sys.exit(44)
sys.exit(f"fake security: unsupported {args}")
"""


class Home:
    def __init__(self, ek: bool = True, secrets: tuple[str, ...] = ALL_SECRETS, own_settings: bool = True,
                 projects: bool = True, reference: bool = True):
        self.root = Path(tempfile.mkdtemp(prefix="butler-home-"))
        r = self.root
        shutil.copytree(REPO / "src", r / "src", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "examples", r / "examples")
        (r / "config").mkdir()
        for f in (REPO / "config").glob("*.example.yaml"):
            shutil.copy(f, r / "config" / f.name)
        (r / "budget").mkdir()
        shutil.copy(REPO / "budget" / "budget.example.xlsx", r / "budget" / "budget.example.xlsx")
        for d in ("data", "logs", "out", "fakes/imap", "fakebin", "bin"):
            (r / d).mkdir(parents=True, exist_ok=True)

        if own_settings:
            settings = yaml.safe_load((REPO / "config" / "settings.example.yaml").read_text())
            settings["household"]["about"] = settings["household"]["about"].strip() + " " + fixtures.ABOUT_MARKER + "."
            (r / "config" / "settings.yaml").write_text(yaml.safe_dump(settings, sort_keys=False, allow_unicode=True))
            shutil.copy(REPO / "budget" / "budget.example.xlsx", r / "budget" / "budget.xlsx")
        if projects:
            (r / "projects").mkdir(exist_ok=True)
            for f in (REPO / "examples" / "projects").glob("*.yaml"):
                shutil.copy(f, r / "projects" / f.name)
        if reference:
            (r / "projects").mkdir(exist_ok=True)
            (r / "projects" / "reference.local.yaml").write_text(fixtures.reference_yaml(date.today()))
        (r / "data" / "feedback.md").write_text(f"- 2026-10-01 (Sam): {fixtures.FEEDBACK_MARKER}\n")
        (r / "data" / "household_notes.md").write_text("- Pat picks Mia up on Fridays.\n")

        if ek:
            shutil.copy(FAKES / "fixtures.py", r / "bin" / "fixtures.py")
            _script(r / "bin" / "butler-ek", (FAKES / "ek_stub.py").read_text())
        _script(r / "fakebin" / "security", SECURITY)
        (r / "fakes" / "secrets.json").write_text(json.dumps({k: fixtures.SECRETS[k] for k in secrets}))
        self._reply_count = 0

    # ---------- setup ----------

    def add_reply(self, sender: str, subject: str, body: str, signed_by: str | None) -> str:
        self._reply_count += 1
        message_id = f"<reply-{self._reply_count}@example.com>"
        (self.root / "fakes" / "imap" / f"{self._reply_count:03d}.eml").write_bytes(
            fixtures.reply_message(sender, subject, body, message_id, signed_by))
        return message_id

    def set_actions(self, by_subject: dict[str, list[dict]]) -> None:
        """Canned Claude plans for replies, by subject. Unused fields are filled with None."""
        fields = ("step_id", "status", "date", "start_time", "end_time", "location", "category", "waiting_on")
        full = {s: [{**{f: None for f in fields}, **a} for a in actions] for s, actions in by_subject.items()}
        (self.root / "fakes" / "actions.json").write_text(json.dumps(full))

    # ---------- running ----------

    def run(self, flow: str) -> dict:
        record = self.root / "fakes" / f"record-{flow}.json"
        env = {**os.environ, "PYTHONPATH": str(self.root / "src"), "TZ": "Europe/Lisbon",
               "PATH": f"{self.root / 'fakebin'}{os.pathsep}{os.environ.get('PATH', '')}",
               "BUTLER_FAKE_SECRETS": str(self.root / "fakes" / "secrets.json"), "MPLBACKEND": "Agg"}
        proc = subprocess.run([sys.executable, str(FAKES / "harness.py"), flow, str(record)],
                              cwd=self.root, env=env, capture_output=True, text=True, timeout=300)
        if not record.exists():
            raise AssertionError(f"harness crashed:\n{proc.stdout}\n{proc.stderr}")
        result = json.loads(record.read_text())
        result["stderr"] = proc.stderr
        return result

    # ---------- reading back ----------

    def ek_calls(self) -> list[dict]:
        path = self.root / "bin" / "ek-calls.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def log(self) -> str:
        path = self.root / "logs" / "butler.log"
        return path.read_text() if path.exists() else ""

    def text_files(self, *folders: str) -> dict[str, str]:
        """Every readable text file under these folders (for leak checks)."""
        out = {}
        for folder in folders:
            for p in (self.root / folder).rglob("*"):
                if p.is_file() and p.suffix in {".html", ".txt", ".md", ".json", ".jsonl", ".log", ".yaml"}:
                    out[str(p.relative_to(self.root))] = p.read_text(errors="replace")
        return out

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
