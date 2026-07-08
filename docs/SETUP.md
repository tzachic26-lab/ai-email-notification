# Setup Guide

Step-by-step instructions for getting **AI Email Notification** running on Windows.

---

## 1. Prerequisites

| Requirement | Purpose |
|-------------|---------|
| **Windows 10/11** | Scheduled tasks, Outlook MCP integration |
| **Python 3.11+** | All agents |
| **[uv](https://docs.astral.sh/uv/)** | Dependency management (`uv sync`) |
| **OpenAI API key** | Summaries, job search, AI trainer, fallback |
| **Google AI API key** | Gemini primary vendor (optional but recommended) |
| **Gmail App Password** | If using Gmail SMTP (`EMAIL_SEND_PROVIDER=gmail`) |
| **Outlook MCP server** | If using Outlook send or Gmail fallback |

Check Python:

```powershell
uv --version
uv run python --version
```

---

## 2. Clone and install

```powershell
git clone https://github.com/tzachic26-lab/ai-email-notification.git
cd ai-email-notification
uv sync
```

---

## 3. Configure environment

Copy the template and fill in your values:

```powershell
copy .env.example .env
```

**Minimum required keys:**

```env
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...

# Recipients
DAILY_NEWS_RECIPIENT=you@example.com
JOB_SEARCH_TO=you@example.com

# Email delivery
EMAIL_SEND_PROVIDER=gmail
EMAIL_SEND_FALLBACK_PROVIDER=outlook
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
GMAIL_TO=you@gmail.com

# Outlook MCP (required for fallback)
OUTLOOK_MCP_DIR=C:\path\to\outlook-mcp-server-v4
```

See [.env.example](../.env.example) for the full list.

> **Security:** Never commit `.env`. Personal CVs, profile JSON, and history files stay local (see `.gitignore`).

---

## 4. Outlook MCP (for Outlook send or Gmail fallback)

1. Install [outlook-mcp-server-v4](https://github.com) (or your org’s copy) and set `OUTLOOK_MCP_DIR` in `.env`.
2. Configure Microsoft Graph credentials inside the MCP project.
3. Start the auth server:

```powershell
powershell -ExecutionPolicy Bypass -File start_outlook_auth_server.ps1
```

4. Sign in at `http://localhost:8081/signin`
5. Test a manual send:

```powershell
uv run python daily_tech_news_email_agent.py --dry-run
uv run python daily_tech_news_email_agent.py --no-retry
```

Details: [README_EMAIL_DELIVERY.md](README_EMAIL_DELIVERY.md)

---

## 5. Job search setup

1. Place your CV at `data/cv.docx` (or PDF) or maintain `data/job_search_cv.md`.
2. Configure search preferences in `.env`:

```env
JOB_SEARCH_CV_DOCX=data/cv.docx
JOB_SEARCH_HOME_LOCATION=Your City, Israel
JOB_SEARCH_LOCATIONS=Jerusalem, Hybrid, Remote
JOB_SEARCH_KEYWORDS=solution architect, senior developer, hi-tech
```

3. Dry run:

```powershell
uv run python daily_job_search_email_agent.py --dry-run
```

Details: [README_JOB_SEARCH.md](README_JOB_SEARCH.md)

---

## 6. Register scheduled tasks

Register all daily agents at once:

```powershell
powershell -ExecutionPolicy Bypass -File setup_all_tasks.ps1
```

Verify:

```powershell
Get-ScheduledTask Daily* | Format-Table TaskName, State
```

Details: [README_SCHEDULING.md](README_SCHEDULING.md)

---

## 7. Quick test checklist

| Step | Command | Expected |
|------|---------|----------|
| 1 | `uv sync` | Dependencies installed |
| 2 | `.env` filled | No missing API key errors |
| 3 | `--dry-run` on any agent | HTML preview in `logs/` |
| 4 | `--no-retry` send | Email received |
| 5 | `setup_all_tasks.ps1` | Tasks show `Ready` |

---

## 8. Network notes

| Environment | Behavior |
|-------------|----------|
| **Home / direct internet** | `AUTO_HTTP_PROXY=1` — direct connection |
| **Corporate VPN** | Auto-detects corp proxy when `genproxy` resolves |
| **Gmail blocked on corp network** | Outlook fallback sends successfully |

Force direct: `HTTP_PROXY=` and `HTTPS_PROXY=` (empty) in `.env`.

---

## Next steps

- [README_JOB_SEARCH.md](README_JOB_SEARCH.md) — job search agent
- [README_MULTI_PROFILE.md](README_MULTI_PROFILE.md) — run for multiple people
- [README_EMAIL_DELIVERY.md](README_EMAIL_DELIVERY.md) — Gmail vs Outlook
