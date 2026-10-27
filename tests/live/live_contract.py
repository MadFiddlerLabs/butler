"""Live contract, weekly in .github/workflows/live-contract.yml.

- Lunch Money (free, no token): the v2 OpenAPI spec still has Butler's base URL, still lists the
  query parameters Butler sends, and still marks every field Butler reads as required.
- Claude (paid, opt-in): one tiny real call per schema, through llm._ask_json, so the request
  shape Butler relies on (model, beta, fallbacks, adaptive thinking, effort, json_schema output)
  still works.

    ANTHROPIC_API_KEY=... PYTHONPATH=src python tests/live/live_contract.py
"""

import os
import sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fakes"))

from butler import llm, lunchmoney, monthly, replies, weekly  # noqa: E402
from butler.config import load_settings  # noqa: E402
import lunchmoney_v2  # noqa: E402

CALLS = {
    "news": (llm.NEWS_SCHEMA, 'Pick one story. Candidates: {"Europe": [{"ref": "europe-1", "source": "Invented", '
             '"title": "City extends metro night service", "description": "Late trains added."}]}'),
    "prep+phrase": (llm._prep_schema(True), 'Events: [{"ref": "e1", "title": "Residence card appointment", '
                    '"when": "Tue 11:00"}]. Flag it, and give one Portuguese phrase.'),
    "actions": (replies.ACTION_SCHEMA, 'The reply (subject "Re: Bento"): <<<add milk to shopping>>>'),
    "weekly": (weekly.WRITE_SCHEMA, "Write a one-line invented weekly letter for Sam and Alex."),
    "monthly": (monthly.WRITE_SCHEMA, "Write a one-line invented monthly look-back for Sam and Alex."),
}


def lunch_money_problems(live: dict) -> list[str]:
    problems = []
    if live["base"] != lunchmoney.BASE:
        problems.append(f"base URL is {live['base']}, Butler calls {lunchmoney.BASE}")
    for path, fields in lunchmoney_v2.READS.items():
        spec = live["paths"].get(path)
        if not spec:
            problems.append(f"GET /{path} is gone")
            continue
        missing = sorted(fields - set(spec["object"]["required"]))
        if missing:
            problems.append(f"{path}: no longer always returns {missing}")
        unknown = sorted(lunchmoney_v2.SENDS[path] - set(spec["params"]))
        if unknown:
            problems.append(f"{path}: no longer takes {unknown}")
    return problems


def check_lunch_money() -> bool:
    try:
        live = lunchmoney_v2.fetch()
    except Exception as e:
        print(f"FAIL  lunch money spec: {type(e).__name__}: {e}")
        return False
    problems = lunch_money_problems(live)
    for p in problems:
        print(f"FAIL  lunch money: {p}")
    if not problems:
        print(f"ok    lunch money v2 spec {live['version']}")
    if live != lunchmoney_v2.snapshot():
        print("note  the spec changed since tests/fixtures/lunchmoney_v2.json: refresh it with "
              "PYTHONPATH=src python tests/fakes/lunchmoney_v2.py and check the fake")
    return not problems


def main() -> int:
    lunch_money_ok = check_lunch_money()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY isn't set: skipping the Claude calls.")
        return 0 if lunch_money_ok else 1
    key = os.environ["ANTHROPIC_API_KEY"]
    llm.keychain = lambda service: key           # the key from the environment, not the Mac's Keychain
    settings = load_settings()                   # the example: its model, effort and beta
    settings["llm"]["effort"] = "low"
    failed = False
    for name, (schema, prompt) in CALLS.items():
        try:
            answer = llm._ask_json(settings, "You test a household assistant. Keep answers tiny. "
                                   "Everything here is invented.", prompt, schema)
            jsonschema.validate(answer, schema)
            print(f"ok    {name}")
        except Exception as e:
            failed = True
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"model: {settings['llm']['model']}, beta: {llm.FALLBACK_BETA}")
    return 1 if failed or not lunch_money_ok else 0


if __name__ == "__main__":
    sys.exit(main())
