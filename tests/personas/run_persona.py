"""Persona tests: a Claude agent plays one of the course's students (personas.yaml), on a fresh
Mac or Windows test machine, through the lessons as the website shows them to that computer. It
types into real terminal windows, talks to the real Claude Code in the Butler folder (answering
its permission questions itself), and does what a person would do on screen through a small set
of actions the harness carries out: download and open the installer, check the inbox, click the
sign-in link, look at the brief in the browser. It makes the run's drawn choices and slips.
Past Module 2, outside.py stands in for the services a student signs up for (Claude Console,
Gmail, Lunch Money, Tailscale), and the windows that open on screen come to the persona.

The student's path, as a student takes it (28 Oct 2026; the owner's test PC found two bugs the
old shortcuts hid):
- The run is a standard account with a space in its name ("BYB Test"), not the runner's admin:
  personas.yml makes it and starts the harness as it (tests/personas/as-student.sh and .ps1).
- The installer opens the way a double-click opens it, and the persona works in the window the
  installer itself opens. Windows: the download carries the Mark of the Web, os.startfile opens
  it, the persona answers each window (a security prompt, the wizard's pages) as it appears, and
  the console the installer opened is read and typed into where it is (windows_screen.py). Mac:
  see install_mac for what a hosted runner can't do, and the closest real path it takes instead.
- `butler claude` runs for real: Anthropic's installer, then Claude Code, started by Butler. The
  harness then takes that Claude Code over through the Agent SDK, with the same binary, mode and
  opening prompt, so the persona can answer its questions (and the run can count its cost).
What still differs from a real student is listed in run.json's `differences`, and in the
transcript as it happens.

Paid (the Claude API) and CI only (.github/workflows/personas.yml): it installs into this
machine's real home folder and signs in, so it must never run on a computer someone uses.
`--rehearse` is free: a scripted student, no Claude API at all, takes the same path from the
download into Claude Code (and the schedule), and the run fails if any of it breaks.

    python tests/personas/run_persona.py --persona grandfather --platform mac --seed 3 \
        --lessons lessons-mac.json --installer dist/Butler-unsigned.pkg --budget 6 --key-file key --out out/

Writes out/run.json (findings, checks, cost) and out/transcript.md.
"""
import argparse
import asyncio
import getpass
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import threading
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
import events  # noqa: E402
from fakes.account_api import FakeAccountAPI  # noqa: E402
from outside import Outside, at_screen, is_admin  # noqa: E402
from screens import (CLAUDE_OPENING, FORWARD, butler_claude, forward, install_log_opened, mac_claude, page_text,  # noqa: E402
                     side_question, site_name, split_windows, terminal_types, trim, trust_folder, waiting_only)
from terminal import Terminal  # noqa: E402

PERSONA_MODEL = "claude-sonnet-5-5"
# Told to the student's Claude Code, which otherwise sees the fakes (127.0.0.1, a test Google
# client, the sample family's calendar) and, rightly, warns the student: round 3 lost a run to it.
TEST_COMPUTER = (
    "A note from the course's automated tests, not from the person you're talking to: this is a test "
    "computer. Google's sign-in and APIs, Gmail, Lunch Money and the Claude API answer on 127.0.0.1 here, "
    "Google's client is a test one, the calendars, tasks and bank hold an invented sample household (the "
    "Johnsons), and the household's example.com addresses receive mail and are their Tailscale logins. "
    "Tailscale and the Calendar helper (bin/butler-ek) are small stand-in programs that behave as the "
    "real ones do: use them as the real ones. Treat all of it exactly as this "
    "household's real accounts and data, and don't mention that any of it is a stand-in. Everything else "
    "is as a real student has it.")

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
         "the downloaded file, check your email inbox, read an email (its number in 'text'), reply to the email you "
         "read (your reply in 'text'), click the link or button in an email, click a button on a web page (its label "
         "in 'text'), open a buildyourbutler.com page (its address in 'text'), look at what the browser shows, buy or "
         "sign in to a Claude plan, open the Butler app (Mac) or Butler in the Start menu (Windows), open a folder, "
         "open an app such as Reminders, Calendar or Google Tasks, or wait a few minutes (how many in 'text'). On "
         "other sites, each action does what takes a few clicks there: in the Claude Console, set a monthly spend "
         "limit (the amount in 'text') or create an API key (its name in 'text'); at Google, create a Gmail account "
         "(the address you want in 'text'), turn on 2-Step Verification, or create an app password (its name in "
         "'text'); at Lunch Money, sign up and connect your bank, or create an access token (its name in 'text'); "
         "install Tailscale ('computer' or 'phone' in 'text'), use Tailscale's admin console (what you do in 'what', "
         "a new machine name in 'text'), open an address in your phone's browser (in 'text'), or switch an app on "
         "or off in System Settings > Privacy & Security (what, in 'text', such as 'Calendars on'). Describe it in "
         "'what' and pick the closest 'action'.",
         {"action": {"type": "string", "enum": ["check_version", "download_butler", "open_download", "check_inbox",
                                                "read_email", "reply_to_email", "click_email_link", "click_on_page",
                                                "open_web_page", "look_at_browser", "claude_plan", "open_butler_app",
                                                "open_folder", "open_app", "wait", "console_set_limit",
                                                "console_create_key", "gmail_create_account", "google_two_step",
                                                "google_app_password", "lunchmoney_sign_up", "lunchmoney_create_token",
                                                "tailscale_install", "tailscale_admin", "phone_open",
                                                "privacy_settings", "other"]},
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
WINDOW_TOOL = tool("window", "Answer the window on screen: the button you click, what you type or paste into its box "
                   "(empty if it has none), and the boxes you tick (empty if none).",
                   {"button": {"type": "string"}, "text": {"type": "string"},
                    "ticked": {"type": "array", "items": {"type": "string"}}}, ["button", "text", "ticked"])
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
        self.rehearse, self.computer = args.rehearse, args.computer or args.platform
        # The run's key, read once and kept out of the environment: every window and program the
        # student opens inherits it, and a student has no ANTHROPIC_API_KEY.
        self.api_key = read_key(args.key_file)
        if not self.api_key and not self.rehearse:
            sys.exit("A paid run needs the Claude API key (--key-file or ANTHROPIC_API_KEY); --rehearse needs none.")
        self.client = None if self.rehearse else anthropic.AsyncAnthropic(api_key=self.api_key)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.differences: list[str] = []   # where this run's path isn't a real student's, and why
        self.claude_exe: str | None = None  # the Claude Code `butler claude` installed and started
        self.claude_window: int | None = None
        self.claude_installed_before = bool(shutil.which("claude")) or (HOME / ".local" / "bin" / ("claude.exe" if WINDOWS else "claude")).exists()
        self.rehearsal: dict = {}
        self.done = False
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
        self.outside = Outside(self.persona["name"], self.email, lambda: self.spent() < self.budget - 0.1, BUTLER,
                               api_key=self.api_key)
        self.events_seen: set[str] = set()
        self.pending: list[str] = []   # windows and pages that came up during the last action
        # No BUTLER_CLAUDE_DRY_RUN: `butler claude` installs and starts the real Claude Code.
        self.env = {**os.environ, "BUTLER_API": self.api.url + "/api",
                    "BUTLER_NO_OPEN": "1", "BUTLER_POLL_SECONDS": "2", "BUTLER_COLUMNS": "1000"}
        if not WINDOWS:   # a Mac's login shell is zsh
            self.env["SHELL"] = "/bin/zsh"
        self.env = self.outside.path(self.env)
        # Everything this process starts gets it too (the installer, and the window it opens), as
        # Explorer or Finder would hand on the student's own.
        os.environ.clear()
        os.environ.update(self.env)
        self.account = {"user": getpass.getuser(), "home": str(HOME), "admin": is_admin(),
                        "machine": platform.machine(), "os": platform.platform()}
        if not WINDOWS:
            self.account["at_screen"] = at_screen()
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
        return self.cost["persona"] + self.cost["claude_code"] + self.outside.relay.cost

    def charge(self, usage) -> None:
        self.cost["persona"] += (usage.input_tokens * PRICES["input"] + usage.output_tokens * PRICES["output"]
                                 + (usage.cache_read_input_tokens or 0) * PRICES["cache_read"]
                                 + (usage.cache_creation_input_tokens or 0) * PRICES["cache_write"]) / 1e6

    def say(self, who: str, text: str) -> None:
        self.log.append(f"**{who}** ({datetime.now(timezone.utc):%H:%M:%S}, ${self.spent():.2f}):\n\n{text}\n")
        try:   # as it happens too, so a run that hangs or is stopped still shows how far it got
            with open(self.out / "live.md", "a", encoding="utf-8") as live:
                live.write(self.log[-1] + "\n")
        except OSError:
            pass

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
- Other websites (the Claude Console, Google and Gmail, Lunch Money, Tailscale) and your phone are on_screen
  actions too. You read and answer your email with check_inbox, read_email and reply_to_email.
- This test computer's calendars and bank hold an invented family's sample data (the Johnsons, in Lisbon)
  instead of yours. Treat it as yours; it isn't something to report.
- When a window or page appears while you're doing something, you'll be asked about it in that moment.
- When Claude Code asks permission for something, you'll be asked in that moment, and what you answer is your
  own choice: the claude_code result then shows the question and your answer.

In this session, you will also:
{cards}"""

    def left(self) -> float:
        """What the run may still spend before it stops, keeping a reserve for the last Claude Code
        turn and the report: round 3's check runs passed their cap by up to $1 without one."""
        return self.budget - max(0.75, 0.1 * self.budget) - self.spent()

    async def ask(self, messages: list, tools: list, max_tokens: int = 4000, effort: str = "medium"):
        if self.client is None:
            raise RuntimeError("a rehearsal asks no persona")
        if self.spent() >= self.budget - 0.1:
            raise RuntimeError("the run's budget is spent")
        response = await self.client.messages.create(
            model=PERSONA_MODEL, max_tokens=max_tokens, system=self.system(), messages=messages, tools=tools,
            output_config={"effort": effort}, cache_control={"type": "ephemeral"})
        self.charge(response.usage)
        return response

    async def persona_loop(self) -> None:
        messages = [{"role": "user", "content": "Your computer is on and the course website is open. Start with the first lesson."}]
        self.messages = messages    # kept for the report, however the session ends
        while self.left() > 0:
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
                if self.pending:
                    out += "\n\n" + "\n\n".join(self.pending)
                    self.pending.clear()
                self.say(f"screen ({call.name})", f"```\n{out[-6000:]}\n```")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": out[-8000:] or "(nothing)"})
            messages.append({"role": "user", "content": results})
            trim(messages)
            if any(c.name == "finish" for c in calls):
                break
        self.messages = messages

    async def do(self, name: str, args: dict, messages: list) -> str:
        if self.outside.hook():
            self.say("harness", "the start-up hook is in Butler's Python now")
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
        if (window == self.claude_window and (self.claude or self.claude_pending) and action != "close"
                and 0 <= window < len(self.windows)):
            return ("Claude Code is open in this window: type to it with the claude_code tool (/exit closes it), "
                    "or open a new window.")
        if not 0 <= window < len(self.windows) or not self.windows[window].open:
            return f"There's no open window {window}. Open windows: {[i for i, w in enumerate(self.windows) if w.open]}"
        w = self.windows[window]
        if action == "close":
            w.close()
            if window == self.claude_window:
                self.claude_window = None
            return "(window closed)"
        if action == "type":
            w.type(text)
        elif action == "keys":
            w.keys("".join(KEYS.get(part, part) for part in re.split(r"(ENTER|CTRL_C|TAB|UP|DOWN|BACKSPACE|ESCAPE)", text)))
        # 'look' is a person watching the window: it waits for something to appear, then for it to settle.
        if action == "look":
            w.wait_new(limit=30.0)   # at once if something's there already
        screen = w.read(quiet=3.0 if action != "look" else 5.0, limit=300.0)
        return self.after_screen(screen, window)

    def spot_claude(self, wait: float) -> dict | None:
        """The Claude Code `butler claude` just started, found by its command line: {exe, mode,
        prompt, pid}. Waits up to `wait` seconds (Anthropic's installer may still be running)."""
        deadline = time.monotonic() + wait
        while True:
            if WINDOWS:
                import windows_screen as ws
                found = [{**info, "pid": p["pid"]} for p in ws.processes("claude.exe")
                         if (info := butler_claude(split_windows(p["command"])))]
            else:
                listed = subprocess.run(["ps", "-U", str(os.getuid()), "-ww", "-o", "pid=,args="],
                                        capture_output=True, text=True).stdout
                found = [{**info, "pid": int(pid)} for pid, _, args in (line.strip().partition(" ") for line in listed.splitlines())
                         if pid.isdigit() and (info := mac_claude(args))]
            if found or time.monotonic() > deadline:
                return found[-1] if found else None
            time.sleep(1)

    def take_over_claude(self, window: int) -> bool:
        """Claude Code started for real in `window`: the harness takes it over. It notes the binary,
        mode and prompt Butler gave it, closes that interactive session (nobody can sign in to it on
        a runner), and opens the same Claude Code through the Agent SDK when the persona types to it."""
        found = self.spot_claude(wait=120)
        if not found:
            return False
        self.claude_exe, self.claude_mode, self.claude_prompt = found["exe"], found["mode"], found["prompt"]
        self.say("harness", f"`butler claude` started {found['exe']} --permission-mode {found['mode']} (pid "
                            f"{found['pid']}); the harness takes it over through the Agent SDK, with the same binary, "
                            "mode and opening prompt")
        if WINDOWS:
            import windows_screen as ws
            ws.kill(found["pid"])
        else:
            try:
                os.kill(found["pid"], 15)
            except ProcessLookupError:
                pass
        time.sleep(2)
        self.windows[window].read(quiet=1.0, limit=10.0)   # its first screen, already shown as the trust question
        self.claude_pending, self.claude_window = True, window
        return True

    def after_screen(self, screen: str, window: int | None = None) -> str:
        """Butler opening Claude Code becomes Claude Code opening in the window, and a brief that
        would open in the browser does."""
        m = CLAUDE_OPENING.search(screen)
        if m and window is not None and not self.claude and not self.claude_pending:
            if not self.take_over_claude(window):
                self.notes.append({"kind": "broken", "lesson": self.current()["id"],
                                   "text": "(harness) Butler said Claude Code was opening, but none started within 2 minutes"})
                return screen
            if self.trusted is True:   # this folder was trusted already
                opened = "\n[Claude Code opens in this window. Type to it with the claude_code tool.]\n"
            else:
                self.trusted = None    # Claude Code asks before anything else
                opened = (f"\n[Claude Code opens in this window.]\n\n Do you trust the files in this folder?\n\n {BUTLER}\n\n"
                          " Claude Code may read, write, or execute files contained in this directory. This can pose "
                          "security risks, so only use files from trusted sources.\n\n ❯ 1. Yes, proceed\n   2. No, exit\n\n"
                          "(Answer with the claude_code tool: 1 or 2.)\n")
            screen = screen[: m.end()] + opened
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
            # As a browser leaves a download: marked as from the internet, so the computer checks it.
            if WINDOWS:
                import windows_screen as ws
                ws.mark_of_the_web(self.downloads, "https://buildyourbutler.com/")
            else:
                subprocess.run(["xattr", "-w", "com.apple.quarantine", f"0083;{int(time.time()):x};Safari;",
                                str(self.downloads)], capture_output=True)
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
                return self.outside.inbox([])
            name = mine[-1]["butler_name"] or "your butler"
            return self.outside.inbox([f"{len(mine)} email(s) from Build Your Butler. The newest: \"Your link to wake up {name}\".\n\n"
                    f"Hello,\n\nHere's your link to finish setting up {name}, your new butler:\n\n[Wake up {name}]\n\n"
                    "It works once, for the next 15 minutes. It opens a page that asks you to confirm it's you. Then go "
                    f"back to your Terminal. {name} carries on from there.\n\nIf you didn't ask for this, ignore it. "
                    "Nothing happens without the link.\n\nBuild Your Butler · support@buildyourbutler.com"])
        if action == "read_email":
            return self.outside.read(typed or what)
        if action == "reply_to_email":
            return self.outside.reply(typed or what) if (typed or what).strip() else "Your reply is empty: type it in 'text'."
        if action == "wait":
            return self.outside.wait(typed or what)
        answer = self.outside.site(action, what, typed)
        if answer:
            return answer
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
                return self.start_menu_butler()
            # The Butler app opens Terminal on `butler resume`. Terminal can't open for an account
            # that isn't at the screen, so the harness opens the window the app would.
            window = self.open_window(["/bin/zsh", "-l", "-c", f'"{BUTLER}/bin/butler" resume; exec /bin/zsh -l'])
            return f"A new window opens (window {window}):\n" + self.after_screen(
                self.windows[window].read(quiet=4.0, limit=300.0), window)
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
        """The installer, opened as a person opens it, and the window it opens at the end."""
        return self.install_windows() if WINDOWS else self.install_mac()

    def ask_window(self, form: dict) -> dict:
        """A window on screen, answered by the persona (or by the rehearsal's student, who clicks
        the way forward), from a thread while the persona's on_screen call is still running."""
        if self.rehearse:
            return forward(form)
        return asyncio.run_coroutine_threadsafe(self.window(self.describe(form), form), self.loop).result(timeout=900)

    def install_windows(self) -> str:
        """A double-click on the download (ShellExecute, so Windows' checks on a download from the
        internet run), then each window the installer shows, answered by the persona as it comes:
        a security prompt, the wizard's pages, any message box. Then the console the installer
        opens at the end, found by its command line and worked in where it is."""
        import windows_screen as ws
        before = {p["pid"] for p in ws.processes("cmd.exe")}
        opener = threading.Thread(target=os.startfile, args=(str(self.downloads),), daemon=True)
        opener.start()   # a security prompt blocks the call that opened the file, so it runs aside
        seen: list[str] = []
        asked: dict[int, tuple[str, float, int, dict]] = {}   # window: (what it showed, when, clicks, the answer)
        ran, stuck, went_on = False, "", False
        started = time.monotonic()
        while time.monotonic() - started < 30 * 60:
            time.sleep(1)
            setup = ws.setup_pids(self.downloads)
            ran = ran or bool(setup)
            for hwnd, title, pid in ws.top_windows():
                if pid not in setup | {os.getpid()} and not any(s in title for s in ws.SMARTSCREEN):
                    continue
                try:
                    shown = ws.form(hwnd, title)
                except Exception:  # noqa: BLE001 - the window went while it was read
                    continue
                key = json.dumps(shown, sort_keys=True)
                last = asked.get(hwnd)
                if last and last[0] == key:
                    # Still showing what was answered: the click didn't take yet. After 10 seconds
                    # it's clicked again another way (windows_screen._click), three ways in all.
                    if last[2] and time.monotonic() - last[1] > 10:
                        if last[2] >= 3:
                            stuck = f"{title!r} ({last[3].get('button')!r})"
                            went_on = (last[3].get("button") or "").lower() in {f.lower() for f in FORWARD}
                            break
                        ws.answer(hwnd, title, last[3], attempt=last[2] + 1)
                        self.say("harness", f"clicked {last[3].get('button')!r} again on {title!r} (way {last[2] + 1})")
                        asked[hwnd] = (key, time.monotonic(), last[2] + 1, last[3])
                    continue
                if waiting_only(shown):
                    seen.append(f"{title}: {shown['text']}")
                    asked[hwnd] = (key, time.monotonic(), 0, {})
                    continue
                choice = self.ask_window(shown)
                clicked = ws.answer(hwnd, title, choice)
                seen.append(f"{title}: {shown['text']} [{' | '.join(shown['buttons'])}] You click "
                            f"{choice.get('button')!r}{'' if clicked else ' (there is no such button)'}.")
                asked[hwnd] = (key, time.monotonic(), 1 if clicked else 3, choice)
            if stuck or not opener.is_alive() and not setup and (ran or time.monotonic() - started > 90):
                break
        else:
            self.notes.append({"kind": "broken", "lesson": self.current()["id"],
                               "text": "(harness) the installer was still running after 30 minutes"})
            self.say("harness", "the installer's windows:\n" + "\n".join(seen))
            return "You double-click the download.\n" + "\n".join(seen) + "\nThe installer is still working after 30 minutes."
        self.say("harness", "the installer's windows, as the persona saw and answered them:\n" + "\n".join(seen))
        told = f"You double-click {self.downloads.name} in your Downloads folder.\n" + "\n".join(seen)
        if stuck and not went_on:
            return told + "\nNothing installs."
        if stuck:
            # The closest real path: the same installer, as the account, then the step the wizard
            # itself runs after copying (not silent), which opens the first run's window.
            self.difference(f"the installer's windows appear for BYB Test but no click lands in them ({stuck}): on a "
                            "hosted runner a second account has no desktop of its own (windows-e2e #91 found the same). "
                            "So the installer ran silently as BYB Test, then ran the wizard's own after-copy step "
                            "(run.py installed, not silent), which opens the first run's window")
            for pid in ws.setup_pids(self.downloads):
                ws.kill(pid)
            p = subprocess.run([str(self.downloads), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                f"/LOG={events.FAKES / 'setup-log.txt'}"], capture_output=True, timeout=1800)
            if p.returncode != 0:
                return told + f"\nThe installer says it couldn't finish (exit {p.returncode})."
            python = BUTLER / ".venv" / "Scripts" / "python.exe"
            subprocess.run([str(python), "-X", "utf8", str(BUTLER / "run.py"), "installed", "--installer", str(self.downloads)],
                           cwd=BUTLER, capture_output=True, timeout=300)
            ran = True
            told += ("\nThe installer's pages follow: Next, Install (\"Getting Butler's own Python. This can take a few "
                     "minutes.\"), then Finish. (This test computer can't show you the installer's own pages.)")
        if not ran:
            return told + "\nNothing installs."
        self.outside.after_install()
        console = None
        for _ in range(90):
            new = [p for p in ws.processes("cmd.exe") if p["pid"] not in before and "butler.cmd" in p["command"].lower()]
            if new:
                console = new[-1]
                break
            time.sleep(1)
        log_file = BUTLER / "logs" / "install.log"
        log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
        if not console:
            self.notes.append({"kind": "broken", "lesson": self.current()["id"],
                               "text": "(harness) the installer finished, but no Butler window opened. install.log: " + log[-400:]})
            return told + "\nThe installer closes. No other window opens."
        self.say("harness", f"the window the installer opened runs: {console['command']}")
        window = ws.ConsoleWindow(console["pid"])
        self.windows.append(window)
        index = len(self.windows) - 1
        return told + f"\nA window opens (window {index}):\n" + self.after_screen(window.read(quiet=5.0, limit=600.0), index)

    def start_menu_butler(self) -> str:
        """Start, Butler: the shortcut the installer made, opened as a click opens it, and the
        console it opens."""
        import windows_screen as ws
        link = (Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming") / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / "Butler.lnk")
        if not link.exists():
            return "Start menu: no app called Butler."
        before = {p["pid"] for p in ws.processes("cmd.exe")}
        threading.Thread(target=os.startfile, args=(str(link),), daemon=True).start()   # it may never return
        new: list[dict] = []
        for attempt in range(2):
            for _ in range(30):
                new = [p for p in ws.processes("cmd.exe") if p["pid"] not in before and "butler.cmd" in p["command"].lower()]
                if new:
                    break
                time.sleep(1)
            if new or attempt:
                break
            # The closest real path: the shortcut's own command, opened in a new console.
            target, arguments, folder = ws.shortcut(link)
            self.difference("opening the Start menu's Butler shortcut (ShellExecute) opens nothing for BYB Test, which has "
                            "no desktop of its own on a hosted runner: the harness ran the shortcut's own command "
                            f"({target} {arguments}) in a new console instead")
            subprocess.Popen(f'"{target}" {arguments}', cwd=folder or str(BUTLER),
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        if new:
            window = ws.ConsoleWindow(new[-1]["pid"])
            self.windows.append(window)
            index = len(self.windows) - 1
            return f"A new window opens (window {index}):\n" + self.after_screen(window.read(quiet=4.0, limit=300.0), index)
        self.notes.append({"kind": "broken", "lesson": self.current()["id"], "text": "(harness) Start, Butler opened no window"})
        return "You click Butler in the Start menu. Nothing opens."

    def install_mac(self) -> str:
        """The package as Installer runs it, and the window its postinstall opens.

        A hosted Mac's only screen session is the runner's admin's, so for the student's account
        (standard, not at the screen) two things can't happen as on a student's Mac (tests/e2e/
        standard-user.sh found both): Installer can't install into the account's home, since a
        home-folder install needs a per-user installd that runs only in a screen session; and
        Terminal can't open for the account. The closest real path: Installer is tried first, as
        the account (it fails, and the run says so); then the account copies the package's
        payload into its home and runs the package's own postinstall, as Installer does. The
        postinstall decides what Terminal should open and logs it; the harness opens exactly that
        .command in a login shell, typed the way Terminal types it."""
        p = subprocess.run(["installer", "-pkg", str(self.downloads), "-target", "CurrentUserHomeDirectory"],
                           capture_output=True, text=True)
        if p.returncode == 0:
            self.difference("Installer ran from the command line (the `installer` command), not Installer.app's "
                            "windows, so the postinstall opened no Terminal; the harness opened what it would have")
        else:
            said = (p.stderr or p.stdout).strip()[-160:]
            self.difference("Installer can't run for an account with no screen session (a hosted runner's only one is its "
                            f"admin's: \"{said}\"). The student's account copied the package's payload into its home and ran "
                            "the package's own postinstall, as Installer does")
            expanded = events.FAKES / "package"
            shutil.rmtree(expanded, ignore_errors=True)
            subprocess.run(["pkgutil", "--expand-full", str(self.downloads), str(expanded)], check=True, capture_output=True)
            component = next(expanded.glob("*.pkg/Payload")).parent
            subprocess.run(["ditto", str(component / "Payload"), str(HOME)], check=True)
            post = subprocess.run(["./postinstall", str(self.downloads), str(HOME)], cwd=component / "Scripts",
                                  env={k: v for k, v in os.environ.items() if k != "COMMAND_LINE_INSTALL"},
                                  capture_output=True, text=True)
            if post.returncode != 0:
                return f"Installer says: The installation failed. ({(post.stderr or post.stdout).strip()[-300:]})"
        self.outside.after_install()
        log_file = BUTLER / "logs" / "install.log"
        opened = install_log_opened(log_file.read_text(errors="replace") if log_file.exists() else "")
        if opened and opened[0]:
            raise RuntimeError("the postinstall opened a real Terminal window, which the harness can't read: run the "
                               "persona as an account that isn't at the screen (personas.yml does)")
        if opened:
            command = opened[1]
            self.difference(f"Terminal couldn't open for the account (install.log: \"could not open Terminal with "
                            f"{command}\"); the harness opened {command} in a login shell, typed as Terminal types it")
        else:
            command = subprocess.run(["bash", "-c", f'. "{BUTLER}/scripts/lib/install.sh"; install_opens "$(install_state "{BUTLER}")"'],
                                     capture_output=True, text=True).stdout.strip() or "first-run.command"
        first = self.open_window(["/bin/zsh", "-l"])
        self.windows[first].type(terminal_types(str(BUTLER / "scripts" / command)))
        return (f"You double-click {self.downloads.name}. Installer: Continue, Continue, Install for me only, Install. "
                f"\"The installation was completed successfully.\" A Terminal window opens (window {first}):\n"
                + self.after_screen(self.windows[first].read(quiet=5.0, limit=600.0), first))

    def difference(self, what: str) -> None:
        """Somewhere this run's path isn't a real student's: kept in run.json, and said as it happens."""
        if what not in self.differences:
            self.differences.append(what)
            self.say("harness", f"differs from a real student: {what}")

    # ---------- Claude Code ----------
    async def claude_say(self, message: str, messages: list) -> str:
        if self.claude_pending and not self.claude and self.trusted is None:
            if message.strip().lower() not in ("1", "yes", "yes, proceed", "2", "no", "no, exit", "esc"):
                return ("Claude Code is still asking: Do you trust the files in this folder?  ❯ 1. Yes, proceed   2. No, "
                        "exit\n(Nothing else can be typed until you answer.)")
            if message.strip().lower() in ("2", "no", "no, exit", "esc"):
                self.claude_pending, self.trusted, self.claude_window = False, False, None
                return "(Claude Code exits. The Terminal prompt is back.)"
            self.trusted = True
            trust_folder(BUTLER)
            message = ""
        if self.claude_pending and not self.claude:
            # The Claude Code `butler claude` installed and started, with its mode. A student signs
            # in with their Claude plan; the run uses its API key, given only to this session.
            env = {k: v for k, v in self.env.items() if k.startswith("BUTLER_")}
            if self.api_key:
                env["ANTHROPIC_API_KEY"] = self.api_key
            options = ClaudeAgentOptions(
                cli_path=self.claude_exe, cwd=str(BUTLER), permission_mode=self.claude_mode,
                can_use_tool=self.permission(messages), setting_sources=["project"], env=env,
                # A margin: Claude Code's last reply lands after its own check.
                max_budget_usd=max(0.3, self.budget - self.spent() - 0.25), model=self.claude_model,
                system_prompt={"type": "preset", "preset": "claude_code", "append": TEST_COMPUTER})
            self.claude = ClaudeSDKClient(options=options)
            await self.claude.connect()
            if self.rehearse:   # free: the session is open on the real binary; nothing is asked of it
                info = await self.claude.get_server_info() or {}
                self.rehearsal["sdk_session"] = {"cli": self.claude_exe, "mode": self.claude_mode,
                                                 "commands": len(info.get("commands", [])),
                                                 "butler_setup": any(c.get("name") == "butler-setup" for c in info.get("commands", []))}
                self.claude_pending = False
                return f"(rehearsal: Claude Code is open through the Agent SDK: {self.rehearsal['sdk_session']})"
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
            window, self.claude_window = self.claude_window, None
            if window is not None and window < len(self.windows) and not self.windows[window].open:
                return "(Claude Code closes, and with it what the window was running: it says [Process completed].)"
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
                if self.left() <= 0:   # the run stops here: Claude Code stops with it
                    raise RuntimeError("the run's budget is spent")
                response = await self.ask(snapshot, [PERMISSION_TOOL], max_tokens=1500, effort="low")
                answer = next((b.input for b in response.content if b.type == "tool_use"), {"choice": "yes", "say": ""})
            except Exception as e:  # noqa: BLE001 - a broken question shouldn't stop the run
                if "budget is spent" in str(e):     # the run is over: Claude Code stops too
                    answer = {"choice": "no", "say": "Let's stop here for today."}
                else:
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
            response = await self.ask(snapshot, [CHOICE_TOOL], max_tokens=1500, effort="low")
            picked = next((b.input["answers"] for b in response.content if b.type == "tool_use"), [])
        except Exception:  # noqa: BLE001 - a broken question shouldn't stop the run
            self.fallbacks += 1
            picked = []
        answers = {q.get("question", ""): (picked[i] if i < len(picked) else "") for i, q in enumerate(questions)}
        self.say(f"{self.persona['name']} chooses", json.dumps(answers, ensure_ascii=False))
        self._shown.append("Claude Code asked you:\n" + "\n".join(shown) + "\n  You answered: "
                           + "; ".join(a or "(nothing)" for a in answers.values()))
        return PermissionResultAllow(updated_input={**tool_input, "answers": answers})

    # ---------- windows and pages that appear ----------
    async def watch(self) -> None:
        """Windows a stub put on screen and pages Butler opened (events.py), answered by the
        persona as they come, while its terminal or Claude Code action is still running."""
        while True:
            await asyncio.sleep(1)
            for name, event in events.waiting(self.events_seen):
                try:
                    await self.on_event(name, event)
                except Exception as e:  # noqa: BLE001 - a window the harness can't show is a harness error
                    self.say("harness", f"window failed: {type(e).__name__}: {e}")
                    events.reply(name, {"button": "", "text": "", "ticked": []})

    async def window(self, question: str, form: dict | None = None) -> dict:
        if self.rehearse:     # the rehearsal's student clicks the way forward, and asks no one
            return forward(form or {})
        snapshot = side_question(self.messages, question)
        try:
            response = await self.ask(snapshot, [WINDOW_TOOL], max_tokens=1500, effort="low")
            return next(b.input for b in response.content if b.type == "tool_use")
        except Exception:  # noqa: BLE001
            self.fallbacks += 1
            return {"button": "", "text": "", "ticked": []}

    async def on_event(self, name: str, event: dict) -> None:
        if event["kind"] == "browser":
            screen, form = self.outside.browser(event["url"])
            if form:
                answer = await self.window(self.describe(form, screen), form)
                result = await asyncio.to_thread(self.outside.google_answer, event["url"], answer)
                screen += f"\nYou ticked {answer.get('ticked') or 'nothing'} and clicked {answer.get('button')!r}. {result}"
            events.reply(name, {})
            self.pending.append(screen)
        else:
            answer = await self.window(self.describe(event), event)
            events.reply(name, answer)
            typed = " typed into the box and" if event.get("box") and answer.get("text") else ""
            self.pending.append(f"A window appeared: {event.get('title') or event.get('text')}. You{typed} clicked "
                                f"{answer.get('button')!r}.")
        self.say(f"{self.persona['name']} answers a window", self.pending[-1])

    @staticmethod
    def describe(form: dict, before: str = "") -> str:
        box = ""
        if form.get("box"):
            box = "\n  [a box to type or paste into" + (", which shows dots instead of what you type" if form.get("hidden") else "") + "]"
        boxes = "\n  Boxes you can tick: " + "; ".join(form["boxes"]) if form.get("boxes") else ""
        return (f"{before}\n\nA window appears on screen" + (f", titled {form['title']!r}" if form.get("title") else "")
                + f":\n\n  {form.get('text', '')}{box}{boxes}\n  Buttons: " + " | ".join(form.get("buttons", []))
                + "\n\nThis is your decision, as you. Answer it with the window tool.")

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
            "butler_mails": len(list((events.FAKES / "mail").glob("*.json"))) if (events.FAKES / "mail").exists() else 0,
            "mails_to_you": len(self.outside.mails()),
            "replies_sent": self.outside.replies,
            "replies_read": len(json.loads((events.FAKES / "imap-seen.json").read_text())) if (events.FAKES / "imap-seen.json").exists() else 0,
            "butler_claude_calls": self.outside.relay.calls,
            "google_connected": bool(self.outside.google.access),
            # Claude Code came from `butler claude` itself (Anthropic's installer), not the harness.
            "claude_code_installed_by_butler": not self.claude_installed_before and bool(self.claude_exe)
                                               and Path(self.claude_exe).exists(),
            "claude_code_cli": self.claude_exe,
            "stand_in_ticks": self.stand_in_ticks,
        }

    # ---------- Windows: the schedule ----------
    stand_in_ticks = 0

    def stand_in_scheduler(self) -> None:
        """Butler's Windows tasks run only while the student is logged on at the screen. A hosted
        runner can't log a second account on, so Task Scheduler takes the request to run them and
        never does (267011, "has not yet run"). While that's so, the harness runs the tick task's
        own action (its program, arguments and folder) as the account every five minutes, as
        tests/e2e/windows-tick.ps1 does. If Task Scheduler runs it itself, this does nothing."""
        import windows_screen as ws
        last_ran = 0.0
        while not self.done:
            time.sleep(30)
            try:
                info = ws.task_info("\\Butler\\tick")
                if not info or info.get("state") == "Running" or time.time() - last_ran < 5 * 60:
                    continue
                if info.get("result") != 267011 and time.time() - (info.get("last") or 0) < 10 * 60:
                    continue    # Task Scheduler runs it
                self.difference("Task Scheduler doesn't start the account's tasks (they run only while it's logged on at "
                                "the screen, and a hosted runner can't log a second account on): the harness runs the tick "
                                "task's own action as the account every five minutes instead")
                last_ran = time.time()
                subprocess.run([info["command"], *split_windows(info.get("arguments") or "")],
                               cwd=info.get("folder") or str(BUTLER), capture_output=True, timeout=1800,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.stand_in_ticks += 1
            except Exception as e:  # noqa: BLE001 - a stand-in that fails is said, not fatal
                self.say("harness", f"the stand-in for Task Scheduler failed: {type(e).__name__}: {e}")

    # ---------- the free rehearsal ----------
    PROMPTS = (("Name ›", ""), ("Press Enter to agree, or type no to stop ›", ""),
               ("Type a number, or press Enter to skip ›", ""), ("Your email ›", "{email}"),
               ("Weekly lesson email? Type yes, or press Enter for no ›", ""),
               ("Is that right? Press Enter, or paste the line again ›", ""), ("Paste here ›", "{lesson}"),
               ("Install Claude Code now? Press Enter for yes, or type no:", ""),
               ("Type 1 or 2, or press Enter for Ask first:", ""))

    async def act(self, name: str, **args) -> str:
        self.say(f"rehearsal → {name}", "```json\n" + json.dumps(args, ensure_ascii=False) + "\n```")
        out = await self.do(name, args, self.messages)
        if self.pending:
            out += "\n\n" + "\n\n".join(self.pending)
            self.pending.clear()
        self.say(f"screen ({name})", f"```\n{out[-6000:]}\n```")
        return out

    async def rehearse_run(self) -> None:
        """A scripted student, no Claude API: downloads Butler as "Mr Pickles", opens it, answers the
        installer's windows the way forward, does the first run in the window the installer
        opened (signed in through the fake account API), types `butler claude`, lets Anthropic's
        installer put Claude Code in, trusts the folder, and opens the Agent SDK session on that
        binary without asking it anything. Then the daily schedule, from a new window."""
        r = self.rehearsal
        await self.act("on_screen", action="download_butler", what="", text="Mr Pickles")
        screen = await self.act("on_screen", action="open_download", what="", text="")
        r["installer_window"] = bool(self.windows)
        if not self.windows:
            return
        window = len(self.windows) - 1
        lessons = ['butler town "Lisbon"', "butler adults Sam Alex", "butler brief", "butler claude"]
        clicked, deadline = False, time.monotonic() + 45 * 60
        while not self.claude_pending and time.monotonic() < deadline and self.windows[window].open:
            if "Waiting for your click" in screen and not clicked:
                await self.act("on_screen", action="click_email_link", what="the link in the email", text="")
                await self.act("on_screen", action="click_on_page", what="Yes, sign me in", text="Yes, sign me in")
                clicked = True
            lines = [line.strip() for line in screen.splitlines() if line.strip()]
            reply = next((a for p, a in self.PROMPTS if lines and lines[-1].endswith(p)), None)
            if reply is None:
                screen = await self.act("terminal", action="look", text="", window=window)
                continue
            if reply == "{lesson}":
                if not lessons:
                    break
                reply = lessons.pop(0)
            screen = await self.act("terminal", action="type", text=reply.replace("{email}", self.email), window=window)
        r["claude_started_by_butler"] = bool(self.claude_pending)
        if self.claude_pending:
            await self.act("claude_code", message="1")
            await self.act("claude_code", message="/exit")
        await self.rehearse_schedule()

    async def rehearse_schedule(self) -> None:
        """The daily schedule as the course has Claude set it up, typed into a new window: it
        installs as the account, the tick runs, `butler status` counts it, and it comes out."""
        log = BUTLER / "logs" / "butler.log"
        lines = lambda: len(log.read_text(errors="replace").splitlines()) if log.exists() else 0  # noqa: E731
        await self.act("terminal", action="new_window", text="", window=0)
        w = len(self.windows) - 1
        before = lines()
        if WINDOWS:
            python, run = BUTLER / ".venv" / "Scripts" / "python.exe", BUTLER / "run.py"
            install, uninstall = f'& "{python}" -X utf8 "{run}" schedule install', f'& "{python}" -X utf8 "{run}" schedule uninstall'
        else:
            install, uninstall = f'"{BUTLER}/launchd/install.sh"', f'"{BUTLER}/launchd/uninstall.sh"'
        await self.act("terminal", action="type", text=install, window=w)
        deadline = time.monotonic() + 12 * 60
        while lines() <= before and time.monotonic() < deadline:
            await asyncio.sleep(10)
        self.rehearsal["tick_ran"] = lines() > before
        status = await self.act("terminal", action="type", text="butler status", window=w)
        self.rehearsal["status_counts_schedule"] = bool(re.search(r"Done:\s+The daily schedule", status))
        await self.act("terminal", action="type", text=uninstall, window=w)
        # The way back: the Butler app (Mac) or Start, Butler (Windows), which runs `butler resume`.
        back = await self.act("on_screen", action="open_butler_app", what="Butler", text="")
        self.rehearsal["way_back_opens"] = "A new window opens" in back
        if self.claude_pending:
            await self.act("claude_code", message="(look)")
        if self.claude:
            await self.act("claude_code", message="/exit")

    def rehearsal_failures(self, checks: dict) -> list[str]:
        """What the rehearsal must show, or the job fails."""
        r = self.rehearsal
        want = {
            "installed": checks["installed"],
            "named from the download (Mr Pickles)": checks["named"] == "Mr Pickles",
            "the installer opened the window the first run ran in": r.get("installer_window"),
            "signed in through the first run": checks["signed_in"],
            "the Terms recorded": checks["terms_agreed"],
            "a first brief": checks["first_brief"],
            "Butler started Claude Code (data/claude-welcomed)": checks["claude_code_opened"],
            "Claude Code installed by Anthropic's installer, through butler claude": checks["claude_code_installed_by_butler"],
            "the Agent SDK opened that Claude Code": bool(r.get("sdk_session")),
            "a standard account, not an admin": not self.account["admin"],
            "a space in the account's home folder": " " in self.account["home"],
            "the schedule's tick ran as the account": r.get("tick_ran"),
            "butler status counts the schedule": r.get("status_counts_schedule"),
            "the way back (the Butler app, or Start, Butler) opens a window": r.get("way_back_opens"),
            "no Python tracebacks on screen": checks["tracebacks"] == 0,
        }
        return [what for what, ok in want.items() if not ok]

    async def main(self) -> int:
        self.loop = asyncio.get_running_loop()
        started = time.monotonic()
        self.messages = [{"role": "user", "content": "(starting)"}]
        self.say("harness", f"account: {json.dumps(self.account)}")
        watcher = asyncio.create_task(self.watch())
        if WINDOWS:
            threading.Thread(target=self.stand_in_scheduler, daemon=True).start()
        try:
            await (self.rehearse_run() if self.rehearse else self.persona_loop())
        except Exception as e:  # noqa: BLE001 - the run so far is still worth its report
            self.outcome = {"outcome": "harness_error", "why": f"{type(e).__name__}: {e}"}
            self.say("harness", f"stopped: {type(e).__name__}: {e}")
        finally:
            if self.claude:
                try:
                    await self.claude.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        self.done = True
        watcher.cancel()
        if self.rehearse:
            report = {"findings": [], "how_it_felt": "(a rehearsal: a scripted student, no persona)"}
        else:
            try:
                report = await self.report()
            except Exception as e:  # noqa: BLE001 - the run is saved whatever happens here
                report = {"findings": [], "how_it_felt": f"(report failed: {e})"}
        checks = self.checks()
        failures = self.rehearsal_failures(checks) if self.rehearse else []
        if self.rehearse:
            self.outcome = {"outcome": "rehearsed" if not failures else "rehearsal_failed",
                            "why": "; ".join(failures) or "every step of the student's path worked"}
        result = {"persona": self.key, "name": self.persona["name"], "platform": self.platform, "computer": self.computer,
                  "seed": self.seed, "cards": self.cards, "outcome": self.outcome, "reached": self.current()["id"],
                  "lessons_total": len(self.lessons), "lessons_read": min(self.at + 1, len(self.lessons)), "notes": self.notes,
                  "report": report, "checks": checks, "account": self.account, "differences": self.differences,
                  "rehearsal": self.rehearsal if self.rehearse else None,
                  "cost_usd": {k: round(v, 3) for k, v in {**self.cost, "butler_claude": self.outside.relay.cost,
                                                           "total": self.spent()}.items()},
                  "minutes": round((time.monotonic() - started) / 60, 1)}
        (self.out / "run.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        (self.out / "transcript.md").write_text(
            f"# {self.persona['name']} ({self.key}) on {self.computer}, run {self.seed}\n\nChoices: {self.cards}\n\n"
            + "\n".join(self.log), encoding="utf-8")
        for w in self.windows:
            w.close()
        self.api.stop()
        self.outside.stop()
        print(json.dumps({k: result[k] for k in ("persona", "computer", "seed", "outcome", "reached", "cost_usd")}))
        return 1 if failures else 0


def read_key(path: str | None) -> str:
    """The run's Claude API key: from a file only the run's account can read (deleted once read), or
    ANTHROPIC_API_KEY. Taken out of the environment either way."""
    key = os.environ.pop("ANTHROPIC_API_KEY", "")
    if path and Path(path).exists():
        key = Path(path).read_text().strip() or key
        Path(path).unlink()
    return key


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", required=True)
    parser.add_argument("--platform", choices=["mac", "windows"], required=True)
    parser.add_argument("--computer", default="", help="The runner, for the report: mac, windows or windows-arm")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--lessons", required=True)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--budget", type=float, default=6.0)
    parser.add_argument("--key-file", default="", help="A file holding the Claude API key, deleted once read")
    parser.add_argument("--rehearse", action="store_true",
                        help="Free: a scripted student takes the path into Claude Code and the schedule; no Claude API")
    parser.add_argument("--claude-model", default="sonnet",
                        help="Claude Code's model, as a Pro-plan student gets it; empty for Claude Code's own default")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not os.environ.get("CI"):
        sys.exit("Persona tests install into this computer's home folder and sign in: CI only.")
    return asyncio.run(Run(args).main())


if __name__ == "__main__":
    sys.exit(main())
