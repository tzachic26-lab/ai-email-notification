"""
Technology news summaries focused on AI and machine learning.

Fetches today's headlines from reputable tech outlets via Google News RSS,
filters for AI/ML relevance, and summarizes with strict accuracy rules.
"""

from __future__ import annotations

import html
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import quote

import feedparser

import truststore

truststore.inject_into_ssl()

from news_headlines_api import (
    MIN_SUMMARY_WORDS,
    MAX_SUMMARY_WORDS,
    MAX_ARTICLES,
    EMAIL_SUMMARY_MODEL,
    MODEL,
    Article,
    TokenUsage,
    _hebrew_date_display,
    _model_display_name,
    _entry_published_at,
    _merge_token_usage,
    _rss_item_date,
    format_publish_date_display,
    _strip_html,
    _strip_urls,
    _word_count,
)
from rss_fetch import fetch_attempts, fetch_retry_delay_seconds, parse_feed as _parse_feed

DEFAULT_SUBJECT = "בינה מלאכותית — שוק, מוצרים ומגמות"

BIG_AI_VENDORS = (
    "openai", "chatgpt", "gpt-", "anthropic", "claude", "google deepmind", "deepmind",
    "gemini", "google ai", "meta ai", "llama", "microsoft", "copilot", "azure ai",
    "nvidia", "blackwell", "apple intelligence", "amazon", "bedrock", "aws ai",
    "xai", "grok", "mistral", "cohere", "stability ai", "midjourney", "hugging face",
    "intel", "amd", "samsung", "ibm", "oracle", "salesforce", "adobe", "tesla",
)

RELEASE_MILESTONE_PATTERN = re.compile(
    r"\b("
    r"launch|launches|released|release|announces|announced|unveil|unveiled|"
    r"milestone|debut|rollout|available now|general availability|ga\b|"
    r"new model|new product|new version|version \d|v\d|update|upgrade|"
    r"partnership|acquisition|funding|raise|benchmark|sota|state-of-the-art|"
    r"open.?source|api|sdk|chip|gpu|datacenter|inference|training|"
    r"agent|multimodal|reasoning|fine-tune|context window|token"
    r")\b",
    re.IGNORECASE,
)


def _today() -> str:
    return date.today().isoformat()


def _recent_dates() -> set[str]:
    today = date.today()
    yesterday = today.fromordinal(today.toordinal() - 1)
    return {today.isoformat(), yesterday.isoformat()}


def _is_recent(item_date: str | None) -> bool:
    return bool(item_date and item_date in _recent_dates())

TRUSTED_RESEARCH_SOURCES = (
    "MIT Technology Review",
    "Ars Technica",
    "Nature",
    "IEEE Spectrum",
)

TRUSTED_INDUSTRY_SOURCES = (
    "TechCrunch",
    "The Verge",
    "Wired",
    "Reuters",
    "Bloomberg",
    "CNBC",
)

SOURCE_ALIASES: dict[str, str] = {
    "mit technology review": "MIT Technology Review",
    "technologyreview.com": "MIT Technology Review",
    "arstechnica.com": "Ars Technica",
    "arstechnica": "Ars Technica",
    "techcrunch.com": "TechCrunch",
    "techcrunch": "TechCrunch",
    "theverge.com": "The Verge",
    "the verge": "The Verge",
    "wired.com": "Wired",
    "wired": "Wired",
    "reuters": "Reuters",
    "bloomberg": "Bloomberg",
    "cnbc": "CNBC",
    "venturebeat": "VentureBeat",
    "venturebeat.com": "VentureBeat",
    "analytics india magazine": "Analytics India Magazine",
    "marktechpost": "MarkTechPost",
}

EXCLUDED_SOURCE_ALIASES = (
    "msn.com",
    "yahoo",
    "flipboard",
    "newsbreak",
)

TECH_SHALLOW_PATTERN = re.compile(
    r"("
    r"tips and tricks|life hack|daily habits|you won.t believe|"
    r"celebrity|viral|meme|influencer|horoscope|clickbait|"
    r"how i used chatgpt for a year|productivity hacks"
    r")",
    re.IGNORECASE,
)

AI_ML_PATTERN = re.compile(
    r"\b("
    r"artificial intelligence|machine learning|deep learning|neural network|"
    r"large language model|llms?|generative ai|gen\s*ai|foundation model|"
    r"transformer|diffusion model|computer vision|natural language processing|"
    r"reinforcement learning|openai|anthropic|deepmind|gemini|llama|copilot|"
    r"nvidia|chatgpt|claude|midjourney|mistral|xai|grok|hugging face|"
    r"ai model|ai chip|ai agent|autonomous agent|robotics ai|mlops|"
    r"inference|training data|pytorch|tensorflow|agentic"
    r")\b",
    re.IGNORECASE,
)

TECH_SUMMARY_PROMPT = f"""You are an expert Israeli technology journalist specializing in AI and machine learning.
Write a detailed, precise summary in natural Israeli Hebrew for a professional audience.

EDITORIAL STANDARDS:
- Be as accurate and detailed as possible — stick strictly to the headline, source, and snippet.
- Do NOT invent statistics, quotes, names, dates, funding amounts, or product details.
- Do NOT speculate, sensationalize, or add gossip, hype, or shallow commentary.
- Do NOT write generic "AI will change everything" filler — every sentence must reflect the reported story.
- Preserve exact product/model names (GPT, Claude, Gemini, Llama, etc.).
- If details are missing, state clearly: "פרטים נוספים לא סופקו בדיווח הראשוני".

CONTENT FOCUS (when supported by the snippet):
- Product launches, model releases, and milestones from major AI vendors
  (OpenAI, Anthropic, Google/DeepMind, Meta, Microsoft, NVIDIA, Apple, Amazon, xAI, Mistral, and peers)
- Industry trends, regulation, benchmarks, and research with concrete impact
- What changed, for whom, and why it matters — cause, effect, and context grounded in the report

FORMAT:
- Between {MIN_SUMMARY_WORDS} and {MAX_SUMMARY_WORDS} words.
- Dense, informative flowing prose — no bullet lists, no markdown, no links.
- Return ONLY the summary text in Hebrew."""

FEED_CONFIGS: list[tuple[str, str | None, bool]] = [
    (
        "https://news.google.com/rss/search?q="
        + quote("OpenAI OR ChatGPT release OR new model")
        + "&hl=en-US&gl=US&ceid=US:en",
        None,
        False,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("Anthropic Claude OR Google Gemini OR Meta Llama release")
        + "&hl=en-US&gl=US&ceid=US:en",
        None,
        False,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("NVIDIA AI chip OR Microsoft Copilot AI OR Amazon Bedrock")
        + "&hl=en-US&gl=US&ceid=US:en",
        None,
        False,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("AI product launch OR generative AI release OR LLM announcement")
        + "&hl=en-US&gl=US&ceid=US:en",
        None,
        False,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("artificial intelligence machine learning trend")
        + "&hl=en-US&gl=US&ceid=US:en",
        None,
        False,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("site:technologyreview.com artificial intelligence")
        + "&hl=en-US&gl=US&ceid=US:en",
        "MIT Technology Review",
        True,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("site:arstechnica.com artificial intelligence OR machine learning")
        + "&hl=en-US&gl=US&ceid=US:en",
        "Ars Technica",
        True,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("site:techcrunch.com AI OR machine learning")
        + "&hl=en-US&gl=US&ceid=US:en",
        "TechCrunch",
        True,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("site:theverge.com AI artificial intelligence")
        + "&hl=en-US&gl=US&ceid=US:en",
        "The Verge",
        True,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("site:wired.com artificial intelligence machine learning")
        + "&hl=en-US&gl=US&ceid=US:en",
        "Wired",
        True,
    ),
    (
        "https://news.google.com/rss/search?q="
        + quote("site:venturebeat.com AI machine learning")
        + "&hl=en-US&gl=US&ceid=US:en",
        "VentureBeat",
        True,
    ),
    (
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "TechCrunch",
        True,
    ),
    (
        "https://venturebeat.com/category/ai/feed/",
        "VentureBeat",
        True,
    ),
]


def _normalize_source_name(source: str) -> str:
    cleaned = source.strip()
    lower = cleaned.casefold()
    for alias, name in SOURCE_ALIASES.items():
        if alias in lower or lower == alias:
            return name
    return cleaned


def _is_excluded_source(source: str) -> bool:
    lower = source.casefold()
    return any(alias in lower for alias in EXCLUDED_SOURCE_ALIASES)


def _is_research_source(source: str) -> bool:
    normalized = _normalize_source_name(source)
    return normalized in TRUSTED_RESEARCH_SOURCES


def _is_industry_source(source: str) -> bool:
    normalized = _normalize_source_name(source)
    return normalized in TRUSTED_INDUSTRY_SOURCES


def _split_google_news_title(raw_title: str) -> tuple[str, str]:
    if " - " in raw_title:
        title, source = raw_title.rsplit(" - ", 1)
        return title.strip(), source.strip()
    return raw_title.strip(), "Unknown source"


def _is_shallow_or_gossip(title: str, snippet: str) -> bool:
    return bool(TECH_SHALLOW_PATTERN.search(f"{title} {snippet}"))


def _is_ai_ml_relevant(title: str, snippet: str) -> bool:
    text = f"{title} {snippet}"
    return bool(AI_ML_PATTERN.search(text) or RELEASE_MILESTONE_PATTERN.search(text))


def _article_priority(item: dict) -> int:
    text = f"{item['title']} {item['snippet']}".casefold()
    if _is_shallow_or_gossip(item["title"], item["snippet"]):
        return -100
    score = 0
    if any(vendor.casefold() in text for vendor in BIG_AI_VENDORS):
        score += 3
    if RELEASE_MILESTONE_PATTERN.search(text):
        score += 3
    if AI_ML_PATTERN.search(text):
        score += 1
    return score


def _collect_rss_entry(
    entry: feedparser.FeedParserDict,
    seen_titles: set[str],
    source_hint: str | None = None,
    *,
    trusted_ai_feed: bool = False,
) -> dict | None:
    item_date = _rss_item_date(entry)
    if not _is_recent(item_date):
        return None

    raw_title = entry.get("title", "")
    if source_hint:
        title = raw_title.strip()
        source = source_hint
    else:
        title, source = _split_google_news_title(raw_title)
        source = _normalize_source_name(source)

    if _is_excluded_source(source):
        return None
    if not title or title in seen_titles:
        return None

    snippet = _strip_html(entry.get("summary", "") or entry.get("description", ""))
    snippet = _strip_urls(snippet)
    if not trusted_ai_feed and not _is_ai_ml_relevant(title, snippet):
        return None
    if _is_shallow_or_gossip(title, snippet):
        return None

    seen_titles.add(title)
    return {
        "title": title,
        "date": item_date,
        "source": source,
        "snippet": snippet,
        "url": (entry.get("link") or "").strip(),
        "published_at": _entry_published_at(entry),
    }


def _select_balanced_items(candidates: list[dict], *, max_articles: int = MAX_ARTICLES) -> list[dict]:
    """Prioritize vendor releases/milestones, then diversify sources and topics."""
    from story_dedup import build_idf_weights, build_story_cluster_map, dedupe_with_backfill, is_similar_story

    idf_weights = build_idf_weights(candidates)
    cluster_map = build_story_cluster_map(
        candidates,
        idf_weights,
        rank_key=lambda item: (_article_priority(item), _is_industry_source(item["source"])),
    )

    ranked = sorted(
        candidates,
        key=lambda item: (_article_priority(item), _is_industry_source(item["source"])),
        reverse=True,
    )

    def _can_add(item: dict, selected: list[dict]) -> bool:
        cluster_id = cluster_map.get(item["title"], item["title"])
        if any(cluster_map.get(kept["title"], kept["title"]) == cluster_id for kept in selected):
            return False
        return not any(
            is_similar_story(item, kept, idf_weights=idf_weights) for kept in selected
        )

    research = next((item for item in ranked if _is_research_source(item["source"])), None)
    industry = next(
        (
            item
            for item in ranked
            if _is_industry_source(item["source"])
            and (not research or item["title"] != research["title"])
        ),
        None,
    )

    selected: list[dict] = []
    used_titles: set[str] = set()
    used_sources: set[str] = set()

    for item in (research, industry):
        if item and item["title"] not in used_titles and _can_add(item, selected):
            selected.append(item)
            used_titles.add(item["title"])
            used_sources.add(item["source"])

    for item in ranked:
        if len(selected) >= max_articles:
            break
        if (
            item["title"] in used_titles
            or item["source"] in used_sources
            or not _can_add(item, selected)
        ):
            continue
        selected.append(item)
        used_titles.add(item["title"])
        used_sources.add(item["source"])

    if len(selected) < max_articles:
        for item in ranked:
            if len(selected) >= max_articles:
                break
            if item["title"] in used_titles or not _can_add(item, selected):
                continue
            selected.append(item)
            used_titles.add(item["title"])

    if not selected:
        raise ValueError(
            f"לא נמצאו כתבות AI/טכנולוגיה עבור ({', '.join(sorted(_recent_dates()))})"
        )

    from story_dedup import dedupe_with_backfill

    return dedupe_with_backfill(selected, ranked, count=max_articles)


logger = logging.getLogger(__name__)


def _fetch_tech_ai_rss_candidates_once() -> tuple[list[dict], int, int]:
    candidates: list[dict] = []
    seen_titles: set[str] = set()
    feeds_ok = 0
    feeds_empty = 0

    for feed_url, source_hint, trusted in FEED_CONFIGS:
        try:
            feed = _parse_feed(feed_url)
        except Exception:
            feeds_empty += 1
            continue
        if feed.entries:
            feeds_ok += 1
        else:
            feeds_empty += 1
        for entry in feed.entries:
            item = _collect_rss_entry(
                entry,
                seen_titles,
                source_hint=source_hint,
                trusted_ai_feed=trusted,
            )
            if item:
                candidates.append(item)

    return candidates, feeds_ok, feeds_empty


def fetch_tech_ai_rss_items(*, max_articles: int = MAX_ARTICLES) -> list[dict]:
    """Fetch AI/tech RSS items; retry when feeds return empty (transient network/proxy)."""
    attempts = fetch_attempts()
    delay = fetch_retry_delay_seconds()
    dates_label = ", ".join(sorted(_recent_dates()))
    candidates: list[dict] = []

    for attempt in range(1, attempts + 1):
        candidates, feeds_ok, feeds_empty = _fetch_tech_ai_rss_candidates_once()
        logger.info(
            "Tech RSS pool: %s candidates from %s feeds (%s ok, %s empty), dates=%s",
            len(candidates),
            len(FEED_CONFIGS),
            feeds_ok,
            feeds_empty,
            dates_label,
        )
        if candidates:
            try:
                return _select_balanced_items(candidates, max_articles=max_articles)
            except ValueError as exc:
                if attempt < attempts:
                    logger.warning("%s — retrying all feeds in %ss (%s/%s)", exc, delay, attempt, attempts)
                    if delay:
                        time.sleep(delay)
                    continue
                raise
        if attempt < attempts:
            logger.warning(
                "Tech RSS: 0 candidates for (%s) — retrying all feeds in %ss (%s/%s)",
                dates_label,
                delay,
                attempt,
                attempts,
            )
            if delay:
                time.sleep(delay)

    raise ValueError(f"לא נמצאו כתבות AI/טכנולוגיה עבור ({dates_label})")


def _summary_user_message(title: str, source: str, snippet: str, date: str, published_at: str = "") -> str:
    publish_line = format_publish_date_display(date, published_at)
    return (
        f"כותרת: {title}\n"
        f"מקור: {source}\n"
        + (f"{publish_line}\n" if publish_line else f"תאריך: {date}\n")
        + f"תקציר: {snippet or 'לא סופק תקציר'}\n\n"
        f"כתוב סיכום בעברית בין {MIN_SUMMARY_WORDS} ל-{MAX_SUMMARY_WORDS} מילים."
    )


def _summarize_item(
    item: dict,
    *,
    summary_model: str = MODEL,
    vendor: str | None = None,
) -> tuple[str, TokenUsage]:
    from llm_providers import (
        GEMINI_GROUNDING_PROMPT_ADDENDUM,
        LLMVendor,
        gemini_grounding_enabled,
        resolve_vendor,
        summarize_with_vendor,
    )

    resolved_vendor = resolve_vendor(vendor)
    user_message = _summary_user_message(
        item["title"], item["source"], item["snippet"], item["date"], item.get("published_at", "")
    )
    system_prompt = TECH_SUMMARY_PROMPT
    use_grounding = False
    if resolved_vendor is LLMVendor.GEMINI and gemini_grounding_enabled():
        system_prompt = TECH_SUMMARY_PROMPT + GEMINI_GROUNDING_PROMPT_ADDENDUM
        use_grounding = True

    summary, tokens, _result = summarize_with_vendor(
        vendor=resolved_vendor,
        system_prompt=system_prompt,
        user_message=user_message,
        strip_urls_fn=_strip_urls,
        word_count_fn=_word_count,
        min_words=MIN_SUMMARY_WORDS,
        max_words=MAX_SUMMARY_WORDS,
        model=summary_model,
        use_grounding=use_grounding,
    )
    return summary, tokens


def _summarize_rss_item(
    item: dict,
    *,
    summary_model: str = MODEL,
    vendor: str | None = None,
) -> tuple[Article | None, TokenUsage]:
    summary, tokens = _summarize_item(item, summary_model=summary_model, vendor=vendor)
    if not summary:
        return None, tokens

    word_count = _word_count(summary)
    return (
        Article(
            title=item["title"],
            date=item["date"],
            source=item["source"],
            summary=summary,
            word_count=word_count,
            input_tokens=tokens.input_tokens,
            output_tokens=tokens.output_tokens,
            url=item.get("url", ""),
            published_at=item.get("published_at", ""),
        ),
        tokens,
    )


def fetch_tech_ai_articles(
    *,
    max_articles: int = MAX_ARTICLES,
    summary_model: str = MODEL,
    vendor: str | None = None,
) -> tuple[list[Article], TokenUsage]:
    from functools import partial

    from llm_providers import LLMVendor, resolve_vendor

    rss_items = fetch_tech_ai_rss_items(max_articles=max_articles)
    articles: list[Article] = []
    total_tokens = TokenUsage()
    summarize = partial(_summarize_rss_item, summary_model=summary_model, vendor=vendor)
    workers = 1 if resolve_vendor(vendor) is LLMVendor.GEMINI else max(1, len(rss_items))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for article, tokens in executor.map(summarize, rss_items):
            _merge_token_usage(total_tokens, tokens)
            if article:
                articles.append(article)
    if not articles:
        raise ValueError(f"No valid AI/ML articles were returned for today ({_today()})")
    return articles, total_tokens


def _format_email_article_block(article: Article, index: int, *, last: bool = False) -> str:
    paragraphs = "".join(
        f'<p style="margin:0 0 14px;font-size:16px;line-height:1.75;color:#374151;text-align:right;">'
        f"{html.escape(part)}</p>"
        for part in [p.strip() for p in article.summary.split("\n\n") if p.strip()]
        or ([article.summary.strip()] if article.summary.strip() else [])
    )
    border = "" if last else "border-bottom:1px solid #e8ecf1;"
    publish_label = format_publish_date_display(article.date, article.published_at)
    safe_publish = html.escape(publish_label) if publish_label else ""
    safe_source = html.escape(article.source)
    meta_line = (
        f'<span style="color:#374151;font-weight:600;">{safe_publish}</span>'
        f'&nbsp;&nbsp;|&nbsp;&nbsp;<span style="font-style:italic;">{safe_source}</span>'
        if safe_publish
        else f'<span style="font-style:italic;">{safe_source}</span>'
    )
    link_html = ""
    if article.url:
        safe_url = html.escape(article.url, quote=True)
        link_html = (
            f'<p style="margin:14px 0 0;font-size:13px;text-align:right;">'
            f'<a href="{safe_url}" style="color:#7c3aed;text-decoration:none;font-weight:600;">'
            f"קרא במקור ({html.escape(article.source)}) ←</a></p>"
        )

    return f"""
    <tr>
      <td style="padding:28px 36px;{border}direction:rtl;text-align:right;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
          <tr>
            <td width="44" valign="top" style="padding-left:14px;">
              <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td align="center" valign="middle"
                      style="width:32px;height:32px;background-color:#7c3aed;border-radius:16px;
                             color:#ffffff;font-family:Segoe UI,Arial,sans-serif;font-size:15px;font-weight:700;">
                    {index}
                  </td>
                </tr>
              </table>
            </td>
            <td valign="top" style="font-family:Segoe UI,Arial,sans-serif;direction:rtl;text-align:right;">
              <p style="margin:0 0 10px;font-size:13px;line-height:1.4;color:#6b7280;">
                {meta_line}
              </p>
              <h2 style="margin:0 0 14px;font-size:20px;line-height:1.35;color:#4c1d95;font-weight:700;direction:ltr;text-align:right;">
                {html.escape(article.title)}
              </h2>
              <div style="font-size:16px;line-height:1.75;color:#374151;">
                {paragraphs}
              </div>
              {link_html}
            </td>
          </tr>
        </table>
      </td>
    </tr>"""


def format_tech_ai_email_html(
    subject: str,
    articles: list[Article],
    total_words: int,
    *,
    summary_model: str = EMAIL_SUMMARY_MODEL,
    ai_provider_label: str | None = None,
    ai_provider_footer_label: str | None = None,
) -> str:
    safe_subject = html.escape(subject)
    display_date = html.escape(_hebrew_date_display())
    article_count = len(articles)
    header_provider = html.escape(ai_provider_label) if ai_provider_label else ""
    footer_provider = html.escape(
        ai_provider_footer_label or ai_provider_label or ""
    )
    sources_line = "מקורות: OpenAI, Anthropic, Google, Meta, Microsoft, NVIDIA ועוד"
    footer_line = html.escape(
        f"{footer_provider} · {sources_line}" if footer_provider else sources_line
    )

    if articles:
        article_rows = "".join(
            _format_email_article_block(article, index, last=(index == article_count))
            for index, article in enumerate(articles, start=1)
        )
        preheader = html.escape(
            f"{articles[0].title} — ועוד {article_count - 1} כתבות AI מהיום"
            if article_count > 1
            else articles[0].title
        )
    else:
        article_rows = """
    <tr>
      <td style="padding:40px 36px;font-family:Segoe UI,Arial,sans-serif;font-size:16px;
                 color:#6b7280;text-align:center;direction:rtl;">
        לא נמצאו כתבות AI/טכנולוגיה עבור הימים האחרונים.
      </td>
    </tr>"""
        preheader = html.escape("סיכום AI יומי")

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>חדשות AI וטכנולוגיה</title>
</head>
<body style="margin:0;padding:0;background-color:#f5f3ff;direction:rtl;">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;color:#f5f3ff;">
    {preheader}
  </div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
         style="background-color:#f5f3ff;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="620" cellspacing="0" cellpadding="0" border="0"
               style="max-width:620px;width:100%;background-color:#ffffff;border-radius:16px;
                      border:1px solid #ddd6fe;overflow:hidden;">
          <tr>
            <td style="padding:36px 36px 32px;direction:rtl;text-align:right;
                       background:linear-gradient(135deg,#4c1d95 0%,#7c3aed 100%);">
              <p style="margin:0 0 8px;font-family:Segoe UI,Arial,sans-serif;font-size:13px;
                        letter-spacing:0.06em;text-transform:uppercase;color:rgba(255,255,255,0.85);">
                בוקר טוב
              </p>
              <h1 style="margin:0 0 12px;font-family:Segoe UI,Arial,sans-serif;font-size:28px;
                         line-height:1.25;color:#ffffff;font-weight:700;">
                חדשות AI וטכנולוגיה
              </h1>
              <p style="margin:0 0 6px;font-family:Segoe UI,Arial,sans-serif;font-size:16px;color:rgba(255,255,255,0.95);">
                מיקוד: <strong>{safe_subject}</strong>
              </p>
              <p style="margin:0;font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:rgba(255,255,255,0.8);">
                {display_date}
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:14px 36px;background-color:#faf5ff;border-bottom:1px solid #ede9fe;direction:rtl;text-align:right;">
              <p style="margin:0;font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#64748b;">
                <span style="display:inline-block;background-color:#ede9fe;color:#6d28d9;
                             padding:4px 12px;border-radius:999px;font-weight:600;margin-left:8px;">
                  {article_count} כתבות מרכזיות
                </span>
                שחרורים · אבני דרך · מגמות · מוצרים חדשים · ~{max(1, total_words // 200)} דקות קריאה
              </p>
              <p style="margin:8px 0 0;font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#64748b;">
                {header_provider if header_provider else "&nbsp;"}
              </p>
            </td>
          </tr>
          {article_rows}
          <tr>
            <td style="padding:24px 36px;background-color:#faf5ff;border-top:1px solid #ede9fe;text-align:center;direction:rtl;">
              <p style="margin:0 0 6px;font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#94a3b8;">
                נשלח אוטומטית כל בוקר בשעה 08:15
              </p>
              <p style="margin:0;font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#cbd5e1;">
                {footer_line}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
