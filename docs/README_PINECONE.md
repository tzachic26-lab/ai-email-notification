# Pinecone Vector Dedup (Job Search)

## What is this?

Optional **semantic duplicate detection** for job search using:

- **Pinecone** (free tier) — stores job embeddings
- **OpenAI** `text-embedding-3-small` — turns job text into vectors

When enabled, jobs that are **worded differently but mean the same role** are skipped before email — even if URL/title changed.

Markdown history (`job_search_history.md`) **stays** as your source of truth. Pinecone adds a second “similar meaning?” check.

---

## Your steps (one-time setup)

### Step 1 — Create a Pinecone account

1. Go to [https://www.pinecone.io/](https://www.pinecone.io/)
2. Sign up (free **Starter** plan)
3. Open the Pinecone console

### Step 2 — Create an index

In the console, **Create index** with these settings:

| Setting | Value |
|---------|--------|
| **Name** | `job-search` (or your choice) |
| **Type** | **Serverless** |
| **Cloud / Region** | e.g. **AWS** / **us-east-1** (any free-tier region) |
| **Dimensions** | **1536** |
| **Metric** | **cosine** |

Wait until the index status is **Ready**.

> The dimension **must** be `1536` — it matches `text-embedding-3-small`.

### Step 3 — Copy your API key

1. Pinecone console → **API keys**
2. Create / copy an API key

### Step 4 — Add to your local `.env`

```env
JOB_SEARCH_VECTOR_DEDUP=1
PINECONE_API_KEY=pcsk_...your_key...
PINECONE_INDEX_NAME=job-search
JOB_SEARCH_EMBEDDING_MODEL=text-embedding-3-small
JOB_SEARCH_VECTOR_DEDUP_THRESHOLD=0.92
```

You already need `OPENAI_API_KEY` for embeddings.

### Step 5 — Install dependencies

```powershell
cd c:\amdocs\ai_email_notification
uv sync
```

### Step 6 — Backfill existing history (recommended)

Upload jobs already in your markdown history so Pinecone knows about them:

```powershell
# Default profile (your .env JOB_SEARCH_HISTORY_FILE)
uv run python daily_job_search_email_agent.py --backfill-vectors --no-retry

# Roi's profile
uv run python daily_job_search_email_agent.py --profile roi_atias --backfill-vectors --no-retry
```

Each profile uses its own **namespace** in Pinecone (`default`, `tzachi`, `roi_atias`, etc.).

### Step 7 — Test a normal run

```powershell
uv run python daily_job_search_email_agent.py --dry-run
```

Check the log for lines like:

- `Vector dedup (Pinecone score=0.95): skipping ...`
- `Pinecone upsert: N job(s) in namespace 'default'`

---

## How it works after setup

```
New job from LLM
      │
      ├─ Exact dedup (URL / title / history MD) ── skip?
      │
      ├─ Embed job text (OpenAI)
      │
      ├─ Query Pinecone (same profile namespace)
      │     score ≥ 0.92 → skip as near-duplicate
      │
      ├─ Email if new
      │
      └─ Upsert embedding to Pinecone + append to markdown history
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `JOB_SEARCH_VECTOR_DEDUP` | `0` | Set `1` to enable |
| `PINECONE_API_KEY` | — | From Pinecone console |
| `PINECONE_INDEX_NAME` | — | Index name you created |
| `JOB_SEARCH_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model |
| `JOB_SEARCH_VECTOR_DEDUP_THRESHOLD` | `0.92` | Higher = stricter dedup (0.85–0.95) |

### Disable without removing keys

```env
JOB_SEARCH_VECTOR_DEDUP=0
```

---

## Free tier notes

- Starter plan has limited storage and requests — enough for personal job search (hundreds/thousands of jobs).
- Embeddings use OpenAI API (small cost per job; typically cents per month at your volume).
- Job text is sent to **OpenAI** for embedding and stored as vectors in **Pinecone** (not full CV — job title/company/description only).

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `PINECONE_API_KEY is not set` | Add key to `.env` |
| Index dimension mismatch | Recreate index with dimension **1536** |
| `describe_index` failed | Check index name spelling |
| Vector dedup off despite `=1` | Check log for missing API key / index name |
| Too many jobs skipped | Lower threshold: `JOB_SEARCH_VECTOR_DEDUP_THRESHOLD=0.88` |
| Duplicates still appear | Raise threshold: `0.94` or run `--backfill-vectors` |

---

## Privacy

- Vectors go to **your** Pinecone account (cloud).
- Do **not** commit `PINECONE_API_KEY` to GitHub.
- Profile JSON and CV files remain local (gitignored).

---

## Related

- [README_JOB_SEARCH.md](README_JOB_SEARCH.md) — main job search agent
- [README_MULTI_PROFILE.md](README_MULTI_PROFILE.md) — separate namespace per profile
