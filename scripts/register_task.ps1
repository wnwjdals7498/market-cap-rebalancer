# Registers a Windows scheduled task that runs `jobs.py tick` every 5 minutes.
# tick decides from the exchange calendars whether a signal/execute job is due,
# so the task itself needs no market times (DST and holidays are handled in Python).
# The 45-minute limit leaves room for execute's price ladder (up to ~6 min per ticker); a run
# killed anyway stays RUNNING and is retried once it is older than db.STALE_RUN.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Unregister
param(
    [string]$TaskName = "MarketCapRebalancer",
    [int]$IntervalMinutes = 5,
    [switch]$Unregister
)

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed task $TaskName"
    return
}

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\pythonw.exe"   # pythonw: no console window every 5 minutes
if (-not (Test-Path $python)) { throw "venv not found: $python (create it first, see README)" }

$action = New-ScheduledTaskAction -Execute $python -Argument "jobs.py tick" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "market-cap-rebalancer: jobs.py tick every $IntervalMinutes min" -Force | Out-Null
Write-Output "registered task $TaskName -> $python jobs.py tick (every $IntervalMinutes min)"
