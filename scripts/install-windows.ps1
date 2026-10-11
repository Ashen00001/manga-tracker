# Run Manga Tracker on a Windows PC that sleeps:
#   - "Manga Tracker" task starts the server (windowless) when you log in, restarts it if it crashes
#   - "Manga Tracker Check" task wakes the PC at -WakeHours to run the watchlist check, then lets it sleep again
#   - Tailscale serve publishes it to your tailnet only, over HTTPS, at https://<this-pc>.<tailnet>.ts.net:<Port>
#
# Re-run any time to update. Remove everything with:  .\scripts\install-windows.ps1 -Uninstall
param(
    [int[]]$WakeHours = @(8, 12, 16, 20),
    [int]$Port = 8787,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$tailscale = 'C:\Program Files\Tailscale\tailscale.exe'
$serverTask = 'Manga Tracker'
$checkTask = 'Manga Tracker Check'

if ($Uninstall) {
    foreach ($name in $serverTask, $checkTask) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    }
    if (Test-Path $tailscale) { & $tailscale serve --https=$Port off }
    Write-Host 'Removed the tasks and the Tailscale serve entry.'
    return
}

if (-not (Test-Path $pythonw)) { throw "No virtualenv at $root\.venv. Create it first (see README)." }

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

$server = @{
    TaskName    = $serverTask
    Description = 'Manga Tracker web app (tailnet only). Logs: data\server.log'
    Principal   = $principal
    Action      = New-ScheduledTaskAction -Execute $pythonw -WorkingDirectory $root `
                    -Argument "-m tracker --log data\server.log serve --host 127.0.0.1 --port $Port"
    Trigger     = New-ScheduledTaskTrigger -AtLogOn -User $user
    Settings    = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
                    -MultipleInstances IgnoreNew
}
Register-ScheduledTask @server -Force | Out-Null

$check = @{
    TaskName    = $checkTask
    Description = 'Wakes the PC to check manga stock and send Discord alerts. Logs: data\check.log'
    Principal   = $principal
    Action      = New-ScheduledTaskAction -Execute $pythonw -WorkingDirectory $root `
                    -Argument '-m tracker --log data\check.log check --if-due'
    Trigger     = $WakeHours | ForEach-Object { New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($_)) }
    Settings    = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
}
Register-ScheduledTask @check -Force | Out-Null

Stop-ScheduledTask -TaskName $serverTask -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $serverTask

if (Test-Path $tailscale) {
    & $tailscale serve --bg --https=$Port "http://127.0.0.1:$Port"
} else {
    Write-Warning "Tailscale isn't installed; the app is only reachable at http://127.0.0.1:$Port on this PC."
}

Write-Host "Installed. Wake checks at $(($WakeHours | ForEach-Object { '{0}:00' -f $_ }) -join ', ')."
