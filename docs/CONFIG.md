# Configuration

Casa reads three files in `config/`. Each falls back to its `.example.yaml` twin until you
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
| `name` | What the assistant calls itself in subjects, the portal and Siri |
| `timezone` | IANA name, e.g. `Europe/Lisbon`. All schedules follow it |
| `currency.primary` | Lunch Money's primary currency |
| `currency.second` | Optional second currency for budget lines (converted at Assumptions!C5) |
| `email.sender`, `sender_name` | The assistant's Gmail account |
| `email.recipients` | Who gets the emails |
| `email.trusted_senders` | Addresses whose replies are acted on (DKIM must pass) |
| `email.send_hour`, `weekly_day`, `weekly_hour`, `monthly_hour` | When each email goes out |
| `household.adults` | The adults, with the addresses they reply from |
| `household.place` | Your town, for the news prompt |
| `household.about` | A short description of the household for Claude. Never put ID numbers here |
| `household.notes_file` | Facts Claude should know, kept up to date by replies |
| `calendar.include` | Calendar titles to read, as `bin/casa-ek calendars` lists them |
| `calendar.holidays` | Holiday calendars, shown as a note on the day |
| `calendar.write_to` | Where replies add events |
| `people` | Everyone events can belong to (children too), with aliases, owned calendars and a color |
| `projects.in_brief` | Show "Next up" in the morning brief |
| `reminders.todo_list`, `shopping_list` | Apple Reminders lists |
| `news.regions` | Regions in order, each with RSS feeds |
| `news.language` | The language summaries are written in |
| `llm.model`, `effort` | The Claude model and effort level |
| `weather` | Optional: `place`, `latitude`, `longitude`, `unit` |
| `language_learning` | Optional: a phrase of the day for `learner` in `language` at `level` |
| `score_tracker` | Optional: practice-test scores for `person` toward `pass_score`/`out_of` |
| `portal` | Port, public URL, allowed Tailscale logins, login-to-name map |
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
| `rules.savings_account`, `savings_draw_payee` | The savings draw and runway |
| `rules.own_transfer_payees` | Transfers that are your own money moving, never asked about |

Replies keep this file current: when the Sunday letter asks about a charge and you answer,
an override is appended with the date and who answered.

## The budget workbook

Casa reads only input cells, so you can add sheets and formatting of your own.

| Sheet | Cells | Meaning |
|---|---|---|
| Assumptions | C5 | Primary-currency units per unit of the second currency. Updated each morning |
| Assumptions | C6 | A management fee, used by Income rows whose Deduction is the word `fee` |
| Income | A–D from row 5 | Source, currency, gross per month, deduction |
| Expenses | A–F and I from row 5 | Category, item, currency, amount, frequency (`Monthly`, `Annual`, `5-Year`), due month, note |

Annual lines appear in "Due this month" in their month. Annual and five-year lines are
smoothed into a monthly set-aside in the Sunday letter. Before Casa updates the exchange rate,
it saves a dated copy of the workbook in `budget/history/`. Budget suggestions from the
monthly look-back are suggestions only: change the workbook yourself, or ask `/casa-tune`.

## deadlines.yaml

Each deadline has `name`, `date` (`MM-DD`, every year), `who`, `note` and optionally
`from_year`. They appear in the morning brief from 14 days before and in the Sunday letter
from 60 days before.
