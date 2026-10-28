"""Skill evals: headless Claude Code runs the course's skills in a fresh install, a scripted student
answers, and Python checks what it did. Paid (it uses the Claude API), so it runs only when someone
starts .github/workflows/skill-evals.yml by hand, after big changes. Never on every push.

    ANTHROPIC_API_KEY=... python tests/evals/run_evals.py [scenario ...]
    python tests/evals/run_evals.py --dry [scenario ...]             # free: no Claude call
    python tests/evals/run_evals.py --plan --platform windows        # the workflow's matrix

On a Mac, scripts/install.sh installs this checkout into a temporary folder, and the Mac's dialogs
are stubbed (osascript, open, sudo) so nothing waits for a human. On Windows, the installer a
student downloads (--installer, built by the workflow) installs into %USERPROFILE%\\Butler, and a
start-up hook in Butler's Python (eval_hook.py) points Gmail, Google and Butler's own Claude calls at
fakes on this machine. When Claude tells the student to type `butler secret <name>` in a window of
their own, the harness stores the scripted student's (invented) secret in Credential Manager.

--dry runs everything except the Claude call: the install, the course stages before the step, the
fakes, and the graders, on the scenario's reference run (scenarios.Scenario.dry), whose commands run
for real. So a dry run proves the harness and the graders on a real Windows or Mac runner, for free.

Scenarios and graders are in scenarios.py; tests/test_evals.py checks them without a Claude call.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tests"))
import scenarios  # noqa: E402
from scenarios import SCENARIOS, Run  # noqa: E402

WINDOWS = sys.platform == "win32"
PLATFORM = "windows" if WINDOWS else "mac"
# Where eval_hook.py finds the fakes. Task Scheduler's processes have no variable of the run's, so
# on Windows it's a fixed place in the home folder; on a Mac (no hook) it's in the run's own folder.
HOME_FAKES = Path(os.environ.get("BUTLER_EVAL_FAKES") or Path.home() / "butler-eval-fakes")
OUT = REPO / "eval-output"

STUBS = {
    "osascript": "echo 'execution error: User canceled. (-128)' >&2; exit 1",
    "open": "exit 0",
    "sudo": "echo 'sudo: not in evals' >&2; exit 1",
}


# ---------- Butler's own Claude calls ----------

class FakeClaude:
    """The Messages API for Butler's own calls (the news, the prep notes), on this machine: each
    answer fills the request's JSON schema with invented text. It records the key on each call, so
    the graders can tell Butler used the one the student stored."""

    def __init__(self):
        self.keys: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                fake.keys.append(self.headers.get("x-api-key", ""))
                if self.headers.get("x-api-key") != scenarios.STORED_KEY:
                    return self._send(401, {"type": "error", "error": {"type": "authentication_error",
                                                                        "message": "invalid x-api-key"}})
                schema = ((body.get("output_config") or {}).get("format") or {}).get("schema") or {}
                prompt = "\n".join(m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
                                    for m in body.get("messages", []))
                self._send(200, {
                    "id": "msg_eval", "type": "message", "role": "assistant", "model": body.get("model", ""),
                    "content": [{"type": "text", "text": json.dumps(fill(schema, prompt))}],
                    "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 1000, "output_tokens": 200}})

            def _send(self, code: int, data: dict):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        self._server.shutdown()


def fill(schema: dict, prompt: str):
    """An invented answer that fits a strict JSON schema. Lists of items with a `ref` take the refs
    the prompt offers (the news stories, the events), so Butler can match them back."""
    kind = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object" or "properties" in schema:
        return {k: fill(v, prompt) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        item = schema.get("items") or {}
        if "ref" in (item.get("properties") or {}):
            refs = list(dict.fromkeys(re.findall(r'"ref": "([^"]+)"', prompt)))[:3]
            return [{**fill(item, prompt), "ref": r} for r in refs]
        return []
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    return "An invented line."


# ---------- the install ----------

def stub_bin(work: Path) -> Path:
    folder = work / "stubs"
    folder.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        (folder / name).write_text(f"#!/bin/bash\n{body}\n")
        (folder / name).chmod(0o755)
    return folder


def install_mac(work: Path) -> Path:
    home = work / "Butler"
    env = {**os.environ, "BUTLER_HOME": str(home), "BUTLER_SOURCE": str(REPO)}
    subprocess.run([str(REPO / "scripts" / "install.sh")], env=env, check=True, capture_output=True)
    return home


def install_windows(installer: Path, work: Path) -> Path:
    """The installer a student downloads, run as the website hands it out (named Bento.exe, which
    names the butler), silently, into %USERPROFILE%\\Butler. An earlier eval's install goes first."""
    home = Path.home() / "Butler"
    reset_windows(home)
    exe = work / "Bento.exe"
    shutil.copyfile(installer, exe)
    log = work / "install.txt"
    p = subprocess.run([str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}"], timeout=1800)
    if p.returncode or not (home / ".venv" / "Scripts" / "python.exe").exists():
        tail = log.read_text(errors="replace")[-3000:] if log.exists() else ""
        raise RuntimeError(f"the installer exited {p.returncode}:\n{tail}")
    return home


def reset_windows(home: Path) -> None:
    for task in ("tick", "morning", "portal"):
        subprocess.run(["schtasks", "/Delete", "/TN", rf"\Butler\{task}", "/F"], capture_output=True)
    for name in ("butler-anthropic", "butler-gmail", "butler-google", "butler-lunchmoney", "butler-account"):
        forget_secret(name)
    if home.exists():
        shutil.rmtree(home, ignore_errors=True)


def python(home: Path) -> Path:
    return home / (".venv/Scripts/python.exe" if WINDOWS else ".venv/bin/python")


def add_hook(home: Path) -> None:
    """eval_hook.py in every Butler process: a .pth file in Butler's venv."""
    sites = list((home / ".venv").glob("Lib/site-packages")) + list((home / ".venv").glob("lib/python3*/site-packages"))
    (sites[0] / "butler-eval.pth").write_text(f"import sys; sys.path.append({str(HERE)!r}); import eval_hook\n")


# ---------- the password store, as the student fills it ----------

def _credentials():
    """Credential Manager, as butler.system.windows reaches it (advapi32)."""
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
    lib = ctypes.WinDLL("advapi32", use_last_error=True)
    lib.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    lib.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    lib.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    lib.CredFree.argtypes = [ctypes.c_void_p]
    return ctypes, lib, CREDENTIAL


def store_secret(service: str, value: str) -> None:
    """What `butler secret <service>` does when the student types the value: straight into
    Credential Manager. Done here, in the harness, in microseconds, so it's there by Claude's next
    tool call."""
    if not WINDOWS:
        raise RuntimeError("the evals store a student's secret on Windows only")
    ctypes, lib, CREDENTIAL = _credentials()
    blob = value.encode()
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL(Type=1, TargetName=service, CredentialBlobSize=len(blob),
                      CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)), Persist=2, UserName="butler")
    if not lib.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(ctypes.get_last_error(), f"Credential Manager refused {service}")


def has_secret(service: str) -> bool:
    """Whether the password store holds the entry. Never reads it out."""
    if not WINDOWS:
        return subprocess.run(["security", "find-generic-password", "-s", service], capture_output=True).returncode == 0
    ctypes, lib, CREDENTIAL = _credentials()
    found = ctypes.POINTER(CREDENTIAL)()
    if not lib.CredReadW(service, 1, 0, ctypes.byref(found)):
        return False
    lib.CredFree(found)
    return True


def forget_secret(service: str) -> None:
    ctypes, lib, _ = _credentials()
    lib.CredDeleteW(service, 1, 0)


def butler_tasks() -> set[str]:
    if not WINDOWS:
        return set()
    return {rf"\Butler\{t}" for t in ("tick", "morning", "portal")
            if subprocess.run(["schtasks", "/Query", "/TN", rf"\Butler\{t}"], capture_output=True).returncode == 0}


# ---------- the course before the step ----------

HOUSEHOLD = """
from butler import setup_settings as ss
s = ss.fresh()
ss.set_name(s, "Bento")
ss.set_adults(s, ["Sam", "Alex"])
s["people"] += [{"name": "Mia", "aliases": ["Mia"], "calendars": [], "color": "#33b679"},
                {"name": "Pat", "aliases": ["Pat"], "calendars": [], "color": "#8e24aa"}]
s["timezone"] = "Europe/Lisbon"
s["weather"] = {"place": "Lisbon", "latitude": 38.72, "longitude": -9.14, "unit": "celsius", "country": "PT"}
s["household"]["place"] = "Lisbon, Portugal"
s["news"]["regions"].insert(0, {"key": "local", "label": "Lisbon",
                                "feeds": [{"name": "Publico", "url": "https://feeds.feedburner.com/PublicoRSS"}]})
ss.save(s)
"""


def edit_settings(home: Path, change) -> None:
    path = home / "config" / "settings.yaml"
    s = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(s)
    path.write_text(yaml.safe_dump(s, sort_keys=False, allow_unicode=True), encoding="utf-8")


def butler(home: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    p = subprocess.run([str(python(home)), "-X", "utf8", *args], cwd=home, env={**env, "PYTHONPATH": "src"},
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if p.returncode:
        raise RuntimeError(f"{' '.join(args)[:60]} failed ({p.returncode}):\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    return p


def prepare(home: Path, stages: tuple[str, ...], env: dict, world: "World") -> None:
    """The modules a student finished before this step, done the way the course does them."""
    for stage in stages:
        if stage == "install_step_settings":     # the Mac's evals, as they have always prepared
            butler(home, env, str(REPO / "tests/e2e/install_step_settings.py"))
        elif stage == "household":               # Modules 0 to 2
            butler(home, env, "-c", HOUSEHOLD)
            butler(home, env, "run.py", "preview")
        elif stage == "google":                  # Module 3, on Google
            butler(home, env, "run.py", "gcal", "connect")

            def calendars(s):
                s["calendar"].update(source="google", include=sorted(scenarios.CALENDARS),
                                     holidays=["Holidays in Portugal"], write_to="Johnson Family")
            edit_settings(home, calendars)
        elif stage == "brain":                   # Module 4
            store_secret("butler-anthropic", scenarios.STORED_KEY)

            def brain(s):
                s["household"]["about"] = "Two adults and a daughter in Lisbon."
                s["llm"]["monthly_limit"] = 5
            edit_settings(home, brain)
        elif stage == "gmail":                   # Module 5, steps 1 to 4: the first send, to Sam
            store_secret("butler-gmail", scenarios.APP_PASSWORD)

            def gmail(s):
                both = sorted(scenarios.ADULTS.values())
                s["email"].update(sender=scenarios.BUTLER_GMAIL, recipients=[scenarios.ADULTS["Sam"]],
                                  trusted_senders=both)
                for adult in s["household"]["adults"]:
                    adult["emails"] = [scenarios.ADULTS[adult["name"]]]
            edit_settings(home, gmail)
            butler(home, env, "run.py", "daily")
            edit_settings(home, lambda s: s["email"].update(recipients=sorted(scenarios.ADULTS.values())))
            shutil.rmtree(world.folder / "mail", ignore_errors=True)    # that send was yesterday's
        else:
            raise ValueError(f"no stage {stage}")


# ---------- the fakes ----------

class World:
    """The run's outside world: FakeGoogle, FakeClaude, the fake Gmail's mailbox and the update
    manifest, written to <fakes>/config.json for eval_hook.py."""

    def __init__(self, home: Path, folder: Path):
        from fakes.google_api import FakeGoogle
        self.folder = folder
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        self.google = FakeGoogle().start()
        self.claude = FakeClaude()
        version = (home / ".butler-version").read_text().strip() if (home / ".butler-version").exists() else "0"
        (folder / "manifest.json").write_text(json.dumps({"version": version, "windows": {"url": "file:///none"}}))
        env = self.google.env()
        (folder / "config.json").write_text(json.dumps({
            "claude": self.claude.url, "google_api": env["BUTLER_GOOGLE_API"],
            "google_client_id": env["BUTLER_GOOGLE_CLIENT_ID"], "google_client_secret": env["BUTLER_GOOGLE_CLIENT_SECRET"],
            "manifest": str(folder / "manifest.json"),
            "gmail": {"address": scenarios.BUTLER_GMAIL, "app_password": scenarios.APP_PASSWORD}}, indent=2))

    def mails(self) -> list[dict]:
        folder = self.folder / "mail"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []

    def stop(self) -> None:
        self.google.stop()
        self.claude.stop()


# ---------- Claude, or the reference run in its place ----------

def claude_command() -> list[str]:
    """Claude Code. On Windows, npm's claude.cmd would pass the arguments through cmd.exe, which
    breaks the scripted student's lines at each newline: so the program itself."""
    if WINDOWS:
        root = subprocess.run("npm root -g", shell=True, capture_output=True, text=True).stdout.strip()
        exe = Path(root) / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return [str(exe)]
    found = shutil.which("claude")
    if not found:
        raise RuntimeError("Claude Code isn't installed: npm install -g @anthropic-ai/claude-code")
    return [found]


class Reader:
    """Reads the transcript as it streams, and does what the scripted student does when Claude
    asks them to: type a secret into `butler secret` in a window of their own."""

    def __init__(self, acts):
        self.events: list[dict] = []
        self.pending = list(acts)

    def take(self, event: dict) -> None:
        self.events.append(event)
        if event.get("type") != "assistant":
            return
        for block in event["message"].get("content", []):
            if block.get("type") != "text":
                continue
            for act in list(self.pending):
                pattern, service, value = act
                if re.search(pattern, block["text"]):
                    store_secret(service, value)
                    self.pending.remove(act)


def run_claude(home: Path, env: dict, prompt: str, system: str, model: str | None, budget: float,
               reader: Reader) -> None:
    cmd = [*claude_command(), "-p", prompt, "--output-format", "stream-json", "--verbose", "--no-session-persistence",
           "--dangerously-skip-permissions", "--disallowedTools", "AskUserQuestion",
           "--max-budget-usd", f"{budget:.2f}", "--append-system-prompt", system]
    if model:
        cmd += ["--model", model]
    proc = subprocess.Popen(cmd, cwd=home, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    errors: list[str] = []
    threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True).start()
    timer = threading.Timer(1800, proc.kill)
    timer.start()
    try:
        for line in proc.stdout:
            if line.startswith("{"):
                reader.take(json.loads(line))
        proc.wait()
    finally:
        timer.cancel()
    if not reader.events:
        raise AssertionError(f"claude produced no output:\n{''.join(errors)[-2000:]}")


def run_dry(home: Path, env: dict, steps: list[tuple], reader: Reader) -> None:
    """The scenario's reference run, in Claude's place: its words go through the same reader (so
    the student's acts happen), its commands run for real in the install, its edits change the
    settings, and the transcript has the shape Claude's has."""
    for i, (kind, what) in enumerate(steps):
        if kind == "say":
            reader.take({"type": "assistant", "message": {"content": [{"type": "text", "text": what}]}})
            continue
        if kind == "edit":
            edit_settings(home, what)
            reader.take({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Edit", "input": {"file_path": "config/settings.yaml"}}]}})
            output = "The file config/settings.yaml has been updated."
        else:
            reader.take({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": what}}]}})
            output = shell(home, env, what)
        reader.take({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                                               "content": output}]}})
    reader.take({"type": "result", "subtype": "success", "total_cost_usd": 0.0})


def shell(home: Path, env: dict, command: str) -> str:
    """One reference command, without a shell: leading NAME=value words set the environment, and
    a program in the Butler folder runs from there, as Claude's shell would run it."""
    words = shlex.split(command)
    extra = {}
    while words and re.match(r"^[A-Z_]+=", words[0]):
        k, v = words.pop(0).split("=", 1)
        extra[k] = v
    if (home / words[0]).exists():
        words[0] = str(home / words[0])
    p = subprocess.run(words, cwd=home, env={**env, **extra}, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    output = (p.stdout + p.stderr)[-4000:]
    print(f"    $ {command}  (exit {p.returncode})")
    return output if not p.returncode else f"Exit code {p.returncode}\n{output}"


def transcript(events: list[dict]) -> tuple[list[str], list[dict], list[str], dict]:
    """(Claude's text blocks, its tool calls, what the tools printed, the final result event)."""
    texts, tools, results, result = [], [], [], {}
    for e in events:
        if e.get("type") == "assistant":
            for block in e["message"].get("content", []):
                if block.get("type") == "text":
                    texts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tools.append({"name": block["name"], "input": block.get("input", {}), "texts_before": len(texts)})
        elif e.get("type") == "user":
            content = e.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    out = block.get("content", "")
                    results.append(out if isinstance(out, str) else json.dumps(out))
        elif e.get("type") == "result":
            result = e
    return texts, tools, results, result


def files_text(home: Path) -> dict[str, str]:
    skip = {".venv", ".venvs", "node_modules", ".git", ".uninstall"}
    out = {}
    for p in home.rglob("*"):
        try:
            if p.is_file() and not skip & set(p.relative_to(home).parts) and p.stat().st_size < 2_000_000:
                out[str(p.relative_to(home))] = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            pass
    return out


# ---------- one scenario ----------

def run_scenario(name: str, args) -> tuple[list[str], float | None, int]:
    sc = SCENARIOS[name]
    if PLATFORM not in sc.platforms:
        raise SystemExit(f"{name} runs on {', '.join(sc.platforms)} only")
    work = Path(tempfile.mkdtemp(prefix=f"eval-{name}-"))
    world = None
    try:
        if WINDOWS:
            if not args.installer:
                raise SystemExit("On Windows, --installer names the Butler-Setup.exe to install")
            home = install_windows(Path(args.installer), work)
        else:
            home = install_mac(work)
        world = World(home, HOME_FAKES if WINDOWS else work / "fakes")
        if WINDOWS:
            add_hook(home)
        env = {**os.environ, "BUTLER_NO_OPEN": "1",
               "PATH": os.pathsep.join([str(home / "bin")] + ([] if WINDOWS else [str(stub_bin(work))])
                                       + [os.environ["PATH"]])}
        prepare(home, scenarios.prepare_for(name, PLATFORM), env, world)
        before = files_text(home)
        # What Claude's step does, not the modules before it.
        calls, keys = len(world.google.calls), len(world.claude.keys)
        reader = Reader(sc.acts if WINDOWS else ())
        if args.dry:
            print(f"  {' '.join(claude_command_or_none())} would run: -p {sc.prompt!r}")
            run_dry(home, env, sc.dry[PLATFORM], reader)
        else:
            run_claude(home, env, sc.prompt, scenarios.user_for(name, PLATFORM), args.model, args.budget, reader)
        texts, tools, results, result = transcript(reader.events)
        run = Run(platform=PLATFORM, home=home, texts=texts, tools=tools, results=results, before=before,
                  after=files_text(home), mails=world.mails(), claude_keys=world.claude.keys[keys:],
                  google_calls=world.google.calls[calls:], has_secret=has_secret, tasks=butler_tasks,
                  real_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        problems = scenarios.grade(name, run)
        if problems or args.keep:
            save(name, reader.events, run.real_key)
        return problems, result.get("total_cost_usd"), len(tools)
    finally:
        if world:
            world.stop()
        installed = Path.home() / "Butler"
        if WINDOWS and python(installed).exists():     # no task of an eval's keeps running after it
            subprocess.run([str(python(installed)), "run.py", "schedule", "uninstall"], cwd=installed, capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


def claude_command_or_none() -> list[str]:
    """In a dry run: the Claude Code a paid run would start, if it's installed, and that it starts."""
    try:
        cmd = claude_command()
    except RuntimeError:
        return ["(claude, not installed here)"]
    version = subprocess.run([*cmd, "--version"], capture_output=True, text=True, timeout=120)
    if version.returncode:
        raise RuntimeError(f"Claude Code doesn't start: {version.stderr[-500:]}")
    return [*cmd, f"({version.stdout.strip()})"]


def save(name: str, events: list[dict], real_key: str) -> None:
    OUT.mkdir(exist_ok=True)
    text = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    if real_key:
        text = text.replace(real_key, "[the run's own key]")
    (OUT / f"{name}-{PLATFORM}.jsonl").write_text(text, encoding="utf-8")


def summary(text: str) -> None:
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scenarios", nargs="*", help=f"some of: {', '.join(SCENARIOS)} (default: all for this computer)")
    parser.add_argument("--dry", action="store_true", help="everything but the Claude call: free")
    parser.add_argument("--plan", action="store_true", help="print the workflow's matrix and the cost estimate")
    parser.add_argument("--platform", default="windows-arm", help=f"with --plan: {', '.join(scenarios.CHOICES)}")
    parser.add_argument("--installer", help="Windows: the Butler-Setup.exe to install")
    parser.add_argument("--budget", type=float, default=float(os.environ.get("EVAL_MAX_BUDGET") or 2),
                        help="dollars: a run stops when it has spent this (default 2)")
    parser.add_argument("--model", default=os.environ.get("EVAL_MODEL") or None)
    parser.add_argument("--keep", action="store_true", help="save every transcript to eval-output/, not only failures")
    args = parser.parse_args(argv)
    names = [n for word in args.scenarios for n in word.split()]

    if args.plan:
        try:
            runs = scenarios.plan(args.platform, names)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        line = scenarios.cost_line(runs, args.budget, args.dry)
        print(json.dumps({"include": [{**r, "name": f"{r['scenario']} on {r['platform']}"} for r in runs]}))
        print(line, file=sys.stderr)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(f"## Skill evals: the plan\n\n{len(runs)} runs: "
                        + ", ".join(f"{r['scenario']} on {r['platform']}" for r in runs) + f".\n\n{line}\n")
        return 0

    if not args.dry and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY isn't set: skipping the skill evals (--dry runs them for free).")
        return 0
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"no scenario {', '.join(unknown)}: there are {', '.join(SCENARIOS)}", file=sys.stderr)
        return 2
    names = names or [n for n, sc in SCENARIOS.items() if PLATFORM in sc.platforms]
    rows, failed = [], False
    for name in names:
        started = time.monotonic()
        print(f"\n== {name} on {PLATFORM}{' (dry)' if args.dry else ''}")
        try:
            problems, cost, calls = run_scenario(name, args)
        except Exception as e:  # noqa: BLE001 - one scenario's harness failure is that scenario's result
            problems, cost, calls = [f"the harness failed: {type(e).__name__}: {e}"], None, 0
        failed |= bool(problems)
        print(f"== {name}: {'pass' if not problems else 'FAIL'} ({calls} tool calls, cost {cost}, "
              f"{time.monotonic() - started:.0f}s)")
        for p in problems:
            print(f"  - {p}")
        rows.append(f"| {name} | {PLATFORM} | {'pass' if not problems else 'FAIL'} | {calls} | "
                    f"{f'${cost:.2f}' if cost is not None else '?'} | ${SCENARIOS[name].estimate:.2f} |")
    summary("## Skill evals" + (" (dry: no Claude call)" if args.dry else "") + "\n\n"
            "| Scenario | Computer | Result | Tool calls | Cost | Estimate |\n|---|---|---|---|---|---|\n"
            + "\n".join(rows) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
