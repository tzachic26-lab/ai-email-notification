@echo off
setlocal
cd /d "%~dp0"
call .venv\Scripts\python.exe daily_job_search_email_agent.py %*
exit /b %ERRORLEVEL%
