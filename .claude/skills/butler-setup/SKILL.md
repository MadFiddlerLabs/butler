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
one sentence, and offer the next step by its module, title and command.

## How to work with this person

- Read `CLAUDE.md` first. Its hard rules apply throughout, and so does "Talking to someone
  learning with the course".
- Use plain words. Explain any technical term the first time, in one short sentence.
- **Start each step from its course page.** Before anything else in a step, read the step's
  `url` in `src/butler/course_progress.json` (never type a course address from memory). Show it
  and ask: "Open it in your browser now? (yes / no)". Yes is the default: on yes, or a plain
  Enter, run `open <url>`, then say "Read or watch as much as you like. Tell me when you're
  back." and wait for them. On no, carry on. Ask once per step.
- **Ask first, or auto.** `claude_mode` in `config/settings.yaml` is how they chose Claude Code
  should work: `ask` (the default: you check in before each change) or `auto` (you act more on
  your own, and a safety check reviews each step). If it isn't set, ask once, in plain words,
  as "Module 1 · How I work with you", and run `bin/butler claude-mode ask` or
  `bin/butler claude-mode auto`. It takes effect the next time they type `butler claude`; in
  this session, Shift+Tab changes the mode, and the line under the typing box shows it
  ("manual mode on" is ask first). Never switch it for them.
- **They decide what Butler gets.** At the start of each step, say in one line what it adds and
  offer to skip it: every module is optional, and they can come back to it later. Never press
  for personal details. If they'd rather not share something, accept it, leave that setting
  empty or at its default, and move on without asking again.
- **Say which module each question belongs to.** Open every question with the module and a
  short topic: "Module 3 · Your calendars: which ones should go in the brief?" The module is in
  each step's heading below; the install step also checks what the Setup Module set up, and
  says which questions those are. A question without its module leaves people unsure where
  they are in the course.
- **End each step with its "Next:" line**, as written at the end of each step below. It names
  the module, its title and the command, for example "Next: Module 3, Show your butler your
  calendar (`/butler-setup calendar`)."
- Do one thing at a time. Say what you're about to do and what they'll see.
- Don't show long command output. Summarize it in a sentence and keep the details to yourself.
- Ask for choices with AskUserQuestion, and for names in a plain question.
- Each step ends with a check. Don't move on until it passes. When it does, say so warmly and
  name the win ("Bento just made its first brief.").
- When something fails, explain it simply, fix what you can, and say what you need from them.
  `/butler-doctor` runs a full health check.
- When a step's check passes, run `bin/butler progress`. It runs that step's own check from
  the Butler folder and, when it passes, marks the module done on their course account, so the
  website and the weekly email know. It sends only lesson names and dates. Tell them in one
  line ("Module 3 is ticked off on buildyourbutler.com."). If it still says "not done yet",
  something the check needs is missing: compare with the "Where am I?" table and fix it.

### Mac or Windows

Butler runs on a Mac or on a Windows PC. Check which once, at the start: Claude Code's
environment names the platform, `darwin` for a Mac and `win32` for Windows. Then say only what's
true for their computer. Never show a student the other computer's way.

The steps are written for a Mac. On Windows, change the commands like this:

| On a Mac | On Windows |
|---|---|
| `bin/butler <command>` | `bin/butler.cmd <command>` |
| `./run.sh <command>` | `.venv/Scripts/python.exe run.py <command>` |
| `.venv/bin/python` | `.venv/Scripts/python.exe` |
| `open <url>` | `.venv/Scripts/python.exe -m webbrowser <url>` |

On Windows, Claude Code runs commands in Git Bash when Git for Windows is installed, and in PowerShell when it isn't. Write each command for the shell it runs in: in PowerShell, `PYTHONPATH=src <command>` is `$env:PYTHONPATH = "src"; <command>`, and a program in the folder runs as `.\.venv\Scripts\python.exe`.

Where a step says "the Mac", say "the computer" to a Windows student. Where a step works
differently on Windows, it says so with "On Windows". Explain Windows' own terms the first time:
Credential Manager is Windows' built-in password safe (Keychain on a Mac), and Task Scheduler is
Windows' scheduler (launchd on a Mac). On Windows, the Butler folder is `%USERPROFILE%\Butler`.

### Three rules that never bend

1. **Secrets never pass through the chat.** To store one on a Mac, run
   `scripts/store-secret.sh <service> "<what to paste>"`. A Mac dialog asks for it, and it
   goes straight into Keychain. If the dialog is cancelled or doesn't open, still never ask for
   the secret in the chat: offer to open the dialog again, or give them
   `security add-generic-password -U -s <service> -a butler -w` to run themselves with the `!`
   prefix. With `-w` last and no value after it, it asks for the value twice without showing
   it. Never put the value on that line: a command line shows in the Mac's process list, where
   other accounts can read it.
   On Windows there's no dialog, and neither command exists. Ask them to open a new Terminal
   window and type `butler secret <service>`. It asks for the value, hides it while they paste,
   and puts it straight into Credential Manager. They tell you when it says "Stored". It needs a
   window of its own, because it reads the keyboard. If they paste a secret into the chat
   anyway, don't repeat it. Tell them to store it the usual way and to make a new one,
   because chats are kept.
2. **No ID numbers** (passport, residence, tax, health) in settings, notes or prompts. They
   belong only in `projects/reference.local.yaml`, which the user fills in themselves.
3. **Nothing is sent without a clear yes.** `./run.sh daily`, `weekly`, `monthly` and
   `replies` email real people (on Windows, `.venv/Scripts/python.exe run.py daily` and so on). `./run.sh preview` and the dry runs send nothing.

Never overwrite a real config file without a yes. Copy it to `<name>.yaml.bak` first.
Examples you show are invented (the Johnsons and their butler Bento), and you say so.

## install (course Module 2: Hire your butler properly)

The win: their household, complete. Their butler knows everyone, the town, the news they want,
and their first brief looks like theirs.

Students reach this step after the Setup Module (the course's name for Module 0, "Your first
two minutes": the guided first run the download opens, with email, `butler town`,
`butler adults` and `butler brief`) and Module 1 ("Meet Claude Code", `butler claude`). So
usually their butler is named, `config/settings.yaml` has their town and adults, and a brief
exists in `out/preview/`. Start by reading the settings and
running `butler status`. Say warmly what's already done. Then review it with them and complete
what the first run skips: everyone else in the household (children, a nanny, a carer), nicknames,
the news. The `butler` command writes the same settings this step does
(`src/butler/setup_settings.py`), so you can use `butler town …` and `butler adults …` here too.
If they never ran the first run, do the whole step below.

1. Check you're in the Butler folder: `scripts/install.sh` exists. If `.venv/bin/python` is
   missing, run `scripts/bootstrap.sh`. It downloads Python with uv, takes a minute or two,
   and needs no Xcode tools. On Windows, if `.venv/Scripts/python.exe` is missing, run
   `powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1` instead.
2. If `config/settings.yaml` exists, ask whether to update it or start over.
3. Interview, in small friendly groups. The name, the adults and the town are what the Setup
   Module set up, so when you check or ask about them, open with "Setup Module ·". The rest is
   new in Module 2, so open with "Module 2 ·". For example: "Setup Module · Where you live: your
   settings say Lisbon, so 7 a.m. there is 2 a.m. in New York. Is Lisbon right?"
   1. **Your butler's name** ("Setup Module · Your butler's name:"). It signs every email and
      answers to it in Siri. Offer three playful ideas and let them pick their own. It's the
      fun part, so give it a moment.
   2. **The adults** ("Setup Module · The adults:") the emails are for: first names. Their
      email addresses come later.
   3. **Everyone else** ("Module 2 · Everyone at home:") whose events matter: children, a
      nanny, a carer. First names only, and only the people they want in the brief.
   4. **Town** ("Setup Module · Where you live:"). Work out the timezone and the weather
      coordinates. State them and let them correct you. Celsius or Fahrenheit.
   5. **News** ("Module 2 · Your news:"). Their local paper first: find its feed and check it
      returns headlines. Then the wider world, and the language for summaries. Swap out the
      example regions they don't want.
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

Next: Module 3, Show your butler your calendar (`/butler-setup calendar`).

## calendar (Module 3: Show your butler your calendar)

The win: the preview shows their real day, and their to-dos.

On a Mac, Butler reads Apple's Calendar and Reminders through the Calendar helper, in steps 1
to 4 and 6. On Windows there's no Apple Calendar: Butler reads Google Calendar and keeps the
to-do and shopping lists in Google Tasks. So on Windows, do "Google Calendar and Tasks" below in
place of steps 1 to 4 and 6.

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
   System Settings > Internet Accounts, with Calendars and Reminders switched on. A Mac
   household that keeps everything in Google can use Google directly instead, as on Windows.
5. Interview: which calendars go in the brief; whose each one is; nicknames that appear in
   event titles (aliases); holiday calendars; which calendar new events from replies go to.
6. Create the Reminders lists: `bin/butler-ek ensure-list --list "<todo_list>"` and the same
   for the shopping list.
7. Update `calendar.include`, `holidays`, `write_to` and `people[].calendars` / `aliases`.
8. Check: the config test passes, then `./run.sh preview`. Today's events appear per person.

### Google Calendar and Tasks (Windows, or a Mac's choice)

Butler can add events and tasks, and mark tasks done. It never edits or deletes anything.

1. Set `calendar.source: google` in `config/settings.yaml`.
2. **Sign in.** Explain in one sentence: they sign in to Google in their browser, and the
   sign-in goes straight into the computer's password safe, so you never see it. Run
   `./run.sh gcal connect`. Their browser opens Google's sign-in page. They choose their own
   Google account and allow Calendar and Tasks. It waits five minutes for them.
   - "Google didn't allow: …" means a box was left unticked. Run it again and tick every box.
   - "no Google client ID yet" means this install can't sign in to Google yet. It isn't
     something they did. Tell them plainly, offer to skip this step for now, and suggest "This
     step didn't work for me" on the course page.
3. `./run.sh gcal calendars` lists their calendars. Use those titles in the interview (step 5
   above) and in step 7.
4. Their to-do and shopping lists are Google Tasks lists, named in `reminders.todo_list` and
   `reminders.shopping_list`. `./run.sh gcal reminder-lists` shows the lists they have, and
   `./run.sh gcal ensure-list --list "<todo_list>"` makes one that's missing. Do the same for the
   shopping list.
5. Then steps 5, 7 and 8 above. Before the preview, `./run.sh gcal status` says "Google is
   connected".

Next: Module 4, Give your butler a brain (`/butler-setup brain`).

## brain (Module 4: Give your butler a brain)

The win: Claude writes the news summaries and notes on what to prepare for.

1. Explain in two sentences: Butler uses Claude through an API key. This is separate from
   their Claude plan and billed by use, so they'll set a spending limit first.
2. They go to platform.claude.com and create an account.
3. If they have a Max plan, its monthly API credit can pay for Butler: in Claude (claude.ai or
   the app), Settings > Billing > API credits > **Link organization**, then pick the Console
   organization they just made. They can skip buying credit, but still set a spend limit. That
   credit expires each billing cycle.
4. They add billing, then set a monthly spend limit: Settings > Billing > Spend limits, then
   **Set limit**. Suggest $5 a month to start, as the course does. Prepaid credit expires, so
   suggest buying a small amount and topping up later. Then they create an API key. Ask what
   limit they set and write it to `llm.monthly_limit` (dollars, a number), so the portal's Money
   page shows "Claude this month: about $X of your $Y limit". It's an estimate; the Console
   has the bill.
5. Store it: `scripts/store-secret.sh butler-anthropic "Your Claude API key"`. On Windows,
   they type `butler secret butler-anthropic` in a new Terminal window.
6. Check it's there without printing it:
   `security find-generic-password -s butler-anthropic >/dev/null && echo ok`. On Windows:
   `PYTHONPATH=src .venv/Scripts/python.exe -c "from butler import system; print(system.has_secret('butler-anthropic'))"`
   prints `True`.
7. Ask for two or three sentences about the household for `household.about`: who lives
   there, work, what's going on. Remind them it goes to Claude, so no ID numbers. They choose
   how much to say. One plain sentence ("Two adults and a daughter in Lisbon.") is enough for
   the course to count Module 4.
8. Ask for one sentence for `news.interests`. It sits next to news from strangers, so no
   names or personal details.
9. Check: `./run.sh preview`. The news is summarized, and the footer shows no Claude error.
   When they ask what Claude sees, be exact. There are two calls, and they get different things.
   - The news call: the headlines, the town and `news.interests`. Nothing else about the household.
   - The prep notes: `household.about`, their notes and feedback, the coming events, and project
     notes about documents and appointments.
   ID numbers go to neither.

Next: Module 5, Give your butler an inbox (`/butler-setup inbox`).

## inbox (Module 5: Give your butler an inbox)

The win: the first real morning email, and a daily schedule.

1. They create a new Gmail account just for the butler. You can't do this for them. Then:
   turn on 2-Step Verification (Google Account > Security), and create an app password at
   myaccount.google.com/apppasswords, named after the butler.
   If the app password page or the first send fails, the usual causes are:
   - 2-Step Verification isn't on yet, so the page says the setting isn't available.
   - The app password was made while signed in to another Google account, not the butler's.
   - They stored the butler's regular Gmail password instead of the app password.
   - The account is a work or school account whose admin turned app passwords off, or it uses
     Advanced Protection. A plain new Gmail account avoids both.
   - The Gmail password was changed. That revokes every app password, so make a new one and
     store it again. Tell them this now, so a later password change doesn't surprise them.
2. Store it: `scripts/store-secret.sh butler-gmail "Your butler's Gmail app password"`. On
   Windows, they type `butler secret butler-gmail` in a new Terminal window.
3. Ask each adult's email address: the one they'll read and reply from. Fill
   `household.adults[].emails`, `email.recipients` and `email.trusted_senders`, and put the
   butler's address in `email.sender`. Recommend personal addresses (Gmail, iCloud, Outlook)
   for `trusted_senders`. Replies count only with a valid email signature (DKIM) from that
   exact address, and work servers, aliases and forwarding often break it.
4. **First send.** Ask first. Suggest sending only to the person you're working with: set
   `email.recipients` to them, run `./run.sh daily` (on Windows,
   `.venv/Scripts/python.exe run.py daily`), then restore the full list. If it lands
   in spam, they mark it "Not spam" and add the butler to their contacts.
5. **The schedule.** Ask the send hour (`email.send_hour`).
   - On a Mac: run `launchd/install.sh`. Then set a daily wake a minute before. This needs an
     administrator's password: a Mac dialog asks for it, and it never reaches you. If their own
     account isn't an administrator, someone whose account is types theirs:
     `osascript -e 'do shell script "pmset repeat wakeorpoweron MTWRFSU 06:59:00" with administrator privileges'`
     (adjust the time).
   - On Windows: run `.venv/Scripts/python.exe run.py schedule install`. It adds Butler's
     tasks to Task Scheduler, with no administrator password: `\Butler\tick` every five
     minutes, `\Butler\portal` at sign-in, and `\Butler\morning`, which wakes the computer a
     minute after the send hour. Windows wakes it only if the power plan allows wake timers:
     Control Panel > Power Options > Change plan settings > Change advanced power settings >
     Sleep > Allow wake timers > Enable. The tasks keep the hours they were made with, so run
     it again whenever `send_hour` or `weekly_hour` changes.

   Tell them: keep the Mac plugged in. A laptop with its lid closed may not wake. After a
   restart (an update, a power cut), log back in: the Mac waits at the login screen, and
   Butler runs only while someone is logged in.
6. Check: `launchctl list | grep com.buildyourbutler` shows the jobs (on Windows,
   `.venv/Scripts/python.exe run.py schedule show` says each of Task Scheduler's three tasks is
   installed), and `tail -n 5 logs/butler.log` shows a tick within five minutes.

Next: Module 6, Talk to your butler (`/butler-setup replies`).

## replies (Module 6: Talk to your butler)

The win: "add milk to shopping" works by email and by Siri.

1. Explain the safety design in two sentences: the butler acts only on mail from the trusted
   addresses with a valid signature, and only through a fixed list of actions. Nothing can
   be deleted.
2. They reply to the brief: "add milk to shopping". Within five minutes the schedule picks it
   up, and a confirmation goes to everyone. Check the shopping list together: in Reminders, or
   in Google Tasks if they use Google.
3. **Siri.** On their iPhone: Shortcuts > + > add "Dictate Text", then "Send Email" to the
   butler's address with the subject set to the butler's name and the message set to the
   Dictated Text; turn "Show Compose Sheet" off. Name it "Tell <butler's name>".
4. Check: "Hey Siri, Tell <name>", "add eggs to shopping", and the confirmation arrives.

Next: Module 7, Your butler keeps the books (`/butler-setup money`).

## money (Module 7: Your butler keeps the books)

The win: money in the brief, in three kinds of spending.

1. They sign up for Lunch Money and connect their banks inside it. You can't do this for
   them. Mention that the course links to Lunch Money with an affiliate link, and that it's
   their choice.
2. In Lunch Money: Settings > Developers > request an access token. Store it:
   `scripts/store-secret.sh butler-lunchmoney "Your Lunch Money access token"`. On Windows,
   they type `butler secret butler-lunchmoney` in a new Terminal window.
3. Set `currency.primary` (Lunch Money's primary currency) and `currency.second` if some
   budget lines are in another currency.
4. Before reading any money data, tell them in one sentence: whatever Claude Code reads in
   this chat goes to Anthropic, so you'll work out totals in Python and read only the
   categories, accounts and totals you need.
5. Read their categories and accounts through the code, so the token stays in the password
   safe:
   ```
   PYTHONPATH=src .venv/bin/python -c "
   from butler import lunchmoney
   print([c['name'] for c in lunchmoney.categories() if not c['is_group']])
   print([(a['institution_name'], a['name'], a['type']) for a in lunchmoney.plaid_accounts()])
   print([(a['name'], a['type']) for a in lunchmoney.manual_accounts()])"
   ```
6. Pull about four months of transactions (`lunchmoney.transactions(start, end)`). Work out
   monthly totals per category and the biggest recurring payees in Python. Don't print
   transaction lists into the chat beyond what you need.
7. Explain the three kinds of spending in two sentences: fixed monthly expenses against
   their plan, daily spending paced through the month, and one-offs kept apart. Then draft
   `config/category_map.yaml` with them from the example:
   - `map`: Lunch Money categories to 8–15 budget categories. Work or business categories
     are `EXCLUDE` unless they want a ledger.
   - `rules.day_to_day`: the categories to pace day by day.
   - `rules.ignore_accounts` / `ignore_institutions`: investment and retirement accounts.
   - Overrides for payees that need a label, a fixed line or rent timing.
   - Optional sections only if they apply: ledgers, cash drawer, managed fees, rental income.
8. Copy `budget/budget.example.xlsx` to `budget/budget.xlsx` and replace the example lines
   with openpyxl (input cells only: Expenses A–F and I from row 5, Income A–D from row 5,
   Assumptions C5–C6). Propose amounts from the averages and let them adjust. Fixed lines in
   daily categories go in `rules.fixed_lines`. Category names must match the category map.
9. Check: the full test suite passes, then `./run.sh preview` shows the money section.

Next: Module 8, Projects and paperwork (`/butler-setup projects`).

## projects (Module 8: Projects and paperwork)

The win: one real household project with steps, and document dates tracked.

1. Explain projects in two sentences (see `docs/PROJECTS.md`). Remind them that whatever
   they tell you, and whatever Claude Code reads here, goes to Anthropic. Offer to turn one or
   two things they're working on into a project in `projects/<name>.yaml`. Steps need an
   owner and, where it applies, a gate (`after`, `not_before`, `every`, `waiting`).
2. If they want the Info page, copy `examples/reference.local.example.yaml` to
   `projects/reference.local.yaml`. You may add names and document expiry dates they tell
   you. They type any ID numbers themselves, in a text editor. Never ask for them, and don't
   open the file once they have.
   - The editor must save plain text with straight quotes. In TextEdit: Format > Make Plain
     Text, and turn off Edit > Substitutions > Smart Quotes. On Windows, Notepad already saves
     plain text with straight quotes. Smart quotes (“ ” ‘ ’) aren't quotes to YAML, so they end
     up inside values or break the file.
   - When they've saved it, check that it parses without printing any of it. The check
     prints only "ok", or the line to look at:
     ```
     .venv/bin/python -c "
     import yaml
     try:
         yaml.safe_load(open('projects/reference.local.yaml'))
         print('ok')
     except yaml.YAMLError as e:
         mark = getattr(e, 'problem_mark', None)
         print('not valid YAML' + (f' near line {mark.line + 1}' if mark else ''))"
     ```
     Never run a plain YAML load or `cat` on it: a YAML error message quotes the line.
3. Create `data/household_notes.md` with facts they shared that Claude should know.
4. Check: the project tests pass, and `./run.sh preview` shows "Next up".

Next: Module 9, The Sunday letter (`/butler-setup letters`).

## letters (Module 9: The Sunday letter)

The win: a weekly letter and a monthly look-back, previewed.

1. Ask the day and hour for the weekly letter (`email.weekly_day`, `weekly_hour`) and the
   hour for the monthly look-back (`monthly_hour`, on the 1st).
2. Preview both: `./run.sh weekly --dry-run` and `./run.sh monthly --preview`. Each one
   writes an HTML file in `out/` and says where. Open it and walk through it together.
3. The schedule already sends them. Nothing more to install on a Mac. On Windows, if the
   weekly hour changed, run `.venv/Scripts/python.exe run.py schedule install` again, so the
   computer also wakes for the letter.

Next: Module 10, Train your butler (`/butler-tune`), after a week or two of briefs. Then
Module 11, Your butler on your phone (`/butler-setup phone`).

## phone (Module 11: Your butler on your phone)

The win: the portal on their phone, for their household only.

1. Explain in two sentences: Tailscale is a private network between their own devices.
   Nothing on the internet can reach the portal.
2. They install Tailscale on the Mac and each phone, signed in to the same account, and
   invite the other adults from the Tailscale admin console. Everyone signs in with a
   personal login (a personal Google, Apple or Microsoft account), not a work one: a work
   login joins the employer's network instead. Tell iPhone users that an iPhone runs one VPN
   at a time, so turning on Tailscale turns off any other VPN (a work VPN, a privacy app).
   The Tailscale app from the App Store or tailscale.com doesn't put a `tailscale` command on
   the PATH, so always run it by its full path, as `src/butler/portal.py` does:
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale`. If that file is missing, Tailscale
   isn't installed in Applications yet. On Windows, the full path is
   `C:\Program Files\Tailscale\tailscale.exe`.
3. **Rename the Mac first.** The portal's address and its HTTPS certificate use the machine's
   name, and certificate names are public. In the Tailscale admin console, Machines > the Mac
   > Edit machine name, or run
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale set --hostname=butler` (on Windows,
   with `tailscale.exe`'s full path). Then, in the admin console's DNS page, they turn on
   MagicDNS and HTTPS certificates.
4. Run `/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8787` (on Windows,
   with `tailscale.exe`'s full path). Put the address it prints in `portal.url`.
5. Put each adult's Tailscale login in `portal.allowed_logins`, and map it to a name in
   `portal.people`.
6. Explain in one sentence: the portal opens only in browsers they've paired, so other
   accounts on the Mac can't read it. Run `bin/butler portal`: it opens the portal in the
   Mac's browser, already paired, and never prints the one-time code.
7. Pair each phone. On the iPhone, first open the address in Safari and use Share > Add to
   Home Screen, then open the portal from the new icon: the icon keeps its own pairing, apart
   from Safari. On the Mac, in the portal, they open Devices > Add a phone or browser, and type
   the code it shows on the phone. Don't ask for the code and don't read it from the screen:
   it's a key, so it stays out of the chat. A code works once, within 10 minutes; make a new
   one if it runs out.
8. Check from their phone: the address opens the portal, not the pairing page.

Next: Module 12, Graduation, on buildyourbutler.com. There's no command: the setup is done.

## Where am I?

When no step was given, run `bin/butler progress`. It lists each step as done or not, from the
same checks that mark the course (`src/butler/progress.py`), in this order. The first that isn't
done is the next step. If they chose to skip that one, say so and offer the one after it.

| Done when | Step |
|---|---|
| `config/settings.yaml` has a real name, the adults, the town and their own news regions (not the example's), and a preview brief exists | install |
| `bin/butler-ek` exists, or with `calendar.source: google` the password safe has `butler-google` (from `gcal connect`), and `calendar.include` isn't empty | calendar |
| The password safe (Keychain, or Credential Manager on Windows) has `butler-anthropic`, and `household.about` isn't empty | brain |
| The password safe has `butler-gmail`, `email.sender` and `email.recipients` are set, a real brief was built in `out/`, and the schedule is installed: `launchctl list` shows `com.buildyourbutler.butler`, or on Windows Task Scheduler has `\Butler\tick` | inbox |
| `data/processed_replies.txt` isn't empty | replies |
| The password safe has `butler-lunchmoney`, and `config/category_map.yaml` and `budget/budget.xlsx` exist | money |
| `projects/` has a project (`reference.local.yaml` doesn't count) | projects |
| A weekly letter (`out/weekly-*.html` or `out/preview/weekly-*.html`) and a monthly look-back (`out/monthly-*.html`) exist | letters |
| `portal.url` is set | phone |

Module 10 is `/butler-tune`, and Module 12 is graduation. When everything's done, say so.

When they stop for the day, tell them how to come back: open the Butler app (in Applications,
or found with Spotlight), or type `butler resume` in Terminal. Either reopens Claude Code here,
on their next step. On Windows there's no Butler app: they choose Butler in the Start menu, or
type `butler resume` in a new Terminal window.
