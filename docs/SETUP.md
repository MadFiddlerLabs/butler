# Manual setup

This is what `/butler-setup` does for you in Claude Code, step by step, and what the course at
[buildyourbutler.com](https://buildyourbutler.com) teaches. Allow about an hour. Each step can be
checked before moving on.

**With a screen reader.** The guided first run (`butler setup`) and the `butler` command switch
to plain output when VoiceOver is running or `CLAUDE_AX_SCREEN_READER` is set: words only, with
no colour, tick marks, middle dots or screen clearing. `BUTLER_PLAIN=1` asks for it anyway, and
`BUTLER_PLAIN=0` turns it off. Every prompt says what to type before its `›` marker.

## 1. Install

The installer downloads the newest release to `~/Butler` and sets up Python:

```
curl -fsSL https://raw.githubusercontent.com/MadFiddlerLabs/butler/main/scripts/install.sh | bash
```

Or, to work on Butler itself, clone it:

```
git clone https://github.com/MadFiddlerLabs/butler.git
cd butler
scripts/bootstrap.sh
```

`bootstrap.sh` creates `.venv` with [uv](https://docs.astral.sh/uv/), which also provides
Python, so no Xcode tools are needed yet. It installs exactly the packages in `requirements.lock`
and removes anything else from `.venv`. To develop, install `requirements-dev.txt` afterwards. `./run.sh preview` then shows a first brief.

Then build `bin/butler-ek`, a small helper that reads macOS Calendar and Reminders. It needs
the Xcode command line tools (`xcode-select --install`):

```
helpers/butler-ek/build.sh
bin/butler-ek calendars
```

The first time it runs, macOS asks for Calendar access, and `bin/butler-ek reminder-lists` asks
for Reminders. Allow both.

The grant belongs to `butler-ek` itself, whatever starts it. By default macOS asks the app that
started a command-line tool, so a grant made in Terminal might not cover the 07:00 run that
launchd starts. To avoid that, `butler-ek` runs itself again once at startup with
responsibility disclaimed (`responsibility_spawnattrs_setdisclaim`, a private libSystem call
that other calendar tools use too). The dialog then names `butler-ek`, and so does its entry in
System Settings > Privacy & Security > Calendars. If a future macOS drops the call, the helper
runs as before and macOS asks the app that started it.

A helper signed ad hoc (built here, or packaged without the Developer ID secrets) looks new to
macOS after each rebuild, so macOS asks again. A Developer ID signature keeps the same identity
from one build to the next.

## 2. Create the assistant's mailbox

Butler sends from its own Gmail account and reads replies there.

1. Create a new Gmail account for the assistant, used only by Butler.
2. Turn on 2-Step Verification for it.
3. Create an app password: Google Account > Security > App passwords.

## 3. Store the secrets in Keychain

Each command opens a Mac dialog for the value, so it never appears in your shell history, on
screen or in a chat:

```
scripts/store-secret.sh butler-gmail "The Gmail app password"
scripts/store-secret.sh butler-anthropic "Your Claude API key"
scripts/store-secret.sh butler-lunchmoney "Lunch Money > Settings > Developers > access token"
```

Running one again replaces the stored value.

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
Mac next wakes. The weekly letter and the monthly look-back have no wake of their own. If the Mac
sleeps through one, it goes out on the first tick in the next two days and says it's late.

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

If you installed with the package, also move the Butler app to the Bin. It's in
`/Applications/Butler.app` on an administrator's account, or `~/Applications/Butler.app`
otherwise. Then move the `~/Butler` folder to the Bin, after keeping anything of yours from it.
