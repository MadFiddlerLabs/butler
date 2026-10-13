# Configuration

Butler reads three files in `config/`. Each falls back to its `.example.yaml` twin until you
create your own, so a fresh clone runs on the invented example household.

| File | What it holds |
|---|---|
| `settings.yaml` | Who, when, where: people, email, calendars, news, optional features |
| `category_map.yaml` | How Lunch Money transactions are counted |
| `deadlines.yaml` | Recurring yearly deadlines for "Heads up" |

Plus the budget workbook, `budget/budget.xlsx`, and the household notes Claude reads,
`data/household_notes.md`. Replies add to the notes ("Pat is off the week of 3 November").

## settings.yaml

| Setting | Meaning |
|---|---|
| `name` | Your butler's name: it signs the emails, titles the portal and answers to it in Siri |
| `timezone` | IANA name, e.g. `Europe/Lisbon`. All schedules follow it |
| `updates` | `auto` (default): install new versions overnight; a major version still asks. `notify`: always ask. `off`: never check. See [UPDATES.md](UPDATES.md) |
| `update_hour` | The hour the daily update check runs, in your timezone (default 3). A Mac asleep then checks when it next wakes |
| `update_report` | `true` (default): after each update, tell buildyourbutler.com the version updated from, the version updated to, and whether it worked, so a broken release can be stopped. Nothing else, and linked to no one. `false` turns it off. See [UPDATES.md](UPDATES.md#telling-the-website-how-it-went) |
| `currency.primary` | Lunch Money's primary currency |
| `currency.second` | Optional second currency for budget lines (converted at Assumptions!C5) |
| `currency.scale` | Optional. How many units of your currency Butler's money thresholds count as one euro, such as "100 over pace" or "right on pace within 25". Set from `currency.primary` when absent: 150 for JPY, 20 for MXN, 10 for SEK and NOK, 7 for DKK, 1 otherwise. A rough size, not an exchange rate |
| `email.sender`, `sender_name` | The assistant's Gmail account |
| `email.recipients` | Who gets the emails |
| `email.trusted_senders` | Addresses whose replies are acted on (DKIM must pass) |
| `email.send_hour`, `weekly_day`, `weekly_hour`, `monthly_hour` | When each email goes out. `weekly_day` is Monday = 0 ... Sunday = 6 (default 6); the weekly letter names that day ("Week ending Friday 16 October"). If the Mac is asleep or offline at a letter's or look-back's hour, it goes out on the first tick in the next two days and says it's late |
| `household.adults` | The adults, with the addresses they reply from |
| `household.place` | Your town, for the news prompt |
| `news.interests` | What you want from the news, in a sentence. With `household.place`, the only household detail the news call sees, so keep names out of it |
| `household.about` | A short description of the household for Claude. Never put ID numbers here |
| `household.notes_file` | Facts Claude should know, kept up to date by replies |
| `calendar.include` | Calendar titles to read, as `bin/butler-ek calendars` lists them |
| `calendar.holidays` | Holiday calendars, shown as a note on the day |
| `calendar.write_to` | Where replies add events |
| `people` | Everyone events can belong to (children too), with aliases, owned calendars and a color (`color`, optional `color_dark` for the portal in dark mode). Names are drawn in their color, so a light one such as Google's Flamingo is darkened until it reaches 4.5:1 against the page |
| `projects.in_brief` | Show "Next up" in the morning brief |
| `projects.appointment_keywords` | Optional: words that mark a project step as an appointment or document note. Those steps go to Claude when it flags what to prepare. Write them in your steps' language. Default: `bring, appointment, document, passport, visa, permit, office`. An empty list sends none |
| `reminders.todo_list`, `shopping_list` | Apple Reminders lists |
| `news.regions` | Regions in order, each with RSS feeds |
| `news.language` | The language summaries are written in |
| `llm.model`, `effort` | The Claude model and effort level |
| `weather` | Optional: `place`, `latitude`, `longitude`, `unit` |
| `language_learning` | Optional: a phrase of the day for `learner` in `language` at `level` |
| `score_tracker` | Optional: practice-test scores for `person` toward `pass_score`/`out_of` |
| `portal` | Port, public URL, allowed Tailscale logins, login-to-name map |
| `portal.hosts` | Optional: more names the portal answers to, besides the host in `portal.url` (e.g. the `.ts.net` name next to your own domain). Any other name is refused |
| `budget.fee_label` | What Assumptions!C6 is (shown on the Budget page) |
| `networth` | Thresholds for explaining a change, known reasons, short account labels |

## category_map.yaml

### The three kinds of spending

- **Fixed monthly expenses**: budget lines whose category is *not* in `rules.day_to_day`,
  plus anything with `fixed: true` or listed in `rules.fixed_lines`. Reported as paid and
  still to come against the plan.
- **Daily spending**: categories in `rules.day_to_day`. Paced through the month and trended.
- **One-offs**: overrides with `one_off: true`. Counted in the month, named, and kept out of
  both. Charts leave them out and say so.

### Sections

| Section | Meaning |
|---|---|
| `map` | Lunch Money category to budget category. `EXCLUDE` means not household spending |
| `overrides` | Per-payee rules that win over everything else (see the example's comments) |
| `ledgers` | Side ledgers kept out of household spending, with optional budget and reimbursements |
| `cash_drawer` | Cash withdrawals that top up a drawer, and a recurring cash payment out of it |
| `cash_funding` | Transfers that fund a cash account (not spending) |
| `managed_fees` | Advisor fees taken from investments: reported, not budgeted |
| `drift_ignore` | Payees the weekly drift watch skips |
| `off_book` | Budget lines paid outside Lunch Money's accounts |
| `rules.ignore_accounts`, `ignore_institutions` | Never spending (investments, retirement) |
| `rules.netted_from_rent` | Budget lines taken out of rental income before the payout |
| `rules.no_suggestions` | Daily categories the monthly look-back leaves alone |
| `rules.rental_income_account` | Credits here are rental income |
| `rules.lump_funding` | Annual lines and the account that pays them |
| `rules.savings_account`, `savings_draw_payee` | The savings draw and runway. Without `savings_draw_payee`, any money leaving the savings account counts as a draw |
| `rules.own_transfer_payees` | Transfers that are your own money moving, never asked about |

Replies keep this file current: when the weekly letter asks about a charge and you answer,
an override is appended with the date and who answered.

## The budget workbook

Butler reads only input cells, so you can add sheets and formatting of your own.

| Sheet | Cells | Meaning |
|---|---|---|
| Assumptions | C5 | Primary-currency units per unit of the second currency. Updated each morning |
| Assumptions | C6 | A management fee, used by Income rows whose Deduction is the word `fee` |
| Income | A–D from row 5 | Source, currency, gross per month, deduction |
| Expenses | A–F and I from row 5 | Category, item, currency, amount, frequency (`Monthly`, `Annual`, `5-Year`), due month, note |

Annual lines appear in "Due this month" in their month. Annual and five-year lines are
smoothed into a monthly set-aside in the weekly letter. Before Butler updates the exchange rate,
it saves a dated copy of the workbook in `budget/history/`.

### Applying a budget suggestion by reply

The monthly look-back suggests a new daily-spending budget for categories whose trend is well
off their budget. Reply to the look-back to apply one:

| Reply | What changes |
|---|---|
| "Transport" or "apply the Transport suggestion" | The category's one Monthly line becomes the suggested amount |
| "Transport 160" | That line becomes 160 |
| "Food: Groceries" | In a category with several lines, Groceries changes so Food reaches the suggestion |
| "Food: Groceries 800" | Groceries becomes 800 |

What keeps it safe:

- Only replies from `email.trusted_senders` with a passing DKIM signature count, as for every
  reply.
- Only the Amount cell (column D) of one Monthly line changes. Fixed lines, off-book lines and
  the cash drawer's line are never touched. Nothing is deleted.
- Before the change, Butler copies the workbook to
  `budget/history/budget_<date>_reply.xlsx` (`_2`, `_3` for more the same day).
- Python reads the figure, not Claude, and the figure must appear in the reply. Figures are in
  the primary currency; a line priced in the second currency converts at Assumptions!C5.
- The category's new budget must be between half the lower and double the higher of its
  current budget and its trend.
- Suggestions stay open for 40 days, until the next look-back replaces them.
- Anything unclear or out of range changes nothing. The confirmation, sent to everyone, asks
  for what's missing. When something changes, it says the line, old and new amounts, and where
  the backup is.

This works once the household has its own `budget/budget.xlsx` (the example is never edited)
and at least one trusted sender. Until then the look-back says to edit the workbook instead.
You can always change the workbook yourself, or ask `/butler-tune`.

## deadlines.yaml

Each deadline has `name`, `date` (`MM-DD`, every year), `who`, `note` and optionally
`from_year`. They appear in the morning brief from 14 days before and in the weekly letter
from 60 days before.
