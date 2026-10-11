"""Hypothesis settings for the property tests, so they never flake.

- No deadline: a slow CI runner must not turn a passing example into a failure.
- `ci` (set by the workflows): derandomized, so a run is repeatable and a red build is a real bug.
- `mutation`: fewer examples, derandomized, for the weekly mutation run.
- The default, on a laptop: random, so new inputs keep being tried. A failure prints the
  example and a `@reproduce_failure` line to paste into the test.

    HYPOTHESIS_PROFILE=ci PYTHONPATH=src python -m unittest discover tests
"""

import os

from hypothesis import HealthCheck, settings

settings.register_profile("default", deadline=None, max_examples=100,
                          suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("ci", deadline=None, max_examples=200, derandomize=True, database=None,
                          print_blob=True, suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("mutation", deadline=None, max_examples=40, derandomize=True, database=None,
                          suppress_health_check=[HealthCheck.too_slow])
PROFILE = os.environ.get("HYPOTHESIS_PROFILE", "default")
settings.load_profile(PROFILE)
