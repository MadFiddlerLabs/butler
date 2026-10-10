# Releasing

A release is a signed, notarized installer package, `Butler.pkg`, attached to a GitHub Release.
The website serves the latest one from
`https://github.com/nerolabs/butler/releases/latest/download/Butler.pkg` under each student's
chosen name (`<Name>.pkg`).

To release, tag and push:

```
git tag v0.1.0
git push origin v0.1.0
```

`.github/workflows/release.yml` then:

1. Builds the universal Calendar helper.
2. Builds the package and signs it.
3. Notarizes the package with Apple and staples the ticket.
4. Publishes the release.

Installed copies pick the release up on their own: the website rebuilds its update manifest
(`/updates/stable.json`) from the latest release, and each Butler checks it overnight (see
docs/UPDATES.md). Write the release notes for households: the first sentences show up in the
brief. Use a major version only for a change the household should agree to first. To stop a bad
release, hold it in the website's `updates/hold.json`.

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
- It includes the Calendar and Reminders helper (`bin/butler-ek`), prebuilt for Apple silicon and
  Intel and signed. Students never need the Xcode tools.
- Its `postinstall` takes the butler's name from the package's filename and opens the guided
  first run (`scripts/first-run.command`) in Terminal. On an upgrade it does neither.

## The tests that guard it

- `tests/test_packaging.py`: names from filenames, postinstall on a first install and an upgrade,
  and the built package's layout.
- `tests/test_first_run.py` and `tests/test_cli.py`: the guided first run and the `butler`
  command, against a fake account API.
- `.github/workflows/macos-e2e.yml`, job `package`: builds the package and installs it into the
  runner's home folder with `installer`, then upgrades it and checks nothing personal changed.

Apple's notarization can only run with the real secrets, so it is checked by the release workflow
itself: `stapler validate`, `pkgutil --check-signature` and `spctl --assess` must all pass.
