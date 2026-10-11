# Updates

Butler keeps itself up to date. Once a day it checks for a new version, and it installs patches
and minor versions overnight on its own. A major version waits for a yes: the brief says
"Reply 'update' to install it". The next brief always says what happened, in one line.

## How an update happens

1. **The check.** At `update_hour` (3 a.m. by default, in the household's timezone), the tick
   reads `https://buildyourbutler.com/updates/stable.json`. It holds the newest released version,
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
5. **The install.** Installer puts the package in `~/Butler` for the current user, with no
   admin password and no prompts. Personal files are never in a package, so config, data,
   projects and the budget stay as they are. postinstall sees the household and opens nothing.
6. **The new code's checks.** In a fresh process, the new version syncs its Python packages
   (`scripts/bootstrap.sh`), runs any pending settings migrations, checks its modules and the
   settings load, and restarts the portal.
7. **Rollback.** If those checks fail, Installer puts the kept package back, and the brief says
   "tried to update … went back to …". The broken package is deleted.

`butler update --check` shows what's available. `butler update` installs it now, after a yes.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `updates` | `auto` | `auto`, `notify` or `off` |
| `update_hour` | `3` | The hour the daily check runs, in the household's timezone |

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

## What updates never do

- Touch `config/`, `projects/`, `budget/`, `out/` or `logs/` beyond appending to the log, or
  `data/` beyond `data/updates/` and `data/migrations.json`. Settings migrations are the one
  exception, and they only append.
- Update a git checkout (use `git pull`) or a copy outside `~/Butler`.
- Install anything unsigned. Test builds carry a `.butler-unsigned-build` marker naming their
  own version. Only that marker together with `BUTLER_UPDATE_ALLOW_UNSIGNED=1` relaxes the
  signature check, and signed packages never carry the marker.

## Tests

- `tests/test_updates.py`: versions, the policy, rollout, signature refusals, checksums,
  rollback, the daily tick, approvals, and migrations.
- `tests/e2e/update.sh`, run in macos-e2e.yml with the real Installer:
  - v0.0.1 → v0.0.2 keeps the household's files byte for byte and runs the migration.
  - A broken v0.0.3 rolls back.
  - An unsigned v0.0.4 is refused without the test flag.
- `tests/e2e/signed-update.sh`, run in release.yml before every release is published: the
  published release, signed and notarized, updates itself to the signed, notarized candidate with
  no check relaxed. Tampered copies are refused first. See docs/RELEASING.md.
