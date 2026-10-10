"""Live contract: one tiny real Claude call per schema, through llm._ask_json, so the request
shape Butler relies on (model, beta, fallbacks, adaptive thinking, effort, json_schema output)
still works. Paid and opt-in: weekly in .github/workflows/live-contract.yml.

    ANTHROPIC_API_KEY=... PYTHONPATH=src python tests/live/live_contract.py
"""

import os
import sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from butler import llm, monthly, replies, weekly  # noqa: E402
from butler.config import load_settings  # noqa: E402

CALLS = {
    "news": (llm.NEWS_SCHEMA, 'Pick one story. Candidates: {"Europe": [{"ref": "europe-1", "source": "Invented", '
             '"title": "City extends metro night service", "description": "Late trains added."}]}'),
    "prep+phrase": (llm._prep_schema(True), 'Events: [{"ref": "e1", "title": "Residence card appointment", '
                    '"when": "Tue 11:00"}]. Flag it, and give one Portuguese phrase.'),
    "actions": (replies.ACTION_SCHEMA, 'The reply (subject "Re: Bento"): <<<add milk to shopping>>>'),
    "weekly": (weekly.WRITE_SCHEMA, "Write a one-line invented weekly letter for Sam and Alex."),
    "monthly": (monthly.WRITE_SCHEMA, "Write a one-line invented monthly look-back for Sam and Alex."),
}


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY isn't set: skipping the live contract check.")
        return 0
    llm.keychain = lambda service: None          # use ANTHROPIC_API_KEY, not the Mac's Keychain
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
