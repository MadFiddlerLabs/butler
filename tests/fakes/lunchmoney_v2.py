"""What Butler relies on in Lunch Money's v2 API, and a trimmed copy of its OpenAPI spec.

tests/fixtures/lunchmoney_v2.json keeps, for each endpoint Butler calls, the query parameters the
spec lists and the object's required and known properties. test_contracts.py checks the client,
the fake and the fixtures against it; tests/live/live_contract.py checks it against the live spec.
Refresh it with:

    PYTHONPATH=src python tests/fakes/lunchmoney_v2.py
"""

import json
import urllib.request
from pathlib import Path

SPEC_URL = "https://lunchmoney.dev/v2/openapi"
SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "lunchmoney_v2.json"
OBJECTS = {"transactions": "transactionObject", "categories": "categoryObject",
           "manual_accounts": "manualAccountObject", "plaid_accounts": "plaidAccountObject"}

# The fields src/butler/lunchmoney.py, finance.py and networth.py read from each object.
READS = {
    "transactions": {"id", "date", "to_base", "payee", "recurring_id", "created_at", "status",
                     "category_id", "plaid_account_id", "manual_account_id"},
    "categories": {"id", "name", "is_income", "exclude_from_totals", "is_group"},
    "manual_accounts": {"id", "name", "institution_name", "type", "currency", "balance", "to_base",
                        "closed_on", "status"},
    "plaid_accounts": {"id", "name", "institution_name", "type", "currency", "balance", "to_base",
                       "mask", "status"},
}

# The query parameters src/butler/lunchmoney.py sends to each endpoint.
SENDS = {"transactions": {"start_date", "end_date", "limit", "offset"}, "categories": {"format"},
         "manual_accounts": set(), "plaid_accounts": set()}


def trim(spec: dict) -> dict:
    """The parts of the spec Butler depends on."""
    schemas = spec["components"]["schemas"]
    paths = {}
    for path, name in OBJECTS.items():
        params = spec["paths"]["/" + path]["get"].get("parameters", [])
        obj = schemas[name]
        paths[path] = {"params": sorted(p["name"] for p in params if "name" in p),
                       "object": {"required": sorted(obj.get("required", [])),
                                  "properties": sorted(obj["properties"])}}
    return {"source": SPEC_URL, "version": str(spec["info"].get("version", "")),
            "base": spec["servers"][0]["url"], "paths": paths}


def fetch() -> dict:
    import yaml
    req = urllib.request.Request(SPEC_URL, headers={"User-Agent": "butler-contract/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return trim(yaml.safe_load(resp.read()))


def snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text())


if __name__ == "__main__":
    live = fetch()
    SNAPSHOT.write_text(json.dumps(live, indent=1) + "\n")
    print(f"wrote {SNAPSHOT.name}: v{live['version']}, {live['base']}")
