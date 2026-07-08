# AI / Tech News Email Agent

## What is this?

The **Tech AI News Agent** (`daily_tech_news_email_agent.py`) delivers a daily Hebrew digest of **AI, ML, and technology** headlines from global sources.

| Property | Value |
|----------|-------|
| **Schedule** | 08:15 daily |
| **Task name** | `DailyTechAINewsEmail` |
| **Subject** | `חדשות AI וטכנולוגיה — YYYY-MM-DD` |
| **Max articles** | 8 |
| **Summary length** | 200–400 words each (Hebrew) |

---

## How it works

```
08:15 trigger
      │
      ▼
tech_ai_news_api.py
  • Google News RSS (AI/tech topic)
  • Filter + deduplicate stories
      │
      ▼
LLM cascade (daily_email_vendor.py)
  1. Gemini Lite
  2. Gemini Flash (on Lite failure)
  3. ChatGPT (on both failures)
      │
      ▼
format HTML email → send via Gmail/Outlook
```

The email footer shows which AI provider and tier were used (e.g. `סיכום באמצעות Gemini`).

---

## Setup

```env
DAILY_TECH_NEWS_RECIPIENT=you@example.com
DAILY_TECH_NEWS_TOPIC=בינה מלאכותית — שוק, מוצרים ומגמות

LLM_VENDOR_PRIMARY=gemini
LLM_VENDOR_FALLBACK=openai
LLM_VENDOR_FALLBACK_ENABLED=1
```

Register task:

```powershell
powershell -ExecutionPolicy Bypass -File setup_daily_tech_news_task.ps1
```

---

## Usage

```powershell
# Preview without sending
uv run python daily_tech_news_email_agent.py --dry-run

# Send once (no retry loop)
uv run python daily_tech_news_email_agent.py --no-retry
```

| Output | Path |
|--------|------|
| Log | `logs/daily_tech_news_email.log` |
| Dry-run preview | `logs/daily_tech_news_preview.html` |

---

## Key modules

| File | Role |
|------|------|
| `daily_tech_news_email_agent.py` | Agent entry point |
| `tech_ai_news_api.py` | RSS fetch, filtering, summarization |
| `daily_email_vendor.py` | Gemini → OpenAI tier fallback |
| `rss_fetch.py` | Shared RSS utilities |
| `llm_providers.py` | OpenAI + Gemini clients |

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DAILY_TECH_NEWS_RECIPIENT` | `DAILY_NEWS_RECIPIENT` | Email TO |
| `DAILY_TECH_NEWS_TOPIC` | AI/tech Hebrew topic | RSS search query |
| `GEMINI_SUMMARY_MODEL` | `gemini-2.5-flash-lite` | Tier 1 model |
| `OPENAI_EMAIL_SUMMARY_MODEL` | `gpt-4.1-mini` | Fallback summaries |

---

## Troubleshooting

| Issue | Action |
|-------|--------|
| Empty digest | Topic too narrow; try broader `DAILY_TECH_NEWS_TOPIC` |
| Gemini 429 | Automatic fallback to ChatGPT — check log |
| No email | See [README_EMAIL_DELIVERY.md](README_EMAIL_DELIVERY.md) |

---

## Related

- [README_TOP_NEWS.md](README_TOP_NEWS.md) — Israeli top-5 ranked news
- [README_SCHEDULING.md](README_SCHEDULING.md) — task registration
