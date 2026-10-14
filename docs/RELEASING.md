# Releasing

A release is a signed, notarized installer package, `Butler.pkg`, attached to a GitHub Release.
The website serves the latest one from
`https://github.com/nerolabs/butler/releases/latest/download/Butler.pkg` under each student's
chosen name (`<Name>.pkg`).

The version lives only in the tag. The package writes it to `~/Butler/.butler-version`, so
there is no version file to bump in the repository.

## Release checklist

1. **Main is green**: the `tests` and `macos-e2e` workflows, including the `package` and
   `updates` jobs.
2. **The lock is current.** After any change to `requirements.txt`, run `scripts/lock.sh` and
   commit `requirements.lock` (docs/UPDATES.md, "Dependencies"). The `tests` workflow checks it.
3. **Settings need no surprise.** A new setting must default to the old behaviour when it's
   absent. Add a migration in `src/butler/migrations.py` only when a household's file has to
   change (docs/UPDATES.md).
4. **Pick the version.** Patches and minors install overnight on their own. Use a major version
   only for a change the household should agree to first: the brief then asks for an "update"
   reply.
5. **Prove the candidate, signed to signed.** Run the release workflow by hand with the new
   version:

   ```
   gh workflow run release.yml -f version=v0.2.0
   ```

   It builds, signs and notarizes the candidate. Then job `signed-update` installs published
   releases from GitHub Releases, each on its own runner, and lets each one's own updater install
   the candidate. It publishes nothing. The releases are the ones installs really update from
   (job `origins`):

   - the latest release, and
   - the oldest release still offered an update: the first at or after the website manifest's
     `min_version`, or the first signed release (v0.1.0) when there's none.

   Installs skip versions, so the oldest matters: a Mac on v0.1.0 updates straight to the
   candidate. When the two are the same release, it runs once. To stop offering updates from old
   versions, set `min_version` in the website's `updates/hold.json` (its DEPLOY.md); older installs are then asked to
   download Butler again, and the gate starts from there.
6. **Tag with the release notes and push.** The annotated tag's message becomes the release
   notes. Write it for households, two or three plain sentences. The first ones show up in the
   brief ("What's new: …"), so lead with what changes for them.

   ```
   git tag -a v0.2.0 -F notes.txt
   git push origin v0.2.0
   ```

7. **Check the published package** once the workflow finishes:

   ```
   gh release download v0.2.0 -p Butler.pkg
   pkgutil --check-signature Butler.pkg
   spctl -a -vv -t install Butler.pkg
   xcrun stapler validate Butler.pkg
   ```

On a tag, `.github/workflows/release.yml`:

1. Builds the universal Calendar helper.
2. Builds the package and signs it.
3. Notarizes the package with Apple and staples the ticket.
4. Picks the releases to update from: the latest and the oldest still offered an update
   (`origins`, `.github/scripts/update_origins.py`).
5. Runs the signed update from each of them to this package, side by side (`signed-update`).
6. Publishes the release, only if every run of step 5 passed.

Installed copies pick the release up on their own: the website rebuilds its update manifest
(`/updates/stable.json`) from the latest release on its next deploy, and each Butler checks it
overnight (see docs/UPDATES.md). To stop a bad release, hold it in the website's
`updates/hold.json`.

Without the signing secrets it still builds, as `Butler-unsigned.pkg` on a prerelease. The job
summary says which secrets are missing. `/releases/latest` ignores prereleases, so students never
get an unsigned package.

## One-time setup: certificates and secrets

You need the Apple Developer Program's **Account Holder** role. Only that role can create
Developer ID certificates. These steps are yours. Never paste a certificate, password or key into
a chat.

### 1. Two Developer ID certificates

Do this twice: once for **Developer ID Installer**, which signs the package, and once for
**Developer ID Application**, which signs the Calendar helper inside it.

1. On your Mac, open **Keychain Access**. Choose **Keychain Access > Certificate Assistant >
   Request a Certificate From a Certificate Authority**. Enter your email, choose **Saved to
   disk**, and save the `.certSigningRequest` file.
2. Go to <https://developer.apple.com/account/resources/certificates/add>. Under **Software**,
   choose **Developer ID Installer** (the second time, **Developer ID Application**, with the G2
   Sub-CA). Upload the request file, then download the `.cer` file.
3. Double-click the `.cer` file to add it to your login keychain.
4. In Keychain Access, open **My Certificates**. Right-click the new certificate and choose
   **Export**, as a `.p12` file. Use the same strong password for both exports. Name the files
   `DeveloperIDInstaller.p12` and `DeveloperIDApplication.p12`.

### 2. An App Store Connect API key, for notarization

1. Go to <https://appstoreconnect.apple.com/access/integrations/api>. Under **Team Keys**,
   choose **Generate API Key**. Name it "Butler notarization", with **Developer** access.
2. Download the `AuthKey_XXXXXXXXXX.p8` file. Apple lets you download it only once.
3. Note the **Key ID** (next to the key) and the **Issuer ID** (above the list).

### 3. Your Team ID

It's on <https://developer.apple.com/account> under **Membership details**: ten characters, like
`AB12CD34EF`.

### 4. Add the secrets to GitHub

Run these yourself in Terminal, from the folder with the files. Each `gh secret set` reads the
value from a file or from a hidden prompt, so it never appears on screen or in your shell history.

```
base64 -i DeveloperIDInstaller.p12 -o installer.b64
base64 -i DeveloperIDApplication.p12 -o application.b64
base64 -i AuthKey_XXXXXXXXXX.p8 -o apikey.b64

gh secret set MACOS_INSTALLER_P12_BASE64 -R nerolabs/butler < installer.b64
gh secret set MACOS_APP_P12_BASE64 -R nerolabs/butler < application.b64
gh secret set APPLE_API_KEY_P8_BASE64 -R nerolabs/butler < apikey.b64
gh secret set MACOS_P12_PASSWORD -R nerolabs/butler      # prompts: the .p12 export password
gh secret set APPLE_API_KEY_ID -R nerolabs/butler        # prompts: the Key ID
gh secret set APPLE_API_ISSUER_ID -R nerolabs/butler     # prompts: the Issuer ID
gh secret set APPLE_TEAM_ID -R nerolabs/butler           # prompts: the Team ID

rm installer.b64 application.b64 apikey.b64
```

Keep the `.p12` and `.p8` files somewhere safe, such as a password manager, then delete them from
your Downloads folder.

### 5. Check it works

Run the workflow by hand: **Actions > release > Run workflow**. Its summary should say "Signed and
notarized". A run by hand only uploads the package as a build artifact. It publishes nothing.

## What the package does

- It installs into the current user's home folder (`~/Butler`), with no admin password.
- It carries only tracked files, so an upgrade never touches the household's settings, data,
  projects or budget.
- It carries `requirements.lock`, so every install of a version gets the same Python packages,
  checked by hash, and `scripts/lib/uv.sh`, which pins uv by checksum.
- It includes the Calendar and Reminders helper (`bin/butler-ek`), prebuilt for Apple silicon and
  Intel and signed. Students never need the Xcode tools.
- It installs the Butler app (`helpers/butler-app/`, built by `packaging/macos/build-app.sh`): a
  universal binary, signed with Developer ID under the hardened runtime like the Calendar helper,
  that opens Terminal on `scripts/butler.command`, which runs `butler resume`. That resumes the
  first run while it isn't finished, and otherwise opens Claude Code on the student's next
  `/butler-setup` step, exactly as `butler claude` does. The payload lays the app in
  `~/Applications/Butler.app`, marked not relocatable, so Installer never moves it into another
  copy elsewhere on the Mac. `postinstall` then places it (`place_app` in `scripts/lib/install.sh`):
  - **An admin account** (`dseditgroup -o checkmember -m <user> admin`) that can write to
    `/Applications` gets it in `/Applications/Butler.app`, owned by the student: copied with
    `ditto`, its signature checked, then swapped in. No password: `/Applications` belongs to the
    admin group. The copy in `~/Applications` is removed.
  - **Any other account** keeps it in `~/Applications`.
  - **A command-line upgrade** (the overnight updater, a rollback) keeps it where it was:
    replaced in `/Applications` if ours is there, left in `~/Applications` otherwise. Installing
    again from the website (Installer.app) places it afresh, so an admin's v0.5.0 app moves to
    `/Applications`.
  - **Another app at `/Applications/Butler.app`** (another bundle identifier, or a link) is never
    touched: ours stays in `~/Applications`.
  - **Anything that fails** (the copy, the signature, the swap) leaves the old app working and
    ours in `~/Applications`. Nothing fails the install, and `logs/install.log` names the place.
- Its `postinstall` decides from the Butler folder (`scripts/lib/install.sh`):
  - **fresh** (nothing of the household's yet): takes the butler's name from the package's
    filename and opens the guided first run (`scripts/first-run.command`).
  - **started** or **finished** (`data/first-run-started`, `data/first-run-done`, or settings
    from before those markers): keeps the name and opens `scripts/welcome-back.command`, which
    offers to resume (the default) or start fresh.
  - **a command-line install** (`installer`, so the overnight updater and CI): opens nothing.

  It opens Terminal in the logged-in user's session (`launchctl asuser`), adds `butler` to the
  PATH in `~/.zprofile`, and writes every outcome to `logs/install.log`, including a window that
  couldn't open.

## The tests that guard it

- `tests/test_packaging.py`: names from filenames; what postinstall finds (fresh, started,
  finished) and what it opens for each, through `launchctl asuser`, with a fallback and a log
  line when Terminal won't open; the Done screen's words; the built package's layout (the lock,
  the scripts it opens, and the Butler app, signed, universal and not relocatable); and the lock
  matching `requirements.txt`.
- `tests/e2e/installer-opens.sh`, in `macos-e2e.yml`'s `package` job: runs the built package's
  postinstall the way Installer.app does (no `COMMAND_LINE_INSTALL`) in the runner's logged-in
  session, and checks that Terminal really ran the first run, then the welcome back over a
  started first run. Then the Butler app: in `/Applications` (the runner's account is an admin),
  owned by the runner, signature intact, no copy left in `~/Applications`; opened, Terminal runs
  `butler resume`, which resumes the unfinished first run; with the first run finished, it opens
  Claude Code (a stub that records its folder and arguments) on Module 1's welcome in ask-first
  mode. It can't click through Installer.app itself, so the GUI's own way of running the script
  stays unproven; the script and its log line are the same. A non-admin account, another app
  called Butler and a failed copy are covered with fakes in `tests/test_packaging.py`.
- `tests/test_first_run.py` and `tests/test_cli.py`: the guided first run and the `butler`
  command, against a fake account API.
- `.github/workflows/macos-e2e.yml`, job `package`: builds the package and installs it into the
  runner's home folder with `installer`, then upgrades it and checks nothing personal changed
  and the Butler app was replaced in `/Applications`, where it was.

- `tests/e2e/signed-update.sh`, run in release.yml as job `signed-update`: the update students
  really get, signed to signed. It installs a published release from GitHub Releases (the latest,
  and in a second run the oldest still offered an update), then that release's own updater
  installs the new signed, notarized package, with no check relaxed.
  First it must refuse three tampered copies: a checksum mismatch, a changed byte and a repacked,
  unsigned package. Afterwards the household's files are byte for byte the same, `butler update
  --check` reports the new version, the health check passes (today's brief included), and the
  Calendar helper is signed by the team.

Apple's notarization can only run with the real secrets, so it is checked by the release workflow
itself: `stapler validate`, `pkgutil --check-signature` and `spctl --assess` must all pass. For
the same reason `signed-update` runs only there, never on a pull request.

- `tests/test_packaging.py`, `UpdateOriginsTest` and `ReleaseWorkflowTest`: which releases the
  gate updates from (`min_version`, drafts and prereleases left out, never the candidate itself),
  and that publishing waits for every one of them.

The `origins` job reads `min_version` from the live manifest. If the website can't be reached,
the job fails and says so: run it again rather than guess.
