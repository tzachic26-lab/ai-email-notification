"""
Top Israeli news from the last 24 hours — LLM-ranked importance + detailed summaries.

Pipeline (retrieve-then-rerank, common in news aggregators and LLM research):
  1. Collect hard-news candidates from Israeli RSS (last 24h)
  2. LLM selects the most nationally important stories (Gemini or ChatGPT)
  3. LLM summarizes each story in Hebrew (facts only)
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import partial
from urllib.parse import quote

import feedparser

import truststore

truststore.inject_into_ssl()

from news_headlines_api import (
    DEFAULT_SUBJECT,
    EMAIL_SUMMARY_MODEL,
    MIN_SUMMARY_WORDS,
    MAX_SUMMARY_WORDS,
    Article,
    TokenUsage,
    _entry_published_at,
    _hebrew_date_display,
    _has_hard_news_signal,
    _is_excluded_source,
    _is_shallow_or_gossip,
    hard_news_only_enabled,
    _merge_token_usage,
    _normalize_source_name,
    _rss_entry_datetime,
    _rss_item_date,
    _split_google_news_title,
    _strip_html,
    _strip_urls,
    _substance_score,
    _summarize_rss_item,
    format_publish_date_display,
    format_outlook_newsletter_html,
)
from rss_fetch import fetch_attempts, fetch_retry_delay_seconds, parse_feed as _parse_feed

DEFAULT_SUBJECT_TOP = "האירועים המרכזיים בישראל — 24 שעות"
LOOKBACK_HOURS = 24
MAX_CANDIDATE_POOL = 45


def top_news_count() -> int:
    raw = os.getenv("DAILY_TOP_NEWS_COUNT", "8")
    try:
        return max(1, min(15, int(raw)))
    except ValueError:
        return 8


def top_news_headline(count: int | None = None) -> str:
    n = count if count is not None else top_news_count()
    return f"{n} האירועים המרכזיים בישראל"


TOP_NEWS_COUNT = top_news_count()

logger = logging.getLogger(__name__)

IMPORTANCE_SELECTION_PROMPT = """You are the chief editor of a major Israeli news desk.
From the numbered list of recent Israeli news items (last 24 hours), select exactly the stories
with the highest NATIONAL importance for Israel.

RANKING CRITERIA (in order of weight):
1. Security, war, terrorism, major military/diplomatic developments
2. Government, Knesset, legislation, major political decisions
3. Economy affecting the whole country (budget, markets, major policy)
4. Major social/legal events with nationwide impact
5. Significant international events directly affecting Israel

EXCLUDE completely:
- Crime blotter, local police stories, trials of private individuals
- Entertainment, celebrities, sports, lifestyle, consumer tips
- Gossip, viral fluff, human-interest without national significance
- Culture, leisure, tourism, food, fashion, TV/reality shows (תרבות, בידור, בילויים, רכילות)

RULES:
- Pick exactly {count} items by their list index (1-based).
- Each pick MUST be a distinct news event — never two items about the same story, angle, or ongoing situation.
- If several headlines cover the same event (e.g. Lebanon, Iran nuclear deal, same Knesset vote, same market move), pick ONLY the single most important one.
- Prefer diverse stories across security, politics, economy, and society.
- Use only information visible in the list; do not invent facts.
- importance_he: one sentence in Hebrew explaining why this story matters nationally.

Return JSON only:
{{"selected": [{{"index": <int>, "importance_he": "<Hebrew sentence>"}}, ...]}}"""


def _since_cutoff() -> datetime:
    return datetime.now().replace(microsecond=0) - timedelta(hours=LOOKBACK_HOURS)


def _collect_candidate_entry(
    entry: feedparser.FeedParserDict,
    seen_titles: set[str],
    *,
    since: datetime,
    source_hint: str | None = None,
) -> dict | None:
    dt = _rss_entry_datetime(entry)
    if not dt or dt < since:
        return None

    title, source = _split_google_news_title(entry.get("title", ""))
    source = source_hint or _normalize_source_name(source)
    if _is_excluded_source(source):
        return None
    if not title or title in seen_titles:
        return None

    snippet = _strip_html(entry.get("summary", "") or entry.get("description", ""))
    snippet = _strip_urls(snippet)
    if _is_shallow_or_gossip(title, snippet):
        return None
    if hard_news_only_enabled() and not _has_hard_news_signal(title, snippet):
        return None

    seen_titles.add(title)
    item_date = _rss_item_date(entry) or dt.date().isoformat()
    return {
        "title": title,
        "date": item_date,
        "source": source,
        "snippet": snippet,
        "url": (entry.get("link") or "").strip(),
        "published_at": _entry_published_at(entry),
    }


def _feed_configs() -> list[tuple[str, str | None]]:
    """Broad Israeli feeds for 24h candidate pool."""
    return [
        ("https://news.google.com/rss?hl=he&gl=IL&ceid=IL:he", None),
        (
            f"https://news.google.com/rss/search?q={quote('ישראל')}&hl=he&gl=IL&ceid=IL:he",
            None,
        ),
        (
            f"https://news.google.com/rss/search?q={quote('site:ynet.co.il')}&hl=he&gl=IL&ceid=IL:he",
            "ynet",
        ),
        (
            f"https://news.google.com/rss/search?q={quote('site:walla.co.il')}&hl=he&gl=IL&ceid=IL:he",
            "Walla",
        ),
        (
            f"https://news.google.com/rss/search?q={quote('site:maariv.co.il')}&hl=he&gl=IL&ceid=IL:he",
            "מעריב",
        ),
        (
            f"https://news.google.com/rss/search?q={quote('site:mako.co.il')}&hl=he&gl=IL&ceid=IL:he",
            "mako",
        ),
        (
            f"https://news.google.com/rss/search?q={quote('site:now14.co.il')}&hl=he&gl=IL&ceid=IL:he",
            "עכשיו 14",
        ),
        (
            f"https://news.google.com/rss/search?q={quote('site:israelhayom.co.il')}&hl=he&gl=IL&ceid=IL:he",
            "ישראל היום",
        ),
        (
            f"https://news.google.com/rss/search?q={quote('ביטחון ישראל')}&hl=he&gl=IL&ceid=IL:he",
            None,
        ),
        (
            f"https://news.google.com/rss/search?q={quote('כלכלה ישראל')}&hl=he&gl=IL&ceid=IL:he",
            None,
        ),
        (
            f"https://news.google.com/rss/search?q={quote('כנסת ממשלה')}&hl=he&gl=IL&ceid=IL:he",
            None,
        ),
    ]


def _fetch_last_24h_candidates_once(*, max_pool: int = MAX_CANDIDATE_POOL) -> list[dict]:
    since = _since_cutoff()
    seen_titles: set[str] = set()
    candidates: list[dict] = []
    feeds_ok = 0
    feeds_empty = 0

    for feed_url, source_hint in _feed_configs():
        feed = _parse_feed(feed_url)
        if feed.entries:
            feeds_ok += 1
        else:
            feeds_empty += 1
        for entry in feed.entries:
            item = _collect_candidate_entry(
                entry, seen_titles, since=since, source_hint=source_hint
            )
            if item:
                candidates.append(item)

    ranked = sorted(
        candidates,
        key=lambda item: (
            _substance_score(item["title"], item["snippet"]),
            item.get("published_at", ""),
        ),
        reverse=True,
    )
    pool = ranked[:max_pool]
    logger.info(
        "RSS pool: %s candidates from %s feeds (%s ok, %s empty), cutoff=%s",
        len(pool),
        len(_feed_configs()),
        feeds_ok,
        feeds_empty,
        since.isoformat(sep=" ", timespec="minutes"),
    )
    return pool


def fetch_last_24h_candidates(*, max_pool: int = MAX_CANDIDATE_POOL) -> list[dict]:
    """Collect hard-news candidates; retry when the pool is empty (transient network/proxy)."""
    min_required = TOP_NEWS_COUNT
    attempts = fetch_attempts()
    delay = fetch_retry_delay_seconds()
    pool: list[dict] = []

    for attempt in range(1, attempts + 1):
        pool = _fetch_last_24h_candidates_once(max_pool=max_pool)
        if len(pool) >= min_required:
            if attempt > 1:
                logger.info(
                    "RSS candidate pool recovered on attempt %s/%s (%s items)",
                    attempt,
                    attempts,
                    len(pool),
                )
            return pool
        if attempt < attempts:
            logger.warning(
                "Only %s hard-news candidates (need %s) — retrying all feeds in %ss (%s/%s)",
                len(pool),
                min_required,
                delay,
                attempt,
                attempts,
            )
            if delay:
                time.sleep(delay)

    return pool


def _format_candidate_list(candidates: list[dict]) -> str:
    lines: list[str] = []
    for index, item in enumerate(candidates, start=1):
        publish = format_publish_date_display(item["date"], item.get("published_at", ""))
        snippet = item["snippet"][:280].replace("\n", " ")
        lines.append(
            f"{index}. [{item['source']}] {item['title']}\n"
            f"   {publish or item['date']}\n"
            f"   {snippet or '(אין תקציר)'}"
        )
    return "\n\n".join(lines)


def _extract_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM selection response is not a JSON object")
    return data


def _parse_selection_response(raw: str, pool_size: int) -> list[dict]:
    data = _extract_json_object(raw)
    selected = data.get("selected", [])
    if not isinstance(selected, list):
        raise ValueError("LLM selection response missing 'selected' list")
    parsed: list[dict] = []
    used_indices: set[int] = set()
    for entry in selected:
        if not isinstance(entry, dict):
            continue
        index = int(entry.get("index", 0))
        if index < 1 or index > pool_size or index in used_indices:
            continue
        used_indices.add(index)
        parsed.append(
            {
                "index": index,
                "importance_he": str(entry.get("importance_he", "")).strip(),
            }
        )
    return parsed


def select_top_stories(
    candidates: list[dict],
    *,
    count: int = TOP_NEWS_COUNT,
    vendor: str | None = None,
    rank_model: str | None = None,
) -> tuple[list[dict], TokenUsage]:
    """Use Gemini or ChatGPT to pick the most important stories from the candidate pool."""
    from llm_providers import complete_chat, resolve_vendor, top_news_rank_model

    if len(candidates) < count:
        raise ValueError(
            f"Only {len(candidates)} hard-news items found in the last {LOOKBACK_HOURS}h; need {count}. "
            "Usually means Google News RSS returned empty (network/proxy not ready) — check logs for 'RSS empty'."
        )

    resolved_vendor = resolve_vendor(vendor)
    resolved_rank_model = rank_model or top_news_rank_model(resolved_vendor)
    pool = candidates[:MAX_CANDIDATE_POOL]
    user_message = (
        f"בחר בדיוק {count} כתבות החשובות ביותר לישראל מהרשימה.\n"
        f"חלון זמן: {LOOKBACK_HOURS} שעות אחרונות.\n\n"
        f"{_format_candidate_list(pool)}"
    )

    result = complete_chat(
        vendor=resolved_vendor,
        system_prompt=IMPORTANCE_SELECTION_PROMPT.format(count=count),
        user_message=user_message,
        model=resolved_rank_model,
        temperature=0.1,
        max_tokens=1024,
        json_response=True,
    )

    tokens = TokenUsage(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
    )

    picks = _parse_selection_response(result.text, len(pool))
    if len(picks) < count:
        # Fallback: fill from substance-ranked pool if LLM returned too few
        picked_indices = {p["index"] for p in picks}
        for index, item in enumerate(pool, start=1):
            if len(picks) >= count:
                break
            if index in picked_indices:
                continue
            picks.append({"index": index, "importance_he": ""})
            picked_indices.add(index)

    selected_items: list[dict] = []
    for pick in picks[:count]:
        item = dict(pool[pick["index"] - 1])
        item["importance_note"] = pick.get("importance_he", "")
        selected_items.append(item)

    from story_dedup import dedupe_with_backfill

    selected_items = dedupe_with_backfill(selected_items, pool, count=count)
    if len(selected_items) < count:
        raise ValueError(
            f"Only {len(selected_items)} diverse stories after deduplication; need {count}"
        )
    return selected_items, tokens


def select_top_stories_gpt(
    candidates: list[dict],
    *,
    count: int = TOP_NEWS_COUNT,
) -> tuple[list[dict], TokenUsage]:
    """Backward-compatible alias — ranks with OpenAI."""
    return select_top_stories(candidates, count=count, vendor="openai")


def _summarize_top_item(
    item: dict,
    subject: str,
    *,
    vendor: str | None = None,
    summary_model: str | None = None,
) -> tuple[Article | None, TokenUsage]:
    from llm_providers import email_summary_model, resolve_vendor

    model = summary_model or email_summary_model(resolve_vendor(vendor))
    article, tokens = _summarize_rss_item(
        item, subject, summary_model=model, vendor=vendor
    )
    if not article:
        return None, tokens
    return (
        Article(
            title=article.title,
            date=article.date,
            source=article.source,
            summary=article.summary,
            word_count=article.word_count,
            input_tokens=article.input_tokens,
            output_tokens=article.output_tokens,
            url=item.get("url", ""),
            published_at=article.published_at,
            importance_note=item.get("importance_note", ""),
        ),
        tokens,
    )


def fetch_top_israel_articles(
    subject: str = DEFAULT_SUBJECT,
    *,
    count: int = TOP_NEWS_COUNT,
    vendor: str | None = None,
    rank_model: str | None = None,
    summary_model: str | None = None,
) -> tuple[list[Article], TokenUsage]:
    """Fetch, LLM-rank, and summarize the top Israeli stories from the last 24 hours."""
    from llm_providers import LLMVendor, email_summary_model, resolve_vendor

    candidates = fetch_last_24h_candidates()
    selected, tokens = select_top_stories(
        candidates, count=count, vendor=vendor, rank_model=rank_model
    )

    resolved_vendor = resolve_vendor(vendor)
    effective_summary_model = summary_model or email_summary_model(resolved_vendor)

    articles: list[Article] = []
    summarize = partial(
        _summarize_top_item,
        subject=subject,
        vendor=vendor,
        summary_model=effective_summary_model,
    )
    workers = 1 if resolve_vendor(vendor) is LLMVendor.GEMINI else len(selected)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for article, article_tokens in executor.map(summarize, selected):
            _merge_token_usage(tokens, article_tokens)
            if article:
                articles.append(article)

    if len(articles) < count:
        raise ValueError(
            f"Only {len(articles)} valid summaries from the last {LOOKBACK_HOURS}h"
        )
    return articles, tokens


def format_top_news_email_html(
    articles: list[Article],
    total_words: int,
    *,
    summary_model: str = EMAIL_SUMMARY_MODEL,
    ai_provider_label: str | None = None,
    ai_provider_footer_label: str | None = None,
) -> str:
    return format_outlook_newsletter_html(
        title=top_news_headline(len(articles)),
        subject=DEFAULT_SUBJECT_TOP,
        articles=articles,
        total_words=total_words,
        summary_model=summary_model,
        schedule_time="08:30",
        stats_label=f"{len(articles)} כתבות נבחרות",
        subtitle=_hebrew_date_display() + " · 24 שעות אחרונות",
        footer_note="חדשות נטו · עובדות מהמקור בלבד",
        ai_provider_label=ai_provider_label,
        ai_provider_footer_label=ai_provider_footer_label,
    )
