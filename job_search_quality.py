"""Validate job listing URLs and match scores before save/email."""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_GROUNDING_HOST = "vertexaisearch.cloud.google.com"

_LINKEDIN_CLOSED_MARKERS = (
    "no longer accepting applications",
    "closed-job__flavor--closed",
)

_linkedin_open_cache: dict[str, bool] = {}
_url_reachable_cache: dict[str, bool] = {}
_truststore_injected = False

_FAKE_URL_PATTERNS = (
    re.compile(r"builtin\.com/job/[^/]+/11623\d{2}$", re.I),
    re.compile(r"meetfrank\.com/jobs/.*-(?:12345|67890)$", re.I),
    re.compile(r"careers\.google\.com/jobs/results/123456789/?$", re.I),
    re.compile(r"remotive\.com/remote-jobs/.*-\d{4}$", re.I),
)

_TRUSTED_JOB_URL_PATTERNS = (
    re.compile(r"linkedin\.com/jobs/view/\d{8,}", re.I),
    re.compile(r"alljobs\.co\.il/.*(?:JobID|positionid)=\d+", re.I),
    re.compile(r"jobnet\.co\.il/.*positionid=\d+", re.I),
    re.compile(r"drushim\.co\.il/", re.I),
    re.compile(r"jobmaster\.co\.il/", re.I),
    re.compile(r"gotfriends\.co\.il/", re.I),
    re.compile(r"comeet\.com/jobs/", re.I),
    re.compile(r"boards\.greenhouse\.io/", re.I),
    re.compile(r"jobs\.lever\.co/", re.I),
    re.compile(r"myworkdayjobs\.com/", re.I),
    re.compile(r"smartrecruiters\.com/", re.I),
    re.compile(r"joborderid=\d+", re.I),
    re.compile(r"il\.indeed\.com/viewjob", re.I),
)

_INVENTED_CAREER_PATH = re.compile(
    r"/(?:careers?|jobs?)/[^/?#]*(?:"
    r"[A-Z][a-z]+(?:[A-Z][a-z]+)+|"  # camelCase slugs e.g. LeadAIEngineer-2026
    r"[A-Za-z]{2,}-(?:20\d{2})(?:-\d{2})?(?:-\d{2})?"  # SA-2026, SA-2026-07-01
    r")/?$",
    re.I,
)

_INVENTED_POSITION_ID = re.compile(
    r"^(?:[A-Za-z]+[-_])+(?:20\d{2}|AI|SA|TA)(?:[-_/]|$)",
    re.I,
)

_GENERIC_CAREER_PATHS = (
    "/careers",
    "/jobs",
    "/en/careers",
    "/company/careers",
    "/about/careers",
)

_FAKE_POSITION_IDS = frozenset({"12345", "67890", "123456789", "6789"})

_LINKEDIN_JOB_ID_RE = re.compile(
    r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d{8,})",
    re.I,
)

_TRUSTED_JOB_HOSTS = (
    "linkedin.com",
    "il.linkedin.com",
    "alljobs.co.il",
    "drushim.co.il",
    "jobmaster.co.il",
    "jobnet.co.il",
    "gotfriends.co.il",
    "ethosia.co.il",
    "seeve.co.il",
    "winwin.co.il",
    "jobinfo.co.il",
    "il.indeed.com",
    "indeed.com",
    "comeet.co",
    "comeet.com",
    "jobs.ynet.co.il",
    "careers.",
    "jobs.",
    "checkpoint.com",
    "globallogic.com",
    "matrix.co.il",
    "ness.com",
)


def parse_match_score(raw: object, *, match_reasons: list[str], requirements: list[str]) -> int:
    score = 0
    if raw is not None and raw != "":
        text = str(raw).strip().rstrip("%")
        try:
            score = int(float(text))
        except (TypeError, ValueError):
            score = 0
    score = max(0, min(100, score))
    if score == 0 and (match_reasons or requirements):
        # Partial JSON recovery often drops match_score while keeping reasons.
        base = 70
        base += min(20, len(match_reasons) * 5)
        base += min(10, len(requirements) * 2)
        score = min(95, base)
    return score


def _is_fake_position_id(position_id: str) -> bool:
    pid = (position_id or "").strip()
    if not pid or pid in ("—", "unknown"):
        return False
    if pid in _FAKE_POSITION_IDS:
        return True
    if pid.isdigit():
        n = int(pid)
        if 1162380 <= n <= 1162400:
            return True
    return False


def linkedin_verify_enabled() -> bool:
    return os.getenv("JOB_SEARCH_LINKEDIN_VERIFY", "1").lower() in ("1", "true", "yes")


def url_verify_enabled() -> bool:
    return os.getenv("JOB_SEARCH_URL_VERIFY", "1").lower() in ("1", "true", "yes")


def _ensure_truststore() -> None:
    global _truststore_injected
    if not _truststore_injected:
        import truststore

        truststore.inject_into_ssl()
        _truststore_injected = True


def _is_trusted_job_url_pattern(url: str) -> bool:
    return any(pattern.search(url) for pattern in _TRUSTED_JOB_URL_PATTERNS)


def _redirect_lost_job_page(original: str, final: str) -> bool:
    orig_path = urlparse(original).path.rstrip("/").lower()
    final_path = urlparse(final).path.rstrip("/").lower()
    if not orig_path or orig_path == final_path:
        return False
    if final_path in _GENERIC_CAREER_PATHS or any(final_path.endswith(p) for p in _GENERIC_CAREER_PATHS):
        return True
    if "/job/" in orig_path and "/job/" not in final_path:
        return True
    return False


def _looks_invented_career_url(url: str, *, position_id: str = "") -> bool:
    if not url or _is_trusted_job_url_pattern(url):
        return False
    path = urlparse(url).path
    if _INVENTED_CAREER_PATH.search(path):
        return True
    pid = (position_id or "").strip()
    if pid and pid not in ("—", "unknown") and not pid.isdigit():
        if _INVENTED_POSITION_ID.search(pid):
            return True
    return False


def is_url_reachable(url: str, *, position_id: str = "") -> bool | None:
    """Return True if URL looks live, False if dead/invented, None if check inconclusive."""
    if not url:
        return False
    if url in _url_reachable_cache:
        return _url_reachable_cache[url]
    if _looks_invented_career_url(url, position_id=position_id):
        logger.info("Rejecting invented career URL: %s", url)
        _url_reachable_cache[url] = False
        return False
    if _looks_hallucinated_url(url, position_id=position_id):
        _url_reachable_cache[url] = False
        return False

    _ensure_truststore()
    proxies, headers = _http_session()
    try:
        response = requests.head(
            url,
            allow_redirects=True,
            timeout=20,
            headers=headers,
            proxies=proxies,
        )
        if response.status_code in (405, 501):
            response = requests.get(
                url,
                allow_redirects=True,
                timeout=25,
                headers=headers,
                proxies=proxies,
                stream=True,
            )
        status = response.status_code
        final_url = (response.url or url).strip()
        if status in (404, 410):
            logger.info("Job URL returned %s: %s", status, url)
            _url_reachable_cache[url] = False
            return False
        if _redirect_lost_job_page(url, final_url):
            logger.info("Job URL redirects to generic careers page: %s -> %s", url, final_url)
            _url_reachable_cache[url] = False
            return False
        if status >= 400:
            if status in (403, 429):
                return None
            logger.info("Job URL returned HTTP %s: %s", status, url)
            _url_reachable_cache[url] = False
            return False
        _url_reachable_cache[url] = True
        return True
    except requests.exceptions.ConnectionError:
        logger.info("Job URL DNS/connection failed (likely invented): %s", url)
        _url_reachable_cache[url] = False
        return False
    except Exception as exc:
        logger.warning("URL reachability check failed for %s: %s", url, exc)
        return None


def _linkedin_job_id(url: str, position_id: str) -> str:
    match = _LINKEDIN_JOB_ID_RE.search(url or "")
    if match:
        return match.group(1)
    pid = (position_id or "").strip()
    if pid.isdigit() and len(pid) >= 8:
        return pid
    return ""


def _http_session() -> tuple[dict[str, str] | None, dict[str, str]]:
    from network_env import apply_http_proxy_to_env

    env = os.environ.copy()
    apply_http_proxy_to_env(env)
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or env.get("HTTP_PROXY") or env.get("http_proxy")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    return proxies, headers


def is_linkedin_job_open(url: str, *, position_id: str = "") -> bool | None:
    """Return True if open, False if closed, None if not LinkedIn or check unavailable."""
    if "linkedin.com" not in (url or "").lower():
        return None
    job_id = _linkedin_job_id(url, position_id)
    if not job_id:
        return None
    if job_id in _linkedin_open_cache:
        return _linkedin_open_cache[job_id]
    _ensure_truststore()
    proxies, headers = _http_session()
    api_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    try:
        response = requests.get(api_url, headers=headers, proxies=proxies, timeout=25)
        if response.status_code == 404:
            _linkedin_open_cache[job_id] = False
            logger.info("LinkedIn job %s not found (404) — treating as closed", job_id)
            return False
        response.raise_for_status()
        text = response.text.lower()
        closed = any(marker in text for marker in _LINKEDIN_CLOSED_MARKERS)
        _linkedin_open_cache[job_id] = not closed
        if closed:
            logger.info("LinkedIn job %s is closed (no longer accepting applications)", job_id)
        return not closed
    except Exception as exc:
        logger.warning("LinkedIn status check failed for %s: %s", job_id, exc)
        return None


def _is_stale_posted_date(posted_date: str, *, max_days: int | None = None) -> bool:
    raw = (posted_date or "").strip()
    if not raw or raw.lower() == "unknown":
        return False
    if max_days is None:
        max_days = int(os.getenv("JOB_SEARCH_MAX_POSTED_DAYS", "45"))
    try:
        posted = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return (date.today() - posted).days > max_days


def _looks_hallucinated_url(url: str, *, position_id: str = "") -> bool:
    if not url:
        return True
    lower = url.lower()
    if _GROUNDING_HOST in lower:
        return False
    for pattern in _FAKE_URL_PATTERNS:
        if pattern.search(url):
            return True
    if _is_fake_position_id(position_id):
        host = urlparse(url).netloc.lower()
        if not any(trusted in host for trusted in _TRUSTED_JOB_HOSTS):
            return True
    return False


def _linkedin_canonical_url(url: str, position_id: str) -> str:
    match = _LINKEDIN_JOB_ID_RE.search(url or "")
    job_id = match.group(1) if match else ""
    if not job_id and position_id.isdigit() and len(position_id) >= 8:
        job_id = position_id
    if job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}"
    return url


def _resolve_grounding_redirect(url: str) -> str:
    if _GROUNDING_HOST not in url.lower():
        return url
    try:
        _ensure_truststore()
        proxies, headers = _http_session()
        response = requests.get(
            url,
            allow_redirects=True,
            timeout=25,
            headers=headers,
            proxies=proxies,
        )
        final = (response.url or "").strip()
        if final and _GROUNDING_HOST not in final.lower():
            return final
    except Exception as exc:
        logger.warning("Could not resolve grounding redirect: %s", exc)
    return ""


def normalize_job_url(url: str, *, position_id: str = "") -> str:
    raw = (url or "").strip()
    if not raw:
        if position_id.isdigit() and len(position_id) >= 8:
            return f"https://www.linkedin.com/jobs/view/{position_id}"
        if position_id.isdigit() and len(position_id) >= 4:
            return (
                f"https://careers.checkpoint.com/index.php?"
                f"a=show&joborderid={position_id}&m=cpcareers"
            )
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw.lstrip('/')}"
    if _GROUNDING_HOST in raw.lower():
        resolved = _resolve_grounding_redirect(raw)
        if not resolved:
            return ""
        raw = resolved
    if "careers.checkpoint.com" in raw.lower() and position_id.isdigit():
        if "joborderid=" not in raw.lower():
            raw = (
                f"https://careers.checkpoint.com/index.php?"
                f"a=show&joborderid={position_id}&m=cpcareers"
            )
    if "linkedin.com" in raw.lower():
        raw = _linkedin_canonical_url(raw, position_id)
    return raw


def job_url_status(url: str, *, position_id: str = "") -> str:
    """Return verified | unchecked | unavailable for display."""
    status, _ = evaluate_job_url(url, position_id=position_id)
    return status


def evaluate_job_url(
    url: str,
    *,
    position_id: str = "",
    posted_date: str = "",
) -> tuple[str, str]:
    """Return (status, note). status: verified | unchecked | unavailable."""
    raw = (url or "").strip()
    if not raw:
        return "unavailable", "No URL provided"

    if _looks_invented_career_url(raw, position_id=position_id):
        return "unavailable", "Link invalid (404 or guessed URL — search company site manually)"

    if _looks_hallucinated_url(raw, position_id=position_id):
        return "unavailable", "Link could not be verified"

    if "linkedin.com" in raw.lower():
        if _is_stale_posted_date(posted_date):
            return "unavailable", "LinkedIn posting may be stale (>45 days)"
        if linkedin_verify_enabled():
            linkedin_open = is_linkedin_job_open(raw, position_id=position_id)
            if linkedin_open is False:
                return "unavailable", "Closed on LinkedIn (no longer accepting applications)"

    if url_verify_enabled():
        reachable = is_url_reachable(raw, position_id=position_id)
        if reachable is False:
            return "unavailable", "Link returned 404 or is unreachable"
        if reachable is None:
            return "unchecked", "Link not verified — site may block automated checks"

    return "verified", "Link verified"


def has_listing_substance(
    *,
    company: str,
    title: str,
    description: str = "",
    match_score: int = 0,
    match_reasons: list[str],
    requirements: list[str],
) -> bool:
    if not (company or "").strip() or not (title or "").strip():
        return False
    if (description or "").strip():
        return True
    real_reasons = [r for r in match_reasons if r and r.strip() not in ("—", "-")]
    real_reqs = [r for r in requirements if r and r.strip() not in ("—", "-")]
    if real_reasons or real_reqs:
        return True
    return match_score > 0


def is_usable_job_listing(
    *,
    url: str = "",
    company: str = "",
    title: str = "",
    description: str = "",
    match_score: int,
    match_reasons: list[str],
    requirements: list[str],
    position_id: str = "",
    posted_date: str = "",
    verify_linkedin: bool | None = None,
) -> bool:
    """Keep listings with substance; drop closed/stale LinkedIn jobs entirely."""
    if not has_listing_substance(
        company=company,
        title=title,
        description=description,
        match_score=match_score,
        match_reasons=match_reasons,
        requirements=requirements,
    ):
        return False

    check_url = (url or "").strip()
    if check_url and "linkedin.com" in check_url.lower():
        should_verify = linkedin_verify_enabled() if verify_linkedin is None else verify_linkedin
        if should_verify:
            if _is_stale_posted_date(posted_date):
                logger.info("Skipping stale LinkedIn job: %s | %s", company, title)
                return False
            linkedin_open = is_linkedin_job_open(check_url, position_id=position_id)
            if linkedin_open is False:
                logger.info(
                    "Skipping closed LinkedIn job (no longer accepting applications): %s | %s",
                    company,
                    title,
                )
                return False
    return True
