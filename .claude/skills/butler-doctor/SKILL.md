---
name: butler-doctor
description: Health check for a Butler install, in plain language. Checks Python, settings, the Calendar helper and its permissions, Keychain entries (without reading them), the schedule and the Mac's wake time, the log and the last brief, then explains each problem and offers the fix. Use when the user runs /butler-doctor, or says Butler isn't working, the brief didn't arrive, or something looks wrong.
---

# Butler doctor

Someone's butler isn't behaving, and they may not be technical. Find out what's wrong, explain
it in plain words, and fix what you safely can. Read `CLAUDE.md` first: its hard rules apply.
Never print a secret. Never send email without a clear yes: every check here is read-only.

## Run the checks

Run them quietly and collect the results. Don't show raw output.

| Area | How to check | Healthy when |
|---|---|---|
| Python | `.venv/bin/python --version` | 3.12 or later |
| Tests | `PYTHONPATH=src .venv/bin/python -m unittest discover tests` | all pass |
| Settings | `config/settings.yaml` exists; `unittest tests.test_config` | exists and passes |
| Calendar helper | `bin/butler-ek calendars` | prints a list (not a permission error) |
| Reminders | `bin/butler-ek reminder-lists` | prints a list, including `reminders.todo_list` |
| Secrets | `security find-generic-password -s <name> >/dev/null && echo ok`, for `butler-anthropic`, `butler-gmail`, `butler-lunchmoney` | each one their setup has reached is present |
| Schedule | `launchctl list \| grep com.buildyourbutler` | the jobs are listed, with exit status 0 |
| Wake | `pmset -g sched` | a daily wake a minute or so before `email.send_hour` |
| Log | the last 200 lines of `logs/butler.log` | ticks every few minutes; no ERROR lines today |
| Last brief | the newest `out/sent-*` marker and `out/brief-*.html` | today's, once past the send hour |
| Internet | `curl -sI https://api.anthropic.com -o /dev/null -w '%{http_code}'` | any HTTP code |
| Preview | `BUTLER_NO_OPEN=1 ./run.sh preview` | finishes; the footer shows no "Problems this morning" |

Skip checks for steps they haven't reached yet (`/butler-setup` with no step works out where
they are). Something not connected yet is normal mid-course, not a problem.

## Common problems and fixes

| What you see | What it means | Fix |
|---|---|---|
| "update … didn't happen" in the log, or the brief says it went back | An update was refused or rolled back; the log line says why | Read `data/updates/state.json` and the log; `butler update --check` shows what's available. Never relax the signature check |
| Calendar permission error | macOS isn't letting Butler read Calendar | System Settings > Privacy & Security > Calendars: switch it on. The same for Reminders |
| `bin/butler-ek` missing | The Calendar step isn't done | `/butler-setup calendar` |
| A secret is missing | It was never stored, or stored under another name | `scripts/store-secret.sh <name> "..."` |
| Gmail "Username and Password not accepted" in the log, or "queued" and "outbox" lines | The app password is wrong or was revoked. Built emails wait in `data/outbox/` and go out on their own once Gmail takes them (tried hourly after three refusals; dropped after three days) | Make a new app password; store it again. Don't rebuild or resend by hand |
| Claude API error 401 | The API key is wrong or was deleted | Make a new key in the Console; store it again |
| Claude API error 429, or "credit balance" | The spending limit was reached | Raise the limit in the Console, or wait for next month |
| No ticks in the log | The schedule isn't running | `launchd/install.sh` |
| Ticks, but no brief | The Mac was asleep or offline at send time | Check the wake time and that the Mac is plugged in. A closed laptop may not wake |
| "I couldn't handle …'s reply after 3 tries", or "gave up on" in the log | One reply failed three times, so Butler stopped sending it to Claude. Nothing in it was done | Read the "replies:" log lines before it and fix the cause. Then they send the reply again |
| "brief failed" emails | A section broke | Read the log lines around the error. Explain it, then fix the config |
| The brief is in spam | Gmail doesn't know the butler yet | Mark it "Not spam" and add the butler to contacts |

## Report

Give a short report: one line per area with ✅ or ⚠️, then each problem with what it means
and the fix. Offer to apply fixes that change only local files. Ask first for anything that
sends email, changes the schedule or needs their password.

If you can't find the cause, suggest they use "This step didn't work for me" on the course
page for the module they're on. Remind them to leave out personal details.
