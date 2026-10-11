# Testing

Butler is tested by machines only. Every promise the engine and the course make has a check
that runs without a person: on every push, weekly on a schedule, or both. When a scheduled check
fails, it opens a GitHub issue.

## The layers

| Layer | Where | Runs | What it catches |
|---|---|---|---|
| Unit | `tests/test_finance.py`, `test_networth.py`, `test_projects.py`, `test_inbox.py`, `test_portal.py`, `test_config.py`, `test_budget_changes.py` | Every push (Linux, macOS) | Wrong sums, broken money rules, the DKIM check, the portal gate, a bad config, a budget change by reply that isn't clear, bounded and backed up |
| Integration | `tests/test_flows.py` | Every push | Any email flow breaking end to end: the brief, replies, the weekly letter, the monthly look-back, every portal page |
| Privacy | `tests/test_privacy.py` | Every push | An ID number, phone or secret reaching an email, a prompt, a log or a file; household details reaching the news call |
| Contracts | `tests/test_contracts.py` | Every push | Claude schemas that aren't strict, a reply action that could delete, the test stub drifting from the Swift helper, renamed course steps or scripts |
| Dates | `tests.yml`, `dates` job | Every push | Failures on the 1st, at month and year ends, and on DST changes (libfaketime) |
| Screenshots | `tests/test_screenshots.py` | Every push (Linux) | The README images failing to build, coming out blank or the wrong size, or changing between two identical runs |
| macOS end to end | `tests/e2e/macos.sh` | Every push; weekly with the hosted installer | The installer, uv's Python, the first preview, the helper build and signature, launchd plists, shell scripts |
| Skill evals | `tests/evals/run_evals.py` | Weekly and on demand (paid) | `/butler-setup install` and `/butler-doctor` not doing their job; Claude repeating or storing a pasted secret; Claude sending email |
| Live contract | `tests/live/live_contract.py` | Weekly and on demand (paid) | The Claude API no longer accepting Butler's request shape (model, beta, fallbacks, thinking, effort, JSON schema) |

## How the fakes work

The integration and privacy tests run each flow in a throwaway install (`tests/fakes/home.py`)
with the invented Johnsons' settings, projects, budget and reference file. A harness
(`tests/fakes/harness.py`) runs the flow in a subprocess, so `ROOT` is the test install and the
repo is never touched. Inside it:

- **Claude:** `llm._ask_json` answers from canned responses. Each schema is checked as a valid,
  strict JSON schema, and each canned answer is validated against it.
- **Gmail:** a recording SMTP server and an IMAP server holding invented replies, with
  Gmail-style `Authentication-Results` headers.
- **Lunch Money and Open-Meteo:** answered from `tests/fakes/fixtures.py`, dated relative to
  the day the test runs.
- **News:** invented stories, one carrying a prompt injection.
- **Calendar and Reminders:** a stub `butler-ek` that logs every call and refuses any command
  the real helper doesn't have.
- **Keychain:** a fake `security` command on `PATH`.
- **Network:** any other connection fails the test.

All data is invented. The ID numbers, phones and secrets in the fixtures exist so the privacy
tests can prove they never leak.

## README screenshots

The README's images in `docs/images/` are made by a script, never by hand.
`scripts/screenshots.py` builds the morning brief (`daily --dry-run --no-llm`) and the Sunday
letter (`weekly --dry-run`) in a throwaway install of the invented Johnsons, with the same fakes
as the integration tests. It then serves that install's portal on 127.0.0.1 and photographs the
emails and three portal pages with headless Chromium. It sends nothing and calls no outside
service.

```
pip install -r requirements-screenshots.txt
python -m playwright install chromium
python scripts/screenshots.py              # writes docs/images/*.png
```

Everything that could make an image change between runs is pinned:

| What | How |
|---|---|
| The clock | time-machine freezes the Butler processes at Sunday 18 October 2026, 17:00 (the letter) and Monday 19 October, 07:00 (the brief and the portal), Lisbon time |
| The data | The test fixtures for the calendar, Reminders, Lunch Money and the weather. The news comes from invented stories by invented outlets, so no real outlet is quoted. The Sunday letter's Claude-written parts are fixed text built from the letter's own numbers |
| The fonts | Inter (`scripts/fonts/`, SIL Open Font License), with DejaVu Sans from matplotlib for other symbols. No glyph comes from the operating system |
| The browser | Chromium from the pinned Playwright version, light mode, fixed viewports. Requests outside 127.0.0.1 are blocked |
| The files | 256-color PNGs without dithering, cropped between blocks of text, from pinned Pillow and matplotlib |

`.github/workflows/screenshots.yml` reruns the script on Linux when code or example data
changes on main, and on demand. When an image changes, it pushes the `chore/screenshots` branch
and opens a pull request. If the repository doesn't let Actions open pull requests (Settings >
Actions > General), it leaves a warning instead, and the branch is ready for one. The images in
`docs/images/` come from that Linux run; a Mac renders text slightly differently.

## Running it

```
pip install -r requirements-dev.txt                      # or into .venv
PYTHONPATH=src python -m unittest discover tests          # unit, integration, privacy, contracts
tests/e2e/macos.sh                                        # on a Mac; installs into a temp folder
ANTHROPIC_API_KEY=... python tests/evals/run_evals.py     # paid: skill evals
ANTHROPIC_API_KEY=... PYTHONPATH=src python tests/live/live_contract.py   # paid: live contract
```

The paid checks skip cleanly when `ANTHROPIC_API_KEY` isn't set.

## Cost

| Workflow | Runner | Needs | Cost |
|---|---|---|---|
| `tests.yml` | Linux | Nothing | Free for a public repository |
| `macos-e2e.yml` | macOS | Nothing | Free for a public repository |
| `screenshots.yml` | Linux | Nothing | Free for a public repository |
| `skill-evals.yml` | macOS | `ANTHROPIC_API_KEY` secret | Claude API usage: roughly $1–4 per scenario, three scenarios a week. Each run's summary shows the real cost |
| `live-contract.yml` | Linux | `ANTHROPIC_API_KEY` secret | A few cents a week |

Set the secret once (it prompts for the value):

```
gh secret set ANTHROPIC_API_KEY --repo nerolabs/butler
```

## What machines can't check, and what covers it

| Gap | Why | What covers it instead |
|---|---|---|
| Other companies' screens (Google, Anthropic Console, Lunch Money, Tailscale, the Claude app, macOS Settings) | They need real accounts, and automating sign-ups breaks their terms | The course site checks every link weekly; lessons name buttons rather than depend on screenshots; "This step didn't work" reports from students |
| Real Gmail delivery and spam filtering | Needs a real mailbox | The SMTP and IMAP fakes cover Butler's side; `/butler-doctor` explains spam placement |
| macOS permission prompts (Calendar, Reminders) | CI can't click "Allow" | The helper's build and signature are tested; the permission step is `/butler-setup calendar`'s check, and `/butler-doctor` diagnoses it |
| launchd firing at 07:00 and `pmset` waking the Mac | Needs a real, sleeping Mac | The plists are linted and their placeholders checked; the schedule logic (`tick.py`, `--scheduled`) is plain Python |
