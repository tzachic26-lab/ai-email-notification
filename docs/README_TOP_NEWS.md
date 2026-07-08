# Top Israeli News Email Agent (24h Ranked)

## What is this?

The **Top News Agent** (`daily_top_news_email_agent.py`) identifies and summarizes the **5 most important Israeli news stories from the last 24 hours**, ranked by national significance.

| Property | Value |
|----------|-------|
| **Schedule** | 08:30 daily |
| **Task name** | `DailyIsraelTopNewsEmail` |
| **Subject** | `5 האירועים המרכזיים בישראל — 24 שעות — YYYY-MM-DD` |
| **Stories** | 5 ranked + summarized in Hebrew |

---

## How it works

Unlike the standard news digest (which takes today’s RSS headlines as-is), this agent uses an **LLM ranking step**:

```
08:30 trigger
      │
      ▼
israel_top_news_api.py
  • Fetch candidate stories (24h window)
  • LLM ranks by national importance
  • Summarize top 5
      │
      ▼
Model tier cascade
  1. Gemini Lite  (rank + summarize)
  2. Gemini Flash
  3. ChatGPT (gpt-4.1 rank + gpt-4.1-mini summary)
      │
      ▼
HTML email → send
```

Footer labels distinguish ranking tier: `דירוג וסיכום באמצעות Gemini (...)` or ChatGPT fallback.

---

## Setup

```env
DAILY_TOP_NEWS_RECIPIENT=you@example.com
# Falls back to DAILY_NEWS_RECIPIENT if unset

OPENAI_TOP_NEWS_RANK_MODEL=gpt-4.1
GEMINI_TOP_NEWS_RANK_MODEL=gemini-2.5-flash
```

Register task:

```powershell
powershell -ExecutionPolicy Bypass -File setup_daily_top_news_task.ps1
```

---

## Usage

```powershell
uv run python daily_top_news_email_agent.py --dry-run
uv run python daily_top_news_email_agent.py --no-retry
```

| Output | Path |
|--------|------|
| Log | `logs/daily_top_news_email.log` |
| Preview | `logs/daily_top_news_preview.html` |

---

## Key modules

| File | Role |
|------|------|
| `daily_top_news_email_agent.py` | Agent entry point |
| `israel_top_news_api.py` | 24h fetch, rank, summarize |
| `story_dedup.py` | Deduplicate similar headlines |
| `daily_email_vendor.py` | Tier fallback orchestration |

---

## Content filtering

Shared with other Israeli news agents:

- **Excluded source:** Haaretz
- **Excluded topics:** gossip, crime blotter, entertainment, sports, lifestyle
- **Source normalization:** e.g. israelhayom → ישראל היום

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DAILY_TOP_NEWS_RECIPIENT` | `DAILY_NEWS_RECIPIENT` | Recipients |
| `GEMINI_TOP_NEWS_LITE_MODEL` | `GEMINI_LITE_MODEL` | Tier 1 |
| `GEMINI_TOP_NEWS_FLASH_MODEL` | `GEMINI_FLASH_MODEL` | Tier 2 |
| `OPENAI_TOP_NEWS_RANK_MODEL` | `gpt-4.1` | OpenAI ranking |

---

## Troubleshooting

| Issue | Action |
|-------|--------|
| Same stories as 08:00 news email | Expected overlap; ranking differs |
| Ranking seems off | Try forcing OpenAI: `LLM_VENDOR_PRIMARY=openai` |
| Quota errors | Fallback enabled by default |

---

## Related

- [README_ISRAEL_NEWS.md](README_ISRAEL_NEWS.md) — simpler 5-article digest at 08:00
- [README_TECH_NEWS.md](README_TECH_NEWS.md) — global AI/tech news
