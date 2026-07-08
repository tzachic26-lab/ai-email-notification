@echo off
setlocal
cd /d "%~dp0"

set PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    echo ERROR: Python venv not found at .venv\Scripts\python.exe
    echo Run: cd %~dp0 ^&^& uv sync
    pause
    exit /b 1
)

echo Sending daily AI trainer exercise email...
echo (--force allows re-send if today already has an exercise)
"%PYTHON%" daily_ai_trainer_email_agent.py --no-retry --force
set EXIT_CODE=%ERRORLEVEL%
echo.
if %EXIT_CODE%==0 (echo Done.) else (echo Failed with exit code %EXIT_CODE%.)
pause
exit /b %EXIT_CODE%
