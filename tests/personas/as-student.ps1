# A persona run on Windows, as the student's own account: "BYB Test", a standard account with a
# space in its name and no admin rights, as on the owner's test PC (28 Oct 2026) and in
# windows-e2e.yml's standard-user job. The runner's admin only makes the account, hands it the
# harness, the installer and the lessons, and does the one admin step the course has (admin.py);
# everything the student does runs as the account, with its own profile and environment
# (tests/e2e/run_as.py).
#
# A student's computer has no Git, so Git for Windows is hidden for the run and put back after
# (the runner needs it to clean up). What a hosted runner can't give the account, and what stands
# in, is in run_persona.py and in each run.json's `differences`.
#
#   pwsh tests\personas\as-student.ps1 -Out <folder> -Arguments @(<run_persona.py arguments, without --out>)
#   ($env:ANTHROPIC_API_KEY for a paid run; none for --rehearse)
param(
    [Parameter(Mandatory)] [string] $Out,
    [Parameter(Mandatory)] [string[]] $Arguments
)
$ErrorActionPreference = "Stop"
$user = "BYB Test"
$work = "C:\byb"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
function Step($what) { Write-Host ""; Write-Host "== $what" }

Step "a standard account, $user"
$chars = [char[]]"abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
$env:BYB_PASSWORD = "Aa1!" + -join (1..24 | ForEach-Object { $chars | Get-Random })
Write-Host "::add-mask::$env:BYB_PASSWORD"
New-LocalUser -Name $user -Password (ConvertTo-SecureString $env:BYB_PASSWORD -AsPlainText -Force) -PasswordNeverExpires -AccountNeverExpires | Out-Null
Add-LocalGroupMember -Group Users -Member $user
if ((Get-LocalGroupMember Administrators).Name -contains "$env:COMPUTERNAME\$user") { throw "$user is an admin" }

Step "the harness, its Python, the installer and the lessons, where the account can read them"
New-Item -ItemType Directory -Force $work, "$work\admin", "$work\out", "$work\secret" | Out-Null
robocopy $repo "$work\engine" /E /XD .git .venv dist /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copying the harness failed ($LASTEXITCODE)" }
python -m pip install --disable-pip-version-check --quiet anthropic==2.3.0 claude-agent-sdk==0.2.174 pyyaml tzdata==2026.5 pywinpty==3.0.5 pywinauto==0.6.9
if ($LASTEXITCODE -ne 0) { throw "the harness's packages didn't install" }
icacls $work /grant "${user}:(OI)(CI)M" | Out-Null
icacls $env:pythonLocation /grant "${user}:(OI)(CI)RX" /T /C /Q | Out-Null
icacls "$work\secret" /inheritance:r /grant "${user}:(OI)(CI)F" /grant "Administrators:(OI)(CI)F" | Out-Null
if ($env:ANTHROPIC_API_KEY) {
    Set-Content -NoNewline -Encoding ascii "$work\secret\key" $env:ANTHROPIC_API_KEY
}
python -c "import platform; print('this computer:', platform.machine(), platform.platform())"

Step "the admin step the course has (Tailscale's install), for the length of the run"
$admin = Start-Process python -ArgumentList "-X utf8 `"$work\engine\tests\personas\admin.py`" serve `"$work\admin`"" -PassThru -WindowStyle Hidden -RedirectStandardOutput "$work\admin.log" -RedirectStandardError "$work\admin-err.log"

Step "a computer without Git, as a student's is"
$git = "$env:ProgramFiles\Git"
if (Test-Path $git) { Rename-Item $git Git.hidden }

$code = 0
try {
    Step "the persona, as $user"
    $quoted = ($Arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join " "
    $line = "set CI=1&& set BYB_ADMIN_REQUESTS=$work\admin&& `"$env:pythonLocation\python.exe`" -X utf8 `"$work\engine\tests\personas\run_persona.py`" $quoted --key-file `"$work\secret\key`" --out `"$work\out`""
    python "$repo\tests\e2e\run_as.py" --user $user --password-env BYB_PASSWORD --out "$work\harness.txt" --cwd $work --timeout 18000 -- $line
    $code = $LASTEXITCODE
} finally {
    if (Test-Path "$env:ProgramFiles\Git.hidden") { Rename-Item "$env:ProgramFiles\Git.hidden" Git }
    Stop-Process -Id $admin.Id -ErrorAction SilentlyContinue
    Remove-Item -ErrorAction SilentlyContinue "$work\secret\key"
}

Step "what the run left"
New-Item -ItemType Directory -Force $Out | Out-Null
Copy-Item -Recurse -Force "$work\out\*" $Out -ErrorAction SilentlyContinue
foreach ($log in "$work\admin.log", "$work\admin-err.log", "C:\Users\$user\Butler\logs\install.log", "C:\Users\$user\Butler\logs\bootstrap.log", "C:\Users\$user\Butler\logs\setup.log", "C:\Users\$user\Butler\logs\butler.log") {
    Write-Host "--- $log"; Get-Content -ErrorAction SilentlyContinue $log -Tail 40
}
exit $code
