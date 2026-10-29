# Runs Butler's tick as the student's account, the way its scheduled task would. Used by
# windows-e2e.yml (standard-user) and tests/e2e/windows-signed-update.ps1.
#
# Butler's tasks run only while the student is logged on (LogonType InteractiveToken), as on a
# household's computer. A CI runner can't log a second account on to the desktop: the student
# account runs through run_as.py, which Task Scheduler doesn't count as logged on. So the account
# asks Task Scheduler to run the tick (it accepts, and the task stays "has not run", 267011), and
# then runs the task's own registered action itself, as that account: the same program,
# arguments and folder Task Scheduler would start. Prints which happened. Task Scheduler's own
# launch of the tick is covered by windows-e2e's e2e job, as the runner's (logged-on) account.
#
#   pwsh tests\e2e\windows-tick.ps1 -User "BYB Test" -Out C:\byb\tick.txt     (BYB_PASSWORD set)
param(
    [Parameter(Mandatory)] [string] $User,
    [Parameter(Mandatory)] [string] $Out
)
$ErrorActionPreference = "Stop"
$runAs = Join-Path $PSScriptRoot "run_as.py"

[xml]$task = (schtasks /Query /TN "\Butler\tick" /XML | Out-String)
$principal = $task.Task.Principals.Principal
$exec = $task.Task.Actions.Exec
Write-Host "the tick task: runs as $($principal.UserId), $($principal.LogonType), $($principal.RunLevel): $($exec.Command) $($exec.Arguments)"
# Task Scheduler names the account by its SID.
$sid = (New-Object System.Security.Principal.NTAccount("$env:COMPUTERNAME\$User")).Translate([System.Security.Principal.SecurityIdentifier]).Value
if ($principal.UserId -ne $sid -and $principal.UserId -notlike "*\$User") { Write-Host "FAIL: the tick runs as $($principal.UserId), not $User ($sid)"; exit 1 }
if ($principal.LogonType -ne "InteractiveToken") { Write-Host "FAIL: the tick's logon type is $($principal.LogonType), not InteractiveToken"; exit 1 }
# No RunLevel means the default, LeastPrivilege: a standard account's rights.
if ($principal.RunLevel -and $principal.RunLevel -ne "LeastPrivilege") { Write-Host "FAIL: the tick asks for $($principal.RunLevel)"; exit 1 }

python $runAs --user $User --password-env BYB_PASSWORD --out "$Out.run" -- 'schtasks /Run /TN "\Butler\tick"' | Out-Host
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: $User couldn't ask Task Scheduler to run the tick"; exit 1 }
$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Seconds 3
    $info = schtasks /Query /TN "\Butler\tick" /V /FO LIST | Out-String
} until ($info -notmatch "Last Result:\s+267011" -or (Get-Date) -gt $deadline)
if ($info -notmatch "Last Result:\s+267011") {
    Write-Host "Task Scheduler started the tick itself."
    do { Start-Sleep -Seconds 5; $info = schtasks /Query /TN "\Butler\tick" /V /FO LIST | Out-String } while ($info -match "Status:\s+Running")
    if ($info -notmatch "Last Result:\s+0\b") { Write-Host $info; Write-Host "FAIL: the tick didn't finish cleanly"; exit 1 }
    exit 0
}
Write-Host "Task Scheduler didn't start it: $User isn't logged on to the desktop (a CI runner can't do that). $User runs the task's own action instead."
$command = "start `"`" /wait `"$($exec.Command)`" $($exec.Arguments)"
python $runAs --user $User --password-env BYB_PASSWORD --out $Out --cwd $exec.WorkingDirectory --timeout 1800 -- $command | Out-Host
$code = $LASTEXITCODE
Write-Host "the tick, as ${User}: exit $code"
exit $code
