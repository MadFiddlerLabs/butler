# Accounts and the download

The contract between the setup app in this repo and the website (buildyourbutler.com, a separate private repo). Both sides are tested against it: here with `tests/fakes/account_api.py`.

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
| `POST /api/auth/start` | `{email, butler_name, client:"setup/<version>"}` | `{request_id, poll_secret, expires_in}`; emails a magic link |
| `GET /verify?t=<token>` | (a page) | "You're in. Go back to your Terminal." It POSTs to `/api/auth/verify` |
| `POST /api/auth/verify` | `{token}` | `{ok:true}`; marks the request verified |
| `POST /api/auth/poll` | `{request_id, poll_secret}` | `{status:"pending"|"verified"|"expired"}`, plus `session_token` once verified (returned once) |
| `GET /api/me` | header `Authorization: Bearer <session>` | `{email, butler_name, progress:[{lesson, status, at}]}` |
| `POST /api/progress` | Bearer + `{lesson, status:"started"|"done"}` | `{ok:true}` |
| `POST /api/auth/logout` | Bearer | `{ok:true}`; revokes the session |

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

## The setup app (engine repo)

- It stores the session in Keychain service `butler-account` (account `butler`), never in a file.
- Lesson ids are shared with the course: `setup.email`, `setup.name`, `setup.town`,
  `setup.first-brief`, `setup.claude`, then the module slugs.
- With no network or a server error, setup still works offline. Progress sync is best-effort.
