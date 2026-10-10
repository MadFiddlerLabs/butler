---
name: butler-setup
description: Set up Butler for a household, end to end. Checks the Mac, interviews the user about their household, writes config/settings.yaml, drafts category_map.yaml and the budget workbook from Lunch Money, guides the user through storing secrets in Keychain, sends a test brief with permission, and installs the schedule. Use when the user runs /butler-setup or asks to set up or install Butler.
---

# Butler setup wizard

You are setting up Butler, a household manager, for the person in front of you. Work through
the stages in order. Each stage ends with a check that passes before you move on. Keep the
user oriented: say which stage you're on and what's next, in a sentence.

Read `CLAUDE.md` first. Its hard rules apply throughout. The ones that matter most here:

- **Secrets never pass through chat.** Give the user the exact `security add-generic-password`
  command and suggest they run it with the `!` prefix, which prompts for the value. Never ask
  them to paste a password, API key or token into the conversation. If they paste one anyway,
  don't repeat it, tell them to store it in Keychain and to rotate it.
- **No ID numbers in config, notes or prompts.** Passport, residence, tax and health numbers
  go only in `projects/reference.local.yaml`, and only if the user wants the Info page.
- **Nothing is sent without a clear yes.** Every real send goes to real people.
- Examples you show are invented, and you say so.

If a setup already exists (`config/settings.yaml` is present), ask whether to review and
update it or start over. Never overwrite a real config file without a yes; copy it to
`<name>.yaml.bak` first.

## Stage 1: Check the Mac

Run these and report what's missing, with the fix:

- macOS 14 or later: `sw_vers -productVersion`
- Python 3.12+: `python3 --version` (fix: `brew install python`)
- Swift compiler: `swiftc --version` (fix: `xcode-select --install`)
- Optional, for the portal: `tailscale version`

Then run `scripts/bootstrap.sh`. It builds `bin/butler-ek`; the first run of the helper makes
macOS ask for Calendar and Reminders access. Ask the user to allow both, then check with
`bin/butler-ek calendars` and `bin/butler-ek reminder-lists`.

## Stage 2: Interview the household

Use AskUserQuestion for choices and plain questions for names. Ask in small groups, not one
long form. Collect:

1. The adults: names, and the email addresses each will reply from.
2. Everyone else events can belong to: children, a nanny, a carer. For each, aliases that
   appear in calendar titles.
3. Timezone and town. Offer the Mac's timezone (`readlink /etc/localtime`) as the default.
4. Lunch Money's primary currency, and whether some budget lines are priced in a second one.
5. Which calendars to include. Show the list from `bin/butler-ek calendars`, plus holiday
   calendars, and which one replies should add events to. Which calendars belong to whom.
6. Reminders lists for to-dos and shopping (offer to create them; `butler-ek ensure-list`).
7. News: their town's paper (find its RSS feed with them; check it parses with
   `.venv/bin/python -c "import feedparser; print(len(feedparser.parse(URL).entries))"`),
   plus national and world regions. The language for summaries.
8. Send hour for the morning brief, and when the Sunday letter should arrive.
9. Two or three sentences about the household for Claude: who lives there, work, what's
   going on (a move, a new baby, a renovation). Remind them: no ID numbers, nothing they
   wouldn't want sent to the Claude API.
10. Optional features, one question with multiSelect: weather, a phrase of the day for a
    language learner, a practice-test score tracker, the portal on their phones.

## Stage 3: Write settings.yaml

Start from `config/settings.example.yaml` and keep its comments. Fill in what you collected.
Remove optional sections the user didn't choose. Get coordinates for the weather from the
town name (state them and let the user correct them). Pick person colors from Google
Calendar's palette (the example's comments list ids).

Check: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_config`.

## Stage 4: The assistant's mailbox and secrets

1. The user creates a new Gmail account for the assistant, turns on 2-Step Verification and
   creates an app password (Google Account > Security > App passwords). You can't do this
   for them. Put the address in `email.sender`.
2. The user stores the secrets. Give these commands, one at a time:
   ```
   ! security add-generic-password -s butler-gmail -a butler -w
   ! security add-generic-password -s butler-anthropic -a butler -w
   ! security add-generic-password -s butler-lunchmoney -a butler -w
   ```
   The Claude API key comes from console.anthropic.com. The Lunch Money token comes from
   Lunch Money > Settings > Developers.
3. Check each exists without printing it:
   `security find-generic-password -s butler-gmail >/dev/null && echo ok`

## Stage 5: Money rules from Lunch Money

Read-only API calls through the code, so the token stays in Keychain:

```
PYTHONPATH=src .venv/bin/python -c "
from butler import lunchmoney
cats = lunchmoney.get('categories')['categories']
print([c['name'] for c in cats if not c.get('is_group')])
print([(a['institution_name'], a['name'], a['type']) for a in lunchmoney.get('plaid_accounts')['plaid_accounts']])"
```

Then pull about four months of transactions (`lunchmoney.transactions(start, end)`) and work
out, in Python, monthly totals per Lunch Money category and the biggest recurring payees.
Don't print transaction lists into the conversation beyond what you need.

With the user, draft `config/category_map.yaml` from the example:

- `map`: each Lunch Money category to a budget category. Keep the budget categories few
  (8 to 15). Mark work or business categories `EXCLUDE` unless they want a ledger.
- `rules.day_to_day`: the categories to pace day by day. Explain the three kinds of spending
  (fixed monthly, daily, one-offs) in two sentences and let them choose.
- `rules.ignore_accounts` / `ignore_institutions`: investment and retirement accounts.
- `rules.savings_account` and `savings_draw_payee`, if they fund spending from savings.
- Overrides for payees that need a label, a fixed line or rent timing.
- Optional sections, only if they apply: ledgers, cash drawer, managed fees, rental income.

## Stage 6: The budget workbook

Copy `budget/budget.example.xlsx` to `budget/budget.xlsx` and replace the example lines with
theirs, using openpyxl (input cells only: Expenses A–F and I from row 5, Income A–D from
row 5, Assumptions C5–C6). Propose amounts from the four-month averages and let them adjust.
Fixed lines in daily categories (a nanny, therapy) go in `rules.fixed_lines`. Category names
must match `category_map.yaml`.

Check: the full test suite passes.

## Stage 7: Projects and the Info page (optional)

Offer to turn one or two things they're working on into a project (see `docs/PROJECTS.md`).
Write it to `projects/<name>.yaml`. Steps need an owner and, where it applies, a gate
(`after`, `not_before`, `every`, `waiting`).

If they want the Info page, copy `examples/reference.local.example.yaml` to
`projects/reference.local.yaml` and let them fill in numbers themselves in an editor. You may
add names and document expiry dates they tell you; don't ask for the ID numbers.

Create `data/household_notes.md` with any facts they shared that Claude should know.

## Stage 8: Dry run, then a first send

1. `./run.sh daily --dry-run` and open `out/preview/brief-<today>.html`. Walk through it
   with the user. Fix what's wrong in config and run again.
2. Ask before sending. Suggest a first send to only the person you're working with: set
   `email.recipients` to them, send with `./run.sh daily`, then restore the full list.
3. Have them reply ("add milk to shopping"). Run `./run.sh replies` and confirm the item
   appeared in Reminders and a confirmation arrived.

## Stage 9: Schedule

1. `launchd/install.sh`
2. The user sets a daily wake a minute before the send hour (needs their password):
   `! sudo pmset repeat wakeorpoweron MTWRFSU 06:59:00`
3. Check: `launchctl list | grep com.butler` shows three jobs, and `tail logs/butler.log` shows
   a tick.

## Stage 10: Portal (if chosen)

Follow `docs/PORTAL.md`, the Tailscale section. The user installs Tailscale and invites the
other adults; you run `tailscale serve --bg 8787`, then put the printed URL in `portal.url`
and their logins in `portal.allowed_logins` and `portal.people`. Check from their phone.

## Finish

Summarize what's set up, when the first scheduled brief arrives, how to reply, and that
`/butler-tune` adjusts things as feedback comes in.
