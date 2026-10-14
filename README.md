# Butler

Butler is your household's own AI butler. It runs on your Mac, works with your data, and
answers to the name you give it. Every morning it emails the adults in your home one brief: the day's calendar for each person, what to prepare for, your to-dos,
where the money stands, the next step on each family project, and the news you care about.
You reply in plain words to change things. A private web portal on your tailnet shows the
same information, with buttons.

It's built for families with real logistics: two calendars or more, a budget, paperwork with
deadlines, kids, maybe a move abroad. Claude does the reading and writing. Python does every
sum, and every change goes through a fixed list of actions.

**Status:** open beta, Mac only.

## Try it in two commands, no account

Butler runs on an invented example household out of the box. In a clone of this repository:

```
scripts/bootstrap.sh       # gives Butler its own Python, through uv
./run.sh preview           # builds this morning's brief for the example household and opens it
```

The preview sends nothing and needs no account, API key or email address. `./run.sh portal`
then serves the portal at http://127.0.0.1:8787, and `bin/butler portal` opens it in your
browser, paired. Cloning needs git, which on a Mac comes with the Xcode command-line tools
(`xcode-select --install`).

**New to all this?** [Build Your Butler](https://buildyourbutler.com) is a free, step-by-step
course for people who've never used a terminal. You finish with a working butler, and you
learn how AI works along the way.

## What it looks like

Every picture below shows an invented household: Sam and Alex Johnson, their daughter Mia and
their nanny Pat, in Lisbon. Their butler is called Bento. A fresh copy of Butler runs on them
until you add your own details. The pictures are made by a script
([docs/TESTING.md](docs/TESTING.md#readme-screenshots)), so they always show the current version.

<table>
<tr>
<td width="50%" valign="top"><img src="docs/images/brief.png" alt="The morning brief: weather, heads-up, to-dos, each person's day, the week, next steps on projects, and money with charts"></td>
<td width="50%" valign="top"><img src="docs/images/weekly-letter.png" alt="The Sunday letter: the week in numbers, money against plan with charts, what's coming up, spending that's creeping up, and the week ahead"></td>
</tr>
<tr>
<td><b>The morning brief.</b> Each person's day, the week ahead, the next step on each project, and where the money stands.</td>
<td><b>The Sunday letter.</b> The week in numbers, spending that's creeping up, and what to get done next week.</td>
</tr>
</table>

<table>
<tr>
<td width="40%" valign="top"><img src="docs/images/portal-projects.png" alt="The portal's Projects page: steps to do now, later, waiting and paused, by person"></td>
<td width="40%" valign="top"><img src="docs/images/portal-money.png" alt="The portal's Money page: fixed monthly expenses, daily spending against pace, one-offs and a month-to-date chart"></td>
<td width="20%" valign="top"><img src="docs/images/portal-phone.png" alt="The portal on a phone, showing this morning's brief"></td>
</tr>
<tr>
<td><b>Projects in the portal.</b> What to do now, what's waiting, and what each step unlocks.</td>
<td><b>Money in the portal.</b> Fixed monthly expenses, daily spending and one-offs, kept apart.</td>
<td><b>On a phone.</b> The same portal, over Tailscale.</td>
</tr>
</table>

## What you get

| Email | When | What's in it |
|---|---|---|
| Morning brief | Daily, at the hour you choose | Weather, heads-up on deadlines and expiring documents, to-dos, today and this week per person, what to prepare for, next project steps, money, news |
| Weekly letter | Sunday, or the day you choose | The week in numbers with charts, bills due in the next 60 days, spending that's creeping up, the week ahead, ideas to save or earn, questions about unexplained transactions, where each project stands |
| Monthly look-back | The 1st | What got done, where the time went (from the calendar), where the money went, budget suggestions from your trend |

Reply to any of them: "add milk to shopping", "dentist for Mia Thursday at 10", "done: school
visit", "that Q-2026-41-1 charge was the plumber, Household", "apply the Transport suggestion",
"less sport in the news please".
A confirmation comes back to everyone within about five minutes.

If Gmail refuses an email, Butler keeps it and sends it once Gmail takes it again. It never builds
the same email twice, so a refusal doesn't cost extra Claude calls.

The portal has the same views plus Money (every transaction, by category), Budget (the plan
as the service reads it), Projects (done, pause, send an update), Lists (Apple Reminders) and
Info (phones, IDs and document expiry dates, shown only there).

### Money, done carefully

Butler reads [Lunch Money](https://lunchmoney.app), which connects to your banks. It keeps
three kinds of spending apart, because mixing them is what makes budgets lie:

- **Fixed monthly expenses** (rent, insurance, a nanny's salary): paid against the plan.
- **Daily spending** (food, transport, household): paced through the month.
- **One-offs** (a sofa, a deposit): counted, named, and kept out of both.

On top of that, optional features cover what real households have: side ledgers kept out of
household spending (a side business, a shared repair with reimbursements), a cash drawer with
a recurring cash payment, advisor fees, rental income, a savings runway, net worth with
exchange-rate moves separated from real ones, and a budget in two currencies.

## Privacy

Butler was designed so a household can trust it with everything.

- **Your data lives on your Mac.** Config, projects, budget, notes and the email archive are
  local files. What goes to Claude, Google and Lunch Money is listed below. The `.gitignore` excludes all of them, so a fork can't publish them by accident.
- **Secrets live in the macOS Keychain**, never in files, logs or chat.
- **Nothing listens on the network.** The portal binds to 127.0.0.1. Tailscale makes it
  reachable from your own devices, and only from them. It opens only in browsers you've
  paired, so other accounts on the Mac can't read it either.
- **ID numbers never leave the Info page.** Passport, residence and health numbers live in a
  git-ignored file that isn't read into emails or sent to Claude. Emails and prompts use
  document names and dates only.
- **Claude only proposes; Python acts.** Replies become actions from a fixed allowlist.
  Nothing can be deleted by a reply. Only trusted senders with a passing DKIM signature count.
- **News is quarantined.** The news call sees only your town and a sentence about what news
  you want, and it has no tools. Links always come from the feed, never from the model.
- **Outsiders can't put links in the butler's mouth.** Calendar invites and bank payees come from
  other people. In anything Claude writes, a link or email address the household didn't write
  itself becomes "[link removed]".

What does leave the Mac: calendar titles, project steps, money summaries and household notes
go to the Claude API to write the emails; email goes through the assistant's Gmail account;
Lunch Money and Open-Meteo are read over their APIs.

## Requirements

- A Mac that's on most mornings (macOS 14 or later). Butler wakes it for the brief.
- A [Claude API key](https://platform.claude.com).
- A new Gmail account for the assistant, with an app password.
- Calendars in macOS Calendar (Google, iCloud or Exchange all work) and Apple Reminders.
- Optional: a [Lunch Money](https://lunchmoney.app) account, for the money sections. Without
  it, the brief leaves money out.
- Optional: [Tailscale](https://tailscale.com) for the portal on your phones.
- Only for a git clone: the Xcode command-line tools, for git and to build the Calendar helper.
  The package and the install script need neither; they provide Python too.

## Get started

Go to [buildyourbutler.com](https://buildyourbutler.com), name your butler, and download it.
The package installs into `~/Butler` without asking for your password. Then a Terminal window
opens, and your butler says hello. In about ten minutes, two of them yours, you sign in by
email and paste three one-line lessons, and your first brief opens:

```
butler town "Lisbon"
butler adults Sam Alex
butler brief
```

Then `butler claude` moves into [Claude Code](https://claude.com/claude-code), where the rest of
the setup happens one step at a time. The first time, it asks how Claude Code should work with
you: ask first (the default: Claude checks with you before each change) or auto (Claude acts more
on its own). `butler claude-mode ask` or `auto` changes it later.

To come back any time, open the **Butler** app (in `~/Applications`) or type `butler` in
Terminal: it says where you are in the course and the one thing to do next, then lists the
commands. Installing again over an existing Butler offers to pick up where you left off or to
start fresh; starting fresh moves your settings to `backups/` and deletes nothing. `butler status`
says what's set up, and `butler help` lists the commands. When a step's check passes, Butler ticks that module off on your course account
by itself (lesson names and dates, nothing from the household); `butler progress` shows what it
sent.

Prefer a terminal? The same install, without the package:

```
curl -fsSL https://raw.githubusercontent.com/nerolabs/butler/main/scripts/install.sh | bash
```

In Claude Code, the steps are:

| Step | What it does |
|---|---|
| `/butler-setup install` | Name your butler and see its first brief |
| `/butler-setup calendar` | Connect Calendar and Reminders |
| `/butler-setup brain` | Add a Claude API key, with a spending limit |
| `/butler-setup inbox` | Give it a Gmail address, send the first brief, start the schedule |
| `/butler-setup replies` | Reply by email or Siri to change things |
| `/butler-setup money` | Connect Lunch Money and draft your budget |
| `/butler-setup projects` | Family projects and paperwork |
| `/butler-setup letters` | The weekly letter and the monthly look-back |
| `/butler-setup phone` | The portal on your phones, with Tailscale |

`/butler-setup` on its own says where you are. You type secrets into a Mac dialog, so they
never pass through the chat. Later, `/butler-tune` adjusts the rules from your feedback, and
`/butler-doctor` checks everything when something seems off.

Prefer to do it by hand? Follow [docs/SETUP.md](docs/SETUP.md).

## Staying up to date

Butler updates itself. Every night it checks for a new version, and it installs patches and
minor versions on its own. It installs only packages signed and notarized by Butler's
publisher, keeps the previous version, and goes back to it if anything doesn't check out. A
major version waits for you to reply "update". The next morning's brief says what happened.
`butler update` installs a new version straight away, and `updates: notify` or `off` in your
settings changes the behaviour. After each update, Butler tells buildyourbutler.com the two
version numbers and whether it worked, so a broken release can be stopped for everyone else.
Nothing else goes, and it isn't linked to you; `update_report: false` turns it off.
[docs/UPDATES.md](docs/UPDATES.md) has the details.

## Documentation

- [docs/SETUP.md](docs/SETUP.md): manual setup, step by step
- [docs/CONFIG.md](docs/CONFIG.md): every setting and money rule
- [docs/PROJECTS.md](docs/PROJECTS.md): family projects with steps, dependencies and dates
- [docs/PORTAL.md](docs/PORTAL.md): the portal over Tailscale, and under your own domain
- [docs/UPDATES.md](docs/UPDATES.md): over-the-air updates, rollback and the kill switch
- [docs/TESTING.md](docs/TESTING.md): how Butler is tested, all automated
- [SECURITY.md](SECURITY.md): how to report a vulnerability

## Support

Butler is free and MIT-licensed. If it saves your household some hours, you can
[buy us a coffee](https://buymeacoffee.com/buildyourbutler).

Issues and pull requests are welcome. Please never include real household data in them: use
invented names, as the examples do.
