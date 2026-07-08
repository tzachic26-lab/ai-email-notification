# Windows Scheduling Guide

## What is this?

All daily agents run as **Windows Task Scheduler** jobs. PowerShell setup scripts in the project root register triggers, Python paths, and working directories.

---

## Default schedule

| Time | Task name | Script | Guide |
|------|-----------|--------|-------|
| **08:00** | `DailyIsraelNewsEmail` | `daily_news_email_agent.py` | [README_ISRAEL_NEWS.md](README_ISRAEL_NEWS.md) |
| **08:15** | `DailyTechAINewsEmail` | `daily_tech_news_email_agent.py` | [README_TECH_NEWS.md](README_TECH_NEWS.md) |
| **08:30** | `DailyIsraelTopNewsEmail` | `daily_top_news_email_agent.py` | [README_TOP_NEWS.md](README_TOP_NEWS.md) |
| **09:00** | `DailyAITrainerEmail` | `daily_ai_trainer_email_agent.py` | [README_AI_TRAINER.md](README_AI_TRAINER.md) |
| **09:45 & 14:00** | `DailyJobSearchEmail` | `daily_job_search_email_agent.py` | [README_JOB_SEARCH.md](README_JOB_SEARCH.md) |
| **09:45 & 14:00** | `DailyJobSearchEmail_<id>` | + `--profile <id>` | [README_MULTI_PROFILE.md](README_MULTI_PROFILE.md) |
| **At logon** | `OutlookAuthServerAtLogon` | `start_outlook_auth_server.ps1` | [README_EMAIL_DELIVERY.md](README_EMAIL_DELIVERY.md) |

---

## Register all tasks

```powershell
cd c:\amdocs\ai_email_notification
powershell -ExecutionPolicy Bypass -File setup_all_tasks.ps1
```

Registers: tech news, top news, AI trainer, job search, Outlook auth at logon.  
*(Israeli news at 08:00 is skipped by default — enable separately.)*

---

## Register individually

```powershell
powershell -ExecutionPolicy Bypass -File setup_daily_tech_news_task.ps1
powershell -ExecutionPolicy Bypass -File setup_daily_top_news_task.ps1
powershell -ExecutionPolicy Bypass -File setup_daily_ai_trainer_task.ps1
powershell -ExecutionPolicy Bypass -File setup_daily_job_search_task.ps1
powershell -ExecutionPolicy Bypass -File setup_daily_job_search_profile_task.ps1 -ProfileId myname
powershell -ExecutionPolicy Bypass -File setup_outlook_auth_at_logon.ps1
```

---

## Verify tasks

```powershell
Get-ScheduledTask Daily* | Format-Table TaskName, State
```

Expected state: **Ready** (or **Running** during execution).

---

## Run a task immediately

```powershell
Start-ScheduledTask -TaskName DailyJobSearchEmail
Start-ScheduledTask -TaskName DailyTechAINewsEmail
```

---

## How tasks are configured

Each setup script creates:

| Setting | Value |
|---------|-------|
| **Execute** | `.venv\Scripts\python.exe` |
| **Arguments** | Agent script path (+ `--profile` if applicable) |
| **Working directory** | Project root |
| **Principal** | Current user, Interactive logon |
| **Settings** | Start when available, 4h max runtime |
| **Triggers** | Daily at configured time(s) |

---

## Retry behavior (in Python, not Scheduler)

Agents use `run_with_scheduled_retry()` from `daily_email_send.py`:

| Setting | Default |
|---------|---------|
| Max attempts | 3 |
| Delay between attempts | 600 seconds (10 min) |

Scheduler fires **once** per trigger; retries happen inside the Python process.

---

## Requirements for successful runs

| Requirement | Why |
|-------------|-----|
| PC **on** at trigger time | Scheduler cannot run on sleeping PC* |
| User **logged in** | `LogonType Interactive` |
| `.env` in project root | API keys, recipients |
| Network available | RSS, LLM APIs, email |
| Outlook tokens cached | If using Outlook fallback |

\*Unless wake timers are configured separately in Windows power settings.

---

## Logs

Check after a scheduled run:

```powershell
Get-Content logs\daily_job_search_email.log -Tail 30
Get-Content logs\daily_tech_news_email.log -Tail 30
```

---

## Disable a task

```powershell
Disable-ScheduledTask -TaskName DailyIsraelNewsEmail
```

Or use `disable_daily_news_task.ps1` for the news agent.

---

## Batch files (optional shortcuts)

| File | Runs |
|------|------|
| `run_daily_news_email.bat` | Israeli news agent |
| `run_daily_tech_news_email.bat` | Tech news agent |
| `run_daily_top_news_email.bat` | Top-5 news agent |
| `run_daily_ai_trainer_email.bat` | AI trainer agent |
| `run_daily_job_search_email.bat` | Job search agent |

Useful for manual double-click runs without opening PowerShell.

---

## Related

- [SETUP.md](SETUP.md) — first-time installation
- [README_EMAIL_DELIVERY.md](README_EMAIL_DELIVERY.md) — auth and send failures
