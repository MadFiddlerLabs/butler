---
name: butler-setup
description: Set up Butler for a household, one step at a time, matching the Build Your Butler course. Steps are install (review and complete the household after the guided first run), calendar, brain (Claude API key), inbox (Gmail and the daily schedule), replies, money (Lunch Money), projects, letters and phone (Tailscale portal). Use when the user runs /butler-setup, with or without a step, or asks to set up, install or continue setting up Butler.
argument-hint: "[install | calendar | brain | inbox | replies | money | projects | letters | phone]"
---

# Butler setup

You're helping someone set up Butler, their household's AI butler. Most people doing this are
following the Build Your Butler course at buildyourbutler.com, and many have never used a
terminal. The step to run is: **$ARGUMENTS**

If no step was given, work out where they are (see "Where am I?" at the end), tell them in
one sentence, and offer the next step.

## How to work with this person

- Read `CLAUDE.md` first. Its hard rules apply throughout.
- Use plain words. Explain any technical term the first time, in one short sentence.
- Do one thing at a time. Say what you're about to do and what they'll see.
- Don't show long command output. Summarize it in a sentence and keep the details to yourself.
- Ask for choices with AskUserQuestion, and for names in a plain question.
- Each step ends with a check. Don't move on until it passes. When it does, say so warmly and
  name the win ("Bento just made its first brief.").
- When something fails, explain it simply, fix what you can, and say what you need from them.
  `/butler-doctor` runs a full health check.

### Three rules that never bend

1. **Secrets never pass through the chat.** To store one, run
   `scripts/store-secret.sh <service> "<what to paste>"`. A Mac dialog asks for it, and it
   goes straight into Keychain. If they paste a secret into the chat anyway, don't repeat it.
   Tell them to store it with the dialog and to make a new one, because chats are kept.
2. **No ID numbers** (passport, residence, tax, health) in settings, notes or prompts. They
   belong only in `projects/reference.local.yaml`, which the user fills in themselves.
3. **Nothing is sent without a clear yes.** `./run.sh daily`, `weekly`, `monthly` and
   `replies` email real people. `./run.sh preview` and the dry runs send nothing.

Never overwrite a real config file without a yes. Copy it to `<name>.yaml.bak` first.
Examples you show are invented (the Riveras and their butler Bento), and you say so.

## install (course Module 2: Hire your butler properly)

The win: their household, complete. Their butler knows everyone, the town, the news they want,
and their first brief looks like theirs.

Students reach this step after Module 0 ("Your first two minutes", the guided first run the
download opens: email, `butler town`, `butler adults`, `butler brief`) and Module 1 ("Meet
Claude Code", `butler claude`). So usually their butler is named, `config/settings.yaml` has
their town and adults, and a brief exists in `out/preview/`. Start by reading the settings and
running `butler status`. Say warmly what's already done. Then review it with them and complete
what the first run skips: everyone else in the household (children, a nanny, a carer), nicknames,
the news. The `butler` command writes the same settings this step does
(`src/butler/setup_settings.py`), so you can use `butler town …` and `butler adults …` here too.
If they never ran the first run, do the whole step below.

1. Check you're in the Butler folder: `scripts/install.sh` exists. If `.venv/bin/python` is
   missing, run `scripts/bootstrap.sh`. It downloads Python with uv, takes a minute or two,
   and needs no Xcode tools.
2. If `config/settings.yaml` exists, ask whether to update it or start over.
3. Interview, in small friendly groups:
   1. **Your butler's name.** It signs every email and answers to it in Siri. Offer three
      playful ideas and let them pick their own. It's the fun part, so give it a moment.
   2. **The adults** the emails are for: first names. Their email addresses come later.
   3. **Everyone else** whose events matter: children, a nanny, a carer. First names only.
   4. **Town.** Work out the timezone and the weather coordinates. State them and let them
      correct you. Celsius or Fahrenheit.
   5. **News.** The language for summaries, and whether to keep the example regions for now.
      Their local paper can come later with `/butler-tune`.
4. Write `config/settings.yaml` from `config/settings.example.yaml`. Keep its comments.
   - Set `name`, `email.sender_name` (the butler's name), `timezone`, `weather`,
     `household.place` and `reminders.todo_list` (the butler's name).
   - Fill `household.adults` with names and `emails: []`. Fill `people` with everyone, with
     `calendars: []` and colors from the example's palette.
   - Until later steps fill them, leave `email.sender` empty, `recipients: []`,
     `trusted_senders: []`, `calendar.include: []`, `calendar.write_to` empty,
     `household.about` empty and `portal.url` empty (the brief links to it once it's set).
   - Remove `language_learning` and `score_tracker`. They can come back as advanced modules.
5. Check: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_config` passes.
6. Run `./run.sh preview`. It opens the brief in their browser. Point out their butler's name
   at the top, the weather and the headlines. The footer says what's "not connected yet":
   that's the rest of the course.

## calendar (Module 3: Show your butler your calendar)

The win: the preview shows their real day, and their to-dos.

1. **Xcode command line tools**, only if `bin/butler-ek` is missing. The downloaded package
   includes the helper, prebuilt and signed, so skip to step 3. Otherwise: `xcode-select -p`
   succeeds if the tools are installed. If not, run `xcode-select --install`. A Mac dialog
   appears; they click Install and agree. It can take 10 to 30 minutes. Check again when they
   say it's done.
2. **Build the helper** (again, only if `bin/butler-ek` is missing): `helpers/butler-ek/build.sh`.
3. **Permission.** Run `bin/butler-ek calendars`. macOS asks to allow access to Calendars;
   they click Allow. Then `bin/butler-ek reminder-lists`, and they allow Reminders. If they
   clicked Don't Allow: System Settings > Privacy & Security > Calendars (and Reminders),
   switch it on, and run the command again.
4. If their calendars live in Google or Microsoft and don't show up, they add the account in
   System Settings > Internet Accounts, with Calendars and Reminders switched on.
5. Interview: which calendars go in the brief; whose each one is; nicknames that appear in
   event titles (aliases); holiday calendars; which calendar new events from replies go to.
6. Create the Reminders lists: `bin/butler-ek ensure-list --list "<todo_list>"` and the same
   for the shopping list.
7. Update `calendar.include`, `holidays`, `write_to` and `people[].calendars` / `aliases`.
8. Check: the config test passes, then `./run.sh preview`. Today's events appear per person.

## brain (Module 4: Give your butler a brain)

The win: Claude writes the news summaries and notes on what to prepare for.

1. Explain in two sentences: Butler uses Claude through an API key. This is separate from
   their Claude plan and billed by use, so they'll set a spending limit first.
2. They go to console.anthropic.com, create an account, add billing, and set a monthly spend
   limit (Settings > Limits). Suggest a low limit to start. Then they create an API key.
3. Store it: `scripts/store-secret.sh butler-anthropic "Your Claude API key"`.
4. Check it's there without printing it:
   `security find-generic-password -s butler-anthropic >/dev/null && echo ok`
5. Ask for two or three sentences about the household for `household.about`: who lives
   there, work, what's going on. Remind them it goes to Claude, so no ID numbers.
6. Ask for one sentence for `news.interests`. It sits next to news from strangers, so no
   names or personal details.
7. Check: `./run.sh preview`. The news is summarized, and the footer shows no Claude error.

## inbox (Module 5: Give your butler an inbox)

The win: the first real morning email, and a daily schedule.

1. They create a new Gmail account just for the butler. You can't do this for them. Then:
   turn on 2-Step Verification (Google Account > Security), and create an app password at
   myaccount.google.com/apppasswords, named after the butler.
2. Store it: `scripts/store-secret.sh butler-gmail "Your butler's Gmail app password"`.
3. Ask each adult's email address: the one they'll read and reply from. Fill
   `household.adults[].emails`, `email.recipients` and `email.trusted_senders`, and put the
   butler's address in `email.sender`.
4. **First send.** Ask first. Suggest sending only to the person you're working with: set
   `email.recipients` to them, run `./run.sh daily`, then restore the full list. If it lands
   in spam, they mark it "Not spam" and add the butler to their contacts.
5. **The schedule.** Ask the send hour (`email.send_hour`). Run `launchd/install.sh`. Then
   set a daily wake a minute before. This shows a Mac password dialog, and the password
   never reaches you:
   `osascript -e 'do shell script "pmset repeat wakeorpoweron MTWRFSU 06:59:00" with administrator privileges'`
   (adjust the time). Tell them: keep the Mac plugged in. A laptop with its lid closed may
   not wake.
6. Check: `launchctl list | grep com.buildyourbutler` shows the jobs, and
   `tail -n 5 logs/butler.log` shows a tick within five minutes.

## replies (Module 6: Talk to your butler)

The win: "add milk to shopping" works by email and by Siri.

1. Explain the safety design in two sentences: the butler acts only on mail from the trusted
   addresses with a valid signature, and only through a fixed list of actions. Nothing can
   be deleted.
2. They reply to the brief: "add milk to shopping". Within five minutes the schedule picks it
   up, and a confirmation goes to everyone. Check Reminders together.
3. **Siri.** On their iPhone: Shortcuts > + > add "Dictate Text", then "Send Email" to the
   butler's address with the subject set to the butler's name and the message set to the
   Dictated Text; turn "Show Compose Sheet" off. Name it "Tell <butler's name>".
4. Check: "Hey Siri, Tell <name>", "add eggs to shopping", and the confirmation arrives.

## money (Module 7: Your butler keeps the books)

The win: money in the brief, in three kinds of spending.

1. They sign up for Lunch Money and connect their banks inside it. You can't do this for
   them. Mention that the course links to Lunch Money with an affiliate link, and that it's
   their choice.
2. In Lunch Money: Settings > Developers > request an access token. Store it:
   `scripts/store-secret.sh butler-lunchmoney "Your Lunch Money access token"`.
3. Set `currency.primary` (Lunch Money's primary currency) and `currency.second` if some
   budget lines are in another currency.
4. Read their categories and accounts through the code, so the token stays in Keychain:
   ```
   PYTHONPATH=src .venv/bin/python -c "
   from butler import lunchmoney
   cats = lunchmoney.get('categories')['categories']
   print([c['name'] for c in cats if not c.get('is_group')])
   print([(a['institution_name'], a['name'], a['type']) for a in lunchmoney.get('plaid_accounts')['plaid_accounts']])"
   ```
5. Pull about four months of transactions (`lunchmoney.transactions(start, end)`). Work out
   monthly totals per category and the biggest recurring payees in Python. Don't print
   transaction lists into the chat beyond what you need.
6. Explain the three kinds of spending in two sentences: fixed monthly expenses against
   their plan, daily spending paced through the month, and one-offs kept apart. Then draft
   `config/category_map.yaml` with them from the example:
   - `map`: Lunch Money categories to 8–15 budget categories. Work or business categories
     are `EXCLUDE` unless they want a ledger.
   - `rules.day_to_day`: the categories to pace day by day.
   - `rules.ignore_accounts` / `ignore_institutions`: investment and retirement accounts.
   - Overrides for payees that need a label, a fixed line or rent timing.
   - Optional sections only if they apply: ledgers, cash drawer, managed fees, rental income.
7. Copy `budget/budget.example.xlsx` to `budget/budget.xlsx` and replace the example lines
   with openpyxl (input cells only: Expenses A–F and I from row 5, Income A–D from row 5,
   Assumptions C5–C6). Propose amounts from the averages and let them adjust. Fixed lines in
   daily categories go in `rules.fixed_lines`. Category names must match the category map.
8. Check: the full test suite passes, then `./run.sh preview` shows the money section.

## projects (Module 8: Projects and paperwork)

The win: one real household project with steps, and document dates tracked.

1. Explain projects in two sentences (see `docs/PROJECTS.md`). Offer to turn one or two
   things they're working on into a project in `projects/<name>.yaml`. Steps need an owner
   and, where it applies, a gate (`after`, `not_before`, `every`, `waiting`).
2. If they want the Info page, copy `examples/reference.local.example.yaml` to
   `projects/reference.local.yaml`. You may add names and document expiry dates they tell
   you. They type any ID numbers themselves, in a text editor. Never ask for them.
3. Create `data/household_notes.md` with facts they shared that Claude should know.
4. Check: the project tests pass, and `./run.sh preview` shows "Next up".

## letters (Module 9: The Sunday letter)

The win: a weekly letter and a monthly look-back, previewed.

1. Ask the day and hour for the weekly letter (`email.weekly_day`, `weekly_hour`) and the
   hour for the monthly look-back (`monthly_hour`, on the 1st).
2. Preview both: `./run.sh weekly --dry-run` and `./run.sh monthly --preview`. Each one
   writes an HTML file in `out/` and says where. Open it and walk through it together.
3. The schedule already sends them. Nothing more to install.

## phone (Module 11: Your butler on your phone)

The win: the portal on their phone, for their household only.

1. Explain in two sentences: Tailscale is a private network between their own devices.
   Nothing on the internet can reach the portal.
2. They install Tailscale on the Mac and each phone, signed in to the same account, and
   invite the other adults from the Tailscale admin console. In the admin console's DNS page
   they turn on MagicDNS and HTTPS certificates.
3. Run `tailscale serve --bg 8787`. Put the address it prints in `portal.url`.
4. Put each adult's Tailscale login in `portal.allowed_logins`, and map it to a name in
   `portal.people`.
5. Check from their phone: the address opens the portal. On iPhone, Share > Add to Home
   Screen gives it an icon.

## Where am I?

When no step was given, check these in order. The first that isn't done is the next step.

| Done when | Step |
|---|---|
| `.venv/bin/python` and `config/settings.yaml` exist | install |
| `bin/butler-ek` exists and `calendar.include` isn't empty | calendar |
| Keychain has `butler-anthropic` | brain |
| Keychain has `butler-gmail` and `launchctl list` shows `com.buildyourbutler.butler` | inbox |
| `data/processed_replies.txt` exists | replies |
| Keychain has `butler-lunchmoney` and `config/category_map.yaml` exists | money |
| `projects/` has a project | projects |
| An `out/weekly-sent-*` marker exists, or they say they've previewed the letters | letters |
| `portal.url` is set | phone |

Module 10 is `/butler-tune`, and Module 12 is graduation. When everything's done, say so.
