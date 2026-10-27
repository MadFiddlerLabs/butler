"""Fakes for the integration tests: a throwaway test home (home.Home), the flow harness that
runs inside it (harness.py), a stub butler-ek (ek_stub.py), a fake Google (google_api.py) and invented
data (fixtures.py)."""

import os


def timeout(seconds: int) -> int | None:
    """A subprocess timeout, or none under libfaketime (the dates job). There, subprocess's
    wait-with-timeout can call sleep() while a child that has closed its pipes is still exiting,
    and the faked sleep fails with EINVAL. The job's own time limit still applies."""
    return None if "faketime" in os.environ.get("LD_PRELOAD", "") else seconds
