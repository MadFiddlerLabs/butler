"""What a persona's standard account asks an admin for. A persona run is a standard account
("BYB Test"), as most students are, so it can't write to /Applications or C:\\Program Files. On a
real computer the one step of the course that needs that, installing Tailscale, asks for an admin's
password (Mac) or a UAC yes (Windows), and the student's household gives it. Here the runner's
admin account gives it: it runs `serve` beside the run and does exactly that step, nothing else.

  python tests/personas/admin.py serve <folder>     (the runner's admin, for the length of the run)

The harness finds the folder in BYB_ADMIN_REQUESTS. Without it (a run as an admin), the harness
does the step itself, as before.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WINDOWS = sys.platform == "win32"
MAC_TAILSCALE = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")


def tailscale(python: str, stub: str) -> None:
    """Tailscale's program, where its installer puts it, running stubs/tailscale.py."""
    if not WINDOWS:
        MAC_TAILSCALE.parent.mkdir(parents=True, exist_ok=True)
        MAC_TAILSCALE.write_text(f'#!/bin/bash\nexec "{python}" "{stub}" "$@"\n')
        MAC_TAILSCALE.chmod(0o755)
        return
    # C:\Program Files\Tailscale\tailscale.exe: a small program built with the C# compiler Windows has.
    target = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = target.parent / "tailscale.cs"
    stub_cs, python_cs = stub.replace("\\", "\\\\"), python.replace("\\", "\\\\")
    source.write_text(
        "using System.Diagnostics; using System.Linq;\n"
        "class P { static int Main(string[] a) {\n"
        f"  var i = new ProcessStartInfo(\"{python_cs}\", \"\\\"{stub_cs}\\\" \" + string.Join(\" \", a.Select(x => \"\\\"\" + x + \"\\\"\")));\n"
        "  i.UseShellExecute = false; var p = Process.Start(i); p.WaitForExit(); return p.ExitCode; } }\n")
    csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not csc.exists():   # Windows on ARM keeps the .NET Framework compiler in Framework64 too, or Framework
        csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe"
    subprocess.run([str(csc), "/nologo", f"/out:{target}", str(source)], check=True, capture_output=True)
    source.unlink(missing_ok=True)


STEPS = {"tailscale": tailscale}


def request(what: str, **payload) -> None:
    """Does an admin step for this run: through the runner's admin when the run is a standard
    account (BYB_ADMIN_REQUESTS), else itself. Raises if the admin says it failed."""
    folder = os.environ.get("BYB_ADMIN_REQUESTS")
    if not folder:
        STEPS[what](**payload)
        return
    name = f"{time.time_ns()}-{what}"
    ask = Path(folder) / f"{name}.json"
    ask.with_suffix(".tmp").write_text(json.dumps({"what": what, **payload}))
    os.replace(ask.with_suffix(".tmp"), ask)
    done = ask.with_suffix(".done")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if done.exists():
            result = json.loads(done.read_text())
            if result.get("error"):
                raise RuntimeError(f"the admin step {what} failed: {result['error']}")
            return
        time.sleep(0.5)
    raise RuntimeError(f"the admin step {what} got no answer in 3 minutes (is admin.py serve running?)")


def serve(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    print(f"admin steps for the persona run: watching {folder}", flush=True)
    while True:
        for ask in sorted(folder.glob("*.json")):
            done = ask.with_suffix(".done")
            if done.exists():
                continue
            try:
                payload = json.loads(ask.read_text())
                what = payload.pop("what")
                STEPS[what](**payload)
                result = {}
                print(f"did {what} for the run", flush=True)
            except Exception as e:  # noqa: BLE001 - the run hears why
                result = {"error": f"{type(e).__name__}: {e}"}
                print(f"{ask.name}: {result['error']}", flush=True)
            done.write_text(json.dumps(result))
        time.sleep(0.5)


if __name__ == "__main__":
    if sys.argv[1:2] != ["serve"] or len(sys.argv) != 3:
        sys.exit("usage: admin.py serve <folder>")
    serve(Path(sys.argv[2]))
