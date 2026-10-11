# Accounts and the download

The contract between the setup app in this repo and the website (buildyourbutler.com, a separate private repo). Both sides are tested against it: here with `tests/fakes/account_api.py`, there with the engine contract check, which reads `src/butler/course_progress.json`. The website's `docs/ACCOUNTS.md` must say the same: change both together.

Decided by the user, 2026-10-10:
- The visitor names their butler on the homepage, and the download starts at once.
- The download is a signed, notarized macOS package. The user is already enrolled in the Apple
  Developer Program.
- The package opens a Terminal setup. It captures the email by magic link (Resend), runs a few
  pasted lessons that build the folder and settings, gives immediate value (the first brief),
  then hands off to Claude Code as fast as possible.

## Download and name handoff

- The website's button goes to `/download/<Name>/<Name>.pkg`. The name is URL-encoded and
  limited to letters, digits, spaces, hyphens and apostrophes, at most 24 characters.
  Netlify `_redirects` rewrites (status 200) `/download/*` to the current package file, so the
  browser saves it as `<Name>.pkg`.
- Store the package in Netlify as `/files/Butler-latest.pkg`. Until the first signed release
  exists, the rewrite target is a placeholder page saying "coming soon".
- In the package's postinstall, `$1` is the package path. Its basename, minus `.pkg` and any
  browser suffix like ` (1)`, is the butler's name. "Butler" or anything invalid means "ask".
  postinstall writes `~/Butler/.butler-name`.
- The package installs to the current user's home (a productbuild distribution with
  `enable_currentUserHome`), so no admin password is needed.

## The account API (Netlify Functions, under https://buildyourbutler.com/api/)

All bodies are JSON. Return clear `error` strings. Rate-limit by email and IP.

| Call | Body | Returns |
|---|---|---|
| `POST /api/auth/start` | `{email, butler_name, client:"setup/<version>", terms_version, terms_accepted_at}` | `{request_id, poll_secret, expires_in}`; emails a magic link, stores the Terms acceptance |
| `GET /verify?t=<token>` | (a page) | "You're in. Go back to your Terminal." It POSTs to `/api/auth/verify` |
| `POST /api/auth/verify` | `{token}` | `{ok:true, client:"setup"|"web"}`; marks the request verified |
| `POST /api/auth/poll` | `{request_id, poll_secret}` | `{status:"pending"|"verified"|"expired"}`, plus `session_token` once verified (returned once) |
| `GET /api/me` | header `Authorization: Bearer <session>` | `{email, butler_name, nudges, progress:[{lesson, status, at}]}` |
| `POST /api/progress` | Bearer + `{lesson, status:"started"|"done", at?}` | `{ok:true}`. Only the course's lesson ids (else `400 bad_lesson`); idempotent; a done lesson stays done; `at` (ISO 8601, within a year) is when it happened |
| `POST /api/prefs` | Bearer + `{nudges: true|false}` | `{ok:true}`; the weekly lesson email, asked once after sign-in, off unless the student says yes |
| `POST /api/auth/logout` | Bearer | `{ok:true}`; revokes the session |
| `POST /api/account/delete` | Bearer + `{confirm: true}` | `{ok:true}`; deletes the account, its progress and every session |

The website also signs students in itself (`client:"web"`, no butler name, the session as an
HttpOnly cookie instead of a token), takes lists of lessons and an `undone` status for its
checkboxes, and has a page to delete the account. The setup app doesn't use those.

- Tokens are 32 random bytes, base64url. Store only their SHA-256. Magic links expire in 15
  minutes and work once. Sessions last until revoked.
- Storage is Netlify Blobs: stores `users` (keyed by the email's hash), `auth`, `sessions`.
  Keep the email itself only in `users`.
- Email goes through Resend, with env `RESEND_API_KEY` and `MAIL_FROM` =
  `Butler <hello@buildyourbutler.com>`. The user sets the env vars and verifies the domain.
  Never handle the key.
- The magic-link email is plain and warm, names the butler, and says "If you didn't ask for
  this, ignore it."
- Privacy: we hold emails, butler names and lesson progress. Nothing from the household.

## The Terms

The homepage downloads at once, so the Terms are agreed to in the Terminal setup, at the email
step: "By continuing, you agree to the Terms (https://buildyourbutler.com/terms/) and Privacy
(https://buildyourbutler.com/privacy/)." then "Continue? [Y/n]". "n" exits kindly, and nothing is
sent. The agreement is recorded in `~/Butler/data/terms.json` (`terms_version` from the repo's
`TERMS_VERSION` file, `terms_accepted_at` in ISO 8601 UTC) and goes with every
`/api/auth/start`, so an agreement made offline reaches the server with the first sign-in. A new
`TERMS_VERSION` asks again.

## The setup app (engine repo)

- It stores the session in Keychain service `butler-account` (account `butler`), never in a file.
- Lesson ids are the course's: `setup.email`, `setup.name`, `setup.town`, `setup.first-brief`,
  `setup.claude`, then `m<module>-<words>` for every other lesson.
- With no network or a server error, setup still works offline. Progress is queued and sent
  later (below).

## Progress that marks itself

When a `/butler-setup` step's check passes on the Mac, Butler reports every lesson of that
step's module as `done`. The student never ticks anything. `src/butler/progress.py` does it.

**One mapping.** `src/butler/course_progress.json` maps each step to its module and that
module's lesson ids. The website's engine contract check fails its CI when the map drifts from
the course: a step must point at the module whose page names it, with exactly its lessons in
order. `tests/test_progress.py` checks this side: every step mapped, in course order, with the
module its skill section names.

**Module-level, because that's what the engine can know.** The engine sees results in the
Butler folder, not which lesson pages someone read. Each check is the module's win, the end of
its last practical lesson, so a passing check means the module is done.

| Step | Module | Done when (all of it) |
|---|---|---|
| `install` | 2 | `config/settings.yaml` has a real name (not "Butler"), the adults, the town, its own news regions (not the example's), and a preview brief exists. The first run never touches news, so Module 0 alone never passes it |
| `calendar` | 3 | `bin/butler-ek` exists and `calendar.include` isn't empty |
| `brain` | 4 | Keychain has `butler-anthropic` and `household.about` isn't empty |
| `inbox` | 5 | Keychain has `butler-gmail`, `email.sender` and `email.recipients` are set, a real brief was built for sending (`out/brief-*.html`, not a preview) and launchd has `com.buildyourbutler.butler` |
| `replies` | 6 | `data/processed_replies.txt` isn't empty: the butler acted on a reply |
| `money` | 7 | Keychain has `butler-lunchmoney`, and `config/category_map.yaml` and `budget/budget.xlsx` exist |
| `projects` | 8 | `projects/` has a project of the household's own (`*.local.yaml` doesn't count) |
| `letters` | 9 | A weekly letter (`out/weekly-*.html` or `out/preview/weekly-*.html`) and a monthly look-back (`out/monthly-*.html`) exist |
| `phone` | 11 | `portal.url` is set |

Keychain checks ask only whether an entry exists, never its value. A check that can't run (no
`security` or `launchctl`, an unreadable settings file) hasn't passed. Starting a step reports
nothing. Once a step has passed, it stays passed. Modules 1, 10 and 12 have no setup step; the
website's ticks cover them.

**When it runs.** On every five-minute tick, and before every `butler` command. The
`/butler-setup` skill also runs `bin/butler progress` when a step's check passes, so it's told
at once.

**What leaves the Mac.** `POST /api/progress {lesson, status:"done", at}` per lesson, with the
session from Keychain (`butler-account`). `at` is when the check passed. Nothing else: no
settings, no names, no file contents. The Module 0 lessons the `butler` command finishes
(`setup.name`, `setup.town`, `setup.first-brief`, and `setup.claude` as `started`) go the same
way.

**Offline or signed out.** What can't be sent waits in `data/progress.json`: lesson ids, statuses
and times, plus when each step passed. The next tick or `butler` command sends it, oldest first,
and stops at the first failure. After a failure the tick waits 30 minutes before trying again;
a `butler` command tries at once. A lesson the server refuses (`400`, `404`) is dropped so it
can't block the rest; `401` and server errors keep everything queued. Nothing is sent twice,
and `done` always wins over `started`. Nothing here raises or slows the student down.

**`butler progress`** lists each step as sent, done and waiting to send, or not done yet, plus
the Module 0 lessons sent, and says why anything is waiting (signed out: "type `butler setup`";
offline: "I'll try again").

**The website's side.** When the setup app's report completes a module on the account, the site
counts `module_completed` with `via: "butler"` in its analytics (no email, no account key), and
the weekly lesson email skips ahead past the modules the butler finished.
