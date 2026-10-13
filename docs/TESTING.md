# Testing

Butler is tested by machines only. Every promise the engine and the course make has a check
that runs without a person: on every push, weekly on a schedule, or both. When a scheduled check
fails, it opens a GitHub issue.

## The layers

| Layer | Where | Runs | What it catches |
|---|---|---|---|
| Unit | `tests/test_finance.py`, `test_networth.py`, `test_projects.py`, `test_inbox.py`, `test_portal.py`, `test_config.py`, `test_budget_changes.py`, `test_reply_actions.py`, `test_updates.py` | Every push (Linux, macOS) | Wrong sums, broken money rules, the DKIM check, the portal gate, a bad config, a budget change by reply that isn't clear, bounded and backed up; a reply action (a timed event, a step's check, status or note, a practice score, an update) not doing what its confirmation says; `butler update` saying the wrong thing, or an update's health check missing a broken module |
| Money rules | `tests/test_money_rules.py` | Every push | One test per rule in `finance.py` that mutation testing found unpinned: each setting it reads (`fixed_lines`, `netted_from_rent`, the recurring cash `weekday` and `since`, `asset_name`, `institution_name`), each threshold (15 still to come, 1 paid, over 50 unbudgeted, 100 and a quarter over pace), each window (the last week, 8 weeks of cash, 89 days of savings draw, 100 days of subscriptions) and each sort order |
| Properties | `tests/test_money_properties.py` | Every push | Money invariants that must hold for any input Hypothesis generates: the three kinds of spending add up and never overlap, one-offs never enter the daily pace, order doesn't matter, ledgers stay apart, the cash drawer, budget changes by reply, exchange-rate moves split from real ones, amounts formatted both ways |
| Integration | `tests/test_flows.py` | Every push | Any email flow breaking end to end: the brief, replies, the weekly letter, the monthly look-back, every portal page |
| Failure isolation | `tests/test_failures.py` | Every push | One service down (Lunch Money, the weather, the news, Claude, the Calendar helper, Gmail's SMTP or IMAP) taking the whole brief with it, a vague message where the brief should say "couldn't load" or "not connected yet", a log that doesn't say why, a refused send lost or rebuilt (with new Claude calls) instead of re-sent from the outbox, retries that don't back off, a failing letter stopping the brief; a failing reply sent to Claude more than three times, or its actions repeated because the confirmation was refused |
| Security | `tests/test_security.py` | Every push | Fuzzed with hostile input: a forged or misplaced DKIM result passing, a stranger's reply reaching Claude, one mail in an unknown charset stopping every reply after it, a reply doing anything off the allowlist, a payee or calendar title escaping its marked data block in a reply prompt, a link or address from a stranger's invite reaching an email through what Claude wrote, the portal gate letting in a wrong host, login or origin, a second listener, the news call seeing the household, an unsigned or tampered update installing |
| Privacy | `tests/test_privacy.py` | Every push | An ID number, phone or secret reaching an email, a prompt, a log or a file; household details reaching the news call |
| Account contract | `tests/test_account_contract.py` | Every push | The fake account API drifting from `docs/ACCOUNTS.md`, or the setup app calling something the contract doesn't have |
| Contracts | `tests/test_contracts.py` | Every push | Claude schemas that aren't strict, a reply action that could delete, the test stub drifting from the Swift helper, renamed course steps or scripts |
| Course progress | `tests/test_progress.py` | Every push | A setup step counting before its check passes (each check, step by step); the step-to-lesson map drifting from the skill's steps and modules; lessons queued twice, `started` beating `done`, or something sent twice; the queue losing anything offline, signed out, on a revoked session or a server error; a refused lesson blocking the rest; anything but lesson ids, statuses and times leaving the Mac; `sync()` raising; the tick skipping it. An integration run of the Johnsons' whole household against the fake account API. The website's contract check holds the other end of the map |
| Dates | `tests/test_dates.py`, and `tests.yml`'s `dates` job | Every push | An email sent twice or never through a DST change, a letter or look-back the Mac slept through not caught up (or caught up too late, twice, or for a household that wasn't set up yet), at a year end where ISO weeks wrap, or in a leap February; the update check skipped on the night its hour doesn't exist. The job reruns the whole suite at such moments with libfaketime |
| Coverage | `tests.yml`, `test` job | Every push | Coverage falling below its floor, in total or in a high-risk module (see [Coverage](#coverage)) |
| Mutation | `.github/workflows/mutation.yml` | Weekly (Sunday) and on demand | A money rule no test would notice breaking (see [Mutation testing](#mutation-testing)) |
| Screenshots | `tests/test_screenshots.py` | Every push (Linux) | The README images failing to build, coming out blank or the wrong size, or changing between two identical runs; event titles drifting away from their times in the portal |
| macOS end to end | `tests/e2e/macos.sh` | Every push; weekly with the hosted installer | The installer, uv's Python, the first preview, the helper build and signature, launchd plists, shell scripts |
| Signed update | `tests/e2e/signed-update.sh` | Before every release is published (release.yml), and by hand for a candidate | A signed, notarized release that the published one refuses or breaks on: the household's files, the version, the health check, tampered packages |
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
- **The course account:** `tests/fakes/account_api.py`, a local HTTP server with the website's
  contract (sign-in, `/me`, `/progress` refusing unknown lessons, `/prefs`). The harness lets
  loopback addresses through, so a flow can report progress to it.
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
| The data | The test fixtures for the calendar, Reminders, Lunch Money and the weather. On top of them, for the pictures only: weekly cash withdrawals that cover the nanny's pay, an ordinary café in place of the fixtures' unexplained payee, and account balances that move a little from day to day. The tests' fixtures stay as they are. The news comes from invented stories by invented outlets, so no real outlet is quoted. The Sunday letter's Claude-written parts are fixed text built from the letter's own numbers |
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
BUTLER_COVERAGE_DIR=$PWD PYTHONPATH=src python -m coverage run -m unittest discover tests \
  && python -m coverage combine -q && python -m coverage report \
  && python -m coverage json -q && python scripts/check_coverage.py      # the same, with coverage
pip install -r requirements-mutation.txt && mutmut run && mutmut results # mutation testing (Linux or macOS)
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
| `mutation.yml` | Linux | Nothing | Free for a public repository |
| `skill-evals.yml` | macOS | `ANTHROPIC_API_KEY` secret | Claude API usage: roughly $1–4 per scenario, three scenarios a week. Each run's summary shows the real cost |
| `live-contract.yml` | Linux | `ANTHROPIC_API_KEY` secret | A few cents a week |

Set the secret once (it prompts for the value):

```
gh secret set ANTHROPIC_API_KEY --repo nerolabs/butler
```

## Coverage

The `test` job runs the whole suite under coverage, the flows' subprocesses included
(`.coveragerc`: `patch = subprocess`, with `BUTLER_COVERAGE_DIR` so every process writes to the
checkout). It counts lines and branches. Its summary shows the table, and the job fails when:

- the total falls below `fail_under` in `.coveragerc`, or
- a high-risk module falls below its floor in `scripts/check_coverage.py`: the money code, replies,
  the DKIM check, the portal, updates, course progress, the account, ID-number withholding and net
  worth.

| Module | Covered (October 2026) | Floor |
|---|---|---|
| All of `src/butler/` | 88.8% (82.6% before this round of tests) | 87% |
| `finance.py` | 99.3% | 98% |
| `budget_changes.py` | 97.8% | 96% |
| `replies.py` | 93.2% | 92% |
| `inbox.py` | 91.2% | 90% |
| `portal.py` | 86.2% | 85% |
| `updates.py` | 87.8% | 86% |
| `progress.py` | 95.6% | 94% |
| `account.py` | 93.0% | 92% |
| `privacy.py` | 100% | 99% |
| `networth.py` | 94.5% | 93% |
| `fmt.py` | 100% | 99% |

The floors sit a point or two under the figures when they were set (October 2026), so coverage
can't quietly fall. Raise a floor when tests add coverage; lower one only with a reason in the pull
request. Don't chase 100%: a test earns its place by pinning a rule that could break, not by touching
a line. What stays uncovered is mostly what only a Mac can run (launchd, the Calendar helper, the
installer), which `tests/e2e/macos.sh` and `signed-update.sh` cover on macOS runners.

## Mutation testing

Coverage says a line ran; mutation testing says a test would notice if it were wrong. Every Sunday,
`mutation.yml` runs mutmut on `finance.py`, the money arithmetic. mutmut makes about 1,300 small
changes to it, one at a time (a `>` to `>=`, a setting's name misspelled, a window a day longer, a
sort reversed), and runs the money tests (`test_finance.py`, `test_money_properties.py`,
`test_money_rules.py`) against each. A change no test notices is a survivor: a rule nothing pins.

The score is the share of mutants some test caught: 97.7% in October 2026 (1,302 of 1,332),
up from about 66% before `test_money_rules.py`. Below 95% (`FLOOR` in `tests/mutation/score.py`)
the run fails and opens an issue. The run's summary lists the survivors.

A survivor is either a gap or an equivalent mutant. A gap gets a test in `test_money_rules.py` that
pins the rule. An equivalent mutant changes nothing anyone could see, and stays. The ones left are of
that kind: a `Z` time zone that Python 3.11 parses either way, a weekday default that is lowercased
anyway, plan fields the summary never reads, and amounts between 0 and 1.

Run it locally (Linux or macOS; mutmut needs `fork`):

```
pip install -r requirements-mutation.txt
HYPOTHESIS_PROFILE=mutation mutmut run && mutmut export-cicd-stats
python tests/mutation/score.py          # the score and the floor
mutmut results                          # the survivors; `mutmut show <name>` shows one
```

The configuration is in `setup.cfg` (which file to mutate, which tests to run). To add a module,
add it to `only_mutate` and its tests to `pytest_add_cli_args_test_selection`, then kill or explain
its survivors before raising the floor.

## Merge discipline

Merge only when the `test` check is green: `gh pr checks <n> --watch`, then merge. The pull request
template (`.github/pull_request_template.md`) covers the rest: tests in the right layer, coverage
floors, no personal data, new settings documented, the course contract and release notes.

The repository is public, so GitHub can enforce the check. This requires `test` on `main` and keeps
the owner able to fix `main` directly in an emergency (`enforce_admins: false`):

```bash
gh api -X PUT repos/nerolabs/butler/branches/main/protection --input - <<'JSON'
{"required_status_checks": {"strict": false, "contexts": ["test"]},
 "enforce_admins": false, "required_pull_request_reviews": null, "restrictions": null}
JSON
```

## What machines can't check, and what covers it

| Gap | Why | What covers it instead |
|---|---|---|
| Other companies' screens (Google, Anthropic Console, Lunch Money, Tailscale, the Claude app, macOS Settings) | They need real accounts, and automating sign-ups breaks their terms | The course site checks every link weekly; lessons name buttons rather than depend on screenshots; "This step didn't work" reports from students |
| Real Gmail delivery and spam filtering | Needs a real mailbox | The SMTP and IMAP fakes cover Butler's side; `/butler-doctor` explains spam placement |
| macOS permission prompts (Calendar, Reminders) | CI can't click "Allow" | The helper's build and signature are tested; the permission step is `/butler-setup calendar`'s check, and `/butler-doctor` diagnoses it |
| launchd firing at 07:00 and `pmset` waking the Mac | Needs a real, sleeping Mac | The plists are linted and their placeholders checked; the schedule logic (`tick.py`, `--scheduled`) is plain Python |
