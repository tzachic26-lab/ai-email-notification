"""Unit tests for job_search_profile (profile loading and env application)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import job_search_profile as jsp


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch) -> Path:
    directory = tmp_path / "job_profiles"
    directory.mkdir()
    monkeypatch.setattr(jsp, "PROFILES_DIR", directory)
    return directory


def _write_profile(directory: Path, profile_id: str, data: dict) -> Path:
    path = directory / f"{profile_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_resolve_path_keeps_absolute_and_anchors_relative(tmp_path):
    absolute = tmp_path / "cv.pdf"
    assert jsp._resolve_path(absolute) == absolute
    assert jsp._resolve_path("data/cv.pdf") == jsp.APP_DIR / "data" / "cv.pdf"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        ("a@x.com", ["a@x.com"]),
        ("a@x.com, b@x.com; c@x.com", ["a@x.com", "b@x.com", "c@x.com"]),
        ([" a@x.com ", "", "b@x.com"], ["a@x.com", "b@x.com"]),
    ],
)
def test_parse_emails(raw, expected):
    assert jsp._parse_emails(raw) == expected


def test_profile_defaults_derived_in_post_init(tmp_path):
    profile = jsp.JobSearchProfile(
        id="dana",
        display_name="Dana Cohen",
        cv_source=Path("data/dana.pdf"),
        to_emails=["dana@example.com", "second@example.com"],
    )
    assert profile.cv_source == jsp.APP_DIR / "data" / "dana.pdf"
    assert profile.candidate_name == "Dana Cohen"
    assert profile.candidate_email == "dana@example.com"
    assert profile.log_stem == "job_search_dana"
    assert profile.log_file == jsp.APP_DIR / "logs" / "job_search_dana.log"
    assert profile.preview_file == jsp.APP_DIR / "logs" / "job_search_dana_preview.html"
    assert profile.email_subject_prefix() == "Job Search — Dana Cohen"


def test_profile_keeps_explicit_values(tmp_path):
    profile = jsp.JobSearchProfile(
        id="dana",
        display_name="Dana Cohen",
        cv_source=tmp_path / "cv.pdf",
        to_emails=["dana@example.com"],
        candidate_name="D. Cohen",
        candidate_email="other@example.com",
        history_file=Path("data/history.md"),
        cv_md_cache=tmp_path / "cv.md",
        log_stem="custom_stem",
    )
    assert profile.candidate_name == "D. Cohen"
    assert profile.candidate_email == "other@example.com"
    assert profile.history_file == jsp.APP_DIR / "data" / "history.md"
    assert profile.cv_md_cache == tmp_path / "cv.md"
    assert profile.log_stem == "custom_stem"


def test_list_profiles_sorted_and_empty(profiles_dir, tmp_path, monkeypatch):
    assert jsp.list_profiles() == []
    _write_profile(profiles_dir, "zoe", {"cv_source": "cv.pdf"})
    _write_profile(profiles_dir, "dana", {"cv_source": "cv.pdf"})
    assert jsp.list_profiles() == ["dana", "zoe"]

    monkeypatch.setattr(jsp, "PROFILES_DIR", tmp_path / "missing")
    assert jsp.list_profiles() == []


def test_load_profile_full_payload(profiles_dir):
    _write_profile(
        profiles_dir,
        "dana",
        {
            "id": "dana",
            "display_name": "Dana Cohen",
            "cv_source": "data/dana.pdf",
            "to_emails": "dana@example.com, hr@example.com",
            "bcc_emails": ["archive@example.com"],
            "candidate_name": "Dana C.",
            "candidate_email": "dana@example.com",
            "candidate_phone": "+972-50-0000000",
            "locations": "Jerusalem, Remote",
            "home_location": "Jerusalem",
            "keywords": "LLM, RAG",
            "notes": "Prefers hybrid",
            "company_watchlist": "Acme",
            "log_stem": "dana_run",
        },
    )
    profile = jsp.load_profile("dana")
    assert profile.display_name == "Dana Cohen"
    assert profile.to_emails == ["dana@example.com", "hr@example.com"]
    assert profile.bcc_emails == ["archive@example.com"]
    assert profile.locations == "Jerusalem, Remote"
    assert profile.home_location == "Jerusalem"
    assert profile.keywords == "LLM, RAG"
    assert profile.log_stem == "dana_run"


def test_load_profile_applies_defaults_and_legacy_keys(profiles_dir):
    _write_profile(profiles_dir, "dana", {"cv_path": "cv.pdf", "to": "dana@example.com"})
    profile = jsp.load_profile("dana")
    assert profile.id == "dana"
    assert profile.display_name == "dana"
    assert profile.to_emails == ["dana@example.com"]
    assert profile.history_file == profiles_dir / "dana_history.md"
    assert profile.cv_md_cache == profiles_dir / "dana_cv.md"
    assert profile.locations == jsp.JobSearchProfile.locations
    assert profile.home_location == "Israel"


def test_load_profile_missing_file_lists_available(profiles_dir):
    _write_profile(profiles_dir, "dana", {"cv_source": "cv.pdf"})
    with pytest.raises(FileNotFoundError) as excinfo:
        jsp.load_profile("nobody")
    assert "Available: dana" in str(excinfo.value)


def test_load_profile_requires_cv_source(profiles_dir):
    _write_profile(profiles_dir, "dana", {"to_emails": "dana@example.com"})
    with pytest.raises(ValueError, match="missing cv_source"):
        jsp.load_profile("dana")


def _sample_profile(tmp_path) -> jsp.JobSearchProfile:
    return jsp.JobSearchProfile(
        id="dana",
        display_name="Dana Cohen",
        cv_source=tmp_path / "cv.pdf",
        to_emails=["dana@example.com"],
        bcc_emails=["archive@example.com"],
        candidate_phone="+972-50-0000000",
        keywords="LLM",
    )


def test_apply_profile_to_env_sets_and_clears(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_CV_DOCX", "legacy.docx")
    monkeypatch.delenv("JOB_SEARCH_PROFILE_ID", raising=False)
    profile = _sample_profile(tmp_path)

    snapshot = jsp.apply_profile_to_env(profile)

    assert os.environ["JOB_SEARCH_PROFILE_ID"] == "dana"
    assert os.environ["JOB_SEARCH_PROFILE_NAME"] == "Dana Cohen"
    assert os.environ["JOB_SEARCH_TO"] == "dana@example.com"
    assert os.environ["JOB_SEARCH_BCC"] == "archive@example.com"
    assert os.environ["JOB_SEARCH_CANDIDATE_EMAIL"] == "dana@example.com"
    # empty values are removed rather than set to ""
    assert "JOB_SEARCH_CV_DOCX" not in os.environ
    assert "JOB_SEARCH_CV_NOTES" not in os.environ
    assert snapshot["JOB_SEARCH_CV_DOCX"] == "legacy.docx"
    assert snapshot["JOB_SEARCH_PROFILE_ID"] is None

    jsp.restore_env(snapshot)
    assert os.environ["JOB_SEARCH_CV_DOCX"] == "legacy.docx"
    assert "JOB_SEARCH_PROFILE_ID" not in os.environ


def test_profile_context_restores_previous_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_PROFILE_ID", "previous")
    profile = _sample_profile(tmp_path)

    with jsp.profile_context(profile) as active:
        assert active is profile
        assert os.environ["JOB_SEARCH_PROFILE_ID"] == "dana"

    assert os.environ["JOB_SEARCH_PROFILE_ID"] == "previous"


def test_profile_context_restores_env_on_exception(tmp_path, monkeypatch):
    monkeypatch.delenv("JOB_SEARCH_PROFILE_ID", raising=False)
    profile = _sample_profile(tmp_path)

    with pytest.raises(RuntimeError):
        with jsp.profile_context(profile):
            raise RuntimeError("boom")

    assert "JOB_SEARCH_PROFILE_ID" not in os.environ
