# The window a household's install opens: checks it's running `butler setup` as the account that
# installed, and that the first run has started (data\first-run-started) within 60 seconds. Then
# closes that window and undoes the start, so later checks begin from a fresh first run.
# Used by windows-e2e.yml's standard-user job, for BYB Test and for the runner's own account.
#
#   pwsh tests\e2e\windows-first-window.ps1 -Butler "C:\Users\BYB Test\Butler" -User "BYB Test"
param(
    [Parameter(Mandatory)] [string] $Butler,
    [Parameter(Mandatory)] [string] $User
)
$ErrorActionPreference = "Stop"
$started = Join-Path $Butler "data\first-run-started"
$pattern = [regex]::Escape($Butler) + '\\bin\\\.\.\\run\.py" cli setup\s*$'
$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Seconds 1
    $setup = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -match $pattern })
} until (($setup -and (Test-Path $started)) -or (Get-Date) -gt $deadline)
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' or Name = 'cmd.exe'" | ForEach-Object {
    [pscustomobject]@{ Id = $_.ProcessId; User = (Invoke-CimMethod -InputObject $_ -MethodName GetOwner).User; CommandLine = $_.CommandLine }
} | Format-Table -AutoSize -Wrap | Out-Host
if (-not $setup) { Write-Host "FAIL: no window is running $Butler's butler setup"; exit 1 }
$owner = (Invoke-CimMethod -InputObject $setup[0] -MethodName GetOwner).User
if ($owner -ne $User) { Write-Host "FAIL: butler setup runs as $owner, not $User"; exit 1 }
if (-not (Test-Path $started)) { Write-Host "FAIL: butler setup didn't start the first run within 60 seconds"; exit 1 }
$window = Get-CimInstance Win32_Process -Filter "ProcessId = $($setup[0].ParentProcessId)"
while ($window -and $window.Name -ne "cmd.exe") { $window = Get-CimInstance Win32_Process -Filter "ProcessId = $($window.ParentProcessId)" }
if ($window) { taskkill /T /F /PID $window.ProcessId | Out-Null }
Remove-Item $started
Write-Host "the window runs butler setup as $User, and the first run started"
