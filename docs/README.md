# Documentation Index

Detailed guides for every agent and subsystem in **AI Email Notification**.

| Guide | Agent / tool | Default schedule |
|-------|----------------|------------------|
| [SETUP.md](SETUP.md) | Full installation & first-run checklist | — |
| [README_JOB_SEARCH.md](README_JOB_SEARCH.md) | CV-based Israeli hi-tech job search | 09:45 & 14:00 |
| [README_PINECONE.md](README_PINECONE.md) | Optional Pinecone semantic dedup | — |
| [README_MULTI_PROFILE.md](README_MULTI_PROFILE.md) | Multiple candidates (profiles) | Per profile |
| [README_TECH_NEWS.md](README_TECH_NEWS.md) | AI/ML tech news digest | 08:15 |
| [README_TOP_NEWS.md](README_TOP_NEWS.md) | Top 5 Israeli stories (24h) | 08:30 |
| [README_ISRAEL_NEWS.md](README_ISRAEL_NEWS.md) | Israeli headlines digest | 08:00 |
| [README_AI_TRAINER.md](README_AI_TRAINER.md) | Daily hands-on AI exercise | 09:00 |
| [README_EMAIL_DELIVERY.md](README_EMAIL_DELIVERY.md) | Gmail + Outlook send pipeline | — |
| [README_SCHEDULING.md](README_SCHEDULING.md) | Windows Task Scheduler setup | — |

## Quick navigation by task

```
Want to…                          Read
────────────────────────────────  ─────────────────────────────
Set up from scratch               SETUP.md
Find jobs matching my CV          README_JOB_SEARCH.md
Run job search for a friend       README_MULTI_PROFILE.md
Fix email not sending             README_EMAIL_DELIVERY.md
Register daily Windows tasks      README_SCHEDULING.md
Understand dedup / history          README_JOB_SEARCH.md → History
```

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Scheduled agents (daily_*_email_agent.py)                       │
│  news · tech · top-5 · AI trainer · job search                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
   RSS + LLM APIs      job_search_api.py    ai_trainer_api.py
   (Gemini / OpenAI)   multi-pass search    exercise generator
         │                   │                   │
         └───────────────────┼───────────────────┘
                             ▼
                    daily_email_send.py
                    Gmail primary → Outlook fallback
                             │
                             ▼
                    HTML email to recipients
```

## Related projects

This repo focuses on **automated email delivery**. For interactive job application workflows (CV tailoring, cover letters), see a separate job-application framework as a companion pattern.
