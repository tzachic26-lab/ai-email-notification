"""Semantic job dedup and storage via Pinecone + OpenAI embeddings."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import TYPE_CHECKING, Any

from llm_providers import get_openai_client

if TYPE_CHECKING:
    from job_search_api import JobListing
    from job_search_store import JobRecord

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1536  # text-embedding-3-small
_RUN_EMBEDDING_CACHE: list[list[float]] = []


def vector_dedup_enabled() -> bool:
    if os.getenv("JOB_SEARCH_VECTOR_DEDUP", "0").lower() not in ("1", "true", "yes"):
        return False
    if not (os.getenv("PINECONE_API_KEY") or "").strip():
        logger.warning("JOB_SEARCH_VECTOR_DEDUP=1 but PINECONE_API_KEY is not set — vector dedup off")
        return False
    if not (os.getenv("PINECONE_INDEX_NAME") or "").strip():
        logger.warning("JOB_SEARCH_VECTOR_DEDUP=1 but PINECONE_INDEX_NAME is not set — vector dedup off")
        return False
    return True


def embedding_model() -> str:
    return os.getenv("JOB_SEARCH_EMBEDDING_MODEL", "text-embedding-3-small").strip()


def dedup_threshold() -> float:
    raw = os.getenv("JOB_SEARCH_VECTOR_DEDUP_THRESHOLD", "0.92")
    try:
        return float(raw)
    except ValueError:
        return 0.92


def profile_namespace() -> str:
    profile_id = (os.getenv("JOB_SEARCH_PROFILE_ID") or "default").strip()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in profile_id)
    return safe or "default"


def job_embedding_text(
    *,
    company: str,
    title: str,
    description: str = "",
    location: str = "",
    url: str = "",
) -> str:
    parts = [
        f"Company: {company.strip()}",
        f"Title: {title.strip()}",
    ]
    if location.strip():
        parts.append(f"Location: {location.strip()}")
    if description.strip():
        parts.append(description.strip())
    elif url.strip():
        parts.append(f"URL: {url.strip()}")
    return "\n".join(parts)[:8000]


def job_vector_id(*, company: str, title: str, url: str = "") -> str:
    raw = f"{company.strip().lower()}|{title.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def reset_run_embedding_cache() -> None:
    _RUN_EMBEDDING_CACHE.clear()


def _pinecone_index():
    from pinecone import Pinecone

    api_key = (os.getenv("PINECONE_API_KEY") or "").strip()
    index_name = (os.getenv("PINECONE_INDEX_NAME") or "").strip()
    pc = Pinecone(api_key=api_key)
    desc = pc.describe_index(index_name)
    return pc.Index(host=desc.host)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = get_openai_client()
    model = embedding_model()
    response = client.embeddings.create(model=model, input=texts)
    by_index = {item.index: item.embedding for item in response.data}
    return [by_index[i] for i in range(len(texts))]


def _max_cosine_similarity(a: list[float], others: list[list[float]]) -> float:
    if not others:
        return 0.0
    best = 0.0
    for b in others:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a and norm_b:
            best = max(best, dot / (norm_a * norm_b))
    return best


def is_semantic_duplicate(
    embedding: list[float],
    *,
    company: str,
    title: str,
) -> bool:
    """True if Pinecone or the current run already has a near-duplicate."""
    threshold = dedup_threshold()

    if _RUN_EMBEDDING_CACHE:
        if _max_cosine_similarity(embedding, _RUN_EMBEDDING_CACHE) >= threshold:
            logger.info(
                "Vector dedup (same run): skipping near-duplicate %s | %s",
                company,
                title,
            )
            return True

    try:
        index = _pinecone_index()
        result = index.query(
            vector=embedding,
            top_k=3,
            namespace=profile_namespace(),
            include_metadata=True,
        )
        for match in result.matches or []:
            score = float(match.score or 0)
            if score >= threshold:
                meta = match.metadata or {}
                logger.info(
                    "Vector dedup (Pinecone score=%.3f): skipping %s | %s (matches %s | %s)",
                    score,
                    company,
                    title,
                    meta.get("company", "?"),
                    meta.get("title", "?"),
                )
                return True
    except Exception as exc:
        logger.warning("Pinecone query failed (job kept): %s", exc)
        return False

    return False


def remember_run_embedding(embedding: list[float]) -> None:
    _RUN_EMBEDDING_CACHE.append(embedding)


def check_job_semantic_duplicate(job: JobListing) -> bool:
    """Embed job and check Pinecone + in-run cache. Returns True if duplicate."""
    if not vector_dedup_enabled():
        return False
    text = job_embedding_text(
        company=job.company,
        title=job.title,
        description=job.description,
        location=job.location,
        url=job.url or job.url_hint,
    )
    try:
        embedding = embed_texts([text])[0]
    except Exception as exc:
        logger.warning("Embedding failed for %s | %s (job kept): %s", job.company, job.title, exc)
        return False
    if is_semantic_duplicate(embedding, company=job.company, title=job.title):
        return True
    remember_run_embedding(embedding)
    return False


def upsert_job_listings(jobs: list[JobListing], *, iso_date: str) -> int:
    if not vector_dedup_enabled() or not jobs:
        return 0

    texts = [
        job_embedding_text(
            company=job.company,
            title=job.title,
            description=job.description,
            location=job.location,
            url=job.url or job.url_hint,
        )
        for job in jobs
    ]
    try:
        embeddings = embed_texts(texts)
        index = _pinecone_index()
        vectors: list[tuple[str, list[float], dict[str, Any]]] = []
        namespace = profile_namespace()
        for job, values in zip(jobs, embeddings, strict=True):
            vid = job_vector_id(
                company=job.company,
                title=job.title,
                url=job.url or job.url_hint,
            )
            vectors.append(
                (
                    vid,
                    values,
                    {
                        "company": (job.company or "")[:200],
                        "title": (job.title or "")[:200],
                        "location": (job.location or "")[:120],
                        "url": (job.url or job.url_hint or "")[:500],
                        "iso_date": iso_date,
                        "profile_id": profile_namespace(),
                        "match_score": int(job.match_score),
                    },
                )
            )
        index.upsert(vectors=vectors, namespace=namespace)
        logger.info(
            "Pinecone upsert: %s job(s) in namespace %r",
            len(vectors),
            namespace,
        )
        return len(vectors)
    except Exception as exc:
        logger.warning("Pinecone upsert failed: %s", exc)
        return 0


def backfill_history_records(records: list[JobRecord]) -> tuple[int, int]:
    """Embed and upsert existing markdown history into Pinecone."""
    if not vector_dedup_enabled():
        raise RuntimeError("Vector dedup is not enabled (JOB_SEARCH_VECTOR_DEDUP + Pinecone env vars)")

    if not records:
        return 0, 0

    texts = [
        job_embedding_text(
            company=rec.company,
            title=rec.title,
            description=rec.description,
            location=rec.location,
            url=rec.url,
        )
        for rec in records
    ]
    embeddings = embed_texts(texts)
    index = _pinecone_index()
    namespace = profile_namespace()
    vectors: list[tuple[str, list[float], dict[str, Any]]] = []
    for rec, values in zip(records, embeddings, strict=True):
        vid = job_vector_id(company=rec.company, title=rec.title, url=rec.url)
        vectors.append(
            (
                vid,
                values,
                {
                    "company": (rec.company or "")[:200],
                    "title": (rec.title or "")[:200],
                    "location": (rec.location or "")[:120],
                    "url": (rec.url or "")[:500],
                    "iso_date": rec.iso_date,
                    "profile_id": namespace,
                    "match_score": int(rec.match_score),
                },
            )
        )
    index.upsert(vectors=vectors, namespace=namespace, batch_size=50)
    return len(vectors), 0
