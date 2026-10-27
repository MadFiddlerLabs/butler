# Updates

Butler keeps itself up to date. Once a day it checks for a new version, and it installs patches
and minor versions overnight on its own. A major version waits for a yes: the brief says
"Reply 'update' to install it". The next brief always says what happened, in one line.

## How an update happens

1. **The check.** Once a day, on the first tick at or after `update_hour` (3 a.m. by default, in the
   household's timezone), the tick reads `https://buildyourbutler.com/updates/stable.json`. A Mac asleep at that hour checks when it
   wakes (setup wakes it at 06:59 for the brief). The manifest holds the newest released version,
   its package URL, SHA-256 and size, `min_version`, the release notes, and an optional
   `rollout_percent`.
2. **The decision** (`updates.decide`):
   - Butler never downgrades and never reinstalls the same version.
   - Under `updates: auto`, patches and minors install. A major version, or anything marked
     `major`, waits for an `update` reply.
   - Under `updates: notify`, every version waits for the reply. `updates: off` never checks.
   - When the installed version is older than `min_version`, the brief asks for a fresh download
     from the website instead.
   - With a `rollout_percent`, only that share of installs takes the version. A stable random
     id in `data/updates/install-id` decides.
3. **The checks on the package**, before anything is installed:
   - Its SHA-256 and size must match the manifest. A mismatch deletes the download.
   - `pkgutil --check-signature` must show the Developer ID Installer chain for Team ID
     `VB9CHJM4AN`.
   - Gatekeeper (`spctl --assess --type install`) must accept it.
   - `xcrun stapler validate` must pass, when the Xcode tools are installed.
4. **A copy to go back to.** The package of the version installed now is kept in
   `data/updates/`. If it isn't there yet, it's fetched from that version's GitHub Release and
   checked the same way. With no copy, the update waits until tomorrow.
5. **A baseline.** The version installed now builds today's brief the way the check in step 7
   will (`butler.updates --brief-check`), and keeps what failed in
   `data/updates/brief-baseline.json` until the update is over.
6. **The install.** Installer puts the package in `~/Butler` for the current user, with no
   admin password and no prompts. Personal files are never in a package, so config, data,
   projects and the budget stay as they are. postinstall sees the household and opens nothing.
   It keeps the Butler app where it was: replaced in `/Applications` if it's there, else in
   `~/Applications` (docs/RELEASING.md, "What the package does").
7. **The new code's checks.** In a fresh process, the new version:
   - syncs its Python packages to its own lock (`scripts/bootstrap.sh`, see
     [Dependencies](#dependencies)),
   - runs any pending settings migrations,
   - checks that its modules load and the settings have `timezone`, `email` and `household`,
   - builds today's brief from the household's own settings and services (`daily.brief_check`),
     and only then restarts the portal.

   The brief check is `daily --dry-run --no-llm` without its side effects: no Claude, nothing
   sent, no file in `out/`, no net worth snapshot and no budget change. Lunch Money's answers are
   cached in `data/raw/` as on any brief. It fails when the brief itself breaks, or when a section
   fails with an error only code makes (`AttributeError`, `ImportError`, `KeyError`, `NameError`,
   `SyntaxError`, `TypeError`) that the baseline didn't have. A service that's down or slow fails
   with something else, and doesn't stop an update. Neither does a failure the old version had
   too, so a household whose data trips a bug still gets the release that fixes it. Without a
   baseline (an update from a version before this check), every such failure counts.
8. **Rollback.** If any check fails, or Installer stops halfway, Installer puts the kept package
   back and that version runs its own checks. The brief says "tried to update … went back to …".
   The broken package is deleted.
9. **A way back that fails.** If reinstalling the kept package fails, or its own checks don't
   pass, the brief says so and says to download Butler again from the website. A plain email
   says the same straight away, to every recipient, because the code left on disk may be too
   broken to send a brief. It's sent by the process that ran the update, which loaded the old
   version's code and the household's settings before installing anything.
10. **Updates that don't happen.** A refused or failed download, a signature that doesn't check
    out, or no copy to go back to stops the update before anything is installed. The next brief
    says so once ("couldn't update to … It tries again tomorrow.").

`butler update --check` shows what's available. `butler update` installs it now, after a yes.

## On Windows

The same steps, with Windows' own tools. Nothing is offered to Windows until its installer is
signed and published (#40).

- **The manifest.** Windows reads its installer from the manifest's `windows` entry
  (`{url, sha256, size}`); the rest (version, notes, `min_version`, rollout) is shared. A manifest
  without that entry has no update for Windows (`updates.for_this_computer`).
- **The checks on the installer.** Its SHA-256 and size must match. Windows must trust its
  Authenticode signature (WinVerifyTrust, as Windows checks a download: an intact signature, a
  trusted chain, not revoked, in date or timestamped), and the signer's certificate must name
  Butler's publisher, Andrew Edmond, who signs as an individual, as its common name, with no other
  organization (`updates.PUBLISHER`).
- **The copy to go back to** is that version's `Butler-Setup.exe` from its GitHub Release, kept
  in `data/updates/` as `Butler-<version>.exe`.
- **The install.** Inno Setup runs silently into `%USERPROFILE%\Butler`: no admin password, no
  prompt, and it never closes a running Butler. `butler.installed` sees the household and opens
  nothing. Its log is `logs/update-install.log`.
- **Dependencies.** Windows won't replace a file a running program has loaded, and the portal keeps
  Butler's packages loaded. So `scripts/bootstrap.ps1` gives each lock its own virtualenv in
  `.venvs\<lock id>-<processor>` and points the `.venv` junction at it. A new lock's virtualenv is
  built beside the one in use, and a rollback points `.venv` back at the old one, still there. The
  current one and the one before it are kept.
- **The restart.** The new code restarts the portal's Task Scheduler task (`\Butler\portal`).
- **Tests.** `tests/test_updates.py` and `tests/test_system.py` (the commands, and
  WinVerifyTrust on a signed and an unsigned file); `tests/e2e/update.ps1` in windows-e2e.yml
  runs update.sh's cases with installers signed by a test certificate, so the real signature check
  runs: an update, two rollbacks, and refusals of an unsigned installer, one signed by someone
  else and one changed after signing.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `updates` | `auto` | `auto`, `notify` or `off` |
| `update_hour` | `3` | The hour the daily check runs, in the household's timezone |
| `update_report` | `true` | Tell the website how each update went (below). `false` turns it off |

## Dependencies

Each version installs exactly the Python packages it was tested with. `requirements.lock` pins
every package, dependencies included, to one version, with the SHA-256 of each of its files on
PyPI. The lock is in the signed package, so its hashes are as trustworthy as the code.

- `scripts/bootstrap.sh` runs `uv pip sync --require-hashes --no-build requirements.lock`. A file
  whose hash isn't in the lock is refused, so a changed or hijacked upload on PyPI can't reach a
  household on its next overnight update. `--no-build` installs only prebuilt wheels: nothing is
  compiled and no unchecked build tool runs. `sync` also removes anything the lock doesn't list,
  so a rollback restores the old version's packages too.
- uv itself is pinned in `scripts/lib/uv.sh`, by version and by the SHA-256 of its download for
  Apple silicon and Intel. A uv already on the Mac is used only if it is that version; otherwise
  the pinned build is downloaded into `bin/uv` and checked first. Nothing is piped from the
  network into a shell. uv checks the Python it downloads against checksums built into it, so the
  pinned uv also pins Butler's Python (3.13).
- One lock serves both kinds of Mac and CI's Linux: it is resolved for every platform
  (`--universal`) from Python 3.12 up, and lists the hashes of every wheel of each version.

To change a dependency, edit `requirements.txt` and run `scripts/lock.sh`; `scripts/lock.sh
--upgrade` takes the newest versions allowed. Commit both files. `tests/test_packaging.py` fails
when `requirements.txt` and the lock disagree, and the `tests` workflow runs `scripts/lock.sh
--check`, which re-resolves and compares the pins. CI installs the lock before the test
dependencies, so the tests run on the locked versions.

The trade-off: the hash check trusts PyPI on the day the lock was made, and uv's checksums trust
its GitHub release on the day it was pinned. A newer uv or package arrives only by changing the
pin in a reviewed commit.

## Migrations

`src/butler/migrations.py` holds numbered settings migrations. Each one runs once, checks
before it changes anything, and appends to `config/settings.yaml` rather than rewriting it.
Applied numbers are recorded in `data/migrations.json`. To add one, append to `MIGRATIONS` with
the next number. Never renumber or remove a migration.

## Stopping a bad release (the kill switch)

The manifest is built by the website, from the latest GitHub Release. To stop a version from
reaching anyone else, add it to `updates/hold.json` in the website repo and push. The manifest
then points at the newest release that isn't held, or says no update is available. To roll a
version out gradually, set `rollout_percent` there too. See the website's DEPLOY.md.

## Telling the website how it went

The kill switch needs a signal. After every update that resolves, Butler sends the website the
version it updated from, the version it updated to, and the outcome: `updated`, `rolled_back` or
`failed`, with a fixed reason code (`src/butler/update_report.py`).

- **What goes:** `{from, to, outcome, reason?}`, and nothing else. No session, no install id, no
  email, nothing from the household. A Butler that never signed in reports too.
- **When:** once, right after the update, from the process that ran it. A package refused for its
  checksum or signature reports `failed` too. A download that didn't arrive, or no copy to go back
  to, is the Mac's problem, not the release's, and reports nothing.
- **Offline:** it waits in `data/updates/outcomes.json`, and the tick sends it, at most every 30
  minutes, for up to three days.
- **Never in the way:** a five-second timeout, errors logged without detail, and an update never
  fails because of it.
- **Off:** `update_report: false`. A report waiting to be sent is deleted on the next tick.

The website keeps daily counts per version and outcome, and its uptime check emails the owner
when the version on offer rolls back or fails too often. The contract is in
[ACCOUNTS.md](ACCOUNTS.md#update-outcomes).

## What updates never do

- Touch `config/`, `projects/`, `budget/`, `out/` or `logs/` beyond appending to the log, or
  `data/` beyond `data/updates/`, `data/migrations.json` and the brief check's Lunch Money cache
  in `data/raw/`. Settings migrations are the one exception, and they only append.
- Update a git checkout (use `git pull`) or a copy outside `~/Butler`.
- Install anything unsigned. Test builds carry a `.butler-unsigned-build` marker naming their
  own version. Only that marker together with `BUTLER_UPDATE_ALLOW_UNSIGNED=1` relaxes the
  signature check, and signed packages never carry the marker.

## Tests

- `tests/test_updates.py`: versions, the policy, rollout, signature refusals, checksums,
  rollback, a failed way back (reported by brief and by email), an Installer that stops halfway,
  the brief check against its baseline, updates that didn't happen, the daily tick, approvals,
  migrations, and the outcome report against the fake account API (each outcome once, nothing
  with the setting off, offline queueing and its limits).
- `tests/test_failures.py`, `UpdateReportIsolationTest`: an update succeeds and is recorded when
  the report raises, hangs up or can't be written.
- `tests/test_failures.py`, `UpdateBriefCheckTest`: the brief check on the invented Johnsons
  passes and writes nothing, passes with services down, and fails on a section that crashes on
  code and on a brief that can't render.
- `tests/test_packaging.py`, `LockTest`: the lock matches `requirements.txt`, every package has
  hashes, bootstrap installs only the lock with hashes required, and uv is pinned by checksum.
- `tests/e2e/update.sh`, run in macos-e2e.yml with the real Installer:
  - v0.0.1 → v0.0.2 keeps the household's files byte for byte and runs the migration.
  - A broken v0.0.3, whose code doesn't load, rolls back.
  - A v0.0.5 whose code loads but whose brief crashes rolls back, on the brief check alone.
  - An unsigned v0.0.4 is refused without the test flag.
- `tests/e2e/signed-update.sh`, run in release.yml before every release is published: the
  latest release and the oldest still offered an update (`min_version`, else the first signed
  release), signed and notarized, each update themselves to the signed, notarized candidate
  with no check relaxed. Tampered copies are refused first. See docs/RELEASING.md.
