"""Job search: web discovery via Gemini grounding + optional ChatGPT web search."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from job_search_quality import (
    evaluate_job_url,
    has_listing_substance,
    is_usable_job_listing,
    normalize_job_url,
    parse_match_score,
)
from job_search_apply import build_application_mailto, normalize_apply_email
from job_search_store import (
    JobRecord,
    append_jobs,
    dedupe_key,
    format_job_markdown,
    history_context_for_llm,
    is_duplicate,
    load_history,
    seen_dedupe_keys,
    today_iso,
)
from llm_providers import LLMVendor, complete_chat, get_openai_client, resolve_vendor, vendor_brand_name
from urllib.parse import quote

from rss_fetch import parse_feed

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
DEFAULT_CV_PATH = APP_DIR / "data" / "job_search_cv.md"

# Israeli hi-tech job boards and aggregators (search these actively)
ISRAEL_HITECH_JOB_SITES = [
    ("alljobs.co.il", "AllJobs — largest Israeli board; filter hi-tech / software"),
    ("drushim.co.il", "Drushim — major jobs site"),
    ("jobmaster.co.il", "JobMaster"),
    ("jobnet.co.il", "JobNet"),
    ("gotfriends.co.il", "GotFriends — startups & hi-tech"),
    ("ethosia.co.il", "Ethosia — hi-tech recruitment"),
    ("seeve.co.il", "SeeVE — hi-tech recruitment"),
    ("winwin.co.il", "WinWin"),
    ("jobinfo.co.il", "JobInfo"),
    ("il.indeed.com", "Indeed Israel"),
    ("comeet.co", "Comeet — startup/tech company jobs"),
    ("jobs.ynet.co.il", "Ynet Jobs"),
    ("linkedin.com/jobs", "LinkedIn Jobs Israel"),
]

# Hi-tech employers & integrators (direct career pages)
ISRAEL_HITECH_EMPLOYERS = [
    "Matrix", "Ness", "Malam Team", "Taldor", "SIS", "ECI",
    "Check Point", "CyberArk", "Radware", "monday.com", "Wix", "Similarweb",
    "JFrog", "Fiverr", "AppsFlyer", "Redis", "Taboola", "Outbrain",
    "Mobileye", "Intel Israel", "Microsoft Israel", "Google Israel", "Amazon Israel",
    "Amdocs", "Nice", "Verint", "NICE", "Priority", "ClickSoftware",
]

JOB_SEARCH_SYSTEM_PROMPT = """You are an expert career researcher focused on the ISRAELI HI-TECH job market.

Your task: find REAL, currently open HI-TECH job listings (software, AI/ML, R&D, architecture, DevOps, data) that match the candidate CV.

Search strategy:
1. Search Israeli hi-tech job boards and recruitment sites (see list below).
2. Search LinkedIn Jobs (site:linkedin.com/jobs) for Israel hi-tech roles.
3. Search direct career pages of Israeli tech companies and IT integrators.
4. SKIP non-hi-tech roles: retail, pure finance without tech, insurance admin, general government unless the role is explicitly software/IT/AI.

Israeli hi-tech job boards (search ALL of these via web search):
- alljobs.co.il, drushim.co.il, jobmaster.co.il, jobnet.co.il
- gotfriends.co.il, ethosia.co.il, seeve.co.il, winwin.co.il, jobinfo.co.il
- il.indeed.com, comeet.co/jobs, jobs.ynet.co.il, linkedin.com/jobs

Hi-tech employers & integrators (career pages):
- Matrix, Ness, Malam, Taldor, SIS, ECI, Check Point, CyberArk, Radware
- monday.com, Wix, Similarweb, JFrog, Fiverr, AppsFlyer, Redis, Taboola
- Mobileye, Intel, Microsoft, Google, Amazon, Amdocs, Nice, Verint, Priority

LinkedIn Jobs:
- site:linkedin.com/jobs + CV keywords + Israel / Jerusalem / hybrid / remote
- Prefer linkedin.com/jobs/view/... URLs; source_site = linkedin.com

Rules:
- HI-TECH ONLY: software engineer, architect, tech lead, AI/ML, DevOps, data, R&D, product engineering.
- Use exact company names and job titles as published.
- Include position / requisition ID when visible.
- Include direct URL to the posting (employer or job board page — NOT a search redirect).
- NEVER construct or guess URLs from company name + role title. Only use URLs found in search results.
- Valid URL examples: linkedin.com/jobs/view/1234567890, alljobs.co.il/...JobID=..., boards.greenhouse.io/..., comeet.com/jobs/...
- INVALID (never use): company.com/careers/RoleName-2026, careers.company.com/job/SA-2026 — these are invented slugs.
- NEVER use vertexaisearch.cloud.google.com or other grounding/search redirect URLs.
- Do NOT invent jobs, companies, URLs, or requisition numbers.
- Skip listings already in tracked history.
- Prefer posts from last 30 days.
- For LinkedIn: only include jobs still accepting applications (not closed).
- Include apply_email when the posting shows a contact email; set apply_method accordingly.
- Search Israeli job boards FIRST (alljobs, drushim, jobnet, LinkedIn) — they have verifiable URLs.
- Prioritize Jerusalem, Shfela, Beit Shemesh; Tel Aviv / Herzliya / Raanana OK for hybrid/remote.
- English for all text fields.
- Keep each description under 300 characters to fit JSON output.
{
  "jobs": [
    {
      "position_id": "requisition number or empty string",
      "company": "exact company name",
      "title": "exact job title",
      "url": "https://...",
      "location": "city / remote / hybrid",
      "employment_type": "full-time | part-time | contract",
      "posted_date": "YYYY-MM-DD or unknown",
      "description": "3-5 sentences summarizing the role and requirements",
      "requirements": ["requirement 1", "requirement 2"],
      "match_score": 85,
      "match_reasons": ["why this fits the CV"],
      "source_site": "domain or site name",
      "apply_email": "hr@company.com if visible on posting, else empty string",
      "apply_method": "linkedin | email | company_site | job_board | unknown"
    }
  ],
  "search_notes": "brief notes on coverage gaps or search limitations"
}"""


LINKEDIN_JOB_SEARCH_PROMPT = """You are a LinkedIn Jobs specialist for Israeli HI-TECH roles.

Search ONLY LinkedIn Jobs (linkedin.com/jobs). Hi-tech roles only: software, AI/ML, architecture, R&D, DevOps.

Example queries (adapt from CV):
- site:linkedin.com/jobs solution architect Israel hi-tech
- site:linkedin.com/jobs technical lead AI engineer Israel
- site:linkedin.com/jobs senior Java developer Israel hybrid remote
- site:linkedin.com/jobs software architect Jerusalem OR Shfela

Rules:
- REAL LinkedIn listings only — linkedin.com/jobs/view/... URLs preferred.
- Skip jobs marked "No longer accepting applications" or closed on LinkedIn.
- Use the final LinkedIn job URL, not Google/Vertex search redirects.
- NEVER return vertexaisearch.cloud.google.com URLs or invented job IDs.
- source_site = linkedin.com. Same JSON schema. Skip tracked history duplicates.
- English for all text fields.

Respond with JSON only (no markdown fences)."""


HITECH_BOARDS_SEARCH_PROMPT = """You are a specialist in Israeli HI-TECH job boards.

Search ONLY these Israeli hi-tech job sites (use web search with site: operator for each):
- site:alljobs.co.il — software, hi-tech, high-tech jobs
- site:drushim.co.il
- site:jobmaster.co.il
- site:jobnet.co.il
- site:gotfriends.co.il
- site:ethosia.co.il
- site:seeve.co.il
- site:winwin.co.il
- site:jobinfo.co.il
- site:il.indeed.com Israel software OR AI OR architect
- site:comeet.co jobs Israel
- site:jobs.ynet.co.il hi-tech

Also search career pages: Matrix, Ness, Malam, monday.com, Wix, Check Point (hi-tech roles only).

Rules:
- HI-TECH ONLY — skip non-software roles.
- REAL listings with direct URLs on the board or company site (no search redirects).
- NEVER use vertexaisearch.cloud.google.com or placeholder URLs.
- Set source_site to the board domain (e.g. drushim.co.il, alljobs.co.il).
- Same JSON schema (jobs array + search_notes). Skip tracked duplicates.
- English for all text fields.

Respond with JSON only (no markdown fences)."""


@dataclass
class JobListing:
    position_id: str
    company: str
    title: str
    url: str
    location: str
    employment_type: str
    posted_date: str
    description: str
    requirements: list[str]
    match_score: int
    match_reasons: list[str]
    source_site: str
    discovered_by: str
    apply_email: str = ""
    apply_method: str = ""
    url_status: str = "verified"
    url_hint: str = ""
    link_note: str = ""


def cv_path() -> Path:
    raw = os.getenv("JOB_SEARCH_CV_PATH", "")
    return Path(raw) if raw else DEFAULT_CV_PATH


def load_cv() -> str:
    from job_search_cv_loader import load_cv as load_cv_text

    return load_cv_text()


def job_search_vendor() -> LLMVendor:
    return resolve_vendor(os.getenv("JOB_SEARCH_VENDOR", "gemini"))


def job_search_fallback_vendor() -> LLMVendor | None:
    if os.getenv("JOB_SEARCH_VENDOR_FALLBACK_ENABLED", "1").lower() not in ("1", "true", "yes"):
        return None
    fallback = resolve_vendor(os.getenv("JOB_SEARCH_VENDOR_FALLBACK", "openai"))
    if fallback is job_search_vendor():
        return None
    return fallback


def job_search_model(vendor: LLMVendor | None = None) -> str:
    v = vendor or job_search_vendor()
    if v is LLMVendor.GEMINI:
        return os.getenv("JOB_SEARCH_MODEL", "gemini-2.5-flash")
    return os.getenv("JOB_SEARCH_OPENAI_MODEL", "gpt-4.1-mini")


def max_jobs() -> int:
    try:
        return max(1, int(os.getenv("JOB_SEARCH_MAX_JOBS", "15")))
    except ValueError:
        return 15


def search_locations() -> str:
    return os.getenv("JOB_SEARCH_LOCATIONS", "Israel, Tel Aviv, Remote, Hybrid")


def extra_keywords() -> str:
    return os.getenv("JOB_SEARCH_KEYWORDS", "").strip()


def use_openai_web() -> bool:
    return os.getenv("JOB_SEARCH_USE_OPENAI_WEB", "1").lower() in ("1", "true", "yes")


def grounding_enabled() -> bool:
    return os.getenv("JOB_SEARCH_GROUNDING", "1").lower() in ("1", "true", "yes")


def linkedin_enabled() -> bool:
    return os.getenv("JOB_SEARCH_LINKEDIN", "1").lower() in ("1", "true", "yes")


def hitech_boards_enabled() -> bool:
    return os.getenv("JOB_SEARCH_HITECH_BOARDS", "1").lower() in ("1", "true", "yes")


def _linkedin_search_terms(cv_text: str) -> list[str]:
    if extra_keywords():
        return [t.strip() for t in re.split(r"[,;]+", extra_keywords()) if t.strip()]
    title = _cv_search_keywords(cv_text)
    terms = [title]
    for phrase in (
        "solution architect",
        "technical lead",
        "AI engineer",
        "senior Java developer",
    ):
        if phrase.lower() not in title.lower():
            terms.append(phrase)
    return terms[:5]


def _linkedin_search_urls(cv_text: str) -> list[str]:
    locations = [
        "Israel",
        "Jerusalem, Israel",
        "Central District, Israel",
        "Beit Shemesh, Israel",
    ]
    urls: list[str] = []
    for term in _linkedin_search_terms(cv_text):
        for loc in locations:
            urls.append(
                "https://www.linkedin.com/jobs/search/?keywords="
                f"{quote(term)}&location={quote(loc)}"
            )
    return urls


def _linkedin_rss_queries(cv_text: str) -> list[str]:
    keywords = _cv_search_keywords(cv_text)
    locations = search_locations().replace(",", " ")
    return [
        f"site:linkedin.com/jobs/view {keywords} Israel",
        f"site:linkedin.com/jobs {keywords} Jerusalem",
        f"site:linkedin.com/jobs {keywords} {locations}",
        f"site:linkedin.com/jobs solution architect Israel",
        f"site:linkedin.com/jobs technical lead AI engineer Israel remote",
    ]


def fetch_linkedin_hints(cv_text: str, *, max_items: int = 15) -> str:
    lines = ["LinkedIn Jobs — start from these search pages (verify listings via web search):"]
    for url in _linkedin_search_urls(cv_text)[:8]:
        lines.append(f"- {url}")

    items: list[str] = []
    for query in _linkedin_rss_queries(cv_text):
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en&gl=IL&ceid=IL:en"
        try:
            feed = parse_feed(url)
        except Exception as exc:
            logger.warning("LinkedIn RSS hint failed: %s", exc)
            continue
        for entry in feed.entries[:4]:
            title = getattr(entry, "title", "") or ""
            link = getattr(entry, "link", "") or ""
            if title and ("linkedin" in link.lower() or "linkedin" in title.lower()):
                items.append(f"- {title}\n  {link}")
        if len(items) >= max_items:
            break

    if items:
        lines.append("")
        lines.append("Recent LinkedIn-related RSS hits:")
        lines.extend(items[:max_items])
    return "\n".join(lines)


def _sanitize_json_text(text: str) -> str:
    """Strip/repair content that breaks json.loads (common in grounded LLM output)."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    # Escape raw newlines/tabs inside JSON string literals
    out: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            out.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string:
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
        out.append(ch)
    return "".join(out)


def _parse_json_payload(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    for candidate in (text, _sanitize_json_text(text)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        blob = _sanitize_json_text(match.group(0))
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            pass
    jobs_match = re.search(r'"jobs"\s*:\s*(\[[\s\S]*\])\s*,?\s*"search_notes"', text)
    if jobs_match:
        jobs_blob = _sanitize_json_text(jobs_match.group(1))
        jobs = json.loads(jobs_blob)
        notes_match = re.search(r'"search_notes"\s*:\s*"([^"]*)"', text)
        note = notes_match.group(1) if notes_match else "Partial JSON recovery"
        return {"jobs": jobs, "search_notes": note}
    raise json.JSONDecodeError("No JSON object found", text, 0)


def _cv_search_keywords(cv_text: str) -> str:
    if extra_keywords():
        return extra_keywords()
    lines = cv_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() == "## title" and i + 1 < len(lines):
            value = lines[i + 1].strip()
            if value:
                return value
    return "senior software engineer AI architect Israel hi-tech"


def _hitech_rss_queries(cv_text: str) -> list[str]:
    keywords = _cv_search_keywords(cv_text)
    kw_short = keywords.split(",")[0].strip() if "," in keywords else keywords
    return [
        f"site:alljobs.co.il {kw_short} hi-tech OR high-tech Israel",
        f"site:drushim.co.il {kw_short} software",
        f"site:jobmaster.co.il {kw_short}",
        f"site:jobnet.co.il {kw_short} hi-tech",
        f"site:gotfriends.co.il {kw_short}",
        f"site:ethosia.co.il OR site:seeve.co.il {kw_short}",
        f"site:il.indeed.com {kw_short} Israel",
        f"site:comeet.co {kw_short} Israel jobs",
        f"site:winwin.co.il OR site:jobinfo.co.il {kw_short}",
        f"site:matrix.co.il OR site:ness.com careers {kw_short}",
    ]


def _hitech_board_urls(cv_text: str) -> list[str]:
    kw = quote(_cv_search_keywords(cv_text).split(",")[0].strip())
    return [
        f"https://www.alljobs.co.il/SearchResultsGuest.aspx?position={kw}&type=&city=&region=",
        f"https://www.drushim.co.il/jobs/search/{kw}/",
        f"https://il.indeed.com/jobs?q={kw}&l=Israel",
        f"https://www.jobmaster.co.il/jobs/?q={kw}",
        f"https://www.gotfriends.co.il/jobs/?q={kw}",
        "https://www.ethosia.co.il/jobs/",
        "https://www.seeve.co.il/jobs/",
        "https://www.comeet.com/jobs",
    ]


def fetch_hitech_job_board_hints(cv_text: str, *, max_items: int = 20) -> str:
    lines = [
        "Israeli HI-TECH job boards — search these (verify listings via web search):",
        "",
        "Boards:",
    ]
    for domain, desc in ISRAEL_HITECH_JOB_SITES:
        lines.append(f"- {domain} — {desc}")
    lines.append("")
    lines.append("Sample search URLs:")
    for url in _hitech_board_urls(cv_text):
        lines.append(f"- {url}")
    lines.append("")
    lines.append("Hi-tech employers to check: " + ", ".join(ISRAEL_HITECH_EMPLOYERS[:15]) + ", ...")

    items: list[str] = []
    for query in _hitech_rss_queries(cv_text):
        feed_url = f"https://news.google.com/rss/search?q={quote(query)}&hl=he&gl=IL&ceid=IL:he"
        try:
            feed = parse_feed(feed_url)
        except Exception as exc:
            logger.warning("Hi-tech RSS hint failed: %s", exc)
            continue
        for entry in feed.entries[:3]:
            title = getattr(entry, "title", "") or ""
            link = getattr(entry, "link", "") or ""
            if title:
                items.append(f"- {title}\n  {link}")
        if len(items) >= max_items:
            break

    if items:
        lines.append("")
        lines.append("Recent hi-tech job RSS hits:")
        lines.extend(items[:max_items])
    return "\n".join(lines)


def _rss_search_queries(cv_text: str) -> list[str]:
    return _hitech_rss_queries(cv_text)


def fetch_rss_hints(cv_text: str, *, max_items: int = 25) -> str:
    items: list[str] = []
    for query in _rss_search_queries(cv_text):
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=he&gl=IL&ceid=IL:he"
        try:
            feed = parse_feed(url)
        except Exception as exc:
            logger.warning("RSS hint fetch failed: %s", exc)
            continue
        for entry in feed.entries[:5]:
            title = getattr(entry, "title", "") or ""
            link = getattr(entry, "link", "") or ""
            summary = getattr(entry, "summary", "") or ""
            if title:
                items.append(f"- {title}\n  {link}\n  {summary[:200]}")
        if len(items) >= max_items:
            break
    if not items:
        return "No RSS seed results."
    return "Recent web/RSS hints (verify with search):\n" + "\n".join(items[:max_items])


def company_watchlist() -> str:
    return os.getenv("JOB_SEARCH_COMPANY_WATCHLIST", "").strip()


def _build_user_message(
    *,
    iso_date: str,
    cv_text: str,
    history_ctx: str,
    rss_hints: str,
    linkedin_hints: str = "",
    hitech_hints: str = "",
) -> str:
    keywords_line = f"\nFocus keywords: {extra_keywords()}" if extra_keywords() else ""
    watchlist = company_watchlist()
    watchlist_block = (
        f"\n\n=== PRIORITY COMPANIES (search careers + LinkedIn first) ===\n{watchlist}"
        if watchlist
        else ""
    )
    linkedin_block = f"\n\n=== LINKEDIN JOBS ===\n{linkedin_hints}" if linkedin_hints else ""
    hitech_block = f"\n\n=== ISRAEL HI-TECH JOB BOARDS ===\n{hitech_hints}" if hitech_hints else ""
    return f"""Search date: {iso_date}
Preferred locations: {search_locations()}{keywords_line}
Industry focus: ISRAELI HI-TECH ONLY (software, AI/ML, R&D, architecture — skip banks/insurance/gov unless pure tech role)

=== CANDIDATE CV ===
{cv_text}

=== TRACKED HISTORY (skip duplicates) ===
{history_ctx}

=== RSS / WEB HINTS ===
{rss_hints}{watchlist_block}{linkedin_block}{hitech_block}

Find up to {max_jobs()} NEW hi-tech job listings that match this CV.
Return valid JSON only."""


def _is_transient_llm_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(token in msg for token in ("429", "503", "timeout", "temporarily unavailable", "resource_exhausted"))


def _complete_job_search_chat(
    *,
    vendor: LLMVendor,
    user_message: str,
    use_grounding: bool,
) -> tuple[dict, str, str]:
    vendors: list[LLMVendor] = [vendor]
    fallback = job_search_fallback_vendor()
    if fallback is not None:
        vendors.append(fallback)

    last_error: BaseException | None = None
    for attempt_vendor in vendors:
        try:
            use_ground = use_grounding and attempt_vendor is LLMVendor.GEMINI
            result = complete_chat(
                vendor=attempt_vendor,
                system_prompt=JOB_SEARCH_SYSTEM_PROMPT,
                user_message=user_message,
                model=job_search_model(attempt_vendor),
                max_tokens=int(os.getenv("JOB_SEARCH_MAX_TOKENS", "8192")),
                temperature=float(os.getenv("JOB_SEARCH_TEMPERATURE", "0.3")),
                use_grounding=use_ground,
                json_response=not use_ground,
            )
            payload = _parse_json_payload(result.text)
            label = f"{vendor_brand_name(attempt_vendor)} ({result.model})"
            return payload, label, attempt_vendor.value
        except Exception as exc:
            if attempt_vendor is not vendors[-1] and _is_transient_llm_error(exc):
                logger.warning(
                    "Job search %s failed (%s) — trying %s",
                    attempt_vendor.value,
                    exc,
                    vendors[-1].value,
                )
                last_error = exc
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Job search LLM call failed")


def _complete_linkedin_search_chat(
    *,
    vendor: LLMVendor,
    user_message: str,
    use_grounding: bool,
) -> tuple[dict, str]:
    vendors: list[LLMVendor] = [vendor]
    fallback = job_search_fallback_vendor()
    if fallback is not None:
        vendors.append(fallback)

    last_error: BaseException | None = None
    for attempt_vendor in vendors:
        try:
            use_ground = use_grounding and attempt_vendor is LLMVendor.GEMINI
            result = complete_chat(
                vendor=attempt_vendor,
                system_prompt=LINKEDIN_JOB_SEARCH_PROMPT,
                user_message=user_message,
                model=job_search_model(attempt_vendor),
                max_tokens=int(os.getenv("JOB_SEARCH_MAX_TOKENS", "8192")),
                temperature=float(os.getenv("JOB_SEARCH_TEMPERATURE", "0.3")),
                use_grounding=use_ground,
                json_response=not use_ground,
            )
            payload = _parse_json_payload(result.text)
            label = f"LinkedIn/{vendor_brand_name(attempt_vendor)} ({result.model})"
            return payload, label
        except Exception as exc:
            if attempt_vendor is not vendors[-1] and _is_transient_llm_error(exc):
                logger.warning(
                    "LinkedIn search %s failed (%s) — trying %s",
                    attempt_vendor.value,
                    exc,
                    vendors[-1].value,
                )
                last_error = exc
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("LinkedIn job search LLM call failed")


def _complete_hitech_boards_search_chat(
    *,
    vendor: LLMVendor,
    user_message: str,
    use_grounding: bool,
) -> tuple[dict, str]:
    vendors: list[LLMVendor] = [vendor]
    fallback = job_search_fallback_vendor()
    if fallback is not None:
        vendors.append(fallback)

    last_error: BaseException | None = None
    for attempt_vendor in vendors:
        try:
            use_ground = use_grounding and attempt_vendor is LLMVendor.GEMINI
            result = complete_chat(
                vendor=attempt_vendor,
                system_prompt=HITECH_BOARDS_SEARCH_PROMPT,
                user_message=user_message,
                model=job_search_model(attempt_vendor),
                max_tokens=int(os.getenv("JOB_SEARCH_MAX_TOKENS", "8192")),
                temperature=float(os.getenv("JOB_SEARCH_TEMPERATURE", "0.3")),
                use_grounding=use_ground,
                json_response=not use_ground,
            )
            payload = _parse_json_payload(result.text)
            label = f"HiTechBoards/{vendor_brand_name(attempt_vendor)} ({result.model})"
            return payload, label
        except Exception as exc:
            if attempt_vendor is not vendors[-1] and _is_transient_llm_error(exc):
                logger.warning(
                    "Hi-tech boards search %s failed (%s) — trying %s",
                    attempt_vendor.value,
                    exc,
                    vendors[-1].value,
                )
                last_error = exc
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Hi-tech job boards search LLM call failed")


def _search_openai_web(user_message: str) -> tuple[dict, str]:
    model = os.getenv("JOB_SEARCH_OPENAI_WEB_MODEL", "gpt-4.1-mini")
    client = get_openai_client()
    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            input=[
                {"role": "developer", "content": JOB_SEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        text = (response.output_text or "").strip()
        if not text:
            raise RuntimeError("OpenAI web search returned empty response")
        return _parse_json_payload(text), f"ChatGPT web ({model})"
    except Exception as exc:
        if os.getenv("JOB_SEARCH_OPENAI_CHAT_FALLBACK", "0").lower() in ("1", "true", "yes"):
            logger.warning("OpenAI web search failed (%s) — falling back to chat completion", exc)
            result = complete_chat(
                vendor=LLMVendor.OPENAI,
                system_prompt=JOB_SEARCH_SYSTEM_PROMPT,
                user_message=user_message,
                model=model,
                max_tokens=int(os.getenv("JOB_SEARCH_MAX_TOKENS", "8192")),
                temperature=0.3,
                json_response=True,
            )
            return _parse_json_payload(result.text), f"ChatGPT ({result.model})"
        logger.warning("OpenAI web search failed (%s) — skipping pass (no chat fallback)", exc)
        raise


def _search_openai_web_linkedin(user_message: str) -> tuple[dict, str]:
    model = os.getenv("JOB_SEARCH_OPENAI_WEB_MODEL", "gpt-4.1-mini")
    client = get_openai_client()
    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            input=[
                {"role": "developer", "content": LINKEDIN_JOB_SEARCH_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        text = (response.output_text or "").strip()
        if not text:
            raise RuntimeError("OpenAI LinkedIn web search returned empty response")
        return _parse_json_payload(text), f"LinkedIn/ChatGPT web ({model})"
    except Exception as exc:
        logger.warning("OpenAI LinkedIn web search failed: %s", exc)
        raise


def _listing_from_dict(data: dict, *, discovered_by: str) -> JobListing | None:
    company = str(data.get("company", "")).strip()
    title = str(data.get("title", "")).strip()
    if not company or not title:
        return None
    position_id = str(data.get("position_id", "")).strip()
    match_reasons = [str(r) for r in data.get("match_reasons", []) if r]
    requirements = [str(r) for r in data.get("requirements", []) if r]
    score = parse_match_score(
        data.get("match_score", 0),
        match_reasons=match_reasons,
        requirements=requirements,
    )
    url = normalize_job_url(str(data.get("url", "")).strip(), position_id=position_id)
    posted_date = str(data.get("posted_date", "")).strip()
    description = str(data.get("description", "")).strip()
    if not is_usable_job_listing(
        url=url,
        company=company,
        title=title,
        description=description,
        match_score=score,
        match_reasons=match_reasons,
        requirements=requirements,
        position_id=position_id,
        posted_date=posted_date,
    ):
        logger.debug("Skipping empty listing: %s | %s", company, title)
        return None
    url_status, link_note = evaluate_job_url(
        url, position_id=position_id, posted_date=posted_date
    )
    url_hint = url if url_status == "unavailable" else ""
    display_url = url if url_status in ("verified", "unchecked") else ""
    apply_email = normalize_apply_email(str(data.get("apply_email", "")))
    apply_method = str(data.get("apply_method", "")).strip()
    return JobListing(
        position_id=position_id,
        company=company,
        title=title,
        url=display_url,
        location=str(data.get("location", "")).strip(),
        employment_type=str(data.get("employment_type", "")).strip(),
        posted_date=posted_date,
        description=description,
        requirements=requirements,
        match_score=score,
        match_reasons=match_reasons,
        source_site=str(data.get("source_site", "")).strip(),
        discovered_by=discovered_by,
        apply_email=apply_email,
        apply_method=apply_method,
        url_status=url_status,
        url_hint=url_hint,
        link_note=link_note,
    )


def _merge_listings(candidates: list[JobListing], seen_keys: set[str]) -> list[JobListing]:
    merged: list[JobListing] = []
    local_keys = set(seen_keys)
    for job in candidates:
        if is_duplicate(
            company=job.company,
            title=job.title,
            position_id=job.position_id,
            url=job.url,
            description=job.description,
            seen_keys=local_keys,
        ):
            continue
        merged.append(job)
        probe = JobRecord(
            iso_date="",
            title=job.title,
            company=job.company,
            position_id=job.position_id,
            url=job.url,
            location=job.location,
            match_score=job.match_score,
            source=job.discovered_by,
            description=job.description,
            markdown_body="",
        )
        local_keys.update(dedupe_key(probe))
    merged.sort(key=lambda j: j.match_score, reverse=True)
    return merged[:max_jobs()]


@dataclass
class JobSearchResult:
    iso_date: str
    new_jobs: list[JobListing]
    search_notes: list[str]
    providers_used: list[str]
    total_tracked: int


def run_job_search(*, save: bool = True, ignore_history: bool = False) -> JobSearchResult:
    iso_date = today_iso()
    cv_text = load_cv()
    _, records = load_history()
    seen_keys: set[str] = set() if ignore_history else seen_dedupe_keys(records)
    history_ctx = (
        "Fresh search — no history filter for this run. Return all matching open jobs."
        if ignore_history
        else history_context_for_llm(records)
    )
    if ignore_history:
        logger.info("Job search: ignoring history (no dedup, LLM not skipping tracked jobs)")

    logger.info("Fetching RSS hints for job search")
    rss_hints = fetch_rss_hints(cv_text)
    linkedin_hints = fetch_linkedin_hints(cv_text) if linkedin_enabled() else ""
    hitech_hints = fetch_hitech_job_board_hints(cv_text) if hitech_boards_enabled() else ""
    user_message = _build_user_message(
        iso_date=iso_date,
        cv_text=cv_text,
        history_ctx=history_ctx,
        rss_hints=rss_hints,
        linkedin_hints=linkedin_hints,
        hitech_hints=hitech_hints,
    )

    all_candidates: list[JobListing] = []
    notes: list[str] = []
    providers: list[str] = []

    vendor = job_search_vendor()
    logger.info("Job search primary vendor: %s", vendor.value)
    payload, label, _ = _complete_job_search_chat(
        vendor=vendor,
        user_message=user_message,
        use_grounding=False,
    )
    providers.append(label)
    notes.append(str(payload.get("search_notes", "")).strip())
    for item in payload.get("jobs", []):
        if isinstance(item, dict):
            listing = _listing_from_dict(item, discovered_by=label)
            if listing:
                all_candidates.append(listing)

    if use_openai_web() and os.getenv("OPENAI_API_KEY"):
        if os.getenv("GEMINI_CALL_DELAY_SECONDS"):
            time.sleep(int(os.getenv("GEMINI_CALL_DELAY_SECONDS", "5")))
        logger.info("Job search secondary pass: OpenAI web search")
        try:
            payload2, label2 = _search_openai_web(user_message)
            providers.append(label2)
            note2 = str(payload2.get("search_notes", "")).strip()
            if note2:
                notes.append(note2)
            for item in payload2.get("jobs", []):
                if isinstance(item, dict):
                    listing = _listing_from_dict(item, discovered_by=label2)
                    if listing:
                        all_candidates.append(listing)
        except Exception as exc:
            logger.warning("OpenAI web search pass skipped: %s", exc)

    if hitech_boards_enabled():
        delay = int(os.getenv("GEMINI_CALL_DELAY_SECONDS", "5"))
        if delay:
            time.sleep(delay)
        logger.info("Job search Israeli hi-tech boards pass")
        try:
            payload_ht, label_ht = _complete_hitech_boards_search_chat(
                vendor=vendor,
                user_message=user_message,
                use_grounding=grounding_enabled(),
            )
            providers.append(label_ht)
            note_ht = str(payload_ht.get("search_notes", "")).strip()
            if note_ht:
                notes.append(f"Hi-tech boards: {note_ht}")
            for item in payload_ht.get("jobs", []):
                if isinstance(item, dict):
                    listing = _listing_from_dict(item, discovered_by=label_ht)
                    if listing:
                        all_candidates.append(listing)
        except Exception as exc:
            logger.warning("Hi-tech boards pass skipped: %s", exc)

    if linkedin_enabled():
        delay = int(os.getenv("GEMINI_CALL_DELAY_SECONDS", "5"))
        if delay:
            time.sleep(delay)
        logger.info("Job search LinkedIn pass (Gemini/OpenAI)")
        try:
            payload_li, label_li = _complete_linkedin_search_chat(
                vendor=vendor,
                user_message=user_message,
                use_grounding=grounding_enabled(),
            )
            providers.append(label_li)
            note_li = str(payload_li.get("search_notes", "")).strip()
            if note_li:
                notes.append(f"LinkedIn: {note_li}")
            for item in payload_li.get("jobs", []):
                if isinstance(item, dict):
                    listing = _listing_from_dict(item, discovered_by=label_li)
                    if listing:
                        all_candidates.append(listing)
        except Exception as exc:
            logger.warning("LinkedIn Gemini/OpenAI pass skipped: %s", exc)

        if use_openai_web() and os.getenv("OPENAI_API_KEY"):
            time.sleep(delay)
            logger.info("Job search LinkedIn pass: OpenAI web search")
            try:
                payload_li2, label_li2 = _search_openai_web_linkedin(user_message)
                providers.append(label_li2)
                note_li2 = str(payload_li2.get("search_notes", "")).strip()
                if note_li2:
                    notes.append(f"LinkedIn: {note_li2}")
                for item in payload_li2.get("jobs", []):
                    if isinstance(item, dict):
                        listing = _listing_from_dict(item, discovered_by=label_li2)
                        if listing:
                            all_candidates.append(listing)
            except Exception as exc:
                logger.warning("LinkedIn OpenAI web pass skipped: %s", exc)

    new_jobs = _merge_listings(all_candidates, seen_keys)
    logger.info(
        "Job search: %s candidates -> %s new after dedup (tracked=%s)",
        len(all_candidates),
        len(new_jobs),
        len(records),
    )

    if save and new_jobs:
        entries = [
            format_job_markdown(
                iso_date=iso_date,
                company=job.company,
                title=job.title,
                position_id=job.position_id,
                url=job.url,
                location=job.location,
                employment_type=job.employment_type,
                match_score=job.match_score,
                match_reasons=job.match_reasons,
                source=job.discovered_by,
                posted_date=job.posted_date,
                description=job.description,
                requirements=job.requirements,
                apply_email=job.apply_email,
                apply_method=job.apply_method,
                url_status=job.url_status,
                url_hint=job.url_hint,
                link_note=job.link_note,
            )
            for job in new_jobs
        ]
        path = append_jobs(entries)
        logger.info("Saved %s new jobs to %s", len(entries), path)

    return JobSearchResult(
        iso_date=iso_date,
        new_jobs=new_jobs,
        search_notes=[n for n in notes if n],
        providers_used=providers,
        total_tracked=len(records) + (len(new_jobs) if save and not ignore_history else 0),
    )


def _short_job_link(url: str) -> str:
    if not url:
        return "—"
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.netloc.removeprefix("www.") or "link"
    label = host if len(host) <= 28 else host[:25] + "…"
    return (
        f'<a href="{html.escape(url)}" style="color:#1565c0;text-decoration:none;'
        f'font-weight:600;">View job</a>'
        f'<br><span style="color:#777;font-size:11px;">{html.escape(label)}</span>'
    )


def _manual_search_link(job: JobListing) -> str:
    query = quote(f"{job.company} {job.title} Israel jobs")
    url = f"https://www.google.com/search?q={query}"
    return (
        f'<a href="{url}" style="color:#1565c0;font-size:12px;">'
        f"Search {html.escape(job.company)} manually</a>"
    )


def _job_action_links(job: JobListing) -> str:
    parts: list[str] = []
    if job.url and job.url_status in ("verified", "unchecked"):
        parts.append(_short_job_link(job.url))
        if job.url_status == "unchecked":
            parts.append(
                '<br><span style="color:#b45309;font-size:11px;">⚠ Link not verified — open manually</span>'
            )
        else:
            parts.append('<br><span style="color:#2e7d32;font-size:11px;">✓ Link verified</span>')
    else:
        parts.append(
            '<span style="color:#c62828;font-weight:600;font-size:12px;">No working link available</span>'
        )
        if job.link_note:
            parts.append(f'<br><span style="color:#777;font-size:11px;">{html.escape(job.link_note)}</span>')
        if job.url_hint:
            parts.append(
                f'<br><span style="color:#777;font-size:11px;">Attempted: {html.escape(job.url_hint[:80])}</span>'
            )
        parts.append(f"<br>{_manual_search_link(job)}")

    if job.apply_email:
        mailto = build_application_mailto(
            apply_email=job.apply_email,
            company=job.company,
            title=job.title,
        )
        if mailto:
            parts.append(
                f'<br><a href="{mailto}" style="color:#1565c0;font-size:12px;">'
                f"Email CV to {html.escape(job.apply_email)}</a>"
            )
    elif job.apply_method.lower() == "linkedin" or "linkedin" in (job.url_hint or job.url).lower():
        parts.append('<br><span style="color:#555;font-size:11px;">Try LinkedIn search for this role</span>')

    if job.source_site:
        parts.append(f'<br><span style="color:#555;font-size:11px;">Source: {html.escape(job.source_site)}</span>')
    return "".join(parts)


def records_to_listings(records: list[JobRecord]) -> list[JobListing]:
    from job_search_store import _match_reasons_from_chunk, _requirements_from_chunk

    listings: list[JobListing] = []
    for rec in records:
        pid = rec.position_id if rec.position_id not in ("—", "unknown") else ""
        chunk = rec.markdown_body or ""
        reasons = _match_reasons_from_chunk(chunk)
        reqs = _requirements_from_chunk(chunk)
        posted_match = re.search(r"\*\*Posted:\*\*\s*(.+)", chunk)
        posted_date = posted_match.group(1).strip() if posted_match else ""
        apply_match = re.search(r"\*\*Apply email:\*\*\s*(.+)", chunk)
        apply_email = normalize_apply_email(apply_match.group(1) if apply_match else "")
        method_match = re.search(r"\*\*Apply method:\*\*\s*(.+)", chunk)
        apply_method = method_match.group(1).strip() if method_match else ""
        status_match = re.search(r"\*\*Link status:\*\*\s*(.+)", chunk)
        url_status = status_match.group(1).strip() if status_match else ""
        hint_match = re.search(r"\*\*URL hint:\*\*\s*(.+)", chunk)
        url_hint = hint_match.group(1).strip() if hint_match else ""
        if url_hint in ("—",):
            url_hint = ""
        note_match = re.search(r"\*\*Link note:\*\*\s*(.+)", chunk)
        link_note = note_match.group(1).strip() if note_match else ""
        if link_note in ("—",):
            link_note = ""
        raw_url = normalize_job_url(rec.url, position_id=pid)
        if not url_status or url_status == "—":
            url_status, link_note_eval = evaluate_job_url(
                raw_url, position_id=pid, posted_date=posted_date
            )
            if not link_note:
                link_note = link_note_eval
        display_url = raw_url if url_status in ("verified", "unchecked") else ""
        if not url_hint and url_status == "unavailable":
            url_hint = raw_url
        if not is_usable_job_listing(
            url=raw_url,
            company=rec.company,
            title=rec.title,
            description=rec.description,
            match_score=rec.match_score,
            match_reasons=reasons,
            requirements=reqs,
            position_id=pid,
            posted_date=posted_date,
        ):
            continue
        listings.append(
            JobListing(
                position_id=pid,
                company=rec.company,
                title=rec.title,
                url=display_url,
                location=rec.location,
                employment_type="",
                posted_date=posted_date,
                description=rec.description,
                requirements=reqs,
                match_score=rec.match_score,
                match_reasons=reasons,
                source_site="",
                discovered_by=rec.source,
                apply_email=apply_email,
                apply_method=apply_method if apply_method not in ("—",) else "",
                url_status=url_status or "unavailable",
                url_hint=url_hint,
                link_note=link_note,
            )
        )
    listings.sort(key=lambda j: j.match_score, reverse=True)
    return listings


def job_search_result_from_history(iso_date: str | None = None) -> JobSearchResult:
    iso = iso_date or today_iso()
    _, records = load_history()
    day_records = [r for r in records if r.iso_date == iso]
    jobs = records_to_listings(day_records)
    return JobSearchResult(
        iso_date=iso,
        new_jobs=jobs,
        search_notes=[f"Resent from history — {len(jobs)} valid job(s) for {iso}."],
        providers_used=["history resend"],
        total_tracked=len(records),
    )


def format_job_search_email_html(result: JobSearchResult) -> str:
    profile_name = os.getenv("JOB_SEARCH_PROFILE_NAME", "").strip()
    heading = (
        f"Job Search — {html.escape(profile_name)} — {result.iso_date}"
        if profile_name
        else f"Job Search Summary — {result.iso_date}"
    )
    providers = ", ".join(result.providers_used) if result.providers_used else "—"
    notes_html = ""
    if result.search_notes:
        notes_html = "<h3>Search notes</h3><ul>" + "".join(
            f"<li>{html.escape(n)}</li>" for n in result.search_notes
        ) + "</ul>"

    if not result.new_jobs:
        jobs_html = (
            "<p><strong>No new matching jobs found today.</strong> "
            "Tracked listings are filtered out automatically on the next run.</p>"
        )
    else:
        rows = []
        for job in result.new_jobs:
            link = _job_action_links(job)
            reasons = "<br>".join(html.escape(r) for r in job.match_reasons[:3])
            reqs = "<br>".join(f"• {html.escape(r)}" for r in job.requirements[:4])
            pid = html.escape(job.position_id) if job.position_id else "—"
            contact_bits = []
            if job.apply_email:
                contact_bits.append(f"Email: {html.escape(job.apply_email)}")
            if job.location:
                contact_bits.append(html.escape(job.location))
            contact_html = "<br>".join(contact_bits)
            rows.append(
                f"""
                <tr>
                  <td style="vertical-align:top;width:11%;"><strong>{html.escape(job.company)}</strong><br>
                      <span style="color:#555;font-size:12px;">{html.escape(job.source_site or job.discovered_by)}</span>
                      {f"<br><span style='color:#555;font-size:11px;'>{contact_html}</span>" if contact_html else ""}</td>
                  <td style="vertical-align:top;width:14%;">{html.escape(job.title)}</td>
                  <td style="vertical-align:top;width:5%;text-align:center;">{job.match_score}%</td>
                  <td style="vertical-align:top;width:10%;">{html.escape(job.location or "—")}<br>
                      <small style="color:#555;">{html.escape(job.employment_type or "")}</small></td>
                  <td style="vertical-align:top;width:8%;">{link}<br>
                      <small style="color:#555;">ID: {pid}</small></td>
                  <td style="vertical-align:top;width:52%;line-height:1.45;">{html.escape(job.description)}<br><br>
                      <strong>Why match</strong><br>{reasons}<br><br>
                      <strong>Requirements</strong><br>{reqs}</td>
                </tr>
                """
            )
        jobs_html = f"""
        <p><strong>{len(result.new_jobs)} new job(s)</strong> (tracked total after save: {result.total_tracked})</p>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;table-layout:fixed;font-family:Arial,sans-serif;font-size:13px;">
          <colgroup>
            <col style="width:11%">
            <col style="width:14%">
            <col style="width:5%">
            <col style="width:10%">
            <col style="width:8%">
            <col style="width:52%">
          </colgroup>
          <thead style="background:#f0f4f8;">
            <tr>
              <th style="text-align:left;">Company</th>
              <th style="text-align:left;">Role</th>
              <th style="text-align:center;">Match</th>
              <th style="text-align:left;">Location</th>
              <th style="text-align:left;">Link</th>
              <th style="text-align:left;">Details</th>
            </tr>
          </thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
        """

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Job Search — {result.iso_date}</title></head>
<body style="font-family:Arial,sans-serif;line-height:1.5;color:#222;max-width:1100px;margin:0 auto;padding:16px;">
  <h2>{heading}</h2>
  <p>Providers: {html.escape(providers)}</p>
  {notes_html}
  {jobs_html}
  <hr>
  <p style="color:#666;font-size:12px;">
    Phase 1: search + verified links + optional CV email draft.
    Set JOB_SEARCH_COMPANY_WATCHLIST for priority employers.
    Auto-send CV: JOB_SEARCH_AUTO_APPLY_EMAIL=1 (future — use Email CV link for now).
    History saved to filter duplicates by position ID, company+title, URL, or company+description.
  </p>
</body></html>"""
