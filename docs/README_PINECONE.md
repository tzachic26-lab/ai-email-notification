# Vector Dedup — Pinecone or Chroma (Job Search)

## What is this?

Optional **semantic duplicate detection** for job search using OpenAI embeddings (`text-embedding-3-small`) plus one of:

| Backend | Env flag | Best for |
|---------|----------|----------|
| **Pinecone** | `JOB_SEARCH_VECTOR_BACKEND=pinecone` | Cloud, free tier, no local DB |
| **Chroma** | `JOB_SEARCH_VECTOR_BACKEND=chroma` | **Local disk**, private, no Pinecone account |

When enabled, jobs that are **worded differently but mean the same role** are skipped before email — even if URL/title changed.

Markdown history (`job_search_history.md`) **stays** as your source of truth. The vector store adds a second “similar meaning?” check.

---

## Quick start — Chroma (local, recommended for privacy)

No Pinecone signup. Data stays on your PC under `data/chroma_job_search/`.

### 1. Add to `.env`

```env
JOB_SEARCH_VECTOR_DEDUP=1
JOB_SEARCH_VECTOR_BACKEND=chroma
CHROMA_PERSIST_DIR=data/chroma_job_search
JOB_SEARCH_EMBEDDING_MODEL=text-embedding-3-small
JOB_SEARCH_VECTOR_DEDUP_THRESHOLD=0.92
```

(`OPENAI_API_KEY` still required for embeddings.)

### 2. Install & backfill

```powershell
uv sync --extra chroma
uv run python daily_job_search_email_agent.py --backfill-vectors --no-retry
```

### 3. Test

```powershell
uv run python daily_job_search_email_agent.py --dry-run
```

Look for log lines: `Vector dedup (Chroma score=...)` or `Chroma upsert: ...`

Each profile gets its own Chroma collection (`jobs_default`, `jobs_roi_atias`, …).

---

## Pinecone setup (cloud alternative)

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
JOB_SEARCH_VECTOR_BACKEND=pinecone
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
| `JOB_SEARCH_VECTOR_BACKEND` | `pinecone` | `pinecone` or `chroma` |
| `PINECONE_API_KEY` | — | Required for Pinecone backend |
| `PINECONE_INDEX_NAME` | — | Pinecone index name |
| `CHROMA_PERSIST_DIR` | `data/chroma_job_search` | Local folder for Chroma backend |
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

| Backend | Where data lives |
|---------|------------------|
| **Chroma** | Local folder `data/chroma_job_search/` (gitignored) |
| **Pinecone** | Your Pinecone cloud account |

Embeddings are computed via **OpenAI** (job title/company/description text only). Do **not** commit API keys to GitHub.

---

## Related

- [README_JOB_SEARCH.md](README_JOB_SEARCH.md) — main job search agent
- [README_MULTI_PROFILE.md](README_MULTI_PROFILE.md) — separate namespace per profile
