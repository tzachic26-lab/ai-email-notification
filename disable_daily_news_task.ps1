#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$TaskName = "DailyIsraelNewsEmail"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
    Write-Host "Disabled scheduled task '$TaskName' (08:00 daily news email)." -ForegroundColor Green
} else {
    Write-Host "Task '$TaskName' not found - nothing to disable." -ForegroundColor Yellow
}

Write-Host "Active morning emails: DailyTechAINewsEmail (08:15), DailyIsraelTopNewsEmail (08:30)" -ForegroundColor Cyan
