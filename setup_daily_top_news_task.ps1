#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$TaskName = "DailyIsraelTopNewsEmail"
$AppDir = $PSScriptRoot
$Python = Join-Path $AppDir ".venv\Scripts\python.exe"
$Script = Join-Path $AppDir "daily_top_news_email_agent.py"
$LogDir = Join-Path $AppDir "logs"

if (-not (Test-Path $Python)) {
    throw "Python venv not found at $Python. Run: cd $AppDir; uv sync"
}
if (-not (Test-Path $Script)) {
    throw "Agent script not found at $Script"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Script`"" `
    -WorkingDirectory $AppDir

$Trigger = New-ScheduledTaskTrigger -Daily -At "08:30"

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Send GPT-ranked top 5 Israeli news (24h) via Outlook at 8:30 AM (ai_email_notification)" `
    -Force | Out-Null

Write-Host "Scheduled task '$TaskName' registered for 8:30 AM daily." -ForegroundColor Green
Write-Host "App dir:   $AppDir" -ForegroundColor Cyan
Write-Host "Test now:  cd $AppDir; uv run python daily_top_news_email_agent.py --dry-run" -ForegroundColor Cyan
Write-Host "Logs:      $LogDir\daily_top_news_email.log" -ForegroundColor Cyan
