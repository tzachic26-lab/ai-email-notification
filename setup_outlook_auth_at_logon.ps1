#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$TaskName = "OutlookAuthServerAtLogon"
$AppDir = $PSScriptRoot
$Starter = Join-Path $AppDir "start_outlook_auth_server.ps1"

if (-not (Test-Path $Starter)) {
    throw "Starter script not found: $Starter"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Starter`"" `
    -WorkingDirectory $AppDir

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Start Outlook MCP auth server (localhost:8081) at Windows logon (ai_email_notification)" `
    -Force | Out-Null

Write-Host "Scheduled task '$TaskName' registered - runs at every Windows logon." -ForegroundColor Green
Write-Host "App dir:   $AppDir" -ForegroundColor Cyan
Write-Host "Auth URL:  http://localhost:8081/signin" -ForegroundColor Cyan
Write-Host "Logs:      $AppDir\logs\outlook_auth_server.log" -ForegroundColor Cyan
