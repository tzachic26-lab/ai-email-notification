#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$TaskName = "DailyJobSearchEmail"
$AppDir = $PSScriptRoot
$Python = Join-Path $AppDir ".venv\Scripts\python.exe"
$Script = Join-Path $AppDir "daily_job_search_email_agent.py"
$LogDir = Join-Path $AppDir "logs"

$MorningTime = "09:45"
$AfternoonTime = "14:00"

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

$TriggerMorning = New-ScheduledTaskTrigger -Daily -At $MorningTime
$TriggerAfternoon = New-ScheduledTaskTrigger -Daily -At $AfternoonTime

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$Description = "CV job search email twice daily at $MorningTime and $AfternoonTime (ai_email_notification)"

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($TriggerMorning, $TriggerAfternoon) `
    -Settings $Settings `
    -Principal $Principal `
    -Description $Description `
    -Force | Out-Null

Write-Host "Scheduled task '$TaskName' registered for $MorningTime and $AfternoonTime daily." -ForegroundColor Green
Write-Host "CV:        set JOB_SEARCH_CV_DOCX in .env (e.g. data/cv.docx)" -ForegroundColor Cyan
Write-Host "History:   $AppDir\data\job_search_history.md" -ForegroundColor Cyan
Write-Host "Test now:  cd $AppDir; uv run python daily_job_search_email_agent.py --dry-run" -ForegroundColor Cyan
Write-Host "Logs:      $LogDir\daily_job_search_email.log" -ForegroundColor Cyan
