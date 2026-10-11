"""Integration: scripts/screenshots.py makes the README's images end to end, twice, with the same
bytes both times. Each run builds the brief and the Sunday letter in a throwaway install with
every outside service faked, serves its portal on 127.0.0.1, and photographs it.

Needs requirements-screenshots.txt and `python -m playwright install chromium`; skips without
them. CI's screenshots job sets BUTLER_SCREENSHOTS_REQUIRED=1, so there it fails instead, and
BUTLER_SCREENSHOTS_KEEP=<folder> to keep the images as a build artifact."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REQUIRED = bool(os.environ.get("BUTLER_SCREENSHOTS_REQUIRED"))

# name -> (width, smallest height, tallest height), in pixels
EXPECTED = {
    "brief.png": (672, 1200, 2200),
    "weekly-letter.png": (672, 1200, 2000),
    "portal-projects.png": (960, 600, 1090),
    "portal-money.png": (960, 600, 1150),
    "portal-phone.png": (780, 1200, 1688),
}


def _ready() -> str:
    """Why the screenshot tools can't run here, or '' when they can."""
    missing = [m for m in ("time_machine", "playwright") if importlib.util.find_spec(m) is None]
    if missing:
        return f"not installed: {', '.join(missing)} (pip install -r requirements-screenshots.txt)"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).exists():
            return "Chromium missing (python -m playwright install chromium)"
    return ""


NOT_READY = _ready()


@unittest.skipIf(NOT_READY and not REQUIRED, NOT_READY)
class ScreenshotsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="butler-shots-"))
        cls.runs = []
        for n in (1, 2):
            out = cls.tmp / f"run{n}"
            proc = subprocess.run([sys.executable, str(REPO / "scripts" / "screenshots.py"), "--out", str(out)],
                                  capture_output=True, text=True, timeout=600)
            if proc.returncode:
                raise AssertionError(f"screenshots.py failed:\n{proc.stdout}\n{proc.stderr}")
            cls.runs.append(out)
        keep = os.environ.get("BUTLER_SCREENSHOTS_KEEP")
        if keep:
            shutil.copytree(cls.runs[0], keep, dirs_exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_makes_each_image_at_a_sane_size(self):
        from PIL import Image
        self.assertEqual(sorted(p.name for p in self.runs[0].iterdir()), sorted(EXPECTED))
        for name, (width, low, high) in EXPECTED.items():
            with Image.open(self.runs[0] / name) as image:
                self.assertEqual(image.format, "PNG", name)
                self.assertEqual(image.width, width, name)
                self.assertTrue(low <= image.height <= high, f"{name}: {image.height}px tall")
                # Not a blank page: a real page has text, so many colors.
                self.assertGreater(len(image.getcolors(256) or []), 20, name)

    def test_images_stay_small(self):
        total = sum(p.stat().st_size for p in self.runs[0].iterdir())
        self.assertLess(total, 1_500_000)

    def test_same_bytes_every_run(self):
        """Pinned clock, data and fonts: a rerun changes nothing, so the workflow only opens a
        pull request when the rendering really changed."""
        for name in EXPECTED:
            self.assertEqual((self.runs[0] / name).read_bytes(), (self.runs[1] / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
