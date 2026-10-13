"""The releases a candidate must update from, signed to signed, before it's published
(release.yml, jobs origins and signed-update). Installs skip versions: a Mac that slept through
a release, or a student who downloaded months ago, updates from whatever it has. So the gate
covers both ends of what's offered an update:

- the latest release, which most installs run, and
- the oldest release still offered one: the first at or after the website manifest's
  min_version (older installs are asked to download Butler again), else the first release.

Only published, signed releases count: drafts and prereleases (unsigned builds) never reach
students. Prints a JSON list, newest first, for the signed-update matrix.

    python3 update_origins.py <candidate> <releases.json> <manifest.json>

releases.json is `gh release list --json tagName,isDraft,isPrerelease`; manifest.json is the
website's /updates/stable.json. Standard library only, so the runner's own Python runs it.
"""

import json
import re
import sys


def parse_version(text):
    """'v1.2.3' -> (1, 2, 3, 1); a pre-release sorts below its release; anything else -> None.
    The same rule as butler.updates.parse_version."""
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?", text.strip())
    if not m:
        return None
    return int(m[1]), int(m[2]), int(m[3]), 0 if m[4] else 1


def origins(candidate, releases, manifest):
    """The versions to update from, newest first. Raises ValueError when there's nothing to test."""
    new = parse_version(candidate)
    if not new:
        raise ValueError(f"not a version: {candidate!r}")
    published = [r["tagName"] for r in releases
                 if not r.get("isDraft") and not r.get("isPrerelease") and parse_version(r.get("tagName"))]
    if candidate in published:
        raise ValueError(f"{candidate} is already released")
    older = sorted((t for t in published if parse_version(t) < new), key=parse_version)
    if not older:
        raise ValueError(f"no signed release older than {candidate} to update from")
    minimum = parse_version((manifest or {}).get("min_version"))
    offered = [t for t in older if not minimum or parse_version(t) >= minimum] or older[-1:]
    picked = [older[-1]] + ([offered[0]] if offered[0] != older[-1] else [])
    return picked


def main(argv):
    candidate, releases_path, manifest_path = argv
    with open(releases_path) as f:
        releases = json.load(f)
    with open(manifest_path) as f:
        manifest = json.load(f)
    try:
        print(json.dumps(origins(candidate, releases, manifest)))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
