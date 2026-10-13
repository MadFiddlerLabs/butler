"""A fake Anthropic client for scripts/model_check.py: no network. It answers every request with
the smallest answer that fits the request's JSON schema, and reports fixed token counts, so the
budget and the cost arithmetic can be checked to the cent.

    client = FakeAnthropic(log="requests.jsonl")   # each request is appended to the log
"""

import json
from types import SimpleNamespace

INPUT_TOKENS = 4000
OUTPUT_TOKENS = 2000


def minimal(schema: dict):
    """The smallest value that fits a strict JSON schema."""
    kind = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if isinstance(kind, list):
        return None if "null" in kind else minimal({**schema, "type": kind[0]})
    if kind == "object":
        return {k: minimal(v) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return []
    if kind == "string":
        return "Invented."
    if kind == "boolean":
        return True
    if kind == "integer":
        return 4
    if kind == "number":
        return 1
    return None


class FakeAnthropic:
    def __init__(self, log: str | None = None, input_tokens: int = INPUT_TOKENS, output_tokens: int = OUTPUT_TOKENS):
        self.log, self.input_tokens, self.output_tokens = log, input_tokens, output_tokens
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        if self.log:
            with open(self.log, "a") as f:
                f.write(json.dumps(request, ensure_ascii=False) + "\n")
        answer = minimal(request["output_config"]["format"]["schema"])
        usage = SimpleNamespace(input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                                cache_creation_input_tokens=0, cache_read_input_tokens=0, iterations=None)
        return SimpleNamespace(model=request["model"], stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text=json.dumps(answer))])
