"""The last check before text leaves the Mac: every Claude request and every email passes through
withhold(), which removes the household's ID numbers.

ID numbers belong only in projects/reference.local.yaml, shown on the portal's Info page. If one
is typed somewhere else too (household notes, a project note, a calendar event, a reply), this
stops it reaching Claude or an inbox. It matches the numbers the reference file holds, in any
case and with spaces, dots or dashes between the characters, rather than guessing at shapes.
"""

import re

from . import reference

WITHHELD = "[ID number withheld]"
MIN_LENGTH = 5          # shorter "numbers" would match ordinary words and amounts
_SEPARATORS = r"[\s.\-/]*"


def id_numbers() -> list[str]:
    """The ID numbers in the reference file, as letters and digits only."""
    found, data = set(), reference.load()
    for person in data.get("people") or []:
        for value in (person.get("ids") or {}).values():
            found.add(re.sub(r"[^0-9A-Za-z]", "", str(value)))
    for doc in data.get("documents") or []:
        if doc.get("number"):
            found.add(re.sub(r"[^0-9A-Za-z]", "", str(doc["number"])))
    return sorted((n for n in found if len(n) >= MIN_LENGTH), key=len, reverse=True)


def _pattern(numbers: list[str]) -> re.Pattern | None:
    if not numbers:
        return None
    alternatives = (_SEPARATORS.join(re.escape(c) for c in n) for n in numbers)
    return re.compile(r"(?<![0-9A-Za-z])(?:" + "|".join(alternatives) + r")(?![0-9A-Za-z])", re.I)


def withhold(*texts: str | None) -> list[str | None]:
    """The texts with every ID number replaced. Reads the reference file once per call."""
    pattern = _pattern(id_numbers())
    if pattern is None:
        return list(texts)
    return [pattern.sub(WITHHELD, t) if t else t for t in texts]
