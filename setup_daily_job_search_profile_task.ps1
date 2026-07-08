#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [string]$ProfileId,

    [string]$MorningTime = "10:00",
    [string]$AfternoonTime = "14:15"
)

$ErrorActionPreference = "Stop"

$AppDir = $PSScriptRoot
$ProfileFile = Join-Path $AppDir "data\job_profiles\$ProfileId.json"
if (-not (Test-Path $ProfileFile)) {
    throw "Profile not found: $ProfileFile"
}

$TaskName = "DailyJobSearchEmail_$ProfileId"
$Python = Join-Path $AppDir ".venv\Scripts\python.exe"
$Script = Join-Path $AppDir "daily_job_search_email_agent.py"
$LogDir = Join-Path $AppDir "logs"

if (-not (Test-Path $Python)) {
    throw "Python venv not found at $Python. Run: cd $AppDir; uv sync"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Script`" --profile $ProfileId" `
    -WorkingDirectory $AppDir

$TriggerMorning = New-ScheduledTaskTrigger -Daily -At $MorningTime
$TriggerAfternoon = New-ScheduledTaskTrigger -Daily -At $AfternoonTime

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$Description = "Job search email for profile '$ProfileId' at $MorningTime and $AfternoonTime"

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($TriggerMorning, $TriggerAfternoon) `
    -Settings $Settings `
    -Principal $Principal `
    -Description $Description `
    -Force | Out-Null

Write-Host "Scheduled task '$TaskName' registered ($MorningTime, $AfternoonTime daily)." -ForegroundColor Green
Write-Host "Profile:   $ProfileFile" -ForegroundColor Cyan
Write-Host "Test:      cd $AppDir; uv run python daily_job_search_email_agent.py --profile $ProfileId --dry-run" -ForegroundColor Cyan
Write-Host "Logs:      $LogDir\job_search_${ProfileId}.log (or log_stem from JSON)" -ForegroundColor Cyan
