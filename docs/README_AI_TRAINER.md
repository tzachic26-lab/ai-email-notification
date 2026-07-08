# AI Trainer Email Agent

## What is this?

The **AI Trainer Agent** (`daily_ai_trainer_email_agent.py`) generates a **fresh hands-on AI engineering exercise** each day and emails it to your team. Past exercises are stored in markdown so the LLM never repeats a topic.

| Property | Value |
|----------|-------|
| **Schedule** | 09:00 daily |
| **Task name** | `DailyAITrainerEmail` |
| **Subject** | `AI Trainer — {exercise title} — YYYY-MM-DD` |
| **Default model** | `gpt-4.1` (deep reasoning) |

---

## How it works

```
09:00 trigger
      │
      ▼
Load exercise history (data/ai_trainer_exercises.md)
      │
      ▼
ai_trainer_api.py
  • LLM generates new exercise (avoids past topics)
  • Includes: goal, steps, hints, stretch goals
      │
      ▼
Append to history MD
      │
      ▼
Email HTML (optional: last N exercises in footer)
```

Exercises are practical coding/ML tasks — not multiple-choice quizzes.

---

## Setup

```env
AI_TRAINER_VENDOR=openai
AI_TRAINER_MODEL=gpt-4.1
AI_TRAINER_TO=you@example.com
AI_TRAINER_BCC=colleague1@example.com,colleague2@example.com

AI_TRAINER_INCLUDE_HISTORY_IN_EMAIL=1
AI_TRAINER_HISTORY_IN_EMAIL_MAX=5
AI_TRAINER_GROUNDING=1
```

Register task:

```powershell
powershell -ExecutionPolicy Bypass -File setup_daily_ai_trainer_task.ps1
```

---

## Usage

```powershell
# Preview today's exercise
uv run python daily_ai_trainer_email_agent.py --dry-run

# Send once
uv run python daily_ai_trainer_email_agent.py --no-retry

# Regenerate same day (new LLM call)
uv run python daily_ai_trainer_email_agent.py --no-retry --force

# Resend today's exercise without calling LLM
uv run python daily_ai_trainer_email_agent.py --resend-today --no-retry
```

| Output | Path |
|--------|------|
| History | `data/ai_trainer_exercises.md` (local, gitignored) |
| Log | `logs/daily_ai_trainer_email.log` |
| Preview | `logs/daily_ai_trainer_preview.html` |

---

## Key modules

| File | Role |
|------|------|
| `daily_ai_trainer_email_agent.py` | Agent + CLI |
| `ai_trainer_api.py` | Exercise generation, HTML formatting |
| `ai_trainer_store.py` | Markdown history, dedup context for LLM |

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_TRAINER_VENDOR` | `openai` | `openai` or `gemini` |
| `AI_TRAINER_MODEL` | `gpt-4.1` | Reasoning model |
| `AI_TRAINER_TO` | `you@example.com` | Primary recipients |
| `AI_TRAINER_BCC` | *(empty)* | BCC list |
| `AI_TRAINER_GROUNDING` | `1` | Gemini: use Google Search for trends |
| `AI_TRAINER_INCLUDE_HISTORY_IN_EMAIL` | `1` | Show recent exercises in email |
| `AI_TRAINER_HISTORY_IN_EMAIL_MAX` | `5` | How many past exercises in footer |

---

## Gemini vs OpenAI

| Vendor | Best for |
|--------|----------|
| **OpenAI** (`gpt-4.1`, `o4-mini`) | Deep multi-step exercises, reliable structure |
| **Gemini** (`gemini-2.5-pro`) | Grounded exercises referencing current AI news |

---

## Troubleshooting

| Issue | Action |
|-------|--------|
| Repeated exercise themes | History file may be empty — check `data/ai_trainer_exercises.md` |
| `--force` same day | Intentionally replaces today’s entry after regeneration |
| Large BCC list | Configure via `AI_TRAINER_BCC` in `.env` only — never commit |

---

## Important notes

- `data/ai_trainer_exercises.md` contains generated content and may reference your team context — **gitignored**.
- BCC colleague lists should stay in local `.env` only.

---

## Related

- [README_SCHEDULING.md](README_SCHEDULING.md)
- [README_EMAIL_DELIVERY.md](README_EMAIL_DELIVERY.md)
