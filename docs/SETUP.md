# Manual setup

This is what `/butler-setup` does for you in Claude Code. Allow about an hour. Each step can be
checked before moving on.

## 1. Install

```
git clone https://github.com/nerolabs/butler.git
cd butler
scripts/bootstrap.sh
```

`bootstrap.sh` creates `.venv`, installs the Python packages and builds `bin/butler-ek`, a small
helper that reads macOS Calendar and Reminders. The first time it runs, macOS asks for Calendar
and Reminders access. Allow both.

Check that it sees your calendars:

```
bin/butler-ek calendars
```

## 2. Create the assistant's mailbox

Butler sends from its own Gmail account and reads replies there.

1. Create a new Gmail account for the assistant, used only by Butler.
2. Turn on 2-Step Verification for it.
3. Create an app password: Google Account > Security > App passwords.

## 3. Store the secrets in Keychain

Each command prompts for the value, so it never appears in your shell history or on screen.

```
security add-generic-password -s butler-gmail -a butler -w          # the Gmail app password
security add-generic-password -s butler-anthropic -a butler -w      # your Claude API key
security add-generic-password -s butler-lunchmoney -a butler -w     # Lunch Money > Settings > Developers
```

To replace one later, add `-U` to update it.

## 4. Write your settings

```
cp config/settings.example.yaml config/settings.yaml
```

Edit `config/settings.yaml`. At minimum:

- `name`, `timezone`, `currency`
- `email`: the assistant's address, the recipients, the trusted senders, the send hour
- `household.adults`: your names and the addresses you reply from
- `household.about`: two or three sentences about your household, for Claude. No ID numbers.
- `calendar.include`, `calendar.write_to` and `people`, using names from `bin/butler-ek calendars`
- `news.regions`: your local paper's RSS feed first

Optional sections (`weather`, `language_learning`, `score_tracker`) switch features on. Delete
a section to switch its feature off. [CONFIG.md](CONFIG.md) explains every setting.

## 5. Set up the money rules and the budget

```
cp config/category_map.example.yaml config/category_map.yaml
cp budget/budget.example.xlsx budget/budget.xlsx
```

In `category_map.yaml`, map each of your Lunch Money categories to a budget category, and list
the categories you want paced day by day in `rules.day_to_day`. In `budget.xlsx`, replace the
example lines with yours: one row per expense on the Expenses sheet, and your income on the
Income sheet. Category names must match between the two files.

Run the checks:

```
PYTHONPATH=src .venv/bin/python -m unittest discover tests
```

## 6. Try it

```
./run.sh daily --dry-run
open out/preview/brief-$(date +%F).html
```

Problems appear as a line at the bottom of the brief. Logs are in `logs/butler.log`.

When it looks right, send one. It goes to every recipient, so start with only yourself in
`email.recipients` if you want a private first look:

```
./run.sh daily
```

Reply to it ("add milk to shopping"), then process the reply:

```
./run.sh replies
```

## 7. Schedule it

```
launchd/install.sh
sudo pmset repeat wakeorpoweron MTWRFSU 06:59:00
```

The first command installs three launchd jobs: a tick every five minutes (replies, and each
email when it's due), a trigger one minute after the send hours, and the portal. The second
wakes the Mac a minute before the brief. Use your `send_hour`. Scheduled wakes are reliable on
power; on battery with the lid closed, macOS may skip them, and the brief goes out when the
Mac next wakes.

The schedule follows `timezone` in your settings, not the Mac's clock, so travel doesn't move
the brief. Marker files in `out/` (`sent-YYYY-MM-DD`) stop double sends. To resend today,
delete the marker.

## 8. Add the portal (optional)

See [PORTAL.md](PORTAL.md).

## 9. Add your projects and reference file (optional)

- Projects: [PROJECTS.md](PROJECTS.md).
- Phones, IDs and document expiry dates for the Info page: copy
  `examples/reference.local.example.yaml` to `projects/reference.local.yaml` and fill it in.
  Expiry dates feed "Heads up" in the emails; the numbers never leave the Info page.

## Uninstall

```
launchd/uninstall.sh
sudo pmset repeat cancel
security delete-generic-password -s butler-gmail
security delete-generic-password -s butler-anthropic
security delete-generic-password -s butler-lunchmoney
```
