# Job Search Email Agent

## What is this?

The **Job Search Email Agent** (`daily_job_search_email_agent.py`) searches the web for Israeli hi-tech jobs that match your CV, filters duplicates and low-quality listings, and emails an HTML summary twice daily.

It is **phase 1**: search + email only. Auto-apply is not implemented yet.

```
09:45 / 14:00 scheduled run
        │
        ▼
   Load CV + preferences + history
        │
        ▼
   Multi-pass LLM search
   (Gemini · OpenAI web · hi-tech boards · LinkedIn)
        │
        ▼
   Quality filter + dedup against history
        │
        ▼
   Email NEW matches only → append to history MD
```

---

## How it works

### Search passes

Each run executes several passes (configurable via `.env`):

| Pass | Source | Env flags |
|------|--------|-----------|
| Primary chat | Gemini or OpenAI | `JOB_SEARCH_VENDOR` |
| OpenAI web search | Live web results | `JOB_SEARCH_USE_OPENAI_WEB=1` |
| Israeli hi-tech boards | Grounded Gemini search | `JOB_SEARCH_HITECH_BOARDS=1` |
| LinkedIn | Gemini/OpenAI + optional OpenAI web | `JOB_SEARCH_LINKEDIN=1` |

RSS hints from Google News and job-board feeds are injected into prompts for context.

### Quality pipeline

Before a job reaches your inbox, `job_search_quality.py` checks:

- URL is not hallucinated (invented career-page patterns blocked)
- HTTP status (404 / DNS failures marked unavailable)
- LinkedIn “no longer accepting applications” filtered out
- Minimum substance (company, title, description, match score)

Jobs **without working links** can still appear with full details, a Google search link, and apply email if found.

### History & deduplication

Tracked in a **Markdown file** (default: `data/job_search_history.md`).

| Mechanism | Purpose |
|-----------|---------|
| **LLM context** | Last ~80 jobs injected: “DO NOT return these again” |
| **Code dedup** | Match on position ID, URL, company+title, company+description hash |
| **Append on save** | Only **new** jobs from the run are written to history |

**Scheduled runs do not re-email the same job** unless you use `--resend-today` or `--ignore-history`.

See also: [README_MULTI_PROFILE.md](README_MULTI_PROFILE.md) for per-candidate history files.

---

## Prerequisites

- OpenAI and/or Google API keys
- CV as DOCX, PDF, or Markdown
- Email delivery configured ([README_EMAIL_DELIVERY.md](README_EMAIL_DELIVERY.md))

---

## Setup

### 1. CV

**Option A — DOCX/PDF (auto-sync to MD):**

```env
JOB_SEARCH_CV_DOCX=data/cv.docx
JOB_SEARCH_CV_PATH=data/job_search_cv.md
JOB_SEARCH_SYNC_MD_FROM_DOCX=1
```

**Option B — Markdown only:**

```env
JOB_SEARCH_CV_PATH=data/job_search_cv.md
```

### 2. Search preferences

```env
JOB_SEARCH_HOME_LOCATION=Beit Shemesh, Israel
JOB_SEARCH_LOCATIONS=Jerusalem, Shfela, Hybrid, Remote
JOB_SEARCH_KEYWORDS=solution architect, AI engineer, Java, hi-tech
JOB_SEARCH_FOCUS=hitech
JOB_SEARCH_MAX_JOBS=15
JOB_SEARCH_TO=you@example.com
JOB_SEARCH_HISTORY_FILE=data/job_search_history.md
```

### 3. Schedule

```powershell
powershell -ExecutionPolicy Bypass -File setup_daily_job_search_task.ps1
```

Runs at **09:45** and **14:00** daily (`DailyJobSearchEmail`).

---

## Usage

### Standard run

```powershell
uv run python daily_job_search_email_agent.py
```

### Dry run (no email, saves preview)

```powershell
uv run python daily_job_search_email_agent.py --dry-run
```

Preview: `logs/daily_job_search_preview.html`

### Resend today from history (no LLM)

```powershell
uv run python daily_job_search_email_agent.py --resend-today --no-retry
```

> Email will show **Providers: history resend** — these are **not** newly discovered jobs.

### Fresh search ignoring history

```powershell
uv run python daily_job_search_email_agent.py --ignore-history
```

### Clean bad entries from history

```powershell
uv run python daily_job_search_email_agent.py --clean-history
```

### Preview before sending to a profile recipient

```powershell
uv run python daily_job_search_email_agent.py --profile roi_atias --me-only --dry-run
```

### All CLI flags

| Flag | Description |
|------|-------------|
| `--profile ID` | Use `data/job_profiles/<id>.json` |
| `--list-profiles` | List available profiles |
| `--dry-run` | Build HTML, no send |
| `--no-retry` | Single attempt (no 10-min retries) |
| `--no-save` | Do not append to history |
| `--clean-history` | Purge invalid history entries and exit |
| `--resend-today` | Email from history, no search |
| `--ignore-history` | Skip dedup for this run |
| `--me-only` | Send to `JOB_SEARCH_ME_ONLY_TO` / `JOB_SEARCH_TO` only |
| `--resend-preview` | Resend last dry-run HTML |

---

## Email content

HTML table columns: **Company · Role · Match % · Location · Link · Details**

- Verified links show a short host label
- Dead links show “No working link available” + manual search
- Match reasons and key requirements included per row

**Subject line:** `Job Search — N new match(es) — YYYY-MM-DD`  
(or profile name when using `--profile`)

---

## Key modules

| File | Role |
|------|------|
| `daily_job_search_email_agent.py` | Orchestrator, CLI, scheduling |
| `job_search_api.py` | LLM search passes, HTML email |
| `job_search_store.py` | Markdown history read/write/dedup |
| `job_search_quality.py` | URL validation, link checks |
| `job_search_cv_loader.py` | CV from DOCX/PDF/MD |
| `job_search_profile.py` | Multi-candidate profiles |
| `job_search_apply.py` | Mailto draft helpers (phase 2 prep) |

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JOB_SEARCH_VENDOR` | `gemini` | Primary LLM |
| `JOB_SEARCH_VENDOR_FALLBACK` | `openai` | Fallback vendor |
| `JOB_SEARCH_MODEL` | `gemini-2.5-flash` | Gemini model |
| `JOB_SEARCH_OPENAI_MODEL` | `gpt-4.1-mini` | OpenAI chat model |
| `JOB_SEARCH_USE_OPENAI_WEB` | `1` | Enable OpenAI web search pass |
| `JOB_SEARCH_LINKEDIN` | `1` | LinkedIn pass |
| `JOB_SEARCH_HITECH_BOARDS` | `1` | Israeli boards pass |
| `JOB_SEARCH_GROUNDING` | `1` | Gemini Google Search grounding |
| `JOB_SEARCH_OPENAI_CHAT_FALLBACK` | `0` | Chat fallback when web JSON fails |
| `JOB_SEARCH_TO` | `you@example.com` | Email recipients |
| `JOB_SEARCH_HISTORY_FILE` | `data/job_search_history.md` | Dedup store |
| `JOB_SEARCH_ME_ONLY_TO` | falls back to `JOB_SEARCH_TO` | Preview recipient |

---

## Troubleshooting

### `0 new jobs` but jobs exist online

- LLM pass may have failed JSON parse — check log for `pass skipped`
- All matches may already be in history
- Try `--ignore-history --dry-run` to inspect raw results

### Duplicate jobs in email

- Normal scheduled runs **should not** duplicate
- If subject says **history resend**, that is intentional (`--resend-today`)
- Slightly different URL/title can bypass dedup (rare)

### Gmail timeout, Outlook fails

See [README_EMAIL_DELIVERY.md](README_EMAIL_DELIVERY.md) — ensure `OUTLOOK_MCP_DIR` is set and `.env` loads before Outlook helper runs.

### Bad / hallucinated URLs in history

```powershell
uv run python daily_job_search_email_agent.py --clean-history
```

---

## Important notes

- History files and profile JSON with real emails are **gitignored** — keep them local.
- Use `data/job_profiles/example_profile.json.template` as a starting point for new candidates.
- Phase 2 (auto-send CV) is planned in `job_search_apply.py` but not wired to daily runs.

---

## Logs

| File | Content |
|------|---------|
| `logs/daily_job_search_email.log` | Default profile runs |
| `logs/job_search_<profile_id>.log` | Per-profile runs |
| `logs/daily_job_search_preview.html` | Last dry-run output |
