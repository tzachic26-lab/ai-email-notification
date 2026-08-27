"""Unit tests for job_search_store (history parsing, dedup keys, markdown IO)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import job_search_store as store

HISTORY_SAMPLE = """# Job Search History

Tracked job listings.

---

## 2026-08-01 | AI Engineer

**Company:** Acme
**Position ID:** 12345
**URL:** https://www.linkedin.com/jobs/view/4239812345
**Location:** Tel Aviv
**Match score:** 87%
**Source:** LinkedIn
**Posted:** 2026-07-30

### Why it matches
- Python depth
- LLM experience

### Description
Build retrieval pipelines for production LLM apps.

### Key requirements
- Python
- Vector databases

---

## 2026-08-02 | Data Scientist

**Company:** Globex
**Position ID:** —
**URL:** https://boards.greenhouse.io/globex/jobs/7
**Location:** Jerusalem
**Match score:** not-a-number
**Source:** Greenhouse
**Posted:** unknown

### Description
Own experimentation and modeling.
"""


def _record(**overrides) -> store.JobRecord:
    base = {
        "iso_date": "2026-08-01",
        "title": "AI Engineer",
        "company": "Acme",
        "position_id": "12345",
        "url": "https://acme.com/job/1",
        "location": "Tel Aviv",
        "match_score": 87,
        "source": "LinkedIn",
        "description": "Build things",
        "markdown_body": "",
    }
    base.update(overrides)
    return store.JobRecord(**base)


def test_summary_line_with_and_without_position_id():
    assert _record().summary_line == "Acme | AI Engineer #12345 (87%)"
    assert _record(position_id="").summary_line == "Acme | AI Engineer (87%)"


def test_history_path_prefers_env(monkeypatch, tmp_path):
    monkeypatch.delenv("JOB_SEARCH_HISTORY_FILE", raising=False)
    assert store.history_path() == store.DEFAULT_HISTORY_PATH
    target = tmp_path / "custom.md"
    monkeypatch.setenv("JOB_SEARCH_HISTORY_FILE", str(target))
    assert store.history_path() == target


def test_ensure_history_file_creates_seed_content(tmp_path):
    target = tmp_path / "nested" / "history.md"
    created = store.ensure_history_file(target)
    assert created == target
    assert target.read_text(encoding="utf-8").startswith("# Job Search History")


def test_ensure_history_file_keeps_existing_content(tmp_path):
    target = tmp_path / "history.md"
    target.write_text("existing", encoding="utf-8")
    store.ensure_history_file(target)
    assert target.read_text(encoding="utf-8") == "existing"


def test_normalize_text_and_url():
    assert store._normalize_text("  Acme   Corp \n") == "acme corp"
    assert store._normalize_url("HTTPS://WWW.Acme.com/job/1/") == "acme.com/job/1"
    assert store._normalize_url("") == ""


def test_description_fingerprint_is_stable_and_whitespace_insensitive():
    first = store.description_fingerprint("Build   retrieval pipelines")
    assert first == store.description_fingerprint("build retrieval pipelines")
    assert len(first) == 16
    assert store.description_fingerprint("   ") == ""


def test_dedupe_key_includes_all_available_identifiers():
    keys = store.dedupe_key(_record())
    assert keys[0] == "pid:12345"
    assert keys[1] == "url:acme.com/job/1"
    assert "ct:acme|ai engineer" in keys
    assert any(key.startswith("cd:acme|") for key in keys)


def test_dedupe_key_skips_missing_fields():
    keys = store.dedupe_key(_record(position_id="", url="", description=""))
    assert keys == ("ct:acme|ai engineer",)


def test_parse_jobs_reads_fields_and_sections():
    records = store.parse_jobs(HISTORY_SAMPLE)
    assert [r.title for r in records] == ["AI Engineer", "Data Scientist"]

    first = records[0]
    assert first.iso_date == "2026-08-01"
    assert first.company == "Acme"
    assert first.position_id == "12345"
    assert first.match_score == 87
    assert first.location == "Tel Aviv"
    assert first.source == "LinkedIn"
    assert first.description == "Build retrieval pipelines for production LLM apps."
    assert "## 2026-08-01 | AI Engineer" in first.markdown_body


def test_parse_jobs_defaults_unparsable_score_to_zero():
    assert store.parse_jobs(HISTORY_SAMPLE)[1].match_score == 0


def test_parse_jobs_ignores_chunks_without_entry_header():
    assert store.parse_jobs("# Job Search History\n\nNothing tracked yet.\n") == []


def test_markdown_section_missing_heading():
    assert store._markdown_section("### Description\nbody\n", "Key requirements") == ""


def test_load_history_creates_file_and_parses(tmp_path):
    target = tmp_path / "history.md"
    target.write_text(HISTORY_SAMPLE, encoding="utf-8")
    path, records = store.load_history(target)
    assert path == target
    assert len(records) == 2


def test_seen_dedupe_keys_union():
    keys = store.seen_dedupe_keys([_record(), _record(company="Globex", position_id="99")])
    assert "pid:12345" in keys
    assert "pid:99" in keys
    assert "ct:globex|ai engineer" in keys


def test_is_duplicate_matches_on_any_key():
    seen = store.seen_dedupe_keys([_record()])
    assert store.is_duplicate(company="Acme", title="AI Engineer", seen_keys=seen) is True
    assert store.is_duplicate(company="Other", title="Other", position_id="12345", seen_keys=seen) is True
    assert (
        store.is_duplicate(
            company="Other", title="Other", url="https://www.acme.com/job/1", seen_keys=seen
        )
        is True
    )
    assert store.is_duplicate(company="Globex", title="Data Scientist", seen_keys=seen) is False


def test_history_context_for_llm_empty_and_populated():
    assert store.history_context_for_llm([]) == "No prior job listings tracked."
    context = store.history_context_for_llm([_record()])
    assert "Acme | AI Engineer #12345 (87%)" in context
    assert "position_id: 12345" in context
    assert "url: https://acme.com/job/1" in context


def test_history_context_for_llm_limits_entries():
    records = [_record(title=f"Role {i}", position_id="", url="") for i in range(10)]
    context = store.history_context_for_llm(records, max_entries=3)
    assert "Role 9" in context
    assert "Role 6" not in context


def test_format_job_markdown_round_trips_through_parse_jobs():
    markdown = store.format_job_markdown(
        iso_date="2026-08-05",
        company="Acme",
        title="AI Engineer",
        position_id="98765",
        url="https://acme.com/job/9",
        location="Tel Aviv",
        employment_type="Full-time",
        match_score=91,
        match_reasons=["Python", ""],
        source="LinkedIn",
        posted_date="2026-08-01",
        description="Own the RAG stack.",
        requirements=["Python"],
    )
    parsed = store.parse_jobs(markdown)
    assert len(parsed) == 1
    assert parsed[0].company == "Acme"
    assert parsed[0].match_score == 91
    assert parsed[0].description == "Own the RAG stack."
    assert store._match_reasons_from_chunk(markdown) == ["Python"]
    assert store._requirements_from_chunk(markdown) == ["Python"]


def test_format_job_markdown_placeholders_and_optional_lines():
    markdown = store.format_job_markdown(
        iso_date="2026-08-05",
        company="Acme",
        title="AI Engineer",
        position_id="",
        url="",
        location="",
        employment_type="",
        match_score=0,
        match_reasons=[],
        source="LLM",
        posted_date="",
        description="",
        requirements=[],
        apply_email="jobs@acme.com",
        apply_method="Email",
        url_status="unavailable",
        url_hint="https://acme.com/careers",
        link_note="Link could not be verified",
    )
    assert "**Position ID:** —" in markdown
    assert "**URL:** https://acme.com/careers" in markdown
    assert "**Apply email:** jobs@acme.com" in markdown
    assert "**Apply method:** Email" in markdown
    assert "**Link status:** unavailable" in markdown
    assert "**Link note:** Link could not be verified" in markdown
    assert "**Posted:** unknown" in markdown
    assert store._match_reasons_from_chunk(markdown) == []


def test_append_jobs_adds_separator_and_keeps_history(tmp_path):
    target = tmp_path / "history.md"
    store.ensure_history_file(target)
    entry = "## 2026-08-05 | AI Engineer\n\n**Company:** Acme\n"
    store.append_jobs([entry, "   "], target)
    text = target.read_text(encoding="utf-8")
    assert "# Job Search History" in text
    assert "\n---\n" in text
    assert len(store.parse_jobs(text)) == 1


def test_today_iso_matches_current_date():
    assert store.today_iso() == date.today().isoformat()


def test_get_records_for_date_filters(tmp_path):
    target = tmp_path / "history.md"
    target.write_text(HISTORY_SAMPLE, encoding="utf-8")
    records = store.get_records_for_date("2026-08-02", target)
    assert [r.title for r in records] == ["Data Scientist"]
    assert store.get_records_for_date("1999-01-01", target) == []


@pytest.fixture
def offline_quality(monkeypatch):
    """Keep purge logic offline: URLs pass through and verify as reachable."""
    import job_search_quality as q

    monkeypatch.setattr(q, "is_url_reachable", lambda url, position_id="": True)
    monkeypatch.setattr(q, "is_linkedin_job_open", lambda url, position_id="": True)


def test_purge_invalid_history_entries_keeps_valid(tmp_path, offline_quality):
    target = tmp_path / "history.md"
    target.write_text(HISTORY_SAMPLE, encoding="utf-8")
    path, kept, removed = store.purge_invalid_history_entries(target)
    assert path == target
    assert (kept, removed) == (2, 0)
    assert len(store.parse_jobs(target.read_text(encoding="utf-8"))) == 2


def test_purge_invalid_history_entries_drops_thin_entry(tmp_path, offline_quality):
    target = tmp_path / "history.md"
    target.write_text(
        "# Job Search History\n\n---\n\n"
        "## 2026-08-01 | AI Engineer\n\n"
        "**Position ID:** —\n"
        "**URL:** https://boards.greenhouse.io/acme/jobs/1\n"
        "**Match score:** 0%\n"
        "**Posted:** unknown\n\n"
        "### Why it matches\n- —\n\n"
        "### Description\n\n\n"
        "### Key requirements\n- —\n",
        encoding="utf-8",
    )
    _, kept, removed = store.purge_invalid_history_entries(target)
    assert (kept, removed) == (0, 1)
    assert store.parse_jobs(target.read_text(encoding="utf-8")) == []


def test_purge_invalid_history_entries_marks_dead_link(tmp_path, monkeypatch):
    import job_search_quality as q

    monkeypatch.setattr(q, "is_url_reachable", lambda url, position_id="": False)
    monkeypatch.setattr(q, "is_linkedin_job_open", lambda url, position_id="": True)
    target = tmp_path / "history.md"
    target.write_text(
        "# Job Search History\n\n---\n\n"
        "## 2026-08-01 | AI Engineer\n\n"
        "**Company:** Acme\n"
        "**Position ID:** —\n"
        "**URL:** https://boards.greenhouse.io/acme/jobs/1\n"
        "**Match score:** 88%\n"
        "**Posted:** unknown\n\n"
        "### Description\nBuild retrieval pipelines.\n",
        encoding="utf-8",
    )
    _, kept, removed = store.purge_invalid_history_entries(target)
    text = target.read_text(encoding="utf-8")
    assert (kept, removed) == (1, 0)
    assert "**URL:** — (no working link)" in text
    assert "**Link status:** unavailable" in text


def test_purge_invalid_history_entries_on_empty_history(tmp_path, offline_quality):
    target = tmp_path / "history.md"
    path, kept, removed = store.purge_invalid_history_entries(target)
    assert (kept, removed) == (0, 0)
    assert Path(path).read_text(encoding="utf-8").startswith("# Job Search History")
