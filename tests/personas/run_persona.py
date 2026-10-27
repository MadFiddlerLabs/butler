"""Persona tests: a Claude agent plays one of the course's students (personas.yaml), on a fresh
Mac or Windows test machine, through the lessons as the website shows them to that computer. It
types into real terminal windows, talks to the real Claude Code in the Butler folder (answering
its permission questions itself), and does what a person would do on screen through a small set
of actions the harness carries out: download and open the installer, check the inbox, click the
sign-in link, look at the brief in the browser. It makes the run's drawn choices and slips.

Paid (the Claude API) and CI only (.github/workflows/personas.yml): it installs into this
machine's real home folder and signs in, so it must never run on a computer someone uses.

    python tests/personas/run_persona.py --persona grandfather --platform mac --seed 3 \
        --lessons lessons-mac.json --installer dist/Butler-unsigned.pkg --budget 6 --out out/

Writes out/run.json (findings, checks, cost) and out/transcript.md.
"""
import argparse
import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml
from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock,
                              ToolUseBlock)
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tests"))
from fakes.account_api import FakeAccountAPI  # noqa: E402
from screens import DRY_CLAUDE, page_text  # noqa: E402
from terminal import Terminal  # noqa: E402

PERSONA_MODEL = "claude-sonnet-5-5"
# Dollars per million tokens for the persona's model (input, output, cache read, cache write).
PRICES = {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50}
WINDOWS = sys.platform == "win32"
HOME = Path.home()
BUTLER = HOME / "Butler"
KEYS = {"ENTER": "\r", "CTRL_C": "\x03", "TAB": "\t", "UP": "\x1b[A", "DOWN": "\x1b[B", "BACKSPACE": "\x7f",
        "ESCAPE": "\x1b"}


def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": properties, "required": required,
                             "additionalProperties": False}}


TOOLS = [
    tool("read_lesson", "Read a lesson page of the course website. 'next' moves on; 'again' shows the current one.",
         {"which": {"type": "string", "enum": ["next", "again"]}}, ["which"]),
    tool("terminal", "Use a Terminal (Mac) or Terminal/PowerShell (Windows) window. 'type' types the text and "
         "presses Enter; 'keys' sends keys without Enter (names in capitals: ENTER, CTRL_C, TAB, UP, DOWN, "
         "BACKSPACE, ESCAPE; anything else is typed as is); 'look' shows what's new; 'new_window' opens a new "
         "Terminal window; 'close' closes the window. Returns what the window shows.",
         {"action": {"type": "string", "enum": ["type", "keys", "look", "new_window", "close"]},
          "text": {"type": "string"}, "window": {"type": "integer"}}, ["action", "text", "window"]),
    tool("claude_code", "Type a message into Claude Code, once it's open in a Terminal window, and press Enter. "
         "Returns what Claude Code shows. '/exit' closes it.",
         {"message": {"type": "string"}}, ["message"]),
    tool("on_screen", "Do something on the computer outside a terminal, the way a person would with the mouse: "
         "check the computer's version, download Butler from the website (give the name you typed), open the "
         "downloaded file, check your email inbox, click the link in an email, look at what the browser shows, "
         "buy or sign in to a Claude plan, open a folder. Describe it in 'what' and pick the closest 'action'.",
         {"action": {"type": "string", "enum": ["check_version", "download_butler", "open_download", "check_inbox",
                                                "click_email_link", "look_at_browser", "claude_plan",
                                                "open_folder", "other"]},
          "what": {"type": "string"}}, ["action", "what"]),
    tool("note", "Write down something as it happens: you were confused, something seems broken, a step "
         "didn't match what you saw, or something was good.",
         {"kind": {"type": "string", "enum": ["confused", "broken", "mismatch", "good", "idea"]},
          "lesson": {"type": "string"}, "text": {"type": "string"}}, ["kind", "lesson", "text"]),
    tool("finish", "Stop: you finished the last lesson, or you're stuck and would give up here.",
         {"outcome": {"type": "string", "enum": ["finished", "stuck", "gave_up"]},
          "why": {"type": "string"}}, ["outcome", "why"]),
]
PERMISSION_TOOL = tool("answer", "Answer Claude Code's question.",
                       {"choice": {"type": "string", "enum": ["yes", "no"]},
                        "say": {"type": "string", "description": "If no: what you tell Claude instead, or empty."}},
                       ["choice", "say"])
REPORT_TOOL = tool("report", "Your report on the session, for the people who make the course.",
                   {"findings": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                     "required": ["severity", "lesson", "what_happened", "expected", "suggestion"],
                     "properties": {"severity": {"type": "string", "enum": ["blocker", "major", "minor", "polish"]},
                                    "lesson": {"type": "string"}, "what_happened": {"type": "string"},
                                    "expected": {"type": "string"}, "suggestion": {"type": "string"}}}},
                    "how_it_felt": {"type": "string"}}, ["findings", "how_it_felt"])


class Run:
    def __init__(self, args):
        spec = yaml.safe_load((HERE / "personas.yaml").read_text())
        self.persona = spec["personas"][args.persona]
        self.key, self.platform, self.seed = args.persona, args.platform, args.seed
        rng = random.Random(f"{args.persona}:{args.platform}:{args.seed}")
        self.cards = rng.sample(self.persona["deck"], spec.get("draw", 3))
        self.email = f"{self.persona['name'].lower()}.{args.seed}@example.com"
        self.lessons = json.loads(Path(args.lessons).read_text())["lessons"]
        self.at = -1
        self.installer, self.budget, self.out = Path(args.installer), args.budget, Path(args.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.client = anthropic.AsyncAnthropic()
        self.cost = {"persona": 0.0, "claude_code": 0.0}
        self.log: list[str] = []
        self.notes: list[dict] = []
        self.windows: list[Terminal] = []
        self.downloads: Path | None = None
        self.claude: ClaudeSDKClient | None = None
        self.claude_ready = False
        self.briefs_seen: set[str] = set()
        self.outcome = {"outcome": "budget", "why": "spent the run's budget"}
        self.api = FakeAccountAPI().start()
        self.env = {**os.environ, "BUTLER_API": self.api.url + "/api", "BUTLER_CLAUDE_DRY_RUN": "1",
                    "BUTLER_NO_OPEN": "1", "BUTLER_POLL_SECONDS": "2", "BUTLER_COLUMNS": "1000"}
        self.claude_pending, self.claude_mode, self.claude_prompt = False, "default", ""

    # ---------- money ----------
    def spent(self) -> float:
        return self.cost["persona"] + self.cost["claude_code"]

    def charge(self, usage) -> None:
        self.cost["persona"] += (usage.input_tokens * PRICES["input"] + usage.output_tokens * PRICES["output"]
                                 + (usage.cache_read_input_tokens or 0) * PRICES["cache_read"]
                                 + (usage.cache_creation_input_tokens or 0) * PRICES["cache_write"]) / 1e6

    def say(self, who: str, text: str) -> None:
        self.log.append(f"**{who}** ({datetime.now(timezone.utc):%H:%M:%S}, ${self.spent():.2f}):\n\n{text}\n")

    # ---------- the persona ----------
    def system(self) -> str:
        computer = "a Mac" if self.platform == "mac" else "a Windows PC"
        cards = "\n".join(f"- {c}" for c in self.cards)
        return f"""{self.persona['who']}

Today you're starting the free Build Your Butler course on your own {computer}, from the course website,
lesson by lesson. You want your butler to work. You are a real person, not a tester: do what the
lessons say the way you, {self.persona['name']}, would, including your habits and mistakes.

How this works:
- You can only see what your tools show you. Never assume what a screen says: look.
- Read a lesson with read_lesson, then do its steps with the other tools. Move on with read_lesson("next").
- Use terminal for Terminal windows (window 0 is the first one; new_window opens another), claude_code
  once Claude Code is open, and on_screen for anything you'd do with the mouse or in a browser.
- Your email address is {self.email}. You already pay for a Claude Pro plan.
- When something confuses you, looks broken, or doesn't match the lesson, use note straight away,
  then do what you'd really do: try the lesson's fix box, ask Claude, or try something else.
- When you finish the last lesson, or you'd really give up, use finish.

In this session, you will also:
{cards}"""

    async def ask(self, messages: list, tools: list, max_tokens: int = 4000):
        response = await self.client.messages.create(
            model=PERSONA_MODEL, max_tokens=max_tokens, system=self.system(), messages=messages, tools=tools,
            output_config={"effort": "medium"}, cache_control={"type": "ephemeral"})
        self.charge(response.usage)
        return response

    async def persona_loop(self) -> None:
        messages = [{"role": "user", "content": "Your computer is on and the course website is open. Start with the first lesson."}]
        while self.spent() < self.budget:
            response = await self.ask(messages, TOOLS)
            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            for b in response.content:
                if b.type == "text" and b.text.strip():
                    self.say(self.persona["name"], b.text)
            if not calls:
                messages.append({"role": "user", "content": "(Keep going: use your tools, or finish.)"})
                continue
            results = []
            for call in calls:
                self.say(f"{self.persona['name']} → {call.name}", "```json\n" + json.dumps(call.input, ensure_ascii=False) + "\n```")
                out = await self.do(call.name, call.input, messages)
                self.say(f"screen ({call.name})", f"```\n{out[-6000:]}\n```")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": out[-12000:] or "(nothing)"})
            messages.append({"role": "user", "content": results})
            if any(c.name == "finish" for c in calls):
                break
        self.messages = messages

    async def do(self, name: str, args: dict, messages: list) -> str:
        if name == "read_lesson":
            if args["which"] == "next":
                self.at += 1
            if self.at >= len(self.lessons):
                return "That was the last lesson in this part of the course."
            return self.lessons[max(self.at, 0)]["text"]
        if name == "terminal":
            return await asyncio.to_thread(self.terminal, args["action"], args["text"], args["window"])
        if name == "claude_code":
            return await self.claude_say(args["message"], messages)
        if name == "on_screen":
            return await asyncio.to_thread(self.on_screen, args["action"], args["what"])
        if name == "note":
            self.notes.append({**args, "at": self.lessons[max(self.at, 0)]["id"]})
            return "Noted."
        if name == "finish":
            self.outcome = args
            return "Thank you."
        return f"(no such tool: {name})"

    # ---------- terminal windows ----------
    def shell(self) -> list[str]:
        if WINDOWS:
            return [str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "-NoLogo"]
        return ["/bin/zsh", "-l"]

    def window_env(self) -> dict:
        env = dict(self.env)
        if WINDOWS:   # a new window gets the PATH from the registry, as Explorer gives it
            import winreg
            def read(root, key):
                try:
                    with winreg.OpenKey(root, key) as k:
                        return str(winreg.QueryValueEx(k, "Path")[0])
                except FileNotFoundError:
                    return ""
            machine = read(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
            env["PATH"] = os.path.expandvars(machine + ";" + read(winreg.HKEY_CURRENT_USER, "Environment") + ";" + os.environ["PATH"])
        return env

    def open_window(self, argv: list[str] | None = None) -> int:
        self.windows.append(Terminal(argv or self.shell(), str(HOME), self.window_env()))
        return len(self.windows) - 1

    def terminal(self, action: str, text: str, window: int) -> str:
        if action == "new_window" or not self.windows:
            window = self.open_window()
            if action == "new_window":
                return f"(window {window} opened)\n" + self.windows[window].read()
        if not 0 <= window < len(self.windows) or not self.windows[window].open:
            return f"There's no open window {window}. Open windows: {[i for i, w in enumerate(self.windows) if w.open]}"
        w = self.windows[window]
        if action == "close":
            w.close()
            return "(window closed)"
        if action == "type":
            w.type(text)
        elif action == "keys":
            w.keys("".join(KEYS.get(part, part) for part in re.split(r"(ENTER|CTRL_C|TAB|UP|DOWN|BACKSPACE|ESCAPE)", text)))
        screen = w.read(quiet=3.0, limit=300.0)
        return self.after_screen(screen)

    def after_screen(self, screen: str) -> str:
        """Butler's dry-run line for Claude Code becomes Claude Code opening, and a brief that would
        open in the browser does."""
        m = DRY_CLAUDE.search(screen)
        if m:
            self.claude_mode, self.claude_prompt = m[2], m[3]
            screen = screen[: m.start()] + "\n[Claude Code opens in this window. Type to it with the claude_code tool.]\n"
            self.claude_pending = True
        for brief in sorted((BUTLER / "out" / "preview").glob("brief-*.html")) if (BUTLER / "out" / "preview").exists() else []:
            if brief.name not in self.briefs_seen:
                self.briefs_seen.add(brief.name)
                screen += f"\n[Your browser opens a page: {brief.name}]\n" + self.page_text(brief)[:2500]
        return screen

    @staticmethod
    def page_text(path: Path) -> str:
        return page_text(path.read_text(encoding="utf-8", errors="replace"))

    # ---------- on screen ----------
    def on_screen(self, action: str, what: str) -> str:
        if action == "check_version":
            if WINDOWS:
                v = subprocess.run(["cmd", "/c", "ver"], capture_output=True, text=True).stdout.strip()
                return f"Settings > System > About: Windows 11 Pro, 64-bit operating system ({v})."
            v = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip()
            return f"About This Mac: macOS {v}."
        if action == "download_butler":
            name = re.sub(r'[\\/:*?"<>|]', "", what.strip()).strip() or "Butler"
            m = re.search(r"['\"](.+?)['\"]", what)
            name = (m[1] if m else name)[:40]
            downloads = HOME / "Downloads"
            downloads.mkdir(exist_ok=True)
            self.downloads = downloads / f"{name}{self.installer.suffix}"
            shutil.copyfile(self.installer, self.downloads)
            return f"The browser downloads {self.downloads.name} into your Downloads folder."
        if action == "open_download":
            if not self.downloads:
                return "Your Downloads folder has no Butler file in it."
            return self.install()
        if action == "check_inbox":
            mine = [r for r in self.api.requests.values() if r["email"].strip().lower() == self.email]
            if not mine:
                return "Your inbox has nothing new from Build Your Butler."
            pending = [r for r in mine if r["status"] == "pending"]
            return (f"An email from Build Your Butler: \"Sign in to Build Your Butler\". It says: \"Click to sign in "
                    f"your butler{' ' + mine[-1]['butler_name'] if mine[-1]['butler_name'] else ''}. The link works once, "
                    f"for 15 minutes.\" [Sign in]" + ("" if pending else " (You've already used this link.)"))
        if action == "click_email_link":
            pending = [(i, r) for i, (rid, r) in enumerate(self.api.requests.items())
                       if r["email"].strip().lower() == self.email and r["status"] == "pending"]
            if not pending:
                return "The page says: \"This link expired or was already used.\""
            self.api.click(self.api.links[pending[-1][0]])
            return "The browser opens buildyourbutler.com: \"You're signed in. Go back to the Terminal window.\""
        if action == "look_at_browser":
            briefs = sorted((BUTLER / "out" / "preview").glob("brief-*.html")) if (BUTLER / "out" / "preview").exists() else []
            return self.page_text(briefs[-1])[:4000] if briefs else "The browser shows the course website."
        if action == "claude_plan":
            return "You're signed in to claude.ai. Your plan: Claude Pro."
        if action == "open_folder":
            folder = Path(os.path.expandvars(os.path.expanduser(what.strip().strip('"')))) if what.strip().startswith(("~", "%", "/", "C:")) else BUTLER
            if not folder.exists():
                return f"There's no folder there ({what})."
            names = sorted(p.name + ("/" if p.is_dir() else "") for p in folder.iterdir())
            return f"{folder}: " + ", ".join(names[:60])
        self.notes.append({"kind": "unsupported", "lesson": self.lessons[max(self.at, 0)]["id"], "text": what})
        return "Nothing you can see happens. (This test can't do that on screen: describe what you'd expect, then carry on.)"

    def install(self) -> str:
        """The installer, as a person runs it: on a Mac, Installer, then postinstall opens the
        first run in Terminal; on Windows, the setup wizard, then the first run's window."""
        if WINDOWS:
            p = subprocess.run([str(self.downloads), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], capture_output=True)
            if p.returncode != 0:
                return f"The installer says it couldn't finish (exit {p.returncode})."
            first = self.open_window(["cmd.exe", "/d", "/k", str(BUTLER / "bin" / "butler.cmd"), "setup"])
            return (f"Windows asks nothing (the test signs nothing; a real download may show SmartScreen first). The "
                    f"installer shows Ready to Install; you click Install, it says \"Getting Butler's own Python. This "
                    f"takes a minute or two.\", then Finish. A window titled Butler opens (window {first}):\n"
                    + self.windows[first].read(quiet=5.0, limit=600.0))
        p = subprocess.run(["installer", "-pkg", str(self.downloads), "-target", "CurrentUserHomeDirectory"],
                           capture_output=True, text=True)
        if p.returncode != 0:
            return f"Installer says: The installation failed. ({p.stderr.strip()[-300:]})"
        opens = subprocess.run(["bash", "-c", f'. "{BUTLER}/scripts/lib/install.sh"; install_opens "$(install_state "{BUTLER}")"'],
                               capture_output=True, text=True).stdout.strip() or "first-run.command"
        first = self.open_window(["/bin/bash", str(BUTLER / "scripts" / opens)])
        return (f"Installer: Continue, Continue, Install for me only, Install. \"The installation was completed "
                f"successfully.\" A Terminal window opens (window {first}):\n" + self.windows[first].read(quiet=5.0, limit=600.0))

    # ---------- Claude Code ----------
    async def claude_say(self, message: str, messages: list) -> str:
        if getattr(self, "claude_pending", False) and not self.claude:
            options = ClaudeAgentOptions(
                cwd=str(BUTLER), permission_mode=self.claude_mode, can_use_tool=self.permission(messages),
                setting_sources=["project"], env={k: v for k, v in self.env.items() if k.startswith("BUTLER_")},
                max_budget_usd=max(0.5, self.budget - self.spent()))
            self.claude = ClaudeSDKClient(options=options)
            await self.claude.connect()
            (BUTLER / "data").mkdir(exist_ok=True)
            (BUTLER / "data" / "claude-welcomed").touch()
            opening = await self.claude_turn(self.claude_prompt)
            self.claude_pending = False
            if message.strip() in ("", "(look)"):
                return opening
            opening = f"(Claude Code's first message, before you typed:)\n{opening}\n\n"
        else:
            opening = ""
        if not self.claude:
            return "Claude Code isn't open. Type `butler claude` in a Terminal window first."
        if message.strip() == "/exit":
            await self.claude.disconnect()
            self.claude = None
            return "(Claude Code closes. The Terminal prompt is back.)"
        return opening + await self.claude_turn(message)

    async def claude_turn(self, message: str) -> str:
        await self.claude.query(message)
        shown = []
        async for msg in self.claude.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        shown.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        shown.append(f"⏺ {block.name}({json.dumps(block.input, ensure_ascii=False)[:300]})")
            elif isinstance(msg, ResultMessage):
                self.cost["claude_code"] += msg.total_cost_usd or 0.0
        return "\n".join(shown) or "(Claude Code shows nothing new.)"

    def permission(self, messages: list):
        async def can_use_tool(tool_name, tool_input, context):
            what = json.dumps(tool_input, ensure_ascii=False)[:800]
            question = (f"Claude Code stops and asks you, in the Terminal window:\n\n  {tool_name}: {what}\n\n"
                        "  Do you want to proceed?\n  ❯ 1. Yes\n    2. No, and tell Claude what to do differently (esc)\n\n"
                        "Answer with the answer tool.")
            snapshot = messages + [{"role": "user", "content": question}] if messages[-1]["role"] == "assistant" else \
                messages[:-1] + [{"role": "user", "content": question}]
            try:
                response = await self.ask(snapshot, [PERMISSION_TOOL], max_tokens=1500)
                answer = next((b.input for b in response.content if b.type == "tool_use"), {"choice": "yes", "say": ""})
            except Exception as e:  # noqa: BLE001 - a broken question shouldn't stop the run
                answer = {"choice": "yes", "say": f"(harness: {e})"}
            self.say(f"{self.persona['name']} answers Claude Code", f"{tool_name}: {answer}")
            if answer["choice"] == "yes":
                return PermissionResultAllow()
            return PermissionResultDeny(message=answer.get("say") or "The user said no.")
        return can_use_tool

    # ---------- the end ----------
    async def report(self) -> dict:
        ask = ("The session is over. Write your report for the people who make the course: every place you got "
               "stuck, were confused, saw something broken or different from the lesson, or had to guess. Be "
               "specific: the lesson, what you did, what you saw, what you expected. Use the report tool.")
        messages = getattr(self, "messages", [{"role": "user", "content": "(no session)"}])
        if messages[-1]["role"] == "user":
            messages = messages + [{"role": "assistant", "content": "I'm done."}]
        try:
            response = await self.ask(messages + [{"role": "user", "content": ask}], [REPORT_TOOL], max_tokens=6000)
            return next((b.input for b in response.content if b.type == "tool_use"), {"findings": [], "how_it_felt": ""})
        except Exception as e:  # noqa: BLE001
            return {"findings": [], "how_it_felt": f"(report failed: {e})"}

    def checks(self) -> dict:
        """What the run left behind, checked by Butler's own files, not the persona's word."""
        def exists(rel):
            return (BUTLER / rel).exists()
        screens = "".join(w.screen for w in self.windows)
        return {
            "installed": exists(".butler-version"),
            "named": (BUTLER / ".butler-name").read_text().strip() if exists(".butler-name") else None,
            "signed_in": any(r["status"] == "verified" for r in self.api.requests.values()),
            "terms_agreed": exists("data/terms.json"),
            "settings": exists("config/settings.yaml"),
            "first_brief": bool(list((BUTLER / "out" / "preview").glob("brief-*.html"))) if exists("out/preview") else False,
            "claude_code_opened": exists("data/claude-welcomed"),
            "lessons_reported": sorted({p.get("lesson") for p in self.api.progress if p.get("lesson")}),
            "tracebacks": len(re.findall(r"Traceback \(most recent call last\)", screens)),
        }

    async def main(self) -> int:
        started = time.monotonic()
        try:
            await self.persona_loop()
        finally:
            if self.claude:
                try:
                    await self.claude.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        report = await self.report()
        result = {"persona": self.key, "name": self.persona["name"], "platform": self.platform, "seed": self.seed,
                  "cards": self.cards, "outcome": self.outcome, "reached": self.lessons[max(self.at, 0)]["id"],
                  "lessons_total": len(self.lessons), "lessons_read": self.at + 1, "notes": self.notes,
                  "report": report, "checks": self.checks(),
                  "cost_usd": {k: round(v, 3) for k, v in {**self.cost, "total": self.spent()}.items()},
                  "minutes": round((time.monotonic() - started) / 60, 1)}
        (self.out / "run.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        (self.out / "transcript.md").write_text(
            f"# {self.persona['name']} ({self.key}) on {self.platform}, run {self.seed}\n\nChoices: {self.cards}\n\n"
            + "\n".join(self.log), encoding="utf-8")
        for w in self.windows:
            w.close()
        self.api.stop()
        print(json.dumps({k: result[k] for k in ("persona", "platform", "seed", "outcome", "reached", "cost_usd")}))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", required=True)
    parser.add_argument("--platform", choices=["mac", "windows"], required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--lessons", required=True)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--budget", type=float, default=6.0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not os.environ.get("CI"):
        sys.exit("Persona tests install into this computer's home folder and sign in: CI only.")
    return asyncio.run(Run(args).main())


if __name__ == "__main__":
    sys.exit(main())
