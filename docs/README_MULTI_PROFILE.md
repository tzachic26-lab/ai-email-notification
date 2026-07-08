# Multi-Profile Job Search

## What is this?

Run the job search agent for **multiple candidates** — each with their own CV, email recipients, search preferences, and **separate history file**.

Use cases:

- Your own job search + a friend’s search on the same machine
- Different keyword/location focus per person
- BCC yourself on someone else’s digest

---

## How it works

```
data/job_profiles/
├── example_profile.json.template   ← template (in git)
├── tzachi.json                     ← local only (gitignored)
└── roi_atias.json                  ← local only (gitignored)

Each profile JSON  →  profile_context()  →  overrides JOB_SEARCH_* env vars
                                              for the duration of the run
```

When `--profile roi_atias` is passed:

1. `load_profile()` reads `data/job_profiles/roi_atias.json`
2. `apply_profile_to_env()` sets CV path, history file, recipients, locations, etc.
3. Search + dedup use **that profile’s history only**
4. Email goes to profile `to_emails` with optional `bcc_emails`

Histories do **not** cross-contaminate between profiles.

---

## Profile JSON format

Copy the template:

```powershell
copy data\job_profiles\example_profile.json.template data\job_profiles\myname.json
```

Example structure:

```json
{
  "id": "myname",
  "display_name": "Your Name",
  "cv_source": "data/your_cv.pdf",
  "cv_md_cache": "data/job_profiles/myname_cv.md",
  "history_file": "data/job_profiles/myname_history.md",
  "to_emails": ["you@example.com"],
  "bcc_emails": ["admin@example.com"],
  "candidate_name": "Your Name",
  "candidate_email": "you@example.com",
  "candidate_phone": "+1-555-0100",
  "home_location": "Ashdod, Israel",
  "locations": "Ashdod, Shfela, Hybrid, Remote",
  "keywords": "backend, Java, Spring Boot, hi-tech",
  "notes": "Prefer hybrid roles near home",
  "company_watchlist": "",
  "log_stem": "job_search_myname"
}
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | CLI `--profile` value |
| `display_name` | Yes | Email subject prefix |
| `cv_source` | Yes | Path to DOCX, PDF, or MD |
| `to_emails` | Yes | Primary recipients |
| `bcc_emails` | No | Hidden copy recipients |
| `history_file` | No | Default: `data/job_profiles/<id>_history.md` |
| `cv_md_cache` | No | Synced markdown cache path |
| `home_location` | No | Used in search prompts |
| `locations` | No | Preferred work locations |
| `keywords` | No | Role/tech keywords |
| `notes` | No | Free-text preferences for LLM |
| `log_stem` | No | Log file name under `logs/` |

---

## Usage

### List profiles

```powershell
uv run python daily_job_search_email_agent.py --list-profiles
```

### Dry run for a profile

```powershell
uv run python daily_job_search_email_agent.py --profile myname --dry-run
```

### Send to profile recipient

```powershell
uv run python daily_job_search_email_agent.py --profile myname --no-retry
```

### Preview to yourself only (no BCC, not their TO)

```powershell
uv run python daily_job_search_email_agent.py --profile myname --me-only --no-retry
```

Uses `JOB_SEARCH_ME_ONLY_TO` or `JOB_SEARCH_TO` from `.env`.

---

## Schedule per profile

```powershell
powershell -ExecutionPolicy Bypass -File setup_daily_job_search_profile_task.ps1 -ProfileId myname
```

Creates task: `DailyJobSearchEmail_myname` at **09:45** and **14:00**.

Verify:

```powershell
Get-ScheduledTask DailyJobSearchEmail_* | Format-Table TaskName, State
```

---

## Files per profile (local)

| File | Purpose |
|------|---------|
| `data/job_profiles/<id>.json` | Config |
| `data/job_profiles/<id>_history.md` | Dedup history |
| `data/job_profiles/<id>_cv.md` | CV markdown cache |
| `logs/<log_stem>.log` | Run logs |
| `logs/<log_stem>_preview.html` | Dry-run preview |

All of the above (except `.template`) are **gitignored** for privacy.

---

## Example: two candidates on one PC

| Task | Profile | History | TO |
|------|---------|---------|-----|
| `DailyJobSearchEmail` | *(none — uses .env)* | `data/job_search_history.md` | From `JOB_SEARCH_TO` |
| `DailyJobSearchEmail_roi_atias` | `roi_atias` | `data/job_profiles/roi_atias_history.md` | Friend’s email, BCC you |

---

## Important notes

- Default scheduled task (`setup_daily_job_search_task.ps1`) runs **without** `--profile` — uses root `.env` only.
- To migrate from single-user to profiles, copy existing history to the profile’s `history_file` path.
- Profile JSON contains PII — never commit to public repos.

---

## Related

- [README_JOB_SEARCH.md](README_JOB_SEARCH.md) — search logic, dedup, quality
- [README_SCHEDULING.md](README_SCHEDULING.md) — Windows tasks
