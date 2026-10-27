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
from screens import DRY_CLAUDE, page_text, side_question, site_name  # noqa: E402
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
         "check the computer's version, download Butler from the website (put the name you type in 'text'), open "
         "the downloaded file, check your email inbox, click the link or button in an email, click a button on a "
         "web page (its label in 'text'), open a buildyourbutler.com page (its address in 'text'), look at what the browser shows, buy or sign in to a Claude plan, open the "
         "Butler app (Mac) or Butler in the Start menu (Windows), open a folder. Describe it in 'what' and pick the "
         "closest 'action'.",
         {"action": {"type": "string", "enum": ["check_version", "download_butler", "open_download", "check_inbox",
                                                "click_email_link", "click_on_page", "open_web_page", "look_at_browser",
                                                "claude_plan", "open_butler_app", "open_folder", "other"]},
          "what": {"type": "string"},
          "text": {"type": "string", "description": "What you type, if anything (the butler's name for download_butler); else empty."}},
         ["action", "what", "text"]),
    tool("note", "Write down something as it happens: you were confused, something seems broken, a step "
         "didn't match what you saw, or something was good.",
         {"kind": {"type": "string", "enum": ["confused", "broken", "mismatch", "good", "idea"]},
          "lesson": {"type": "string"}, "text": {"type": "string"}}, ["kind", "lesson", "text"]),
    tool("finish", "Stop: only after read_lesson has told you that was the last lesson, or when you're truly stuck "
         "and would give up here. Not to take a break: carry on with the next lesson instead.",
         {"outcome": {"type": "string", "enum": ["finished", "stuck", "gave_up"]},
          "why": {"type": "string"}}, ["outcome", "why"]),
]
CHOICE_TOOL = tool("choose", "Answer Claude Code's question: for each question, the label of the option you pick, or "
                   "your own words if none fits.",
                   {"answers": {"type": "array", "items": {"type": "string"}}}, ["answers"])
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
        if not WINDOWS:   # a Mac's login shell is zsh; the runner's account says bash
            self.env["SHELL"] = "/bin/zsh"
        self.claude_pending, self.claude_mode, self.claude_prompt = False, "default", ""
        self.claude_model = args.claude_model or None
        self.trusted = False
        self.page = None          # the web page open in the browser, when it has a button to click
        self._shown: list[str] = []
        self.claude_closed = 0.0  # what Claude Code sessions already closed cost
        self.fallbacks = 0        # questions the harness answered because asking the persona failed

    def current(self) -> dict:
        """The lesson the persona is on (the last one, once it has read past the end)."""
        return self.lessons[min(max(self.at, 0), len(self.lessons) - 1)]

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
- Keep going, lesson after lesson, until read_lesson tells you that was the last lesson. Use finish only then,
  or if you're truly stuck and would give up.
- This computer is already signed in to Claude (claude.ai), so Claude Code won't ask you to sign in.
- When Claude Code asks permission for something, you'll be asked in that moment, and what you answer is your
  own choice: the claude_code result then shows the question and your answer.

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
        self.messages = messages    # kept for the report, however the session ends
        while self.spent() < self.budget - 0.1:
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
                self.at = len(self.lessons)
                return "That was the last lesson in this part of the course."
            return self.current()["text"]
        if name == "terminal":
            return await asyncio.to_thread(self.terminal, args["action"], args["text"], args["window"])
        if name == "claude_code":
            return await self.claude_say(args["message"], messages)
        if name == "on_screen":
            return await asyncio.to_thread(self.on_screen, args["action"], args["what"], args["text"])
        if name == "note":
            self.notes.append({**args, "at": self.current()["id"]})
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
        # 'look' is a person watching the window: it waits for something to appear, then for it to settle.
        if action == "look":
            w.wait_new(limit=30.0)   # at once if something's there already
        screen = w.read(quiet=3.0 if action != "look" else 5.0, limit=300.0)
        return self.after_screen(screen)

    def after_screen(self, screen: str) -> str:
        """Butler's dry-run line for Claude Code becomes Claude Code opening, and a brief that would
        open in the browser does."""
        m = DRY_CLAUDE.search(screen)
        if m:
            self.claude_mode, self.claude_prompt = m[2], m[3]
            self.claude_pending = True
            if self.trusted is True:   # this folder was trusted already
                opened = "\n[Claude Code opens in this window. Type to it with the claude_code tool.]\n"
            else:
                self.trusted = None    # Claude Code asks before anything else
                opened = (f"\n[Claude Code opens in this window.]\n\n Do you trust the files in this folder?\n\n {BUTLER}\n\n"
                          " Claude Code may read, write, or execute files contained in this directory. This can pose "
                          "security risks, so only use files from trusted sources.\n\n ❯ 1. Yes, proceed\n   2. No, exit\n\n"
                          "(Answer with the claude_code tool: 1 or 2.)\n")
            screen = screen[: m.start()] + opened
        for brief in sorted((BUTLER / "out" / "preview").glob("brief-*.html")) if (BUTLER / "out" / "preview").exists() else []:
            if brief.name not in self.briefs_seen:
                self.briefs_seen.add(brief.name)
                screen += f"\n[Your browser opens a page: {brief.name}]\n" + self.page_text(brief)[:2500]
        return screen

    @staticmethod
    def page_text(path: Path) -> str:
        return page_text(path.read_text(encoding="utf-8", errors="replace"))

    # ---------- on screen ----------
    def on_screen(self, action: str, what: str, typed: str = "") -> str:
        if action == "check_version":
            if WINDOWS:
                v = subprocess.run(["cmd", "/c", "ver"], capture_output=True, text=True).stdout.strip()
                return f"Settings > System > About: Windows 11 Pro, 64-bit operating system ({v})."
            v = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip()
            return f"About This Mac: macOS {v}."
        if action == "download_butler":
            name, said = site_name(typed)
            downloads = HOME / "Downloads"
            downloads.mkdir(exist_ok=True)
            self.downloads = downloads / f"{name}{self.installer.suffix}"
            shutil.copyfile(self.installer, self.downloads)
            shown = f"The box shows: {typed[:24]!r}. " if typed else "The name box is empty. "
            return (shown + (f"Under it, in red: \"{said}\" " if said else "")
                    + f"You click Download. The browser downloads {self.downloads.name} into your Downloads folder.")
        if action == "open_download":
            if not self.downloads:
                return "Your Downloads folder has no Butler file in it."
            return self.install()
        if action == "check_inbox":
            mine = [r for r in self.api.requests.values() if r["email"].strip().lower() == self.email]
            if not mine:
                return "Your inbox has nothing new from Build Your Butler."
            name = mine[-1]["butler_name"] or "your butler"
            return (f"{len(mine)} email(s) from Build Your Butler. The newest: \"Your link to wake up {name}\".\n\n"
                    f"Hello,\n\nHere's your link to finish setting up {name}, your new butler:\n\n[Wake up {name}]\n\n"
                    "It works once, for the next 15 minutes. It opens a page that asks you to confirm it's you. Then go "
                    f"back to your Terminal. {name} carries on from there.\n\nIf you didn't ask for this, ignore it. "
                    "Nothing happens without the link.\n\nBuild Your Butler · support@buildyourbutler.com")
        if action == "click_email_link":
            pending = [(i, r) for i, (rid, r) in enumerate(self.api.requests.items())
                       if r["email"].strip().lower() == self.email and r["status"] == "pending"]
            if not pending:
                return ("The browser opens buildyourbutler.com: \"This link was already used\". If your Terminal says "
                        "you're signed in, you're all set. If not, ask it for a new link.")
            index, request = pending[-1]
            self.page = ("confirm", index)
            asker = "A Terminal on a Mac" if self.platform == "mac" else "A Terminal window"
            butler = f" and set up {request['butler_name']}" if request["butler_name"] else ""
            return (f"The browser opens buildyourbutler.com: \"Sign in your butler?\" {asker} asked to sign in{butler} "
                    "just now. [Yes, sign me in]")
        if action == "click_on_page":
            if self.page and self.page[0] == "confirm" and "sign me in" in typed.lower() + what.lower():
                self.api.click(self.api.links[self.page[1]])
                self.page = None
                return "The page says: \"You're in.\" Go back to your Terminal. Your butler is waiting for you there."
            return "Nothing on the page matches that."
        if action == "look_at_browser":
            briefs = sorted((BUTLER / "out" / "preview").glob("brief-*.html")) if (BUTLER / "out" / "preview").exists() else []
            return self.page_text(briefs[-1])[:4000] if briefs else "The browser shows the course website."
        if action == "open_butler_app":
            if not (BUTLER / "bin").exists():
                return "There's no Butler app yet." if not WINDOWS else "Start menu: no app called Butler."
            if WINDOWS:
                argv = ["cmd.exe", "/d", "/k", str(BUTLER / "bin" / "butler.cmd"), "resume"]
            else:
                argv = ["/bin/zsh", "-l", "-c", f'"{BUTLER}/bin/butler" resume; exec /bin/zsh -l']
            window = self.open_window(argv)
            return f"A new window opens (window {window}):\n" + self.windows[window].read(quiet=4.0, limit=300.0)
        if action == "open_web_page":
            address = (typed or what).strip()
            if not re.match(r"^(https://)?(www\.)?buildyourbutler\.com(/[\w/#.-]*)?$", address):
                return "This test opens only buildyourbutler.com pages: describe what you'd look for, then carry on."
            url = address if address.startswith("https://") else "https://" + address
            try:
                import urllib.request
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "persona-test"}), timeout=20) as r:
                    return page_text(r.read().decode("utf-8", errors="replace"))[:6000]
            except OSError as e:
                return f"The page doesn't load ({e})."
        if action == "claude_plan":
            return "You're signed in to claude.ai. Your plan: Claude Pro."
        if action == "open_folder":
            folder = Path(os.path.expandvars(os.path.expanduser(what.strip().strip('"')))) if what.strip().startswith(("~", "%", "/", "C:")) else BUTLER
            if not folder.exists():
                return f"There's no folder there ({what})."
            names = sorted(p.name + ("/" if p.is_dir() else "") for p in folder.iterdir())
            return f"{folder}: " + ", ".join(names[:60])
        self.notes.append({"kind": "unsupported", "lesson": self.current()["id"], "text": what})
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
        if self.claude_pending and not self.claude and self.trusted is None:
            if message.strip().lower() not in ("1", "yes", "yes, proceed", "2", "no", "no, exit", "esc"):
                return ("Claude Code is still asking: Do you trust the files in this folder?  ❯ 1. Yes, proceed   2. No, "
                        "exit\n(Nothing else can be typed until you answer.)")
            if message.strip().lower() in ("2", "no", "no, exit", "esc"):
                self.claude_pending, self.trusted = False, False
                return "(Claude Code exits. The Terminal prompt is back.)"
            self.trusted = True
            message = ""
        if self.claude_pending and not self.claude:
            options = ClaudeAgentOptions(
                cwd=str(BUTLER), permission_mode=self.claude_mode, can_use_tool=self.permission(messages),
                setting_sources=["project"], env={k: v for k, v in self.env.items() if k.startswith("BUTLER_")},
                # A margin: Claude Code's last reply lands after its own check.
                max_budget_usd=max(0.3, self.budget - self.spent() - 0.25), model=self.claude_model)
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
            self.claude_closed = self.cost["claude_code"]
            self.claude, self.claude_pending = None, False   # a folder is trusted once, as Claude Code keeps it
            return "(Claude Code closes. The Terminal prompt is back.)"
        return opening + await self.claude_turn(message)

    async def claude_turn(self, message: str) -> str:
        await self.claude.query(message)
        shown = self._shown = []    # the permission question goes in here too, where it appears
        async for msg in self.claude.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        shown.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        shown.append(f"⏺ {block.name}({json.dumps(block.input, ensure_ascii=False)[:300]})")
            elif isinstance(msg, ResultMessage):
                # total_cost_usd is the session's running total, not this turn's: the sessions
                # already closed, plus this one's latest.
                self.cost["claude_code"] = self.claude_closed + (msg.total_cost_usd or 0.0)
        return "\n".join(shown) or "(Claude Code shows nothing new.)"

    def permission(self, messages: list):
        async def can_use_tool(tool_name, tool_input, context):
            if tool_name == "AskUserQuestion":
                return await self.choose(tool_input, messages)
            what = json.dumps(tool_input, ensure_ascii=False)[:800]
            question = (f"Claude Code stops and asks you, in the Terminal window:\n\n  {tool_name}: {what}\n\n"
                        "  Do you want to proceed?\n  ❯ 1. Yes\n    2. No, and tell Claude what to do differently (esc)\n\n"
                        "This is your decision, as you, with this session's habits and choices in mind. Answer with the "
                        "answer tool.")
            snapshot = side_question(messages, question)
            try:
                response = await self.ask(snapshot, [PERMISSION_TOOL], max_tokens=1500)
                answer = next((b.input for b in response.content if b.type == "tool_use"), {"choice": "yes", "say": ""})
            except Exception as e:  # noqa: BLE001 - a broken question shouldn't stop the run
                self.fallbacks += 1     # counted in the checks: the persona didn't decide this one
                answer = {"choice": "yes", "say": f"(harness: {e})"}
            self.say(f"{self.persona['name']} answers Claude Code", f"{tool_name}: {answer}")
            self._shown.append(f"Claude Code stopped and asked you: {tool_name}: {what[:300]}\n  Do you want to proceed?\n"
                               f"  You answered: {'1. Yes' if answer['choice'] == 'yes' else '2. No'}"
                               f"{(', and told Claude: ' + answer['say']) if answer['choice'] != 'yes' and answer.get('say') else ''}")
            if answer["choice"] == "yes":
                return PermissionResultAllow()
            return PermissionResultDeny(message=answer.get("say") or "The user said no.")
        return can_use_tool

    async def choose(self, tool_input: dict, messages: list):
        """Claude Code's multiple-choice question (AskUserQuestion): the persona picks, and the
        answers go back to Claude as the Agent SDK expects, keyed by each question's text."""
        questions = tool_input.get("questions", [])
        shown = []
        for i, q in enumerate(questions, 1):
            options = "\n".join(f"    {j}. {o.get('label')}: {o.get('description', '')}" for j, o in enumerate(q.get("options", []), 1))
            shown.append(f"  {i}. {q.get('question')}\n{options}\n    (or type something else)")
        question = ("Claude Code asks you, with choices to pick from:\n\n" + "\n".join(shown)
                    + "\n\nThis is your decision, as you. Answer with the choose tool, one answer per question.")
        snapshot = side_question(messages, question)
        try:
            response = await self.ask(snapshot, [CHOICE_TOOL], max_tokens=1500)
            picked = next((b.input["answers"] for b in response.content if b.type == "tool_use"), [])
        except Exception:  # noqa: BLE001 - a broken question shouldn't stop the run
            self.fallbacks += 1
            picked = []
        answers = {q.get("question", ""): (picked[i] if i < len(picked) else "") for i, q in enumerate(questions)}
        self.say(f"{self.persona['name']} chooses", json.dumps(answers, ensure_ascii=False))
        self._shown.append("Claude Code asked you:\n" + "\n".join(shown) + "\n  You answered: "
                           + "; ".join(a or "(nothing)" for a in answers.values()))
        return PermissionResultAllow(updated_input={**tool_input, "answers": answers})

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
            "harness_answered": self.fallbacks,
        }

    async def main(self) -> int:
        started = time.monotonic()
        try:
            await self.persona_loop()
        except Exception as e:  # noqa: BLE001 - the run so far is still worth its report
            self.outcome = {"outcome": "harness_error", "why": f"{type(e).__name__}: {e}"}
            self.say("harness", f"stopped: {type(e).__name__}: {e}")
        finally:
            if self.claude:
                try:
                    await self.claude.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        try:
            report = await self.report()
        except Exception as e:  # noqa: BLE001 - the run is saved whatever happens here
            report = {"findings": [], "how_it_felt": f"(report failed: {e})"}
        result = {"persona": self.key, "name": self.persona["name"], "platform": self.platform, "seed": self.seed,
                  "cards": self.cards, "outcome": self.outcome, "reached": self.current()["id"],
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
    parser.add_argument("--claude-model", default="sonnet",
                        help="Claude Code's model, as a Pro-plan student gets it; empty for Claude Code's own default")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not os.environ.get("CI"):
        sys.exit("Persona tests install into this computer's home folder and sign in: CI only.")
    return asyncio.run(Run(args).main())


if __name__ == "__main__":
    sys.exit(main())
