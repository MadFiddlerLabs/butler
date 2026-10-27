"""BUTLER_BOOTSTRAP for SetupHome on Windows, where the Mac's is a bash line: a .venv in the
current folder whose Python sees this test run's packages (yaml, flask, ...), as a real
bootstrap's would. Run it with the test's own Python."""
import os
import sys
import venv
from pathlib import Path

venv.EnvBuilder(with_pip=False).create(".venv")
if os.name == "nt":
    lib = Path(".venv") / "Lib" / "site-packages"
else:
    lib = Path(".venv") / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
(lib / "test-packages.pth").write_text("\n".join(p for p in sys.path if p.endswith("site-packages")) + "\n")
