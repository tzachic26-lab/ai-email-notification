#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$OutlookDir = "C:\amdocs\mcp-servers\outlook-mcp-server-v4"
$Python = Join-Path $OutlookDir ".venv\Scripts\python.exe"
$App = Join-Path $OutlookDir "app.py"
$LogDir = Join-Path $PSScriptRoot "logs"
$LogFile = Join-Path $LogDir "outlook_auth_server.log"
$Port = 8081

if (-not (Test-Path $Python)) {
    throw "Outlook MCP Python not found: $Python"
}
if (-not (Test-Path $App)) {
    throw "Outlook auth app not found: $App"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$ApplyEnv = Join-Path $PSScriptRoot "apply_network_env.py"
$EmailPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if ((Test-Path $ApplyEnv) -and (Test-Path $EmailPy)) {
    & $EmailPy $ApplyEnv | ForEach-Object {
        $parts = $_ -split "=", 2
        if ($parts.Count -eq 2) {
            Set-Item -Path "env:$($parts[0])" -Value $parts[1]
        }
    }
} else {
    $env:NO_PROXY = "localhost,127.0.0.1,::1"
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Add-Content -Path $LogFile -Value "$(Get-Date -Format o) Auth server already listening on port $Port"
    exit 0
}

$ApplyEnv = Join-Path $PSScriptRoot "apply_network_env.py"
$EmailPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if ((Test-Path $ApplyEnv) -and (Test-Path $EmailPy)) {
    & $EmailPy $ApplyEnv | ForEach-Object {
        $parts = $_ -split "=", 2
        if ($parts.Count -eq 2) {
            Set-Item -Path "env:$($parts[0])" -Value $parts[1]
        }
    }
} else {
    $env:NO_PROXY = "localhost,127.0.0.1,::1"
}

$OutLog = Join-Path $LogDir "outlook_auth_server.out.log"
$ErrLog = Join-Path $LogDir "outlook_auth_server.err.log"

Add-Content -Path $LogFile -Value "$(Get-Date -Format o) Starting Outlook auth server on port $Port"

Start-Process `
    -FilePath $Python `
    -ArgumentList "`"$App`"" `
    -WorkingDirectory $OutlookDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog

Start-Sleep -Seconds 3

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    Add-Content -Path $LogFile -Value "$(Get-Date -Format o) Failed to start auth server on port $Port"
    exit 1
}

Add-Content -Path $LogFile -Value "$(Get-Date -Format o) Auth server running on http://localhost:$Port/signin"
exit 0
