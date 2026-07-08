# AI Email Notification

Automated daily email agents: Israeli news digests, AI/tech headlines, hands-on AI trainer exercises, and **CV-based Israeli hi-tech job search** — delivered via Gmail or Outlook with Gemini/OpenAI summarization.

## Documentation

**Full guides:** [docs/README.md](docs/README.md)

| Guide | Description |
|-------|-------------|
| [docs/SETUP.md](docs/SETUP.md) | Installation & first-run checklist |
| [docs/README_JOB_SEARCH.md](docs/README_JOB_SEARCH.md) | Job search agent (dedup, quality, CLI) |
| [docs/README_MULTI_PROFILE.md](docs/README_MULTI_PROFILE.md) | Multiple candidates / profiles |
| [docs/README_EMAIL_DELIVERY.md](docs/README_EMAIL_DELIVERY.md) | Gmail + Outlook pipeline |
| [docs/README_SCHEDULING.md](docs/README_SCHEDULING.md) | Windows Task Scheduler |

---

## What This Project Does

| Component | Description | Default schedule |
|-----------|-------------|------------------|
| **Job search email** | CV-matched Israeli hi-tech jobs (LinkedIn + boards, dedup history) | 09:45 & 14:00 |
| **AI/tech news email** | Up to 8 AI/ML stories from global tech outlets (Gemini first, OpenAI fallback) | 08:15 daily |
| **Top-5 ranked email** | LLM-ranked most important Israeli stories from the last 24 hours | 08:30 daily |
| **AI trainer email** | Daily hands-on AI engineering exercise (deep-reasoning model) | 09:00 daily |
| **Israeli news email** | Top 5 Israeli headlines from today, summarized in Hebrew | 08:00 daily |
| **News UI** (optional) | Gradio browser app for browsing and follow-up Q&A | Manual |
| **REST API** (optional) | FastAPI endpoints for headlines and follow-up questions | Manual |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Daily email agents                          │
│  daily_news_email_agent.py      ← Gemini first, OpenAI fallback │
│  daily_top_news_email_agent.py  ← Gemini first, OpenAI fallback │
│  daily_tech_news_email_agent.py                                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  news_headlines_api.py / tech_ai_news_api.py /                  │
│  israel_top_news_api.py                                         │
│  • Google News RSS → filter → OpenAI/Gemini summarize           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTML report
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  daily_email_send.py → outlook_send_helper.py                   │
│  • Writes temp HTML → calls Outlook MCP v4                      │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  C:\amdocs\mcp-servers\outlook-mcp-server-v4                    │
│  • Microsoft Graph OAuth (localhost:8081)                       │
│  • send_mail via OutlookClient                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

- **Windows** (scheduled tasks and Outlook MCP are Windows-oriented)
- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** package manager
- **OpenAI API key** with access to `gpt-4.1-nano`, `gpt-4.1-mini`, and `gpt-4.1`
- **Outlook MCP server** installed at:
  ```
  C:\amdocs\mcp-servers\outlook-mcp-server-v4
  ```
  with its own `.venv` and Microsoft Graph app credentials configured
- **Corporate network** (uses Amdocs proxy by default)

---

## Installation

```powershell
cd c:\amdocs\ai_email_notification
uv sync
```

Create a `.env` file in the project root:

```env
# Required (both keys recommended for morning fallback)
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...

# Morning emails: Gemini first, ChatGPT fallback on failure
LLM_VENDOR_PRIMARY=gemini
LLM_VENDOR_FALLBACK=openai
LLM_VENDOR_FALLBACK_ENABLED=1

# Models
OPENAI_EMAIL_SUMMARY_MODEL=gpt-4.1-mini
GEMINI_SUMMARY_MODEL=gemini-2.5-flash-lite
GEMINI_LITE_MODEL=gemini-2.5-flash-lite
GEMINI_FLASH_MODEL=gemini-2.5-flash
GEMINI_TOP_NEWS_LITE_MODEL=gemini-2.5-flash-lite
GEMINI_TOP_NEWS_FLASH_MODEL=gemini-2.5-flash
OPENAI_TOP_NEWS_RANK_MODEL=gpt-4.1

# Gemini options
GEMINI_GROUNDING=1
GEMINI_CALL_DELAY_SECONDS=10
GEMINI_MAX_RETRY_ATTEMPTS=2

# Recipients (comma- or semicolon-separated)
DAILY_NEWS_RECIPIENT=you@amdocs.com,other@gmail.com
DAILY_TECH_NEWS_RECIPIENT=you@amdocs.com
DAILY_TOP_NEWS_RECIPIENT=you@amdocs.com

# Optional topic overrides
DAILY_NEWS_TOPIC=חדשות ישראל
DAILY_TECH_NEWS_TOPIC=בינה מלאכותית — שוק, מוצרים ומגמות

# Optional retry behavior for scheduled runs
DAILY_EMAIL_RETRY_DELAY_SECONDS=600
DAILY_EMAIL_MAX_ATTEMPTS=3
```

### Optional: UI and REST API extras

The Gradio UI and uvicorn API server are not in `pyproject.toml` by default. Install when needed:

```powershell
uv pip install gradio uvicorn
```

---

## Outlook Authentication (One-Time Setup)

Emails are sent through the Outlook MCP server, which requires Microsoft Graph OAuth.

### 1. Start the auth server

```powershell
powershell -ExecutionPolicy Bypass -File start_outlook_auth_server.ps1
```

Or open in a browser after it starts:

```
http://localhost:8081/signin
```

### 2. Sign in interactively

Run a manual send once from an interactive terminal (not a scheduled task) so the browser auth flow can complete:

```powershell
uv run python daily_news_email_agent.py --no-retry
```

### 3. Register logon task (recommended)

Keeps the auth server available after Windows login:

```powershell
powershell -ExecutionPolicy Bypass -File setup_outlook_auth_at_logon.ps1
```

### Outlook environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OUTLOOK_SEND_NON_INTERACTIVE` | `1` when no TTY | Scheduled tasks skip browser auth |
| `OUTLOOK_SEND_ALLOW_BROWSER` | unset | Set to `1` to allow browser even without TTY |

---

## Running Email Agents Manually

All agents support `--dry-run` (fetch + build HTML, no send) and `--no-retry` (single attempt, no 30-minute retry).

### Israeli news (5 articles from today)

```powershell
uv run python daily_news_email_agent.py --dry-run
uv run python daily_news_email_agent.py --no-retry
```

- **Subject:** `חדשות ישראל מהיום — YYYY-MM-DD`
- **Log:** `logs\daily_news_email.log`
- **Preview:** `logs\daily_news_preview.html`

### AI/ML tech news (up to 8 articles)

```powershell
uv run python daily_tech_news_email_agent.py --dry-run
uv run python daily_tech_news_email_agent.py --no-retry
```

- **Subject:** `חדשות AI וטכנולוגיה — YYYY-MM-DD`
- **Log:** `logs\daily_tech_news_email.log`
- **Preview:** `logs\daily_tech_news_preview.html`

### GPT-ranked top 5 Israeli stories (last 24 hours)

```powershell
uv run python daily_top_news_email_agent.py --dry-run
uv run python daily_top_news_email_agent.py --no-retry
```

- **Subject:** `5 האירועים המרכזיים בישראל — 24 שעות — YYYY-MM-DD`
- **Log:** `logs\daily_top_news_email.log`
- **Preview:** `logs\daily_top_news_preview.html`

### AI Trainer (daily hands-on exercise)

Uses a deep-reasoning model (`gpt-4.1` by default) to generate a fresh AI engineering exercise each day. History is saved to `data\ai_trainer_exercises.md` and included in every email so ideas never repeat.

```powershell
uv run python daily_ai_trainer_email_agent.py --dry-run
uv run python daily_ai_trainer_email_agent.py --no-retry
uv run python daily_ai_trainer_email_agent.py --no-retry --force   # regenerate same day
```

- **Subject:** `AI Trainer — {title} — YYYY-MM-DD`
- **History:** `data\ai_trainer_exercises.md`
- **Log:** `logs\daily_ai_trainer_email.log`
- **Preview:** `logs\daily_ai_trainer_preview.html`

| Variable | Default | Purpose |
|----------|---------|---------|
| `AI_TRAINER_VENDOR` | `openai` | `openai` or `gemini` |
| `AI_TRAINER_MODEL` | `gpt-4.1` | Deep-reasoning model (e.g. `o4-mini`, `gemini-2.5-pro`) |
| `AI_TRAINER_RECIPIENT` | `DAILY_NEWS_RECIPIENT` | Email recipient |
| `AI_TRAINER_GROUNDING` | `1` | Gemini only: use Google Search for latest trends |

---

## Windows Scheduled Tasks

Register all tasks at once:

```powershell
powershell -ExecutionPolicy Bypass -File setup_all_tasks.ps1
```

Or individually:

| Script | Task name | Time |
|--------|-----------|------|
| `setup_daily_news_task.ps1` | `DailyIsraelNewsEmail` | 08:00 |
| `setup_daily_tech_news_task.ps1` | `DailyTechAINewsEmail` | 08:15 |
| `setup_daily_top_news_task.ps1` | `DailyIsraelTopNewsEmail` | 08:30 |
| `setup_daily_ai_trainer_task.ps1` | `DailyAITrainerEmail` | 09:00 |
| `setup_daily_job_search_task.ps1` | `DailyJobSearchEmail` | 09:45 & 14:00 |
| `setup_daily_job_search_profile_task.ps1` | `DailyJobSearchEmail_<id>` | 09:45 & 14:00 |
| `setup_outlook_auth_at_logon.ps1` | `OutlookAuthServerAtLogon` | At logon |

Verify registration:

```powershell
Get-ScheduledTask DailyIsraelNewsEmail, DailyTechAINewsEmail, DailyIsraelTopNewsEmail, DailyAITrainerEmail | Format-Table TaskName, State
```

Trigger a task immediately:

```powershell
Start-ScheduledTask -TaskName DailyIsraelNewsEmail
```

---

## News UI (Gradio)

Interactive Hebrew news browser with per-article follow-up Q&A:

```powershell
uv pip install gradio
uv run python news_headlines_api.py
```

Opens a browser automatically. Features:

- Up to 10 articles from Israeli Google News RSS
- Summaries in Hebrew (200–400 words each)
- Follow-up questions: `gpt-4.1-mini` (standard) or `gpt-4.1` + web search (deep analysis)

---

## REST API (FastAPI)

```powershell
uv pip install uvicorn
uv run python news_headlines_api.py --api
```

Server: `http://127.0.0.1:8000`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/headlines?subject=חדשות+ישראל` | Fetch and summarize headlines |
| `POST` | `/headlines` | Same, JSON body: `{"subject": "חדשות ישראל"}` |
| `POST` | `/followup` | Ask a follow-up about an article |

### Example: fetch headlines

```powershell
curl "http://127.0.0.1:8000/headlines?subject=%D7%97%D7%93%D7%A9%D7%95%D7%AA%20%D7%99%D7%A9%D7%A8%D7%90%D7%9C"
```

### Example: follow-up question

```json
POST /followup
{
  "title": "...",
  "date": "2026-06-18",
  "source": "ynet",
  "summary": "...",
  "question": "מה ההשלכות על ישראל?"
}
```

Interactive docs: `http://127.0.0.1:8000/docs`

---

## OpenAI Models Used

| Use case | Model |
|----------|-------|
| UI / API quick summaries | `gpt-4.1-nano` |
| Email summaries | `gpt-4.1-mini` |
| Follow-up (standard) | `gpt-4.1-mini` |
| Follow-up (deep / web search) | `gpt-4.1` |
| Top-5 story ranking (Gemini) | `gemini-2.5-flash-lite` (or `GEMINI_TOP_NEWS_RANK_MODEL`) |
| Top-5 story ranking (OpenAI fallback) | `gpt-4.1` |

### Article limits

| Agent | Max articles | Summary length |
|-------|--------------|----------------|
| Israeli news email | 5 | 200–400 words |
| Tech AI email | 8 | 200–400 words |
| Top-5 ranked email | 5 | 200–400 words |
| UI / API | 10 | 200–400 words |

---

## Morning Email AI Strategy (Lite → Flash → ChatGPT)

The **08:00** Israeli news email and **08:30** top-5 ranked email share the same 3-tier model cascade:

```
08:00 / 08:30 scheduled run
        │
        ▼
   1. Gemini Lite (gemini-2.5-flash-lite)
        │
        ├─ success ──► email sent
        │
        └─ failure
                │
                ▼
   2. Gemini Flash (gemini-2.5-flash)
        │
        ├─ success ──► email sent · "גיבוי מ-Gemini Lite"
        │
        └─ failure
                │
                ▼
   3. ChatGPT (gpt-4.1-mini summaries; gpt-4.1 ranking for 24h email)
        │
        └─ success ──► email sent · "גיבוי אוטומטי (Gemini Lite → Flash)"
```

Orchestration lives in `daily_email_vendor.py` (`build_with_model_tier_fallback` / `build_with_top_news_tier_fallback`). Each email footer shows which AI and tier were used.

| Scenario | Daily news label | 24h top-news label |
|----------|------------------|-------------------|
| Lite succeeded | `סיכום באמצעות Gemini (gemini-2.5-flash-lite)` | `דירוג וסיכום באמצעות Gemini (...)` |
| Flash (after Lite failed) | `... · גיבוי מ-Gemini Lite` | `... · גיבוי מ-Gemini Lite` |
| ChatGPT (after both failed) | `... · גיבוי אוטומטי (Gemini Lite → Flash)` | `... · גיבוי אוטומטי (Gemini Lite → Flash)` |

**Note:** The **08:15** tech news email still uses OpenAI only. The 24h email uses the same tier cascade for **both ranking and summaries**.

### Disable fallback (Gemini only, fail if quota exhausted)

```env
LLM_VENDOR_FALLBACK_ENABLED=0
```

### Force ChatGPT as primary

```env
LLM_VENDOR_PRIMARY=openai
LLM_VENDOR_FALLBACK=gemini
```

---

## LLM Vendor Support (OpenAI vs Gemini)

Summarization supports two vendors via `llm_providers.py`:

| Vendor | Brand in email | Email model | Env key |
|--------|----------------|-------------|---------|
| OpenAI | **ChatGPT** | `gpt-4.1-mini` | `OPENAI_API_KEY` |
| Gemini (free tier) | **Gemini** | `gemini-2.5-flash` | `GOOGLE_API_KEY` |

For UI/API/comparison tooling, set a single vendor:

```env
LLM_VENDOR=openai   # or: gemini
```

Morning scheduled agents use `LLM_VENDOR_PRIMARY` / `LLM_VENDOR_FALLBACK` instead (see above).

### Side-by-side quality comparison

```powershell
uv run python vendor_comparison.py --articles 3
```

Outputs:

- `logs/vendor_comparison.html` — Hebrew side-by-side report
- `logs/vendor_comparison.json` — scores, latency, token usage

### Comparison results (2026-06-18, 3 articles)

| Metric | OpenAI (`gpt-4.1-mini`) | Gemini (`gemini-2.5-flash`) |
|--------|-------------------------|-----------------------------|
| Judge wins | **2** | 1 |
| Avg objective score | 97.5 | 97.5 |
| Avg latency | 9.1s | **8.0s** |
| Avg tokens/article | **973** | 2,708 |
| Word-count compliance | Strong | Strong (after `thinking_budget=0` fix) |
| Factual discipline | **Better** — stays closer to RSS snippet | Occasionally invents titles/roles |

**Recommendation: keep OpenAI (`gpt-4.1-mini`) as the default** for production emails.

Reasons:

1. More reliable factuality — Gemini sometimes invented details (e.g. wrong ministerial title) not in the source snippet.
2. ~3× lower token usage → lower cost and faster scheduled runs.
3. Won 2 of 3 blind judge evaluations on the same headlines.
4. Follow-up Q&A and web search still depend on OpenAI.

**When Gemini makes sense:** primary vendor on the free tier with ChatGPT as backup when quota or rate limits hit. Requires `thinking_budget=0` (already configured in `llm_providers.py`).

### Gemini free-tier rate limits (approximate)

| Limit | Free tier |
|-------|-----------|
| Requests per minute (RPM) | ~10–15 |
| Requests per day (RPD) | ~250–1,500 (model-dependent) |
| Error when exceeded | `429 RESOURCE_EXHAUSTED` |

The morning fallback handles daily quota exhaustion automatically. For burst testing, use `vendor_comparison.py` sparingly or set `GEMINI_CALL_DELAY_SECONDS=10`.

---

## Project Structure

```
ai_email_notification/
├── docs/                          # Detailed per-tool documentation
│   ├── README.md                  # Documentation index
│   ├── SETUP.md
│   ├── README_JOB_SEARCH.md
│   └── …
├── daily_job_search_email_agent.py
├── job_search_api.py              # Job search LLM passes + HTML
├── job_search_store.py            # Markdown history + dedup
├── job_search_profile.py          # Multi-candidate profiles
├── outlook_mcp_env.py             # Outlook MCP path resolution
├── daily_news_email_agent.py      # Israeli news daily email
├── daily_tech_news_email_agent.py # AI/ML tech daily email (OpenAI only)
├── daily_top_news_email_agent.py  # GPT-ranked top-5 email
├── daily_email_vendor.py          # Gemini-first / OpenAI-fallback orchestration
├── daily_email_send.py            # Shared Outlook send + retry logic
├── llm_providers.py               # OpenAI + Gemini summarization
├── vendor_comparison.py           # Side-by-side quality benchmark
├── outlook_send_helper.py         # CLI wrapper for Outlook MCP
├── news_headlines_api.py          # Core RSS + summarization + UI + API
├── tech_ai_news_api.py            # AI/ML news pipeline
├── israel_top_news_api.py         # 24h GPT-ranked top stories
├── setup_all_tasks.ps1            # Register all scheduled tasks
├── setup_daily_news_task.ps1
├── setup_daily_tech_news_task.ps1
├── setup_daily_top_news_task.ps1
├── setup_outlook_auth_at_logon.ps1
├── start_outlook_auth_server.ps1
├── pyproject.toml
├── uv.lock
├── .env                           # Secrets (not committed)
└── logs/
    ├── daily_news_email.log
    ├── daily_tech_news_email.log
    ├── daily_top_news_email.log
    ├── outlook_auth_server.log
    └── *_preview.html             # Dry-run HTML previews
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes* | — | OpenAI API key (summaries + ranking + fallback) |
| `GOOGLE_API_KEY` | Yes* | — | Gemini API key (Google AI Studio free tier) |
| `LLM_VENDOR_PRIMARY` | No | `gemini` (or `LLM_VENDOR`) | Primary vendor for morning emails |
| `LLM_VENDOR_FALLBACK` | No | `openai` | Fallback vendor when primary fails |
| `LLM_VENDOR_FALLBACK_ENABLED` | No | `1` | Set `0` to disable automatic fallback |
| `LLM_VENDOR` | No | `openai` | Single vendor for UI/API/comparison |
| `GEMINI_LITE_MODEL` | No | `GEMINI_SUMMARY_MODEL` | Tier 1 (Lite) for morning emails |
| `GEMINI_FLASH_MODEL` | No | `gemini-2.5-flash` | Tier 2 (Flash) for morning emails |
| `GEMINI_SUMMARY_MODEL` | No | `gemini-2.5-flash-lite` | Default Gemini summary model |
| `OPENAI_EMAIL_SUMMARY_MODEL` | No | `gpt-4.1-mini` | OpenAI model for email summaries (tier 3) |
| `GEMINI_TOP_NEWS_LITE_MODEL` | No | `GEMINI_LITE_MODEL` | Tier 1 for 24h ranking + summaries |
| `GEMINI_TOP_NEWS_FLASH_MODEL` | No | `GEMINI_FLASH_MODEL` | Tier 2 for 24h ranking + summaries |
| `OPENAI_TOP_NEWS_RANK_MODEL` | No | `gpt-4.1` | OpenAI model for top-5 story ranking (tier 3) |
| `GEMINI_GROUNDING` | No | `1` | Enable Google Search grounding for Gemini |
| `GEMINI_CALL_DELAY_SECONDS` | No | `0` | Delay between Gemini API calls (rate limits) |
| `GEMINI_MAX_RETRY_ATTEMPTS` | No | `2` | Retries on Gemini 429 before fallback |
| `DAILY_NEWS_RECIPIENT` | No | `you@example.com` | Israeli news recipients |
| `DAILY_TECH_NEWS_RECIPIENT` | No | falls back to `DAILY_NEWS_RECIPIENT` | Tech news recipient |
| `DAILY_TOP_NEWS_RECIPIENT` | No | falls back to `DAILY_NEWS_RECIPIENT` | Top-5 news recipient |
| `DAILY_NEWS_TOPIC` | No | `חדשות ישראל` | RSS search topic for Israeli news |
| `DAILY_TECH_NEWS_TOPIC` | No | `בינה מלאכותית — שוק, מוצרים ומגמות` | Tech news topic |
| `DAILY_EMAIL_RETRY_DELAY_SECONDS` | No | `600` (10 min) | Delay before each retry on failure |
| `DAILY_EMAIL_MAX_ATTEMPTS` | No | `3` | Total runs per schedule (1 initial + 2 retries) |
| `HTTP_PROXY` | No | *(auto)* | Set to force corp proxy; empty `HTTP_PROXY=` forces direct |
| `HTTPS_PROXY` | No | *(auto)* | Same as `HTTP_PROXY` |
| `AUTO_HTTP_PROXY` | No | `1` | When `HTTP_PROXY` unset: use corp proxy if `genproxy.corp.amdocs.com` resolves, else direct |
| `NO_PROXY` | No | `localhost,127.0.0.1,::1` | Proxy bypass list |

Off VPN / at home, leave proxy unset (or `AUTO_HTTP_PROXY=1`) — agents use direct internet. On corp network/VPN, corp proxy is auto-detected.

At least one of `OPENAI_API_KEY` or `GOOGLE_API_KEY` is required depending on vendor settings. Morning emails with fallback enabled need **both** keys. Follow-up Q&A requires OpenAI.

### `429 RESOURCE_EXHAUSTED` (Gemini quota)

Morning emails fall back to ChatGPT automatically. Check `logs\daily_*_email.log` for `falling back to openai`. To test fallback immediately:

```powershell
uv run python daily_news_email_agent.py --dry-run
```

---

## Troubleshooting

### `OPENAI_API_KEY is not set`

Create or update `.env` in the project root with a valid key.

### `Outlook MCP Python not found` / `No module named 'outlook_client'`

Set `OUTLOOK_MCP_DIR` in `.env` to your outlook-mcp-server folder. See [docs/README_EMAIL_DELIVERY.md](docs/README_EMAIL_DELIVERY.md).

### `Outlook not authenticated` / send timeout (180s)

1. Start auth server: `start_outlook_auth_server.ps1`
2. Visit `http://localhost:8081/signin` and sign in
3. Run a manual send from an interactive terminal (not scheduled task)

### Scheduled task runs but no email

- PC must be on and logged in at the scheduled time (tasks use `LogonType Interactive`)
- Check `logs\daily_*_email.log` for errors
- Ensure `OutlookAuthServerAtLogon` task is registered

### Unicode logging errors in console

Hebrew log messages may fail on Windows `cp1252` console encoding. This does not affect email delivery — check the log files in `logs/` (UTF-8).

### No articles found

- RSS may be empty early in the day or for narrow topics
- Haaretz is excluded by design (`EXCLUDED_SOURCE_ALIASES`)
- Crime/gossip/entertainment stories are filtered out

### Dry run works but send fails

Authentication issue with Outlook MCP. Re-authenticate interactively, then retry.

---

## News Filtering (Israeli Agents)

Stories are filtered to keep hard news:

- **Excluded sources:** Haaretz
- **Excluded content:** gossip, crime blotter, entertainment, sports, lifestyle
- **Source normalization:** e.g. ערוץ 14 → עכשיו 14, israelhayom → ישראל היום

The top-5 agent uses the same LLM vendor (Gemini or ChatGPT) to rank candidates by national importance and summarize them.

---

## Quick Start Checklist

1. `uv sync`
2. Create `.env` with `OPENAI_API_KEY`, `GOOGLE_API_KEY`, and recipient addresses
3. Confirm Outlook MCP at `C:\amdocs\mcp-servers\outlook-mcp-server-v4`
4. `powershell -ExecutionPolicy Bypass -File start_outlook_auth_server.ps1`
5. Sign in at `http://localhost:8081/signin`
6. Test: `uv run python daily_news_email_agent.py --dry-run`
7. Send: `uv run python daily_news_email_agent.py --no-retry`
8. Schedule: `powershell -ExecutionPolicy Bypass -File setup_all_tasks.ps1`
