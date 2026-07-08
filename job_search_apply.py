"""Job application helpers: mailto drafts and optional CV email send."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote

APP_DIR = Path(__file__).resolve().parent
DEFAULT_CV_DOCX = APP_DIR / "data" / "cv.docx"


def cv_docx_path() -> Path:
    raw = os.getenv("JOB_SEARCH_CV_DOCX", "")
    return Path(raw) if raw else DEFAULT_CV_DOCX


def candidate_contact() -> tuple[str, str, str]:
    """Return (name, email, phone) from env or CV defaults."""
    name = (os.getenv("JOB_SEARCH_CANDIDATE_NAME") or "Your Name").strip()
    email = (os.getenv("JOB_SEARCH_CANDIDATE_EMAIL") or "you@example.com").strip()
    phone = (os.getenv("JOB_SEARCH_CANDIDATE_PHONE") or "").strip()
    return name, email, phone


def normalize_apply_email(raw: str) -> str:
    value = (raw or "").strip()
    if not value or value in ("—", "unknown", "n/a"):
        return ""
    match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", value)
    return match.group(0) if match else ""


def build_application_mailto(
    *,
    apply_email: str,
    company: str,
    title: str,
) -> str:
    email = normalize_apply_email(apply_email)
    if not email:
        return ""
    name, candidate_email, phone = candidate_contact()
    subject = f"Application: {title} — {name}"
    body = (
        f"Dear {company} hiring team,\n\n"
        f"I am writing to apply for the {title} position.\n\n"
        f"I am a Solution Architect and AI Engineer with 25+ years of experience in "
        f"Java microservices, LLM/RAG integration, and technical leadership. "
        f"My CV is attached / available on request.\n\n"
        f"I would welcome the opportunity to discuss how my background fits your team.\n\n"
        f"Best regards,\n{name}\n{candidate_email}\n{phone}"
    )
    return f"mailto:{quote(email)}?subject={quote(subject)}&body={quote(body)}"


def auto_apply_enabled() -> bool:
    return os.getenv("JOB_SEARCH_AUTO_APPLY_EMAIL", "0").lower() in ("1", "true", "yes")
