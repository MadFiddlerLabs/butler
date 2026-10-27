"""Loads projects/*.yaml and works out what's actionable today. See docs/PROJECTS.md.

While the app runs on the example household, the examples in examples/projects/ stand in
until projects/ has a project of your own. A real household never sees the Johnsons' projects."""

from dataclasses import dataclass, field
from datetime import date, timedelta

import yaml

from .config import ROOT, on_example
from .fmt import strftime

PROJECTS_DIR = ROOT / "projects"
EXAMPLES_DIR = ROOT / "examples" / "projects"
PRIORITY = {"high": 0, "normal": 1, "low": 2}


@dataclass
class Step:
    id: str
    do: str
    who: str
    project: str
    status: str = "todo"
    after: list[str] = field(default_factory=list)
    not_before: date | None = None
    every: int | None = None
    last_done: date | None = None
    waiting_on: str = ""
    chase_after: date | None = None
    priority: str = "normal"
    notes: str = ""
    short: str = ""
    until: str = ""      # a step id; once that step is done, this one retires (e.g. daily practice)

    @property
    def label(self) -> str:
        """The outcome name other steps use for it ("Renewal documents ready")."""
        return self.short or self.do


@dataclass
class Project:
    name: str
    summary: str
    steps: list[Step]
    rules: list[str] = field(default_factory=list)


def _own_files() -> list:
    return [p for p in sorted(PROJECTS_DIR.glob("*.yaml")) if ".local." not in p.name]


def _files() -> list:
    """Your project files, or the examples while the app runs on the example household."""
    own = _own_files()
    return own if own or not on_example() else sorted(EXAMPLES_DIR.glob("*.yaml"))


def load() -> list[Project]:
    projects = []
    for path in _files():
        if path.name == "archive.yaml":
            continue
        data = yaml.safe_load(path.read_text())
        steps = [Step(project=data["name"], **{k: v for k, v in s.items()})
                 for s in data.get("steps", [])]
        projects.append(Project(data["name"], data.get("summary", "").strip(), steps,
                                data.get("rules", [])))
    _check(projects)
    return projects


def _check(projects: list[Project]) -> None:
    ids = [s.id for p in projects for s in p.steps]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate step ids: {sorted(dupes)}")
    missing = {a for p in projects for s in p.steps for a in s.after + ([s.until] if s.until else [])
               if a not in ids}
    if missing:
        raise ValueError(f"'after' names unknown steps: {sorted(missing)}")


@dataclass
class Today:
    actionable: list[Step]          # can be done now, highest priority first
    checks_due: list[Step]          # repeat checks that are due
    to_chase: list[Step]            # waiting too long; time to chase
    countdowns: list[tuple[Step, int]]   # (step, days until not_before), soonest first


def today_view(projects: list[Project], today: date) -> Today:
    steps = {s.id: s for p in projects for s in p.steps}

    def unblocked(s: Step) -> bool:
        return all(steps[a].status == "done" for a in s.after)

    actionable, checks, chase, countdowns = [], [], [], []
    for s in steps.values():
        if s.status in ("done", "paused") or (s.until and steps[s.until].status == "done"):
            continue
        # Dates count down even while other steps block them.
        if s.not_before and s.not_before > today:
            countdowns.append((s, (s.not_before - today).days))
            continue
        if not unblocked(s):
            continue
        if s.status == "waiting":
            if s.chase_after and s.chase_after <= today:
                chase.append(s)
            continue
        if s.every:
            if s.last_done is None or s.last_done + timedelta(days=s.every) <= today:
                checks.append(s)
            continue
        actionable.append(s)
    actionable.sort(key=lambda s: PRIORITY.get(s.priority, 1))
    countdowns.sort(key=lambda c: c[1])
    return Today(actionable, checks, chase, countdowns)


GROUPS = ("now", "waiting", "later", "paused", "done")


def state(s: Step, steps: dict[str, Step], today: date) -> tuple[str, str]:
    """(group, chip): which group a step belongs in and a short status for display."""
    if s.until and steps[s.until].status == "done":
        return "done", f"retired: {steps[s.until].label}"
    if s.status == "done":
        return "done", "done" + (f" {strftime(s.last_done, '%-d %b')}" if s.last_done else "")
    if s.status == "paused":
        return "paused", f"paused until {strftime(s.not_before, '%-d %b %Y')}" if s.not_before else "paused"
    if s.status == "waiting":
        due = s.chase_after and s.chase_after <= today
        return ("now" if due else "waiting",
                "chase now" if due else "waiting" + (f" · back {strftime(s.chase_after, '%-d %b')}" if s.chase_after else ""))
    needs = [steps[a].label for a in s.after if steps[a].status != "done"]
    if needs:
        return "later", "needs: " + ", ".join(needs)
    if s.not_before and s.not_before > today:
        return "later", f"from {strftime(s.not_before, '%-d %b %Y')}"
    if s.every and s.last_done and s.last_done + timedelta(days=s.every) > today:
        return "later", f"next check {strftime(s.last_done + timedelta(days=s.every), '%-d %b')}"
    return "now", "now"


def unlocks(step_id: str, steps: dict[str, Step]) -> list[list[Step]]:
    """What this step leads to, as waves: [directly unlocked], [unlocked after those], ..."""
    waves, seen, frontier = [], {step_id}, {step_id}
    while frontier:
        nxt = [s for s in steps.values() if s.id not in seen and set(s.after) & frontier]
        if not nxt:
            break
        waves.append(nxt)
        seen |= {s.id for s in nxt}
        frontier = {s.id for s in nxt}
    return waves


def for_person(view: Today, person: str) -> list[Step]:
    """A person's own steps plus shared ('Both') ones, actionable or due checks."""
    return [s for s in view.actionable + view.checks_due + view.to_chase
            if s.who in (person, "Both")]


STEP_LOG = ROOT / "data" / "step_updates.jsonl"


def log_update(step_id: str, who: str, text: str, today: date) -> None:
    import json
    STEP_LOG.parent.mkdir(exist_ok=True)
    with STEP_LOG.open("a") as f:
        f.write(json.dumps({"date": today.isoformat(), "step": step_id, "who": who, "text": text},
                           ensure_ascii=False) + "\n")


def updates(step_id: str | None = None) -> list[dict]:
    """Logged updates, oldest first; all of them, or one step's."""
    import json
    if not STEP_LOG.exists():
        return []
    rows = [json.loads(l) for l in STEP_LOG.read_text().splitlines() if l.strip()]
    return [r for r in rows if step_id is None or r["step"] == step_id]


def update_step(step_id: str, status: str | None = None, last_done: date | None = None,
                waiting_on: str | None = None, chase_after: date | None = None,
                who: str | None = None) -> str:
    """Sets `status` and/or `last_done` on one step, editing only those lines so the
    file's comments and layout survive. Returns the project name."""
    if not _own_files() and on_example():   # first change to the examples: make them yours
        PROJECTS_DIR.mkdir(exist_ok=True)
        for example in EXAMPLES_DIR.glob("*.yaml"):
            (PROJECTS_DIR / example.name).write_text(example.read_text())
    for path in _own_files():
        lines = path.read_text().splitlines(keepends=True)
        start = next((i for i, l in enumerate(lines) if l.strip() == f"- id: {step_id}"), None)
        if start is None:
            continue
        indent = " " * (len(lines[start]) - len(lines[start].lstrip()) + 2)
        end = start + 1
        while end < len(lines) and (lines[end].startswith(indent) or not lines[end].strip()):
            if not lines[end].strip() and (end + 1 >= len(lines) or not lines[end + 1].startswith(indent)):
                break
            end += 1
        block = lines[start:end]
        import json
        quoted = json.dumps(waiting_on, ensure_ascii=False) if waiting_on else None
        for key, value in (("who", who), ("status", status), ("last_done", last_done),
                           ("waiting_on", quoted), ("chase_after", chase_after)):
            if value is None:
                continue
            new = f"{indent}{key}: {value}\n"
            at = next((i for i, l in enumerate(block) if l.startswith(f"{indent}{key}:")), None)
            if at is None:
                who = next((i for i, l in enumerate(block) if l.startswith(f"{indent}who:")), 0)
                block.insert(who + 1, new)
            else:
                block[at] = new
        lines[start:end] = block
        path.write_text("".join(lines))
        load()  # re-validate
        return yaml.safe_load(path.read_text())["name"]
    raise KeyError(f"no project step with id '{step_id}'")
