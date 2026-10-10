# Casa

Casa is a private household manager that runs on your Mac. Every morning it emails the adults
in your home one brief: the day's calendar for each person, what to prepare for, your to-dos,
where the money stands, the next step on each family project, and the news you care about.
You reply in plain words to change things. A private web portal on your tailnet shows the
same information, with buttons.

It's built for families with real logistics: two calendars or more, a budget, paperwork with
deadlines, kids, maybe a move abroad. Claude does the reading and writing. Python does every
sum, and every change goes through a fixed list of actions.

## What you get

| Email | When | What's in it |
|---|---|---|
| Morning brief | Daily, at the hour you choose | Weather, heads-up on deadlines and expiring documents, to-dos, today and this week per person, what to prepare for, next project steps, money, news |
| Sunday letter | Weekly | The week in numbers with charts, bills due in the next 60 days, spending that's creeping up, the week ahead, ideas to save or earn, questions about unexplained transactions, where each project stands |
| Monthly look-back | The 1st | What got done, where the time went (from the calendar), where the money went, budget suggestions from your trend |

Reply to any of them: "add milk to shopping", "dentist for Mia Thursday at 10", "done: school
visit", "that Q-2026-41-1 charge was the plumber, Household", "less sport in the news please".
A confirmation comes back to everyone within about five minutes.

The portal has the same views plus Money (every transaction, by category), Budget (the plan
as the service reads it), Projects (done, pause, send an update), Lists (Apple Reminders) and
Info (phones, IDs and document expiry dates, shown only there).

### Money, done carefully

Casa reads [Lunch Money](https://lunchmoney.app), which connects to your banks. It keeps
three kinds of spending apart, because mixing them is what makes budgets lie:

- **Fixed monthly expenses** (rent, insurance, a nanny's salary): paid against the plan.
- **Daily spending** (food, transport, household): paced through the month.
- **One-offs** (a sofa, a deposit): counted, named, and kept out of both.

On top of that, optional features cover what real households have: side ledgers kept out of
household spending (a side business, a shared repair with reimbursements), a cash drawer with
a recurring cash payment, advisor fees, rental income, a savings runway, net worth with
exchange-rate moves separated from real ones, and a budget in two currencies.

## Privacy

Casa was designed so a household can trust it with everything.

- **Your data stays on your Mac.** Config, projects, budget, notes and the email archive are
  local files. The `.gitignore` excludes all of them, so a fork can't publish them by accident.
- **Secrets live in the macOS Keychain**, never in files, logs or chat.
- **Nothing listens on the network.** The portal binds to 127.0.0.1. Tailscale makes it
  reachable from your own devices, and only from them.
- **ID numbers never leave the Info page.** Passport, residence and health numbers live in a
  git-ignored file that isn't read into emails or sent to Claude. Emails and prompts use
  document names and dates only.
- **Claude only proposes; Python acts.** Replies become actions from a fixed allowlist.
  Nothing can be deleted by a reply. Only trusted senders with a passing DKIM signature count.
- **News is quarantined.** The news call sees no personal data and has no tools, and links
  always come from the feed, never from the model.

What does leave the Mac: calendar titles, project steps, money summaries and household notes
go to the Claude API to write the emails; email goes through the assistant's Gmail account;
Lunch Money and Open-Meteo are read over their APIs.

## Requirements

- A Mac that's on most mornings (macOS 14 or later). Casa wakes it for the brief.
- Python 3.12 or later and the Xcode command-line tools.
- A [Lunch Money](https://lunchmoney.app) account (for the money sections).
- A [Claude API key](https://console.anthropic.com).
- A new Gmail account for the assistant, with an app password.
- Calendars in macOS Calendar (Google, iCloud or Exchange all work) and Apple Reminders.
- Optional: [Tailscale](https://tailscale.com) for the portal on your phones.

## Get started

The easiest way is with [Claude Code](https://claude.com/claude-code). Clone the repo, open
Claude Code in it, and run the setup wizard:

```
git clone https://github.com/nerolabs/casa.git
cd casa
claude
> /casa-setup
```

The wizard checks your Mac, interviews you about your household, writes your config, drafts
your category rules and budget from your Lunch Money history, walks you through the secrets
(you type them into Keychain yourself; they never pass through chat), and sends a test brief.
Later, `/casa-tune` reads your feedback and replies and adjusts the rules.

Prefer to do it by hand? Follow [docs/SETUP.md](docs/SETUP.md).

To look around first, Casa runs on an invented example household out of the box:

```
scripts/bootstrap.sh
./run.sh daily --dry-run --no-llm     # writes out/preview/brief-YYYY-MM-DD.html
./run.sh portal                       # http://127.0.0.1:8787
```

## Documentation

- [docs/SETUP.md](docs/SETUP.md): manual setup, step by step
- [docs/CONFIG.md](docs/CONFIG.md): every setting and money rule
- [docs/PROJECTS.md](docs/PROJECTS.md): family projects with steps, dependencies and dates
- [docs/PORTAL.md](docs/PORTAL.md): the portal over Tailscale, and under your own domain

## Support

Casa is free and MIT-licensed. If it saves your household some hours, you can buy me a coffee.
<!-- TODO: Buy Me a Coffee link -->
(Link coming soon.)

Issues and pull requests are welcome. Please never include real household data in them: use
invented names, as the examples do.
