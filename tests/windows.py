"""The suite on Windows, less what doesn't run there yet (#40). tests/windows_skip.txt names
each test module, class or test to leave out, one per line, with a comment saying why. The list
should only shrink: a line that no longer matches any test fails the run, so it can't go stale.

Run with: PYTHONPATH=src python tests/windows.py"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent


def skip_list() -> list[str]:
    lines = (HERE / "windows_skip.txt").read_text().splitlines()
    return [line.split("#")[0].strip() for line in lines if line.split("#")[0].strip()]


def tests(suite) -> list:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from tests(item)
        else:
            yield item


def main() -> int:
    sys.path.insert(0, str(HERE))
    loader = unittest.TestLoader()
    every = list(tests(loader.discover(str(HERE))))
    skips = skip_list()
    used = set()
    kept = unittest.TestSuite()
    for test in every:
        # A module that doesn't import on Windows comes back as one failed test with its name.
        name = test.id().removeprefix("unittest.loader._FailedTest.")
        match = next((s for s in skips if name == s or name.startswith(s + ".")), None)
        if match:
            used.add(match)
        else:
            kept.addTest(test)
    stale = [s for s in skips if s not in used]
    print(f"{len(every) - kept.countTestCases()} of {len(every)} tests left out (tests/windows_skip.txt)")
    result = unittest.TextTestRunner(verbosity=1).run(kept)
    if stale:
        print("These lines in tests/windows_skip.txt match no test. Remove them:", *stale, sep="\n  ")
    return 0 if result.wasSuccessful() and not stale else 1


if __name__ == "__main__":
    sys.exit(main())
