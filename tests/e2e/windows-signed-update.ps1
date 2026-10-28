# A Windows update as a household gets it: a standard account ("BYB Test", no admin rights)
# installs an older Butler, and that Butler's own scheduled tick finds the new one in the
# manifest overnight and installs it. Nothing is relaxed: the updater checks the signature with
# WinVerifyTrust and the publisher's name, as on any computer.
#
# release.yml (job windows-signed-update) runs it on a signed candidate, from a published
# release's Butler-Setup.exe, with real signatures. windows-e2e.yml (job standard-user-update)
# runs it on every change, with test installers signed by a certificate the runner's admin has
# trusted machine-wide first: the one step a student never does, since a real certificate
# authority is already trusted on every Windows computer.
#
# The runner's admin makes the account, hands it the files and writes the manifest the website
# would serve. Everything Butler does runs as the account: the install, the household's files,
# the schedule, the tick and the update (tests/e2e/run_as.py).
#
#   pwsh tests\e2e\windows-signed-update.ps1 -New <Butler-Setup.exe> -NewVersion v0.5.7 -OldVersion v0.5.6 [-Old <exe>]
#
# Without -Old it downloads OldVersion's Butler-Setup.exe from GitHub Releases (GH_TOKEN). A
# release from before Windows has none: the summary says so, and nothing is tested.
param(
    [Parameter(Mandatory)] [string] $New,
    [Parameter(Mandatory)] [string] $NewVersion,
    [Parameter(Mandatory)] [string] $OldVersion,
    [string] $Old = ""
)
$ErrorActionPreference = "Stop"
$user = "BYB Test"
$work = "C:\byb"
$b = "C:\Users\$user\Butler"
$runAs = Join-Path $PSScriptRoot "run_as.py"
function Fail($why) { Write-Host "FAIL: $why"; Show-Logs; exit 1 }
function Step($what) { Write-Host ""; Write-Host "== $what" }
function Show-Logs {
    foreach ($log in "install.log", "bootstrap.log", "scheduled.log", "butler.log") {
        Write-Host "-- $log"; Get-Content -ErrorAction SilentlyContinue "$b\logs\$log" -Tail 40 | Out-Host
    }
}
function As-Student($out, $command, $cwd = $work, $timeout = 900) {
    python $runAs --user $user --password-env BYB_PASSWORD --out (Join-Path $work $out) --cwd $cwd --timeout $timeout -- $command | Out-Host
    return $LASTEXITCODE
}
function Version { if (Test-Path "$b\.butler-version") { (Get-Content -Raw "$b\.butler-version").Trim() } else { "" } }

New-Item -ItemType Directory -Force $work | Out-Null

if (-not $Old) {
    Step "$OldVersion's Windows installer, from GitHub Releases"
    gh release download $OldVersion --repo $env:GITHUB_REPOSITORY --pattern Butler-Setup.exe --dir (Join-Path $work "old") 2>&1 | Out-Host
    $Old = Join-Path $work "old\Butler-Setup.exe"
    if (-not (Test-Path $Old)) {
        "### Windows: $OldVersion has no Butler-Setup.exe (it's from before Windows), so there's no update from it to test" >> $env:GITHUB_STEP_SUMMARY
        Write-Host "$OldVersion has no Windows installer: nothing to update from."
        exit 0
    }
}
foreach ($exe in $Old, $New) {
    $sig = Get-AuthenticodeSignature $exe
    Write-Host "$exe : $($sig.Status), $($sig.SignerCertificate.Subject)"
    if ($sig.Status -ne "Valid") { Fail "$exe isn't validly signed ($($sig.Status))" }
}

Step "a standard account, BYB Test"
if (-not $env:BYB_PASSWORD) {
    $chars = [char[]]"abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    $env:BYB_PASSWORD = "Aa1!" + -join (1..24 | ForEach-Object { $chars | Get-Random })
    Write-Host "::add-mask::$env:BYB_PASSWORD"
    New-LocalUser -Name $user -Password (ConvertTo-SecureString $env:BYB_PASSWORD -AsPlainText -Force) -PasswordNeverExpires -AccountNeverExpires | Out-Null
    Add-LocalGroupMember -Group Users -Member $user
}
if ((Get-LocalGroupMember Administrators).Name -contains "$env:COMPUTERNAME\$user") { Fail "BYB Test is an admin" }
Copy-Item $Old (Join-Path $work "old.exe") -Force
Copy-Item $New (Join-Path $work "Butler-Setup-$NewVersion.exe") -Force
icacls $work /grant "${user}:(OI)(CI)M" | Out-Null
icacls $env:pythonLocation /grant "${user}:(OI)(CI)RX" /T /C /Q | Out-Null

Step "BYB Test installs $OldVersion"
if ((As-Student "install-old.txt" "$work\old.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=$work\install-old-log.txt") -ne 0) {
    Get-Content -ErrorAction SilentlyContinue "$work\install-old-log.txt" -Tail 40; Fail "$OldVersion's installer failed"
}
if ((Version) -ne $OldVersion) { Fail "$OldVersion isn't installed (found '$(Version)')" }

Step "a household's files, and the manifest the website would serve"
# Written by the account, as its own files. BUTLER_UPDATE_MANIFEST points the tick at this
# manifest, and BUTLER_API at a port nothing listens on, so no test outcome reaches the website.
# Both go in the account's own environment, which its scheduled tasks start with.
$notes = "- the Johnsons' plumber is Rui (invented)"
$setup = @(
    "copy /y `"%USERPROFILE%\Butler\config\settings.example.yaml`" `"%USERPROFILE%\Butler\config\settings.yaml`"",
    "(if not exist `"%USERPROFILE%\Butler\data`" mkdir `"%USERPROFILE%\Butler\data`")",
    "(echo $notes)> `"%USERPROFILE%\Butler\data\household_notes.md`"",
    "setx BUTLER_UPDATE_MANIFEST $work\stable.json",
    "setx BUTLER_API http://127.0.0.1:9/api"
) -join " & "
if ((As-Student "household.txt" $setup) -ne 0) { Fail "the household's files couldn't be written" }
$household = (Get-FileHash "$b\data\household_notes.md").Hash
$newExe = Join-Path $work "Butler-Setup-$NewVersion.exe"
@{ available = $true; version = $NewVersion; notes = "Test release $NewVersion."
   windows = @{ url = $newExe; sha256 = (Get-FileHash -Algorithm SHA256 $newExe).Hash.ToLower(); size = (Get-Item $newExe).Length } } |
    ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $work "stable.json")

Step "BYB Test installs the schedule; its tick checks for updates and installs $NewVersion"
if ((As-Student "schedule.txt" ".venv\Scripts\python.exe -X utf8 run.py schedule install" $b) -ne 0) { Fail "schedule install failed" }
pwsh (Join-Path $PSScriptRoot "windows-tick.ps1") -User $user -Out (Join-Path $work "tick.txt")
if ($LASTEXITCODE -ne 0) { Fail "the tick, as BYB Test, exited $LASTEXITCODE" }
$info = schtasks /Query /TN "\Butler\tick" /V /FO LIST | Out-String
if ($info -notmatch "Run As User:\s+.*BYB Test") { Fail "the tick doesn't run as BYB Test" }
if ((Version) -ne $NewVersion) { Fail "the tick didn't update $OldVersion to $NewVersion (still '$(Version)')" }
if ((Get-FileHash "$b\data\household_notes.md").Hash -ne $household) { Fail "the household's notes changed" }
$log = Get-Content -Raw "$b\logs\butler.log"
if ($log -notmatch "update check") { Fail "butler.log doesn't show the tick's update check" }
$state = Get-Content -Raw "$b\data\updates\state.json" | ConvertFrom-Json
Write-Host "last result: $($state.last_result | ConvertTo-Json -Compress)"
if ($state.last_result.status -ne "updated" -or $state.last_result.from -ne $OldVersion -or $state.last_result.to -ne $NewVersion) {
    Fail "the update's recorded result isn't updated from $OldVersion to $NewVersion"
}
if ($log -notmatch "update: now on $([regex]::Escape($NewVersion))") { Fail "butler.log doesn't say it's now on $NewVersion" }
Show-Logs
"### Windows: BYB Test (a standard account) updated itself from $OldVersion to $NewVersion, through its tick (its scheduled task's action, run as BYB Test)" >> $env:GITHUB_STEP_SUMMARY
Write-Host ""
Write-Host "Windows signed update as a standard account, through the tick: all good"
