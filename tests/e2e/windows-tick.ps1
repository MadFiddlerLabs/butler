# Runs Butler's tick as the student's account, the way its scheduled task would. Used by
# windows-e2e.yml (standard-user) and tests/e2e/windows-signed-update.ps1.
#
# Butler's tasks run only while the student is logged on (LogonType InteractiveToken), as on a
# household's computer. A CI runner can't log a second account on to the desktop: the student
# account runs through run_as.py, which Task Scheduler doesn't count as logged on. So the account
# asks Task Scheduler to run the tick (it accepts, and the task stays "has not run", 267011), and
# then runs the task's own registered action itself, as that account: the same program,
# arguments and folder Task Scheduler would start. Prints which happened.
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
if ($principal.UserId -notlike "*\$User" -and $principal.UserId -ne $User) { Write-Host "FAIL: the tick runs as $($principal.UserId), not $User"; exit 1 }
if ($principal.RunLevel -ne "LeastPrivilege") { Write-Host "FAIL: the tick asks for more than a standard account's rights"; exit 1 }

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
