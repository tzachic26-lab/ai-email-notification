#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$AppDir = $PSScriptRoot

Write-Host "Registering daily email tasks from: $AppDir" -ForegroundColor Yellow
Write-Host "(DailyIsraelNewsEmail skipped — use disable_daily_news_task.ps1 / setup_daily_news_task.ps1)" -ForegroundColor DarkGray
Write-Host ""

& (Join-Path $AppDir "setup_daily_tech_news_task.ps1")
Write-Host ""
& (Join-Path $AppDir "setup_daily_top_news_task.ps1")
Write-Host ""
& (Join-Path $AppDir "setup_daily_ai_trainer_task.ps1")
Write-Host ""
& (Join-Path $AppDir "setup_daily_job_search_task.ps1")
Write-Host ""
& (Join-Path $AppDir "setup_outlook_auth_at_logon.ps1")

Write-Host ""
Write-Host "All tasks registered from ai_email_notification." -ForegroundColor Green
Write-Host "Verify:" -ForegroundColor Cyan
Write-Host "  Get-ScheduledTask DailyTechAINewsEmail, DailyIsraelTopNewsEmail, DailyAITrainerEmail, DailyJobSearchEmail | Format-Table TaskName, State"
