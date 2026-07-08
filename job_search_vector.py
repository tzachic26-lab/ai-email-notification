"""Semantic job dedup via vector store (Pinecone or local Chroma) + OpenAI embeddings."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm_providers import get_openai_client

if TYPE_CHECKING:
    from job_search_api import JobListing
    from job_search_store import JobRecord

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
EMBEDDING_DIM = 1536  # text-embedding-3-small
_RUN_EMBEDDING_CACHE: list[list[float]] = []
_chroma_client = None


def vector_backend() -> str:
    """pinecone (cloud) or chroma (local disk)."""
    raw = (os.getenv("JOB_SEARCH_VECTOR_BACKEND") or "pinecone").strip().lower()
    if raw in ("chroma", "local"):
        return "chroma"
    return "pinecone"


def vector_dedup_enabled() -> bool:
    if os.getenv("JOB_SEARCH_VECTOR_DEDUP", "0").lower() not in ("1", "true", "yes"):
        return False
    if vector_backend() == "chroma":
        return True
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


def chroma_persist_dir() -> Path:
    raw = (os.getenv("CHROMA_PERSIST_DIR") or "data/chroma_job_search").strip()
    path = Path(raw)
    return path if path.is_absolute() else APP_DIR / path


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


def _job_metadata(
    *,
    company: str,
    title: str,
    location: str,
    url: str,
    iso_date: str,
    match_score: int,
) -> dict[str, Any]:
    return {
        "company": (company or "")[:200],
        "title": (title or "")[:200],
        "location": (location or "")[:120],
        "url": (url or "")[:500],
        "iso_date": iso_date,
        "profile_id": profile_namespace(),
        "match_score": int(match_score),
    }


def _pinecone_index():
    from pinecone import Pinecone

    api_key = (os.getenv("PINECONE_API_KEY") or "").strip()
    index_name = (os.getenv("PINECONE_INDEX_NAME") or "").strip()
    pc = Pinecone(api_key=api_key)
    desc = pc.describe_index(index_name)
    return pc.Index(host=desc.host)


def _chroma_collection():
    global _chroma_client
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            "Chroma backend requires chromadb. Run: uv sync --extra chroma"
        ) from exc

    persist_dir = chroma_persist_dir()
    persist_dir.mkdir(parents=True, exist_ok=True)
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=str(persist_dir))
    name = f"jobs_{profile_namespace()}"
    return _chroma_client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def _chroma_similarity_from_distance(distance: float) -> float:
    """Chroma cosine distance ≈ 1 - cosine similarity."""
    return 1.0 - float(distance)


def _query_vector_store(
    embedding: list[float],
    *,
    company: str,
    title: str,
) -> bool:
    """Return True if a near-duplicate exists in the active backend."""
    threshold = dedup_threshold()
    backend = vector_backend()

    try:
        if backend == "chroma":
            collection = _chroma_collection()
            if collection.count() == 0:
                return False
            result = collection.query(
                query_embeddings=[embedding],
                n_results=min(3, collection.count()),
                include=["metadatas", "distances"],
            )
            distances = (result.get("distances") or [[]])[0]
            metas = (result.get("metadatas") or [[]])[0]
            for dist, meta in zip(distances, metas, strict=False):
                score = _chroma_similarity_from_distance(dist)
                if score >= threshold:
                    meta = meta or {}
                    logger.info(
                        "Vector dedup (Chroma score=%.3f): skipping %s | %s (matches %s | %s)",
                        score,
                        company,
                        title,
                        meta.get("company", "?"),
                        meta.get("title", "?"),
                    )
                    return True
            return False

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
        logger.warning("%s query failed (job kept): %s", backend, exc)
        return False

    return False


def _upsert_vectors(
    items: list[tuple[str, list[float], dict[str, Any]]],
) -> int:
    if not items:
        return 0
    backend = vector_backend()
    namespace = profile_namespace()

    try:
        if backend == "chroma":
            collection = _chroma_collection()
            collection.upsert(
                ids=[item[0] for item in items],
                embeddings=[item[1] for item in items],
                metadatas=[item[2] for item in items],
            )
            logger.info(
                "Chroma upsert: %s job(s) in collection %r (%s)",
                len(items),
                collection.name,
                chroma_persist_dir(),
            )
            return len(items)

        index = _pinecone_index()
        vectors = [(vid, emb, meta) for vid, emb, meta in items]
        index.upsert(vectors=vectors, namespace=namespace, batch_size=50)
        logger.info("Pinecone upsert: %s job(s) in namespace %r", len(vectors), namespace)
        return len(vectors)
    except Exception as exc:
        logger.warning("%s upsert failed: %s", backend, exc)
        return 0


def is_semantic_duplicate(
    embedding: list[float],
    *,
    company: str,
    title: str,
) -> bool:
    """True if vector store or the current run already has a near-duplicate."""
    threshold = dedup_threshold()

    if _RUN_EMBEDDING_CACHE:
        if _max_cosine_similarity(embedding, _RUN_EMBEDDING_CACHE) >= threshold:
            logger.info(
                "Vector dedup (same run): skipping near-duplicate %s | %s",
                company,
                title,
            )
            return True

    return _query_vector_store(embedding, company=company, title=title)


def remember_run_embedding(embedding: list[float]) -> None:
    _RUN_EMBEDDING_CACHE.append(embedding)


def check_job_semantic_duplicate(job: JobListing) -> bool:
    """Embed job and check vector store + in-run cache. Returns True if duplicate."""
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
        items: list[tuple[str, list[float], dict[str, Any]]] = []
        for job, values in zip(jobs, embeddings, strict=True):
            vid = job_vector_id(
                company=job.company,
                title=job.title,
                url=job.url or job.url_hint,
            )
            items.append(
                (
                    vid,
                    values,
                    _job_metadata(
                        company=job.company,
                        title=job.title,
                        location=job.location,
                        url=job.url or job.url_hint or "",
                        iso_date=iso_date,
                        match_score=job.match_score,
                    ),
                )
            )
        return _upsert_vectors(items)
    except Exception as exc:
        logger.warning("Vector upsert failed: %s", exc)
        return 0


def backfill_history_records(records: list[JobRecord]) -> tuple[int, int]:
    """Embed and upsert existing markdown history into the active vector backend."""
    if not vector_dedup_enabled():
        raise RuntimeError(
            "Vector dedup is not enabled (JOB_SEARCH_VECTOR_DEDUP=1 and backend config)"
        )

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
    items: list[tuple[str, list[float], dict[str, Any]]] = []
    for rec, values in zip(records, embeddings, strict=True):
        vid = job_vector_id(company=rec.company, title=rec.title, url=rec.url)
        items.append(
            (
                vid,
                values,
                _job_metadata(
                    company=rec.company,
                    title=rec.title,
                    location=rec.location,
                    url=rec.url,
                    iso_date=rec.iso_date,
                    match_score=rec.match_score,
                ),
            )
        )
    return _upsert_vectors(items), 0
