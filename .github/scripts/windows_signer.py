"""Checks a signed Butler-Setup.exe the way Butler's updater will (release.yml, job windows):
Windows must trust its Authenticode signature (system/windows.py, WinVerifyTrust), and the signer
must be updates.PUBLISHER, as updates.verify_exe accepts it. Windows only.

  python .github/scripts/windows_signer.py dist\\Butler-Setup.exe"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from butler.system import windows  # noqa: E402

publisher = re.search(r'^PUBLISHER = "(.+)"', (ROOT / "src" / "butler" / "updates.py").read_text(), re.M)[1]
signer = windows.installer_signer(sys.argv[1])
print(f"signer: {signer}; Butler's updater accepts: {publisher}")
ok = bool(signer) and signer.get("name") == publisher and signer.get("organization", "") in ("", publisher)
sys.exit(0 if ok else 1)
