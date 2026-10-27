"""Butler's scheduled jobs on Windows, in Task Scheduler. launchd/install.sh does the same on a
Mac, and each task here is one of its launchd jobs:

| Task | launchd job | What it does |
|---|---|---|
| \\Butler\\tick | com.buildyourbutler.butler | Every 5 minutes and at logon: replies, the update check, each email if due |
| \\Butler\\morning | com.buildyourbutler.morning | At send_hour:01 and weekly_hour:01, waking the computer for it |
| \\Butler\\portal | com.buildyourbutler.portal | At logon, restarted if it stops; 127.0.0.1 only |

The morning task's WakeToRun is the Mac's `pmset repeat wakeorpoweron`: Windows wakes the
computer from sleep for it, when the power plan allows wake timers. Like launchd's agents, the
tasks run only while the household's user is logged on, as that user, without admin rights.
A missed run (the computer was off) runs as soon as it can (StartWhenAvailable).

  python run.py schedule install | uninstall | show"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

FOLDER = "\\Butler\\"
NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
# Any date in the past: a trigger starts counting from it.
START = "2026-01-01"


def tasks(root: Path, send_hour: int, weekly_hour: int) -> dict[str, str]:
    """Each task's name and its Task Scheduler XML."""
    def at(hour: int) -> str:
        return (f"<CalendarTrigger><StartBoundary>{START}T{hour:02d}:01:00</StartBoundary>"
                "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>")
    every_5 = (f"<TimeTrigger><StartBoundary>{START}T00:00:00</StartBoundary><Repetition><Interval>PT5M</Interval>"
               "<StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger>")
    logon = f"<LogonTrigger><UserId>{escape(_user())}</UserId></LogonTrigger>"
    return {
        "tick": _task(root, "tick", "Butler: replies, the update check and each email when it's due.",
                      every_5 + logon, limit="PT30M"),
        "morning": _task(root, "tick", "Butler: wakes the computer for the morning brief and the weekly letter.",
                         at(send_hour) + ("" if weekly_hour == send_hour else at(weekly_hour)),
                         limit="PT30M", wake=True),
        "portal": _task(root, "portal", "Butler: the household portal, on this computer only (127.0.0.1).",
                        logon, limit="PT0S", restart=True),
    }


def _user() -> str:
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def _task(root: Path, command: str, description: str, triggers: str, limit: str,
          wake: bool = False, restart: bool = False) -> str:
    python = root / ".venv" / "Scripts" / "pythonw.exe"   # pythonw: no console window every 5 minutes
    arguments = f'-X utf8 "{root / "run.py"}" {command}'
    restart_on_failure = ("\n    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>"
                          if restart else "")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="{NAMESPACE}">
  <RegistrationInfo><Description>{escape(description)}</Description></RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals>
    <Principal id="Author"><UserId>{escape(_user())}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>{str(wake).lower()}</WakeToRun>
    <ExecutionTimeLimit>{limit}</ExecutionTimeLimit>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>{restart_on_failure}
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(str(python))}</Command><Arguments>{escape(arguments)}</Arguments><WorkingDirectory>{escape(str(root))}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, timeout=60)


def install(root: Path, send_hour: int, weekly_hour: int, folder: str = FOLDER) -> list[str]:
    """Creates or replaces each task. Returns their full names."""
    names = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, xml in tasks(root, send_hour, weekly_hour).items():
            path = Path(tmp) / f"{name}.xml"
            path.write_text(xml, encoding="utf-16")    # schtasks reads task XML as UTF-16
            result = _schtasks("/Create", "/TN", folder + name, "/XML", str(path), "/F")
            if result.returncode != 0:
                raise RuntimeError(f"Task Scheduler refused {folder + name}: {result.stderr.strip()}")
            names.append(folder + name)
    return names


def uninstall(folder: str = FOLDER) -> list[str]:
    """Removes each of Butler's tasks that exists. Your settings and data stay where they are."""
    gone = []
    for name in ("tick", "morning", "portal"):
        if _schtasks("/Query", "/TN", folder + name).returncode == 0:
            _schtasks("/Delete", "/TN", folder + name, "/F")
            gone.append(folder + name)
    return gone


def main(argv: list[str]) -> int:
    from .config import ROOT, load_settings
    what = argv[0] if argv else "show"
    if what == "install":
        email = load_settings()["email"]
        for name in install(ROOT, int(email["send_hour"]), int(email.get("weekly_hour", 17))):
            print(f"installed {name}")
        print(f"watch it with: Get-Content -Wait {ROOT / 'logs' / 'butler.log'}")
    elif what == "uninstall":
        for name in uninstall():
            print(f"removed {name}")
    elif what == "show":
        for name in ("tick", "morning", "portal"):
            found = _schtasks("/Query", "/TN", FOLDER + name, "/FO", "LIST").returncode == 0
            print(f"{FOLDER + name}: {'installed' if found else 'not installed'}")
    else:
        print("usage: python run.py schedule install | uninstall | show", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
