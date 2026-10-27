# Over-the-air updates end to end on Windows, tests/e2e/update.sh's twin, with the real installer
# and the real signature check: install an old Butler into this profile, add a household's files,
# update to a new version from a local manifest, then try one whose code doesn't load and one
# whose brief crashes (both rolled back), and refusals: unsigned, signed by someone else, and a
# signed installer changed after signing.
#
# CI only: it installs into %USERPROFILE%\Butler and trusts two test certificates machine-wide
# (it needs the runner's admin rights for that). The installers are signed with a self-signed
# certificate named for Butler's publisher, so WinVerifyTrust and the publisher check run with
# nothing relaxed; a real install trusts only a real certificate authority. v0.0.2 brings a new
# lock, so the update and the way back each move .venv to another virtualenv (bootstrap.ps1).
#
#   pwsh tests\e2e\update.ps1
$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$work = Join-Path ([IO.Path]::GetTempPath()) ("butler-update-" + [guid]::NewGuid())
New-Item -ItemType Directory $work | Out-Null
$b = Join-Path $env:USERPROFILE "Butler"
function Fail($why) { Write-Host "FAIL: $why"; exit 1 }
function Step($what) { Write-Host ""; Write-Host "== $what" }
if (Test-Path $b) { Fail "$b exists already; this test needs a clean profile" }

function New-TestSigner($name) {
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=$name, O=$name" `
        -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddDays(2)
    $cer = Join-Path $work "$($cert.Thumbprint).cer"
    Export-Certificate -Cert $cert -FilePath $cer | Out-Null
    Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
    Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
    return $cert
}
function Build($checkout, $version) {
    $out = Join-Path $work "build-$version"
    powershell -ExecutionPolicy Bypass -File (Join-Path $checkout "packaging\windows\build-installer.ps1") -Version $version -Out $out | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "building $version failed" }
    return (Join-Path $out "Butler-Setup.exe")
}
function Sign($exe, $cert) {
    $sig = Set-AuthenticodeSignature -FilePath $exe -Certificate $cert -HashAlgorithm SHA256
    if ($sig.Status -ne "Valid") { Fail "signing $exe gave $($sig.Status): $($sig.StatusMessage)" }
}
function Clone($name, $change, $message) {
    $dir = Join-Path $work $name
    git clone -q $repo $dir
    & $change $dir
    git -C $dir -c user.name=ci -c user.email=ci@example.com commit -qam $message
    return $dir
}
function Manifest($version, $exe) {
    $sha = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLower()
    $size = (Get-Item $exe).Length
    @{ available = $true; version = $version; notes = "Test release $version."
       windows = @{ url = "$exe"; sha256 = $sha; size = $size } } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $work "stable.json")
}
function Fingerprint {
    ("data\household_notes.md", "projects\home.yaml", "budget\budget.xlsx" | ForEach-Object { (Get-FileHash -Algorithm SHA256 (Join-Path $b $_)).Hash }) -join " "
}
function Py($code) {
    $env:PYTHONPATH = Join-Path $b "src"
    try { return (& (Join-Path $b ".venv\Scripts\python.exe") -X utf8 -c $code | Out-String).Trim() }
    finally { Remove-Item Env:\PYTHONPATH }
}
function Update($log) {
    & (Join-Path $b "bin\butler.cmd") update --yes *> (Join-Path $work $log)
    $code = $LASTEXITCODE
    Get-Content (Join-Path $work $log) | Out-Host
    return $code
}
function Venv { "$((Get-Item (Join-Path $b ".venv")).Target)" }

Step "two test signers: one named for Butler's publisher, one not"
$butler = New-TestSigner "AELaboratories"
$mallory = New-TestSigner "Mallory Ltd"

Step "build v0.0.1, v0.0.2 with a new lock, a broken v0.0.3, a v0.0.5 whose brief crashes, and spares"
$v1 = Build $repo "v0.0.1"
$newLock = Clone "newlock" { param($d) Add-Content (Join-Path $d "requirements.lock") "# a new lock, as a release brings one" } "a new lock"
$v2 = Build $newLock "v0.0.2"
$broken = Clone "broken" { param($d) Add-Content (Join-Path $d "src\butler\render.py") "`ndef broken(:" } "a broken release"
$v3 = Build $broken "v0.0.3"
# Every module loads, but the brief crashes as it's made: only the brief check catches it.
$crashes = Clone "crashes" { param($d) Add-Content (Join-Path $d "src\butler\render.py") "`n`ndef to_text(*args, **kwargs):`n    raise TypeError(`"a release that breaks at run time`")" } "a release that crashes"
$v5 = Build $crashes "v0.0.5"
$v4 = Build $repo "v0.0.4"      # stays unsigned
$v6 = Build $repo "v0.0.6"      # signed by someone else
$v7 = Build $repo "v0.0.7"      # signed, then changed
foreach ($exe in $v1, $v2, $v3, $v5, $v7) { Sign $exe $butler }
Sign $v6 $mallory
$tampered = Join-Path $work "tampered.exe"
$bytes = [IO.File]::ReadAllBytes($v7); $mid = [int]($bytes.Length / 2); $bytes[$mid] = $bytes[$mid] -bxor 0xFF
[IO.File]::WriteAllBytes($tampered, $bytes)
New-Item -ItemType Directory -Force (Join-Path $work "releases\v0.0.1") | Out-Null
Copy-Item $v1 (Join-Path $work "releases\v0.0.1\Butler-Setup.exe")

Step "install v0.0.1, then a household's files"
$p = Start-Process $v1 -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$work\install-1.txt" -Wait -PassThru
if ($p.ExitCode -ne 0) { Get-Content "$work\install-1.txt" -Tail 40; Fail "v0.0.1's installer exited $($p.ExitCode)" }
if ((Get-Content -Raw "$b\.butler-version").Trim() -ne "v0.0.1") { Fail "v0.0.1 not installed" }
Get-Content "$b\config\settings.example.yaml" | Where-Object { $_ -notmatch '^updates:' -and $_ -notmatch '^update_hour:' } |
    Set-Content -Encoding utf8 "$b\config\settings.yaml"
New-Item -ItemType Directory -Force "$b\data", "$b\projects" | Out-Null
Set-Content -Encoding utf8 "$b\data\household_notes.md" "- the Johnsons' plumber is Rui (invented)"
Copy-Item "$b\examples\projects\home.yaml" "$b\projects\home.yaml"
Copy-Item "$b\budget\budget.example.xlsx" "$b\budget\budget.xlsx"
$settingsBefore = [IO.File]::ReadAllBytes("$b\config\settings.yaml")
$household = Fingerprint
$venv1 = Venv

# BUTLER_COLUMNS: no wrapping, so the checks below see whole sentences. BUTLER_API: nothing listens
# there, so these test updates never report an outcome to the real website (update_report.py).
$env:BUTLER_RELEASES_URL = Join-Path $work "releases"
$env:BUTLER_UPDATE_MANIFEST = Join-Path $work "stable.json"
$env:BUTLER_COLUMNS = "1000"
$env:BUTLER_API = "http://127.0.0.1:9/api"

Step "update to v0.0.2"
Manifest "v0.0.2" $v2
$check = & "$b\bin\butler.cmd" update --check | Out-String
Write-Host $check
if ($check -notmatch "Version v0\.0\.2 is ready") { Fail "--check didn't see v0.0.2" }
if ((Update "update.log") -ne 0) { Fail "the update to v0.0.2 failed" }
if ((Get-Content -Raw "$b\.butler-version").Trim() -ne "v0.0.2") { Fail "v0.0.2 not installed" }
if ((Fingerprint) -ne $household) { Fail "the household's files changed" }
$after = [IO.File]::ReadAllBytes("$b\config\settings.yaml")
if ($after.Length -le $settingsBefore.Length -or [Convert]::ToBase64String($after[0..($settingsBefore.Length - 1)]) -ne [Convert]::ToBase64String($settingsBefore)) {
    Fail "settings.yaml was rewritten, not appended to"
}
if (-not (Select-String -Quiet -Pattern '^updates: auto' "$b\config\settings.yaml")) { Fail "migration 1 didn't add the updates setting" }
if (-not (Test-Path "$b\data\updates\Butler-v0.0.1.exe")) { Fail "the rollback installer isn't kept" }
if (-not (Test-Path "$b\data\updates\Butler-v0.0.2.exe")) { Fail "the installed installer isn't kept" }
$venv2 = Venv
if ($venv2 -eq $venv1) { Fail "v0.0.2's new lock didn't get a virtualenv of its own" }
$line = Py 'from butler import updates; print(updates.brief_lines("Bento"))'
if ($line -notlike "*Bento updated overnight to v0.0.2. What's new: Test release v0.0.2.*") { Fail "brief line: $line" }
Py 'from butler import updates; updates.mark_announced()' | Out-Null

Step "a broken v0.0.3 fails its check and goes back to v0.0.2"
Manifest "v0.0.3" $v3
if ((Update "broken.log") -eq 0) { Fail "the broken update reported success" }
if ((Get-Content -Raw "$work\broken.log") -notmatch "went back to v0\.0\.2") { Fail "no rollback message" }
if ((Get-Content -Raw "$b\.butler-version").Trim() -ne "v0.0.2") { Fail "not back on v0.0.2" }
if ((Fingerprint) -ne $household) { Fail "the household's files changed during rollback" }
if ((Venv) -ne $venv2) { Fail "going back didn't return .venv to v0.0.2's virtualenv" }
Py 'import butler.render' | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "v0.0.2's code wasn't restored" }
if (Test-Path "$b\data\updates\Butler-v0.0.3.exe") { Fail "the broken installer was kept" }
$line = Py 'from butler import updates; print(updates.brief_lines("Bento"))'
if ($line -notlike "*went back to v0.0.2*") { Fail "brief line after rollback: $line" }

Step "a v0.0.5 that loads but breaks the brief fails its check and goes back to v0.0.2"
Py 'from butler import updates; updates.mark_announced()' | Out-Null
Manifest "v0.0.5" $v5
if ((Update "crashes.log") -eq 0) { Fail "the update that breaks the brief reported success" }
if ((Get-Content -Raw "$work\crashes.log") -notmatch "went back to v0\.0\.2") { Fail "no rollback message" }
if ((Get-Content -Raw "$b\.butler-version").Trim() -ne "v0.0.2") { Fail "not back on v0.0.2" }
$problem = Py 'from butler import updates; print(updates.load_state()["last_result"]["problem"])'
if ($problem -notlike "*the morning brief didn't build: the brief (TypeError: a release that breaks at run time)*") { Fail "the problem recorded: $problem" }
if ((Fingerprint) -ne $household) { Fail "the household's files changed during rollback" }
Py 'from butler import daily; assert not daily.brief_check()["failures"]' | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "v0.0.2's brief doesn't build after going back" }
if (Test-Path "$b\data\updates\brief-baseline.json") { Fail "the baseline was left behind" }

foreach ($case in @(@("v0.0.4", $v4, "unsigned"), @("v0.0.6", $v6, "signed by someone else"), @("v0.0.7", $tampered, "changed after signing"))) {
    $version, $exe, $what = $case
    Step "a $version $what is refused"
    Manifest $version $exe
    if ((Update "refused-$version.log") -eq 0) { Fail "the $what installer was installed" }
    if ((Get-Content -Raw "$work\refused-$version.log") -notmatch "isn't signed by Butler's publisher") { Fail "wrong refusal for the $what installer" }
    if ((Get-Content -Raw "$b\.butler-version").Trim() -ne "v0.0.2") { Fail "the version changed" }
    if (Test-Path "$b\data\updates\Butler-$version.exe") { Fail "the refused installer was kept" }
}

Write-Host ""
Write-Host "updates end to end on Windows: all good"
