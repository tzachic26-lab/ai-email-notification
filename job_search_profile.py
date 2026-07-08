"""Job search profiles — one JSON config per candidate (CV, email, history, preferences)."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROFILES_DIR = APP_DIR / "data" / "job_profiles"


@dataclass
class JobSearchProfile:
    id: str
    display_name: str
    cv_source: Path
    to_emails: list[str]
    bcc_emails: list[str] = field(default_factory=list)
    candidate_name: str = ""
    candidate_email: str = ""
    candidate_phone: str = ""
    history_file: Path | None = None
    cv_md_cache: Path | None = None
    locations: str = "Jerusalem, Shfela, Beit Shemesh, Hybrid, Remote, Israel"
    home_location: str = "Israel"
    keywords: str = ""
    notes: str = ""
    company_watchlist: str = ""
    log_stem: str = ""

    def __post_init__(self) -> None:
        self.cv_source = _resolve_path(self.cv_source)
        if self.history_file:
            self.history_file = _resolve_path(self.history_file)
        if self.cv_md_cache:
            self.cv_md_cache = _resolve_path(self.cv_md_cache)
        if not self.candidate_name:
            self.candidate_name = self.display_name
        if not self.candidate_email and self.to_emails:
            self.candidate_email = self.to_emails[0]
        if not self.log_stem:
            self.log_stem = f"job_search_{self.id}"

    @property
    def log_file(self) -> Path:
        return APP_DIR / "logs" / f"{self.log_stem}.log"

    @property
    def preview_file(self) -> Path:
        return APP_DIR / "logs" / f"{self.log_stem}_preview.html"

    def email_subject_prefix(self) -> str:
        return f"Job Search — {self.display_name}"


def _resolve_path(path: Path | str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else APP_DIR / p


def _parse_emails(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [part.strip() for part in re.split(r"[,;]+", str(raw)) if part.strip()]


def list_profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))


def load_profile(profile_id: str) -> JobSearchProfile:
    path = PROFILES_DIR / f"{profile_id}.json"
    if not path.is_file():
        available = ", ".join(list_profiles()) or "(none)"
        raise FileNotFoundError(f"Profile '{profile_id}' not found at {path}. Available: {available}")
    data = json.loads(path.read_text(encoding="utf-8"))
    profile_id = str(data.get("id") or profile_id).strip()
    cv_raw = data.get("cv_source") or data.get("cv_path") or data.get("cv")
    if not cv_raw:
        raise ValueError(f"Profile {profile_id} missing cv_source")
    history = data.get("history_file")
    md_cache = data.get("cv_md_cache")
    return JobSearchProfile(
        id=profile_id,
        display_name=str(data.get("display_name") or profile_id).strip(),
        cv_source=Path(cv_raw),
        to_emails=_parse_emails(data.get("to_emails") or data.get("to_email") or data.get("to")),
        bcc_emails=_parse_emails(data.get("bcc_emails") or data.get("bcc")),
        candidate_name=str(data.get("candidate_name") or "").strip(),
        candidate_email=str(data.get("candidate_email") or "").strip(),
        candidate_phone=str(data.get("candidate_phone") or "").strip(),
        history_file=Path(history) if history else PROFILES_DIR / f"{profile_id}_history.md",
        cv_md_cache=Path(md_cache) if md_cache else PROFILES_DIR / f"{profile_id}_cv.md",
        locations=str(data.get("locations") or JobSearchProfile.locations),
        home_location=str(data.get("home_location") or "Israel"),
        keywords=str(data.get("keywords") or "").strip(),
        notes=str(data.get("notes") or "").strip(),
        company_watchlist=str(data.get("company_watchlist") or "").strip(),
        log_stem=str(data.get("log_stem") or f"job_search_{profile_id}"),
    )


def apply_profile_to_env(profile: JobSearchProfile) -> dict[str, str | None]:
    """Apply profile settings to os.environ. Returns snapshot of overridden keys."""
    snapshot: dict[str, str | None] = {}
    updates = {
        "JOB_SEARCH_PROFILE_ID": profile.id,
        "JOB_SEARCH_PROFILE_NAME": profile.display_name,
        "JOB_SEARCH_CV_SOURCE": str(profile.cv_source),
        "JOB_SEARCH_HISTORY_FILE": str(profile.history_file) if profile.history_file else "",
        "JOB_SEARCH_CV_PATH": str(profile.cv_md_cache) if profile.cv_md_cache else "",
        "JOB_SEARCH_CANDIDATE_NAME": profile.candidate_name,
        "JOB_SEARCH_CANDIDATE_EMAIL": profile.candidate_email,
        "JOB_SEARCH_CANDIDATE_PHONE": profile.candidate_phone,
        "JOB_SEARCH_LOCATIONS": profile.locations,
        "JOB_SEARCH_HOME_LOCATION": profile.home_location,
        "JOB_SEARCH_KEYWORDS": profile.keywords,
        "JOB_SEARCH_CV_NOTES": profile.notes,
        "JOB_SEARCH_COMPANY_WATCHLIST": profile.company_watchlist,
        "JOB_SEARCH_TO": ",".join(profile.to_emails),
        "JOB_SEARCH_BCC": ",".join(profile.bcc_emails),
        # Clear legacy docx-only override so cv_source drives loading
        "JOB_SEARCH_CV_DOCX": "",
    }
    for key, value in updates.items():
        snapshot[key] = os.environ.get(key)
        if value:
            os.environ[key] = value
        elif key in os.environ:
            del os.environ[key]
    return snapshot


def restore_env(snapshot: dict[str, str | None]) -> None:
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@contextmanager
def profile_context(profile: JobSearchProfile):
    snapshot = apply_profile_to_env(profile)
    try:
        yield profile
    finally:
        restore_env(snapshot)
