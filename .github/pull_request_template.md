## What and why

<!-- One or two sentences: what changes for a household, and why. -->

## Checklist

- [ ] **Tests in the right layer** (docs/TESTING.md): unit or property for pure code, integration
      with fakes for a flow, privacy or security for anything that sends, contracts for names
      others depend on. A new outside service has a fake in `tests/fakes/`; a new Claude call has
      its schema in `test_contracts.py`.
- [ ] **Coverage holds**: the `test` job's floors pass. Raise a floor in
      `scripts/check_coverage.py` when you add tests to a module.
- [ ] **No personal data**: only the invented Johnsons. I grepped the diff for real names,
      addresses, account names, amounts and numbers:
      `git diff origin/main | grep -niE '<names, street, bank, employer>'`
- [ ] **New settings**: added to `config/settings.example.yaml` (or the matching example) and
      documented in `docs/CONFIG.md`, off when absent.
- [ ] **Course contract**: if a `/butler-setup` step, a `butler` subcommand, `scripts/install.sh`,
      `./run.sh preview`, `course_progress.json` or the account API changed, the course and the
      website change too (docs/ACCOUNTS.md).
- [ ] **Docs** say what changed (README, docs/, skills).
- [ ] **Release notes**: one plain line for the household, or "none".

Release notes:
