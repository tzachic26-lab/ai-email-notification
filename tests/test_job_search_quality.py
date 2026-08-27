"""Unit tests for job_search_quality (URL validation, scores, listing filters)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import job_search_quality as q


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Reset module caches and env toggles so tests never hit the network."""
    q._url_reachable_cache.clear()
    q._linkedin_open_cache.clear()
    for key in (
        "JOB_SEARCH_LINKEDIN_VERIFY",
        "JOB_SEARCH_URL_VERIFY",
        "JOB_SEARCH_ALLOW_PA_LOCATIONS",
        "JOB_SEARCH_LOCATION_BLOCKLIST",
        "JOB_SEARCH_MAX_POSTED_DAYS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(q, "_ensure_truststore", lambda: None)


class FakeResponse:
    def __init__(self, status_code=200, url="", text=""):
        self.status_code = status_code
        self.url = url
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# --- parse_match_score ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("85", 85), ("85%", 85), (" 85 ", 85), (72.6, 72), (150, 100), (-5, 0)],
)
def test_parse_match_score_parses_and_clamps(raw, expected):
    assert q.parse_match_score(raw, match_reasons=[], requirements=[]) == expected


@pytest.mark.parametrize("raw", [None, "", "not-a-number"])
def test_parse_match_score_zero_without_context(raw):
    assert q.parse_match_score(raw, match_reasons=[], requirements=[]) == 0


def test_parse_match_score_recovers_from_reasons_and_requirements():
    assert q.parse_match_score(None, match_reasons=["a", "b"], requirements=["x"]) == 82
    assert q.parse_match_score(None, match_reasons=["a"], requirements=[]) == 75


def test_parse_match_score_recovery_is_capped_at_95():
    assert (
        q.parse_match_score(None, match_reasons=["a"] * 10, requirements=["r"] * 10) == 95
    )


# --- fake / invented URL detection --------------------------------------


@pytest.mark.parametrize("pid", ["12345", "67890", "123456789", "6789", "1162385"])
def test_is_fake_position_id_true(pid):
    assert q._is_fake_position_id(pid) is True


@pytest.mark.parametrize("pid", ["", "—", "unknown", "4239812345", "1162500", "ABC-123"])
def test_is_fake_position_id_false(pid):
    assert q._is_fake_position_id(pid) is False


def test_looks_hallucinated_url_for_known_fake_patterns():
    assert q._looks_hallucinated_url("https://builtin.com/job/engineer/1162385") is True
    assert q._looks_hallucinated_url("https://meetfrank.com/jobs/ai-engineer-12345") is True
    assert q._looks_hallucinated_url("") is True


def test_looks_hallucinated_url_allows_grounding_redirect():
    url = f"https://{q._GROUNDING_HOST}/grounding-api-redirect/abc"
    assert q._looks_hallucinated_url(url, position_id="12345") is False


def test_looks_hallucinated_url_fake_pid_on_untrusted_host():
    assert q._looks_hallucinated_url("https://acme-startup.io/job/1", position_id="12345") is True
    assert (
        q._looks_hallucinated_url(
            "https://www.linkedin.com/jobs/view/4239812345", position_id="12345"
        )
        is False
    )


def test_looks_invented_career_url_camel_case_and_year_slugs():
    assert q._looks_invented_career_url("https://acme.com/careers/LeadAIEngineer") is True
    assert q._looks_invented_career_url("https://acme.com/jobs/SA-2026") is True
    assert q._looks_invented_career_url("https://acme.com/careers/data-engineer-42") is False


def test_looks_invented_career_url_trusted_pattern_wins():
    assert (
        q._looks_invented_career_url("https://boards.greenhouse.io/careers/LeadAIEngineer")
        is False
    )


def test_looks_invented_career_url_from_invented_position_id():
    assert (
        q._looks_invented_career_url("https://acme.com/openings/42", position_id="ACME-2026")
        is True
    )
    assert (
        q._looks_invented_career_url("https://acme.com/openings/42", position_id="R12345") is False
    )
    assert q._looks_invented_career_url("https://acme.com/openings/42", position_id="") is False


def test_is_trusted_job_url_pattern():
    assert q._is_trusted_job_url_pattern("https://www.linkedin.com/jobs/view/4239812345") is True
    assert q._is_trusted_job_url_pattern("https://devjobs.co.il/job-details/12345") is True
    assert q._is_trusted_job_url_pattern("https://example.com/jobs/1") is False


def test_redirect_lost_job_page():
    assert q._redirect_lost_job_page("https://a.com/job/123", "https://a.com/careers") is True
    assert q._redirect_lost_job_page("https://a.com/job/123", "https://a.com/en/careers/") is True
    assert q._redirect_lost_job_page("https://a.com/job/123", "https://a.com/positions/9") is True
    assert q._redirect_lost_job_page("https://a.com/job/123", "https://a.com/job/123") is False
    assert q._redirect_lost_job_page("https://a.com", "https://a.com/careers") is False


# --- env toggles --------------------------------------------------------


def test_verify_toggles_default_enabled_and_can_be_disabled(monkeypatch):
    assert q.linkedin_verify_enabled() is True
    assert q.url_verify_enabled() is True
    monkeypatch.setenv("JOB_SEARCH_LINKEDIN_VERIFY", "0")
    monkeypatch.setenv("JOB_SEARCH_URL_VERIFY", "false")
    assert q.linkedin_verify_enabled() is False
    assert q.url_verify_enabled() is False


# --- LinkedIn helpers ---------------------------------------------------


def test_linkedin_job_id_from_url_and_position_id():
    assert q._linkedin_job_id("https://www.linkedin.com/jobs/view/4239812345", "") == "4239812345"
    assert (
        q._linkedin_job_id("https://www.linkedin.com/jobs/view/ai-engineer-at-acme-4239812345", "")
        == "4239812345"
    )
    assert q._linkedin_job_id("https://acme.com/job/1", "4239812345") == "4239812345"
    assert q._linkedin_job_id("https://acme.com/job/1", "123") == ""


def test_linkedin_canonical_url_normalizes_slug_urls():
    assert (
        q._linkedin_canonical_url("https://il.linkedin.com/jobs/view/ai-eng-at-acme-4239812345?x=1", "")
        == "https://www.linkedin.com/jobs/view/4239812345"
    )
    assert q._linkedin_canonical_url("https://acme.com/job/1", "4239812345") == (
        "https://www.linkedin.com/jobs/view/4239812345"
    )
    assert q._linkedin_canonical_url("https://acme.com/job/1", "12") == "https://acme.com/job/1"


def test_is_linkedin_job_open_returns_none_for_non_linkedin():
    assert q.is_linkedin_job_open("https://acme.com/job/1") is None
    assert q.is_linkedin_job_open("https://www.linkedin.com/jobs/view/abc") is None


def test_is_linkedin_job_open_true_when_page_has_no_closed_marker(monkeypatch):
    monkeypatch.setattr(q.requests, "get", lambda *a, **k: FakeResponse(200, text="Apply now"))
    assert q.is_linkedin_job_open("https://www.linkedin.com/jobs/view/4239812345") is True
    assert q._linkedin_open_cache == {"4239812345": True}


def test_is_linkedin_job_open_false_when_closed_marker_present(monkeypatch):
    body = "This job is No Longer Accepting Applications"
    monkeypatch.setattr(q.requests, "get", lambda *a, **k: FakeResponse(200, text=body))
    assert q.is_linkedin_job_open("https://www.linkedin.com/jobs/view/4239812345") is False


def test_is_linkedin_job_open_false_on_404(monkeypatch):
    monkeypatch.setattr(q.requests, "get", lambda *a, **k: FakeResponse(404))
    assert q.is_linkedin_job_open("https://www.linkedin.com/jobs/view/4239812345") is False


def test_is_linkedin_job_open_uses_cache(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(args)
        return FakeResponse(200, text="open")

    monkeypatch.setattr(q.requests, "get", fake_get)
    url = "https://www.linkedin.com/jobs/view/4239812345"
    assert q.is_linkedin_job_open(url) is True
    assert q.is_linkedin_job_open(url) is True
    assert len(calls) == 1


def test_is_linkedin_job_open_none_on_request_error(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(q.requests, "get", boom)
    assert q.is_linkedin_job_open("https://www.linkedin.com/jobs/view/4239812345") is None


# --- reachability -------------------------------------------------------


def test_is_url_reachable_empty_url():
    assert q.is_url_reachable("") is False


def test_is_url_reachable_short_circuits_invented_urls(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("no HTTP request expected")

    monkeypatch.setattr(q.requests, "head", boom)
    assert q.is_url_reachable("https://acme.com/careers/LeadAIEngineer") is False
    assert q.is_url_reachable("https://builtin.com/job/eng/1162385") is False


def test_is_url_reachable_true_for_ok_response(monkeypatch):
    url = "https://boards.greenhouse.io/acme/jobs/1"
    monkeypatch.setattr(q.requests, "head", lambda *a, **k: FakeResponse(200, url=url))
    assert q.is_url_reachable(url) is True
    assert q._url_reachable_cache[url] is True


def test_is_url_reachable_falls_back_to_get_when_head_unsupported(monkeypatch):
    url = "https://boards.greenhouse.io/acme/jobs/1"
    monkeypatch.setattr(q.requests, "head", lambda *a, **k: FakeResponse(405, url=url))
    monkeypatch.setattr(q.requests, "get", lambda *a, **k: FakeResponse(200, url=url))
    assert q.is_url_reachable(url) is True


@pytest.mark.parametrize("status", [404, 410, 500])
def test_is_url_reachable_false_for_error_statuses(monkeypatch, status):
    url = f"https://boards.greenhouse.io/acme/jobs/{status}"
    monkeypatch.setattr(q.requests, "head", lambda *a, **k: FakeResponse(status, url=url))
    assert q.is_url_reachable(url) is False


@pytest.mark.parametrize("status", [403, 429])
def test_is_url_reachable_none_for_blocked_statuses(monkeypatch, status):
    url = f"https://boards.greenhouse.io/acme/jobs/{status}"
    monkeypatch.setattr(q.requests, "head", lambda *a, **k: FakeResponse(status, url=url))
    assert q.is_url_reachable(url) is None
    assert url not in q._url_reachable_cache


def test_is_url_reachable_false_when_redirected_to_generic_careers(monkeypatch):
    url = "https://acme.com/job/lead-engineer-9"
    monkeypatch.setattr(
        q.requests, "head", lambda *a, **k: FakeResponse(200, url="https://acme.com/careers")
    )
    assert q.is_url_reachable(url) is False


def test_is_url_reachable_false_on_connection_error(monkeypatch):
    def boom(*args, **kwargs):
        raise q.requests.exceptions.ConnectionError("dns failure")

    monkeypatch.setattr(q.requests, "head", boom)
    assert q.is_url_reachable("https://acme-invented.io/job/1") is False


def test_is_url_reachable_none_on_unexpected_error(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("weird")

    monkeypatch.setattr(q.requests, "head", boom)
    assert q.is_url_reachable("https://acme.com/job/1") is None


def test_is_url_reachable_uses_cache(monkeypatch):
    url = "https://boards.greenhouse.io/acme/jobs/1"
    q._url_reachable_cache[url] = True
    monkeypatch.setattr(
        q.requests, "head", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cached"))
    )
    assert q.is_url_reachable(url) is True


# --- normalization ------------------------------------------------------


def test_normalize_job_url_builds_url_from_position_id():
    assert q.normalize_job_url("", position_id="4239812345") == (
        "https://www.linkedin.com/jobs/view/4239812345"
    )
    assert "joborderid=6712" in q.normalize_job_url("", position_id="6712")
    assert q.normalize_job_url("", position_id="") == ""


def test_normalize_job_url_adds_scheme():
    assert q.normalize_job_url("acme.com/job/1") == "https://acme.com/job/1"
    assert q.normalize_job_url("//acme.com/job/1") == "https://acme.com/job/1"


def test_normalize_job_url_canonicalizes_linkedin():
    assert q.normalize_job_url("https://il.linkedin.com/jobs/view/eng-4239812345?ref=x") == (
        "https://www.linkedin.com/jobs/view/4239812345"
    )


def test_normalize_job_url_adds_checkpoint_job_order_id():
    result = q.normalize_job_url("https://careers.checkpoint.com/index.php", position_id="6712")
    assert "joborderid=6712" in result


def test_normalize_job_url_resolves_grounding_redirect(monkeypatch):
    monkeypatch.setattr(
        q, "_resolve_grounding_redirect", lambda url: "https://acme.com/job/1"
    )
    url = f"https://{q._GROUNDING_HOST}/grounding-api-redirect/abc"
    assert q.normalize_job_url(url) == "https://acme.com/job/1"


def test_normalize_job_url_empty_when_grounding_redirect_unresolved(monkeypatch):
    monkeypatch.setattr(q, "_resolve_grounding_redirect", lambda url: "")
    url = f"https://{q._GROUNDING_HOST}/grounding-api-redirect/abc"
    assert q.normalize_job_url(url) == ""


def test_resolve_grounding_redirect_returns_final_url(monkeypatch):
    monkeypatch.setattr(
        q.requests, "get", lambda *a, **k: FakeResponse(200, url="https://acme.com/job/1")
    )
    url = f"https://{q._GROUNDING_HOST}/grounding-api-redirect/abc"
    assert q._resolve_grounding_redirect(url) == "https://acme.com/job/1"


def test_resolve_grounding_redirect_passthrough_for_other_hosts():
    assert q._resolve_grounding_redirect("https://acme.com/job/1") == "https://acme.com/job/1"


def test_resolve_grounding_redirect_empty_on_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("blocked")

    monkeypatch.setattr(q.requests, "get", boom)
    url = f"https://{q._GROUNDING_HOST}/grounding-api-redirect/abc"
    assert q._resolve_grounding_redirect(url) == ""


# --- posted date --------------------------------------------------------


def test_is_stale_posted_date():
    fresh = (date.today() - timedelta(days=3)).isoformat()
    stale = (date.today() - timedelta(days=90)).isoformat()
    assert q._is_stale_posted_date(fresh) is False
    assert q._is_stale_posted_date(stale) is True
    assert q._is_stale_posted_date("") is False
    assert q._is_stale_posted_date("unknown") is False
    assert q._is_stale_posted_date("not a date") is False


def test_is_stale_posted_date_respects_env_and_argument(monkeypatch):
    posted = (date.today() - timedelta(days=10)).isoformat()
    assert q._is_stale_posted_date(posted) is False
    monkeypatch.setenv("JOB_SEARCH_MAX_POSTED_DAYS", "5")
    assert q._is_stale_posted_date(posted) is True
    assert q._is_stale_posted_date(posted, max_days=30) is False


# --- evaluate_job_url / job_url_status ----------------------------------


def test_evaluate_job_url_missing_url():
    assert q.evaluate_job_url("") == ("unavailable", "No URL provided")


def test_evaluate_job_url_invented_and_hallucinated():
    status, note = q.evaluate_job_url("https://acme.com/careers/LeadAIEngineer")
    assert status == "unavailable"
    assert "guessed URL" in note
    assert q.evaluate_job_url("https://builtin.com/job/eng/1162385")[0] == "unavailable"


def test_evaluate_job_url_verified(monkeypatch):
    monkeypatch.setattr(q, "is_url_reachable", lambda url, position_id="": True)
    assert q.evaluate_job_url("https://boards.greenhouse.io/acme/jobs/1") == (
        "verified",
        "Link verified",
    )


def test_evaluate_job_url_unchecked_when_inconclusive(monkeypatch):
    monkeypatch.setattr(q, "is_url_reachable", lambda url, position_id="": None)
    status, note = q.evaluate_job_url("https://boards.greenhouse.io/acme/jobs/1")
    assert status == "unchecked"
    assert "not verified" in note


def test_evaluate_job_url_unavailable_when_dead(monkeypatch):
    monkeypatch.setattr(q, "is_url_reachable", lambda url, position_id="": False)
    assert q.evaluate_job_url("https://boards.greenhouse.io/acme/jobs/1")[0] == "unavailable"


def test_evaluate_job_url_skips_http_check_when_disabled(monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_URL_VERIFY", "0")
    monkeypatch.setattr(
        q,
        "is_url_reachable",
        lambda url, position_id="": (_ for _ in ()).throw(AssertionError("disabled")),
    )
    assert q.evaluate_job_url("https://boards.greenhouse.io/acme/jobs/1")[0] == "verified"


def test_evaluate_job_url_stale_linkedin(monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_URL_VERIFY", "0")
    stale = (date.today() - timedelta(days=90)).isoformat()
    status, note = q.evaluate_job_url(
        "https://www.linkedin.com/jobs/view/4239812345", posted_date=stale
    )
    assert status == "unavailable"
    assert "stale" in note


def test_evaluate_job_url_closed_linkedin(monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_URL_VERIFY", "0")
    monkeypatch.setattr(q, "is_linkedin_job_open", lambda url, position_id="": False)
    status, note = q.evaluate_job_url("https://www.linkedin.com/jobs/view/4239812345")
    assert status == "unavailable"
    assert "Closed on LinkedIn" in note


def test_job_url_status_returns_only_status(monkeypatch):
    monkeypatch.setattr(q, "is_url_reachable", lambda url, position_id="": True)
    assert q.job_url_status("https://boards.greenhouse.io/acme/jobs/1") == "verified"


# --- locations ----------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "Palestinian Authority",
        "Palestinian Territories",
        "Palestine",
        "Gaza Strip",
        "West Bank, Area C",
    ],
)
def test_is_blocked_job_location_true(location):
    assert q.is_blocked_job_location(location) is True


@pytest.mark.parametrize("location", ["Tel Aviv", "Jerusalem, Israel", "", "Remote — Israel"])
def test_is_blocked_job_location_false(location):
    assert q.is_blocked_job_location(location) is False


def test_is_blocked_job_location_allow_override(monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_ALLOW_PA_LOCATIONS", "1")
    assert q.pa_locations_allowed() is True
    assert q.is_blocked_job_location("Gaza Strip") is False


def test_is_blocked_job_location_extra_blocklist(monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_LOCATION_BLOCKLIST", "cyprus, malta")
    assert q.is_blocked_job_location("Limassol, Cyprus") is True
    assert q.is_blocked_job_location("Tel Aviv") is False


# --- listing substance / usability --------------------------------------


def test_has_listing_substance_requires_company_and_title():
    assert (
        q.has_listing_substance(
            company="", title="Engineer", match_reasons=["x"], requirements=[]
        )
        is False
    )
    assert (
        q.has_listing_substance(company="Acme", title=" ", match_reasons=["x"], requirements=[])
        is False
    )


def test_has_listing_substance_accepts_description_reasons_or_score():
    common = {"company": "Acme", "title": "AI Engineer"}
    assert q.has_listing_substance(**common, description="Real role", match_reasons=[], requirements=[])
    assert q.has_listing_substance(**common, match_reasons=["Strong Python"], requirements=[])
    assert q.has_listing_substance(**common, match_reasons=[], requirements=["Python"])
    assert q.has_listing_substance(**common, match_score=80, match_reasons=[], requirements=[])
    assert (
        q.has_listing_substance(**common, match_score=0, match_reasons=["—"], requirements=["-"])
        is False
    )


def test_is_usable_job_listing_accepts_good_listing():
    assert (
        q.is_usable_job_listing(
            url="https://boards.greenhouse.io/acme/jobs/1",
            company="Acme",
            title="AI Engineer",
            location="Tel Aviv",
            description="Build models",
            match_score=90,
            match_reasons=["Python"],
            requirements=["LLMs"],
        )
        is True
    )


def test_is_usable_job_listing_rejects_blocked_location_and_thin_listing():
    assert (
        q.is_usable_job_listing(
            company="Acme",
            title="AI Engineer",
            location="Gaza Strip",
            match_score=90,
            match_reasons=["Python"],
            requirements=[],
        )
        is False
    )
    assert (
        q.is_usable_job_listing(
            company="Acme",
            title="AI Engineer",
            match_score=0,
            match_reasons=[],
            requirements=[],
        )
        is False
    )


def test_is_usable_job_listing_drops_closed_linkedin_job(monkeypatch):
    monkeypatch.setattr(q, "is_linkedin_job_open", lambda url, position_id="": False)
    assert (
        q.is_usable_job_listing(
            url="https://www.linkedin.com/jobs/view/4239812345",
            company="Acme",
            title="AI Engineer",
            match_score=90,
            match_reasons=["Python"],
            requirements=[],
        )
        is False
    )


def test_is_usable_job_listing_drops_stale_linkedin_job():
    stale = (date.today() - timedelta(days=120)).isoformat()
    assert (
        q.is_usable_job_listing(
            url="https://www.linkedin.com/jobs/view/4239812345",
            company="Acme",
            title="AI Engineer",
            match_score=90,
            match_reasons=["Python"],
            requirements=[],
            posted_date=stale,
        )
        is False
    )


def test_is_usable_job_listing_verify_linkedin_flag_skips_checks():
    stale = (date.today() - timedelta(days=120)).isoformat()
    assert (
        q.is_usable_job_listing(
            url="https://www.linkedin.com/jobs/view/4239812345",
            company="Acme",
            title="AI Engineer",
            match_score=90,
            match_reasons=["Python"],
            requirements=[],
            posted_date=stale,
            verify_linkedin=False,
        )
        is True
    )
