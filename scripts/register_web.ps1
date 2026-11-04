# Registers a Windows scheduled task that keeps the web report (`jobs.py web --serve`) running:
# started at logon and right after registering, restarted if it stops. Listens on 127.0.0.1 only.
# An open page refreshes itself when the SQLite DB changes, so a browser tab can stay open.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_web.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_web.ps1 -Unregister
param(
    [string]$TaskName = "MarketCapRebalancerWeb",
    [int]$Port = 8765,
    [switch]$Unregister
)

if ($Unregister) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed task $TaskName"
    return
}

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\pythonw.exe"   # pythonw: no console window
if (-not (Test-Path $python)) { throw "venv not found: $python (create it first, see README)" }

$action = New-ScheduledTaskAction -Execute $python -Argument "jobs.py web --serve --port $Port" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# ExecutionTimeLimit 0 = no limit (a server runs until logoff); restart a crashed server every minute
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "market-cap-rebalancer: web report on http://127.0.0.1:$Port/" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "registered and started task $TaskName -> http://127.0.0.1:$Port/"
