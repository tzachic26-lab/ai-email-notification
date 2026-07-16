# Israeli News Email Agent

## What is this?

The **Israeli News Agent** (`daily_news_email_agent.py`) sends a daily Hebrew digest of **today’s top Israeli headlines** from Google News RSS — up to 5 articles, summarized in Hebrew.

| Property | Value |
|----------|-------|
| **Schedule** | 08:00 daily |
| **Task name** | `DailyIsraelNewsEmail` |
| **Subject** | `חדשות ישראל מהיום — YYYY-MM-DD` |
| **Max articles** | 5 |

> **Note:** This task may be **disabled** if you use only the ranked top-5 (08:30) and tech news agents. Re-enable with `setup_daily_news_task.ps1`.

---

## How it works

```
08:00 trigger
      │
      ▼
news_headlines_api.py
  • Google News RSS (Israeli topic)
  • Filter hard news, exclude Haaretz / gossip
  • Summarize each article (Hebrew, 200–400 words)
      │
      ▼
Gemini Lite → Flash → ChatGPT fallback
      │
      ▼
HTML email
```

Also powers the optional **Gradio UI** and **FastAPI REST API** when run manually from `news_headlines_api.py`.

---

## Setup

```env
DAILY_NEWS_RECIPIENT=you@example.com
DAILY_NEWS_TOPIC=חדשות ישראל

LLM_VENDOR_PRIMARY=gemini
LLM_VENDOR_FALLBACK=openai
```

Register task:

```powershell
powershell -ExecutionPolicy Bypass -File setup_daily_news_task.ps1
```

Disable:

```powershell
powershell -ExecutionPolicy Bypass -File disable_daily_news_task.ps1
```

---

## Usage

### Daily email

```powershell
uv run python daily_news_email_agent.py --dry-run
uv run python daily_news_email_agent.py --no-retry
```

| Output | Path |
|--------|------|
| Log | `logs/daily_news_email.log` |
| Preview | `logs/daily_news_preview.html` |

### Interactive UI (optional)

```powershell
uv pip install gradio
uv run python news_headlines_api.py
```

Browser UI with per-article follow-up Q&A.

### REST API (optional)

```powershell
uv pip install uvicorn
uv run python news_headlines_api.py --api
```

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/headlines` | GET/POST | Fetch + summarize |
| `/followup` | POST | Q&A on an article |

Docs: `http://127.0.0.1:8000/docs`

---

## Key modules

| File | Role |
|------|------|
| `daily_news_email_agent.py` | Scheduled email agent |
| `news_headlines_api.py` | RSS, summarization, UI, API |
| `rss_fetch.py` | RSS fetching |
| `llm_providers.py` | LLM vendors |

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DAILY_NEWS_RECIPIENT` | `you@example.com` | Comma-separated TO |
| `DAILY_NEWS_TOPIC` | `חדשות ישראל` | RSS search topic |
| `DAILY_NEWS_HARD_ONLY` | `1` | Exclude culture, gossip, leisure, sports |
| `OPENAI_EMAIL_SUMMARY_MODEL` | `gpt-4.1-mini` | OpenAI summaries |

---

## News filtering

| Rule | Detail |
|------|--------|
| Excluded source | Haaretz |
| Excluded content | gossip, culture, leisure, entertainment, sports, lifestyle, crime blotter |
| Hard news only | `DAILY_NEWS_HARD_ONLY=1` (default) — politics, security, economy, policy |
| Normalization | Channel/source name cleanup for display |

---

## Troubleshooting

| Issue | Action |
|-------|--------|
| No articles early morning | RSS may be sparse — retry later |
| Hebrew console garbled | Log **files** are UTF-8; console encoding is cosmetic |
| Follow-up Q&A fails | Requires `OPENAI_API_KEY` |

---

## Related

- [README_TOP_NEWS.md](README_TOP_NEWS.md) — LLM-ranked top 5 (24h)
- [README_TECH_NEWS.md](README_TECH_NEWS.md) — AI/tech global news
