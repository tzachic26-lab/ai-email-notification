"""Load CV from DOCX and/or markdown for job search."""

from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

APP_DIR = Path(__file__).resolve().parent
DEFAULT_MD_PATH = APP_DIR / "data" / "job_search_cv.md"
DEFAULT_DOCX_PATH = APP_DIR / "data" / "cv.docx"

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"


def cv_source_path() -> Path | None:
    raw = os.getenv("JOB_SEARCH_CV_SOURCE", "").strip()
    if raw:
        return _resolve(Path(raw))
    return None


def md_path() -> Path:
    raw = os.getenv("JOB_SEARCH_CV_PATH", "")
    if raw:
        return _resolve(Path(raw))
    source = cv_source_path()
    if source and source.suffix.lower() == ".md":
        return source
    return DEFAULT_MD_PATH


def docx_path() -> Path | None:
    raw = os.getenv("JOB_SEARCH_CV_DOCX", "").strip()
    if raw:
        return _resolve(Path(raw))
    source = cv_source_path()
    if source and source.suffix.lower() == ".docx":
        return source
    default = DEFAULT_DOCX_PATH
    return default if default.is_file() else None


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else APP_DIR / path


def docx_to_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_W}p"):
        parts = [node.text or "" for node in paragraph.iter(f"{_W}t")]
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)


def docx_to_markdown(path: Path) -> str:
    """Best-effort DOCX → markdown (headings inferred from short title-like lines)."""
    paragraphs = [p.strip() for p in docx_to_text(path).split("\n\n") if p.strip()]
    if not paragraphs:
        return ""

    lines: list[str] = ["# Candidate CV", ""]
    heading_like = {
        "professional summary",
        "core competencies",
        "technical skills",
        "professional experience",
        "education",
        "languages",
    }

    for para in paragraphs:
        key = para.lower().strip()
        if key in heading_like:
            lines.append(f"## {para.title() if para.isupper() else para}")
            lines.append("")
            continue
        if len(para) < 80 and not para.endswith("."):
            # Role headers like "Senior Development Expert & AI Tech Lead"
            if any(ch.isdigit() for ch in para) and "|" in para:
                lines.append(f"### {para}")
            elif "University" in para or "B.Sc" in para:
                lines.append(f"### {para}")
            elif para.endswith("Present") or re.search(r"\d{4}\s*[–-]", para):
                lines.append(f"### {para}")
            else:
                lines.append(para)
        else:
            lines.append(para)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def search_preferences_block() -> str:
    locations = os.getenv(
        "JOB_SEARCH_LOCATIONS",
        "Jerusalem, Shfela, Beit Shemesh, Hybrid, Remote",
    )
    home = os.getenv("JOB_SEARCH_HOME_LOCATION", "Beit Shemesh, Israel")
    home_short = home.split(",")[0].strip() or home
    notes = os.getenv("JOB_SEARCH_CV_NOTES", "").strip()
    lines = [
        "## Job search preferences",
        "",
        f"**Home location:** {home}",
        f"**Preferred work areas:** {locations}",
        "- **Industry focus:** Israeli hi-tech only (software, AI/ML, R&D, architecture, DevOps)",
        f"- Prioritize preferred work areas above over distant on-site roles (commute from {home_short})",
        f"- Open to hybrid and remote hi-tech roles that fit the commute from {home_short}",
        "- Skip junior-only roles unless strongly AI-focused",
        "- Skip non-tech roles at banks, insurance, and government",
        "- Prefer stable employers and clear apply links",
    ]
    if notes:
        lines.append(f"- {notes}")
    lines.append("")
    return "\n".join(lines)


def pdf_to_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def pdf_to_markdown(path: Path) -> str:
    body = pdf_to_text(path)
    if not body:
        return ""
    return f"# Candidate CV\n\n{body.strip()}\n"


def source_to_markdown(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_to_markdown(path)
    if suffix == ".pdf":
        return pdf_to_markdown(path)
    if suffix == ".md":
        return path.read_text(encoding="utf-8")
    raise RuntimeError(f"Unsupported CV format: {path.suffix} ({path})")


def sync_markdown_from_docx(*, force: bool = False) -> Path | None:
    """Write markdown cache from CV source (DOCX/PDF) when source is newer."""
    source = cv_source_path()
    if source and source.is_file() and source.suffix.lower() in (".docx", ".pdf"):
        cv_file = source
    else:
        docx = docx_path()
        if not docx or not docx.is_file():
            return None
        cv_file = docx

    md = _resolve(md_path())
    cv_mtime = cv_file.stat().st_mtime
    md_mtime = md.stat().st_mtime if md.is_file() else 0
    if not force and md.is_file() and md_mtime >= cv_mtime:
        return md

    body = source_to_markdown(cv_file)
    preferences = search_preferences_block()
    synced = (
        f"{body}\n\n---\n\n{preferences}\n\n"
        f"<!-- Auto-synced from {cv_file.name} on {datetime.now().isoformat(timespec='seconds')} -->\n"
    )
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(synced, encoding="utf-8")
    return md


def load_cv() -> str:
    """Load CV text for LLM prompts — PDF/DOCX source of truth when configured."""
    source = cv_source_path()
    docx = docx_path()
    md = _resolve(md_path())

    if os.getenv("JOB_SEARCH_SYNC_MD_FROM_DOCX", "1").lower() in ("1", "true", "yes"):
        sync_markdown_from_docx()

    parts: list[str] = []

    if source and source.is_file():
        parts.append(source_to_markdown(source))
    elif docx and docx.is_file():
        parts.append(docx_to_markdown(docx))
    elif md.is_file():
        text = md.read_text(encoding="utf-8")
        text = re.sub(r"\n---\n\n## Job search preferences[\s\S]*", "", text)
        text = re.sub(r"<!-- Auto-synced[\s\S]*?-->\n?", "", text)
        parts.append(text.strip())
    else:
        raise RuntimeError(
            "No CV found. Set cv_source in a job profile JSON, or JOB_SEARCH_CV_SOURCE / "
            "JOB_SEARCH_CV_DOCX / JOB_SEARCH_CV_PATH in .env"
        )

    parts.append(search_preferences_block())

    combined = "\n\n".join(p for p in parts if p.strip())
    if len(combined) < 100:
        raise RuntimeError("CV content is too short — check your DOCX or markdown file.")
    return combined
