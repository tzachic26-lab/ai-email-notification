"""Persist job search findings in markdown for deduplication across runs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
DEFAULT_HISTORY_PATH = APP_DIR / "data" / "job_search_history.md"

_ENTRY_HEADER_RE = re.compile(
    r"^##\s+(?P<iso_date>\d{4}-\d{2}-\d{2})\s+\|\s+(?P<title>.+)$",
    re.MULTILINE,
)
_FIELD_RE = re.compile(r"^\*\*(?P<key>[^*]+):\*\*\s*(?P<value>.+)$", re.MULTILINE)


@dataclass(frozen=True)
class JobRecord:
    iso_date: str
    title: str
    company: str
    position_id: str
    url: str
    location: str
    match_score: int
    source: str
    description: str
    markdown_body: str

    @property
    def summary_line(self) -> str:
        pid = f" #{self.position_id}" if self.position_id else ""
        return f"{self.company} | {self.title}{pid} ({self.match_score}%)"


def history_path() -> Path:
    from os import getenv

    raw = getenv("JOB_SEARCH_HISTORY_FILE", "")
    return Path(raw) if raw else DEFAULT_HISTORY_PATH


def ensure_history_file(path: Path | None = None) -> Path:
    target = path or history_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(
            "# Job Search History\n\n"
            "Tracked job listings. Used to filter duplicates on future searches "
            "(position ID, company + title, URL, or company + description).\n",
            encoding="utf-8",
        )
    return target


def _normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_url(url: str) -> str:
    raw = (url or "").strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


def description_fingerprint(description: str) -> str:
    normalized = _normalize_text(description)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def dedupe_key(record: JobRecord) -> tuple[str, ...]:
    keys: list[str] = []
    if record.position_id:
        keys.append(f"pid:{_normalize_text(record.position_id)}")
    if record.url:
        keys.append(f"url:{_normalize_url(record.url)}")
    company = _normalize_text(record.company)
    title = _normalize_text(record.title)
    if company and title:
        keys.append(f"ct:{company}|{title}")
    if company and record.description:
        keys.append(f"cd:{company}|{description_fingerprint(record.description)}")
    return tuple(keys)


def _markdown_section(body: str, heading: str) -> str:
    pattern = rf"### {re.escape(heading)}\s*\n(.*?)(?=\n### |\Z)"
    match = re.search(pattern, body, re.DOTALL)
    return match.group(1).strip() if match else ""


def parse_jobs(text: str) -> list[JobRecord]:
    records: list[JobRecord] = []
    chunks = re.split(r"\n---+\n", text)
    for chunk in chunks:
        header = _ENTRY_HEADER_RE.search(chunk)
        if not header:
            continue
        fields = {m.group("key").strip().lower(): m.group("value").strip() for m in _FIELD_RE.finditer(chunk)}
        desc = _markdown_section(chunk, "Description")
        score_raw = fields.get("match score", "0").replace("%", "").strip()
        try:
            score = int(float(score_raw))
        except ValueError:
            score = 0
        records.append(
            JobRecord(
                iso_date=header.group("iso_date"),
                title=header.group("title").strip(),
                company=fields.get("company", ""),
                position_id=fields.get("position id", ""),
                url=fields.get("url", ""),
                location=fields.get("location", ""),
                match_score=score,
                source=fields.get("source", ""),
                description=desc,
                markdown_body=chunk.strip(),
            )
        )
    return records


def load_history(path: Path | None = None) -> tuple[Path, list[JobRecord]]:
    target = ensure_history_file(path)
    text = target.read_text(encoding="utf-8")
    return target, parse_jobs(text)


def seen_dedupe_keys(records: list[JobRecord]) -> set[str]:
    keys: set[str] = set()
    for record in records:
        keys.update(dedupe_key(record))
    return keys


def is_duplicate(
    *,
    company: str,
    title: str,
    position_id: str = "",
    url: str = "",
    description: str = "",
    seen_keys: set[str],
) -> bool:
    probe = JobRecord(
        iso_date="",
        title=title,
        company=company,
        position_id=position_id,
        url=url,
        location="",
        match_score=0,
        source="",
        description=description,
        markdown_body="",
    )
    return any(key in seen_keys for key in dedupe_key(probe))


def history_context_for_llm(records: list[JobRecord], *, max_entries: int = 80) -> str:
    if not records:
        return "No prior job listings tracked."
    recent = records[-max_entries:]
    lines = [
        "Already tracked jobs (DO NOT return these again unless materially changed):",
        "",
    ]
    for rec in recent:
        lines.append(f"- {rec.summary_line}")
        if rec.position_id:
            lines.append(f"  position_id: {rec.position_id}")
        if rec.url:
            lines.append(f"  url: {rec.url}")
    return "\n".join(lines)


def format_job_markdown(
    *,
    iso_date: str,
    company: str,
    title: str,
    position_id: str,
    url: str,
    location: str,
    employment_type: str,
    match_score: int,
    match_reasons: list[str],
    source: str,
    posted_date: str,
    description: str,
    requirements: list[str],
    apply_email: str = "",
    apply_method: str = "",
    url_status: str = "",
    url_hint: str = "",
    link_note: str = "",
) -> str:
    reasons = "\n".join(f"- {r}" for r in match_reasons if r) or "- —"
    reqs = "\n".join(f"- {r}" for r in requirements if r) or "- —"
    apply_line = f"\n**Apply email:** {apply_email}" if apply_email else ""
    method_line = f"\n**Apply method:** {apply_method}" if apply_method else ""
    link_status_line = f"\n**Link status:** {url_status}" if url_status else ""
    hint_line = f"\n**URL hint:** {url_hint}" if url_hint else ""
    note_line = f"\n**Link note:** {link_note}" if link_note else ""
    url_display = url if url else (url_hint or "— (no working link)")
    return f"""---

## {iso_date} | {title}

**Company:** {company}
**Position ID:** {position_id or "—"}
**URL:** {url_display}{apply_line}{method_line}{link_status_line}{hint_line}{note_line}
**Location:** {location or "—"}
**Employment type:** {employment_type or "—"}
**Match score:** {match_score}%
**Source:** {source}
**Posted:** {posted_date or "unknown"}

### Why it matches
{reasons}

### Description
{description}

### Key requirements
{reqs}
"""


def _match_reasons_from_chunk(chunk: str) -> list[str]:
    section = _markdown_section(chunk, "Why it matches")
    reasons: list[str] = []
    for line in section.splitlines():
        text = line.strip()
        if not text.startswith("-"):
            continue
        value = text.lstrip("- ").strip()
        if value and value not in ("—", "-"):
            reasons.append(value)
    return reasons


def _requirements_from_chunk(chunk: str) -> list[str]:
    section = _markdown_section(chunk, "Key requirements")
    reqs: list[str] = []
    for line in section.splitlines():
        text = line.strip()
        if not text.startswith("-"):
            continue
        value = text.lstrip("- ").strip()
        if value and value not in ("—", "-"):
            reqs.append(value)
    return reqs


def purge_invalid_history_entries(path: Path | None = None) -> tuple[Path, int, int]:
    """Remove low-quality / hallucinated entries from history. Returns (path, kept, removed)."""
    from job_search_quality import evaluate_job_url, has_listing_substance, is_usable_job_listing, normalize_job_url

    target = ensure_history_file(path)
    text = target.read_text(encoding="utf-8")
    header, _, _ = text.partition("\n---")
    chunks = re.split(r"\n---+\n", text)
    kept_chunks: list[str] = []
    removed = 0
    for chunk in chunks:
        entry_header = _ENTRY_HEADER_RE.search(chunk)
        if not entry_header:
            continue
        fields = {m.group("key").strip().lower(): m.group("value").strip() for m in _FIELD_RE.finditer(chunk)}
        score_raw = fields.get("match score", "0").replace("%", "").strip()
        try:
            score = int(float(score_raw))
        except ValueError:
            score = 0
        position_id = fields.get("position id", "")
        if position_id in ("—",):
            position_id = ""
        url = normalize_job_url(fields.get("url", ""), position_id=position_id)
        reasons = _match_reasons_from_chunk(chunk)
        reqs = _requirements_from_chunk(chunk)
        posted_date = fields.get("posted", "")
        description = _markdown_section(chunk, "Description")
        location = fields.get("location", "")
        if not has_listing_substance(
            company=fields.get("company", ""),
            title=entry_header.group("title"),
            description=description,
            match_score=score,
            match_reasons=reasons,
            requirements=reqs,
        ):
            removed += 1
            continue
        if not is_usable_job_listing(
            url=url,
            company=fields.get("company", ""),
            title=entry_header.group("title"),
            location=location,
            description=description,
            match_score=score,
            match_reasons=reasons,
            requirements=reqs,
            position_id=position_id,
            posted_date=posted_date,
        ):
            removed += 1
            continue
        url_status, link_note = evaluate_job_url(
            url, position_id=position_id, posted_date=posted_date
        )
        body = chunk.strip()
        if url_status == "unavailable":
            body = re.sub(r"(\*\*URL:\*\*)\s*[^\n]+", r"\1 — (no working link)", body, count=1)
            if "**Link status:**" not in body:
                body = body.replace(
                    f"**Posted:** {posted_date or 'unknown'}",
                    f"**Link status:** {url_status}\n**URL hint:** {url or '—'}\n**Link note:** {link_note}\n**Posted:** {posted_date or 'unknown'}",
                    1,
                )
        elif url and url != fields.get("url", ""):
            body = re.sub(r"(\*\*URL:\*\*)\s*[^\n]+", rf"\1 {url}", body, count=1)
        kept_chunks.append(body)
    intro = header.strip() or (
        "# Job Search History\n\n"
        "Tracked job listings. Used to filter duplicates on future searches "
        "(position ID, company + title, URL, or company + description)."
    )
    if kept_chunks:
        target.write_text(f"{intro}\n\n---\n\n" + "\n\n---\n\n".join(kept_chunks) + "\n", encoding="utf-8")
    else:
        target.write_text(f"{intro}\n", encoding="utf-8")
    return target, len(kept_chunks), removed


def append_jobs(markdown_entries: list[str], path: Path | None = None) -> Path:
    target = ensure_history_file(path)
    text = target.read_text(encoding="utf-8").rstrip()
    block = "\n\n".join(entry.strip() for entry in markdown_entries if entry.strip())
    if block and not block.startswith("---"):
        block = f"---\n\n{block}"
    target.write_text(f"{text}\n\n{block}\n", encoding="utf-8")
    return target


def today_iso() -> str:
    return date.today().isoformat()


def get_records_for_date(iso_date: str, path: Path | None = None) -> list[JobRecord]:
    _, records = load_history(path)
    return [r for r in records if r.iso_date == iso_date]
