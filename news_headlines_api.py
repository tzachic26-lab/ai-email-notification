"""
Israeli news summaries — today's headlines from Israel in Hebrew via GPT-4.1 Nano.

Headlines are fetched from Israeli Google News RSS; each article is summarized by gpt-4.1-nano only.

Run the UI (opens browser and loads today's Israeli news automatically):
    uv run python news_headlines_api.py

Run the REST API:
    uv run python news_headlines_api.py --api
"""

import html
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import feedparser

import truststore

truststore.inject_into_ssl()

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from llm_providers import get_openai_client
from pydantic import BaseModel, Field

load_dotenv(override=True)

from network_env import configure_http_proxy

configure_http_proxy()

MODEL = os.getenv("OPENAI_UI_MODEL", "gpt-4.1-nano")
EMAIL_SUMMARY_MODEL = os.getenv("OPENAI_EMAIL_SUMMARY_MODEL", "gpt-4.1-mini")
FOLLOWUP_MODEL = "gpt-4.1-mini"  # standard follow-up + web search
FOLLOWUP_DEEP_MODEL = "gpt-4.1"  # in-depth analysis when user asks for depth
DEEP_QUESTION_HINTS = (
    "מעמיק",
    "לעומק",
    "עומק",
    "ניתוח מעמיק",
    "ניתוח מלא",
    "בפירוט",
    "פירוט",
    "מפורט",
    "הרחב",
    "הרחבה",
    "הסבר מפורט",
    "תעמיק",
    "כל ההיבטים",
    "כל הפרטים",
    "השלכות מלאות",
    "מחקר",
    "deep dive",
    "in depth",
    "in-depth",
    "comprehensive",
    "detailed analysis",
    "elaborate",
    "thorough",
)
# OpenAI list price per 1M tokens (USD) — https://developers.openai.com/api/docs/models/gpt-4.1-nano
INPUT_PRICE_PER_M = 0.10
OUTPUT_PRICE_PER_M = 0.40
MAX_ARTICLES = 10
EMAIL_MAX_ARTICLES = 5
EMAIL_MAX_TECH_ARTICLES = 8
MIN_SUMMARY_WORDS = 200
MAX_SUMMARY_WORDS = 400
DEFAULT_SUBJECT = "חדשות ישראל"
TODAY = date.today().isoformat()

# ערוץ 14 rebranded in 2021 — official name: עכשיו 14 (site: now14.co.il / c14.co.il)
CHANNEL_14_OFFICIAL = "עכשיו 14"
CHANNEL_14_ALIASES = (
    "ערוץ 14",
    "עכשיו 14",
    "now 14",
    "now14",
    "c14",
    "חדשות c14",
    "דסק החדשות c14",
    "channel 14",
)

ISRAEL_HAYOM_OFFICIAL = "ישראל היום"
ISRAEL_HAYOM_ALIASES = (
    "ישראל היום",
    "israel hayom",
    "israelhayom",
    "israel-hayom",
    "היום",  # Google News short label for israelhayom.co.il
)

EXCLUDED_SOURCE_ALIASES = (
    "הארץ",
    "haaretz",
    "haaretz.co.il",
)

SHALLOW_GOSSIP_PATTERN = re.compile(
    r"("
    r"רכילות|סלבריט|סנסצי|ויראל|לא תאמינו|דרמה ב(?:ין|תוך)|"
    r"תמונ(?:ה|ות) hot|פרש(?:ן|נים) ש(?:ח|מ)|מזל(?:\s|)|הורוסקופ|"
    r"נותרו בהלם|התיעוד המפתיע|מפתיע של ה|"
    r"יש\s+לנו\s+בשורה|חוסם\s+פרסומות|adblock|"
    r"gossip|celebrity|viral video|clickbait|meme|influencer|"
    r"tiktok|life hack|dating tips|horoscope"
    r")",
    re.IGNORECASE,
)

# Crime blotter / פלילים — not political or security hard news.
CRIME_NEWS_PATTERN = re.compile(
    r"("
    r"נעצר(?:ה|ים|ות)?|נתפ(?:ס|סה|סים|סות)|חשוד(?:ים|ות)?\s|הובא(?:ה)?\s+לדין|"
    r"רצח|נרצח(?:ה|ו)?|דקיר(?:ה|ות|ו)?|דוקר(?:ה|ו)?|"
    r"אונס(?:ו)?|שוד(?:ו|ה|ים)?|פריצ(?:ה|ות)\s+ל(?:בית|דירה)|"
    r"גנ(?:ב|יב(?:ה|ות))|תקיפ(?:ה|ות)|עביר(?:ה|ות)\s+מין|פש(?:ע|יע)|"
    r"תקיפה\s+מינית|אלימות\s+ב(?:משפחה|זוג)|סחר\s+בסמים|"
    r"ירי\s+ל(?:עבר|כיוון)|רכב\s+גנוב|גניב(?:ת|ה)\s+רכב|"
    r"הורשע(?:ה|ו)?|הוכרע(?:ה|ו)?|"
    r"murder|murdered|stabbing|stabbed|robbery|robbed|arrested|"
    r"sexual assault|rape suspect|crime scene"
    r")",
    re.IGNORECASE,
)

# Entertainment, celebrities, pop culture — not hard news.
ENTERTAINMENT_CULTURE_PATTERN = re.compile(
    r"("
    r"זמר(?:ה|ים|ית)?|שחק(?:ן|נית|נים|ניות)|סלב(?:ריט)?|מפורסם(?:ה|ים|ות)?|"
    r"(?:^|\s)אמנ(?:ות|י(?:ת|ים)?)|(?:^|\s)אומ(?:ן|נ(?:ית|ים|יות)?)|הוליווד|אוסקר|"
    r"פרס\s+(?:אמי|גרמי|אופיר)|ריאליטי|אלבום(?:\s|)|הופע(?:ה|ות)|"
    r"אירווי(?:זיון|zyon)|שיר(?:\s|ה\s+)ראשון|להק(?:ה|ת)|"
    r"פסטיבל\s+(?:סרטים|קאן)|קולנ(?:וע|ועי)|סדר(?:ה|ות)\s+חדש|"
    r"נטflix|נתflix|תוכנית\s+(?:ריאליטי|בידור)|עולם\s+הה(?:יפ|י)ופ|"
    r"אופנ(?:ה|ות)|מסעד(?:ה|ות)|מתכון|דיאט(?:ה|ות)|פחות\s+זה\s+יותר|"
    r"לפני\s+ש(?:משלמים|קונים)|מחלק(?:ה|ות)\s+(?:פרימיום|עסקים)|"
    r"singer|actor|actress|album|concert|reality show|box office|"
    r"red carpet|grammy|emmy|oscar|hollywood|celebrity"
    r")",
    re.IGNORECASE,
)

# Sports — not hard news.
SPORTS_NEWS_PATTERN = re.compile(
    r"("
    r"כדורגל|כדורסל|הפועל|מכבי(?:\s|)|ליג(?:ה|ת)|משחק(?:\s|)|מונדיאל|אליפות(?:\s|)|"
    r"nba|אבדיה|פורטלנד|יורוליג|"
    r"football|premier league|champions league|world cup|nba|euro 20"
    r")",
    re.IGNORECASE,
)

# Political, security, diplomacy — keep even if polluted snippet mentions these.
POLITICAL_NEWS_OVERRIDE_PATTERN = re.compile(
    r"("
    r"נתניהו|ראש\s+(?:הממשלה|ממשלה)|שר(?:\s|)|ח[\"']כ|כנסת|ממשל(?:ה|ת)?|"
    r"בג[\"']ץ|ועד(?:ה|ת)|חקיק|דיפלומט|שגריר|הסכם|מלחמה|צה[\"']ל|"
    r"חיל\s+(?:האוויר|הים|היבשה)|מבצע|ביטחון|איראן|לבנון|חמאס|"
    r"כלכלה|שוק|בנק(?:\s|)|ריבית|תקציב|"
    r"government|parliament|election|sanctions|diplomacy|security|"
    r"prime minister|minister|knesset|idf|air force"
    r")",
    re.IGNORECASE,
)

SUBSTANTIVE_NEWS_PATTERN = re.compile(
    r"("
    r"ממשל|כנסת|ביטחון|צה[\"']ל|רצח|פיגוע|מלחמה|הסכם|משפט|עלייה לתורה|"
    r"כלכלה|שוק|בנק|ריבית|תקציב|דיפלומט|שר(?:\s|)|ראש הממשלה|"
    r"חקיק|בג[\"']ץ|משטרה|רשות|ועדת|מבצע|ירי|טיל|"
    r"policy|election|security|economy|court|government|parliament|sanctions"
    r")",
    re.IGNORECASE,
)

ISRAELI_SOURCES = (
    "Ynet, Walla, Maariv, Globes, The Marker, Kan, Channel 12, "
    f"{CHANNEL_14_OFFICIAL}, {ISRAEL_HAYOM_OFFICIAL}, Calcalist"
)

SUMMARY_PROMPT = f"""You are an expert Israeli news editor writing for a serious, well-informed audience.
Write a detailed, precise news summary in natural Israeli Hebrew.

EDITORIAL STANDARDS:
- Be as accurate and detailed as possible within the word limit — every sentence should carry factual weight.
- Stick strictly to the headline, source, and snippet provided. Do not drift from the reported story.
- Do NOT invent facts, quotes, numbers, names, or timelines not supported by the source material.
- Do NOT add gossip, speculation, sensationalism, moralizing, or filler phrases.
- Do NOT write shallow or "clickbait-style" prose — no empty hype, no vague generalities.
- If the snippet lacks detail, say explicitly what is known and what remains unconfirmed.
- You may add only brief, widely established background context that helps understand the event — not new claims.

CONTENT FOCUS (hard news only):
- Politics, government, security, diplomacy, economy, legislation, and major public policy.
- Do NOT cover crime blotter, entertainment, celebrities, sports gossip, or lifestyle fluff.
- What happened, who is involved, when and where (as reported), and verified consequences so far.
- Why it matters for Israel and for the reader — policy, security, economy, society, or diplomacy as relevant.
- Cause-and-effect and context only when grounded in the snippet or uncontroversial public knowledge.

FORMAT:
- Between {MIN_SUMMARY_WORDS} and {MAX_SUMMARY_WORDS} words.
- Dense, informative flowing prose — no bullet lists, no markdown, no links, no image references.
- Return ONLY the summary text in Hebrew."""

FOLLOWUP_SYSTEM_PROMPT = """You are an expert Israeli news analyst answering follow-up questions in Hebrew.
Use the article context (source, title, summary) as your primary basis.
When the user needs current or broader facts, use web search for up-to-date information.
Answer in natural Israeli Hebrew — clear, accurate, and helpful.
If something is uncertain, say so. Do not invent quotes or facts."""

FOLLOWUP_DEEP_SYSTEM_PROMPT = """You are a senior Israeli news analyst providing in-depth follow-up analysis in Hebrew.
Use the article context (source, title, summary) as your starting point.
Use web search extensively for current facts, background, and multiple perspectives.
Provide a thorough, structured analysis: context, key developments, implications for Israel,
broader regional/global angles, and open questions where relevant.
Write in natural Israeli Hebrew — detailed but clear. If uncertain, say so explicitly."""

client = get_openai_client()
app = FastAPI(
    title="News Headlines API",
    description=f"Up to {MAX_ARTICLES} Israeli news summaries from today ({MIN_SUMMARY_WORDS}-{MAX_SUMMARY_WORDS} words, Hebrew, gpt-4.1-nano).",
    version="2.0.0",
)


class HeadlinesRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200, examples=["בינה מלאכותית"])


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class CostUsage(BaseModel):
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    total_cost_usd: float = 0.0


class Article(BaseModel):
    title: str
    date: str
    source: str
    summary: str
    word_count: int
    input_tokens: int = 0
    output_tokens: int = 0
    url: str = ""
    published_at: str = ""
    importance_note: str = ""


class HeadlinesResponse(BaseModel):
    subject: str
    articles: list[Article]
    html: str
    article_count: int
    total_word_count: int
    token_usage: TokenUsage
    cost_usage: CostUsage


class FollowupRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    date: str = Field(..., min_length=1, max_length=32)
    source: str = Field(..., min_length=1, max_length=200)
    summary: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=1000)


class FollowupResponse(BaseModel):
    answer: str
    model: str
    token_usage: TokenUsage


def _model_display_name(model: str) -> str:
    names = {
        "gpt-4.1-nano": "GPT-4.1 Nano",
        "gpt-4.1-mini": "GPT-4.1 Mini",
        "gpt-4.1": "GPT-4.1",
    }
    return names.get(model, model)


def _calculate_cost(usage: TokenUsage) -> CostUsage:
    input_cost = usage.input_tokens / 1_000_000 * INPUT_PRICE_PER_M
    output_cost = usage.output_tokens / 1_000_000 * OUTPUT_PRICE_PER_M
    return CostUsage(
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        total_cost_usd=input_cost + output_cost,
    )


def _format_usd(amount: float) -> str:
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def _token_cost_panel_html(usage: TokenUsage, cost: CostUsage) -> str:
    return f"""
  <aside class="token-cost-panel">
    <div class="panel-title">טוקנים ועלות · GPT-4.1 Nano</div>
    <div class="panel-row">
      <span class="panel-label">קלט (Input)</span>
      <span class="panel-metrics">{usage.input_tokens:,} · {_format_usd(cost.input_cost_usd)}</span>
    </div>
    <div class="panel-row">
      <span class="panel-label">פלט (Output)</span>
      <span class="panel-metrics">{usage.output_tokens:,} · {_format_usd(cost.output_cost_usd)}</span>
    </div>
    <div class="panel-row panel-total">
      <span class="panel-label">סה״כ</span>
      <span class="panel-metrics">{usage.total_tokens:,} · {_format_usd(cost.total_cost_usd)}</span>
    </div>
    <div class="panel-note">${INPUT_PRICE_PER_M:.2f}/1M input · ${OUTPUT_PRICE_PER_M:.2f}/1M output</div>
  </aside>"""


def _word_count(text: str) -> int:
    plain = re.sub(r"https?://\S+", "", text)
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
    return len([word for word in re.split(r"\s+", plain.strip()) if word])


def _strip_urls(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _rss_entry_datetime(entry: feedparser.FeedParserDict) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = entry.get(attr)
        if parsed:
            return datetime(*parsed[:6])
    for attr in ("published", "updated"):
        raw = entry.get(attr)
        if raw:
            try:
                return parsedate_to_datetime(raw)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _rss_item_date(entry: feedparser.FeedParserDict) -> str | None:
    dt = _rss_entry_datetime(entry)
    return dt.date().isoformat() if dt else None


def _entry_published_at(entry: feedparser.FeedParserDict) -> str:
    dt = _rss_entry_datetime(entry)
    if not dt:
        return ""
    return dt.replace(microsecond=0).isoformat(sep="T", timespec="minutes")


def format_publish_date_display(date: str, published_at: str = "") -> str:
    """Hebrew publish date for emails/UI; includes time when RSS provides it."""
    months = [
        "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
        "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
    ]
    if published_at:
        try:
            dt = datetime.fromisoformat(published_at)
            date_part = f"{dt.day} ב{months[dt.month - 1]} {dt.year}"
            if dt.hour or dt.minute:
                return f"פורסם: {date_part}, {dt.strftime('%H:%M')}"
            return f"פורסם: {date_part}"
        except ValueError:
            pass
    if date:
        try:
            d = date.fromisoformat(date)
            return f"פורסם: {d.day} ב{months[d.month - 1]} {d.year}"
        except ValueError:
            return f"פורסם: {date}"
    return ""


def _normalize_source_name(source: str) -> str:
    """Map RSS/Google News publisher labels to a consistent display name."""
    cleaned = source.strip()
    lower = cleaned.casefold()
    for alias in CHANNEL_14_ALIASES:
        alias_folded = alias.casefold()
        if lower == alias_folded or alias_folded in lower:
            return CHANNEL_14_OFFICIAL
    for alias in ISRAEL_HAYOM_ALIASES:
        alias_folded = alias.casefold()
        if lower == alias_folded or alias_folded in lower:
            return ISRAEL_HAYOM_OFFICIAL
    return cleaned


def _is_channel_14(source: str) -> bool:
    return _normalize_source_name(source) == CHANNEL_14_OFFICIAL


def _is_israel_hayom(source: str) -> bool:
    return _normalize_source_name(source) == ISRAEL_HAYOM_OFFICIAL


def _is_excluded_source(source: str) -> bool:
    lower = source.strip().casefold()
    return any(alias.casefold() in lower or lower == alias.casefold() for alias in EXCLUDED_SOURCE_ALIASES)


def _is_shallow_or_gossip(title: str, snippet: str) -> bool:
    """Exclude gossip, crime blotter, entertainment, and sports — hard news only."""
    title = title.strip()
    # RSS snippets often concatenate unrelated headlines; filter by title first.
    if SHALLOW_GOSSIP_PATTERN.search(title):
        return True
    if ENTERTAINMENT_CULTURE_PATTERN.search(title):
        return True
    if SPORTS_NEWS_PATTERN.search(title):
        return True
    if CRIME_NEWS_PATTERN.search(title):
        return True
    combined = f"{title} {snippet.strip()}"
    if SHALLOW_GOSSIP_PATTERN.search(combined):
        return True
    if ENTERTAINMENT_CULTURE_PATTERN.search(combined):
        return True
    if SPORTS_NEWS_PATTERN.search(combined):
        return True
    if (
        CRIME_NEWS_PATTERN.search(combined)
        and not POLITICAL_NEWS_OVERRIDE_PATTERN.search(title)
    ):
        return True
    return False


def _substance_score(title: str, snippet: str) -> int:
    if _is_shallow_or_gossip(title, snippet):
        return -100
    text = f"{title} {snippet}"
    score = 0
    if SUBSTANTIVE_NEWS_PATTERN.search(text):
        score += 3
    if len(snippet.strip()) > 80:
        score += 1
    return score


def _split_google_news_title(raw_title: str) -> tuple[str, str]:
    if " - " in raw_title:
        title, source = raw_title.rsplit(" - ", 1)
        return title.strip(), source.strip()
    return raw_title.strip(), "מקור לא ידוע"


def _collect_rss_entry(
    entry: feedparser.FeedParserDict,
    seen_titles: set[str],
    source_hint: str | None = None,
) -> dict | None:
    item_date = _rss_item_date(entry)
    if item_date != TODAY:
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
    seen_titles.add(title)
    return {
        "title": title,
        "date": item_date,
        "source": source,
        "snippet": _strip_urls(snippet),
        "url": (entry.get("link") or "").strip(),
        "published_at": _entry_published_at(entry),
    }


def _subject_matches(subject: str, title: str, snippet: str) -> bool:
    if subject.strip() == DEFAULT_SUBJECT:
        return True
    haystack = f"{title} {snippet}".lower()
    for token in re.split(r"\s+", subject.strip()):
        if len(token) >= 3 and token.lower() in haystack:
            return True
    return False


def _source_feed_query(source_term: str, subject: str) -> str:
    """For the default subject, search the outlet alone — extra terms hide today's items."""
    if subject == DEFAULT_SUBJECT:
        return source_term
    return f"{source_term} {subject}"


def _fetch_israel_rss_items(subject: str, *, max_articles: int = MAX_ARTICLES) -> list[dict]:
    """Fetch today's Israeli headlines with balance: עכשיו 14 + ישראל היום + others."""
    subject = subject.strip()
    # Dedicated source feeds first (indices 0–3); general feeds last (4–5).
    feed_configs: list[tuple[str, str | None]] = [
        (
            f"https://news.google.com/rss/search?q={quote(_source_feed_query('עכשיו 14', subject))}&hl=he&gl=IL&ceid=IL:he",
            None,
        ),
        (
            f"https://news.google.com/rss/search?q={quote(_source_feed_query('ערוץ 14', subject))}&hl=he&gl=IL&ceid=IL:he",
            None,
        ),
        (
            f"https://news.google.com/rss/search?q={quote(_source_feed_query('site:israelhayom.co.il', subject))}&hl=he&gl=IL&ceid=IL:he",
            ISRAEL_HAYOM_OFFICIAL,
        ),
        (
            f"https://news.google.com/rss/search?q={quote(_source_feed_query(ISRAEL_HAYOM_OFFICIAL, subject))}&hl=he&gl=IL&ceid=IL:he",
            None,
        ),
        (f"https://news.google.com/rss/search?q={quote(f'{subject} ישראל')}&hl=he&gl=IL&ceid=IL:he", None),
        ("https://news.google.com/rss?hl=he&gl=IL&ceid=IL:he", None),
    ]

    candidates: list[dict] = []
    seen_titles: set[str] = set()

    for index, (feed_url, source_hint) in enumerate(feed_configs):
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            item = _collect_rss_entry(entry, seen_titles, source_hint=source_hint)
            if not item:
                continue
            if index >= 4 and not _subject_matches(subject, item["title"], item["snippet"]):
                continue
            candidates.append(item)

    return _select_balanced_items(candidates, max_articles=max_articles)


def _is_reserved_source(source: str) -> bool:
    """Sources already guaranteed by balance rules — excluded when filling the rest."""
    return _is_channel_14(source) or _is_israel_hayom(source)


def _select_balanced_items(candidates: list[dict], *, max_articles: int = MAX_ARTICLES) -> list[dict]:
    """Prefer one article from עכשיו 14 and ישראל היום when available, then fill substantively."""
    import logging

    logger = logging.getLogger(__name__)
    ranked = sorted(
        candidates,
        key=lambda item: _substance_score(item["title"], item["snippet"]),
        reverse=True,
    )
    if not ranked:
        return []

    channel_14 = next((item for item in ranked if _is_channel_14(item["source"])), None)
    israel_hayom = next((item for item in ranked if _is_israel_hayom(item["source"])), None)

    missing = []
    if not channel_14:
        missing.append(CHANNEL_14_OFFICIAL)
    if not israel_hayom:
        missing.append(ISRAEL_HAYOM_OFFICIAL)
    if missing:
        logger.warning(
            "No today article from preferred source(s) %s — continuing with other outlets",
            ", ".join(missing),
        )

    selected: list[dict] = []
    used_titles: set[str] = set()
    used_sources: set[str] = set()

    for preferred in (channel_14, israel_hayom):
        if preferred and preferred["title"] not in used_titles:
            selected.append(preferred)
            used_titles.add(preferred["title"])
            used_sources.add(preferred["source"])

    # Prefer other Israeli outlets — one article per source when possible.
    for item in ranked:
        if len(selected) >= max_articles:
            break
        if (
            item["title"] in used_titles
            or _is_reserved_source(item["source"])
            or item["source"] in used_sources
        ):
            continue
        selected.append(item)
        used_titles.add(item["title"])
        used_sources.add(item["source"])

    # Allow duplicate sources only if unique outlets did not fill all slots.
    if len(selected) < max_articles:
        for item in ranked:
            if len(selected) >= max_articles:
                break
            if item["title"] in used_titles or _is_reserved_source(item["source"]):
                continue
            selected.append(item)
            used_titles.add(item["title"])

    # Only if general feeds did not yield enough — add more from the reserved outlets.
    if len(selected) < max_articles:
        for item in ranked:
            if len(selected) >= max_articles:
                break
            if item["title"] in used_titles:
                continue
            selected.append(item)
            used_titles.add(item["title"])

    return selected[:max_articles]


def _summarize_with_nano(
    title: str,
    source: str,
    snippet: str,
    subject: str,
    date: str = "",
    published_at: str = "",
    *,
    summary_model: str = MODEL,
    vendor: str | None = None,
) -> tuple[str, TokenUsage]:
    """Use the configured model/vendor to write a Hebrew summary (200-400 words) for one news item."""
    from llm_providers import (
        GEMINI_GROUNDING_PROMPT_ADDENDUM,
        LLMVendor,
        gemini_grounding_enabled,
        resolve_vendor,
        summarize_with_vendor,
    )

    resolved_vendor = resolve_vendor(vendor)
    user_message = _summary_user_message(title, source, snippet, subject, date, published_at)
    system_prompt = SUMMARY_PROMPT
    use_grounding = False
    if resolved_vendor is LLMVendor.GEMINI and gemini_grounding_enabled():
        system_prompt = SUMMARY_PROMPT + GEMINI_GROUNDING_PROMPT_ADDENDUM
        use_grounding = True
    summary, tokens, _result = summarize_with_vendor(
        vendor=resolved_vendor,
        system_prompt=system_prompt,
        user_message=user_message,
        strip_urls_fn=_strip_urls,
        word_count_fn=_word_count,
        min_words=MIN_SUMMARY_WORDS,
        max_words=MAX_SUMMARY_WORDS,
        model=summary_model if resolved_vendor is LLMVendor.OPENAI else None,
        use_grounding=use_grounding,
    )
    return summary, tokens


def _summary_user_message(
    title: str, source: str, snippet: str, subject: str, date: str, published_at: str = ""
) -> str:
    publish_line = format_publish_date_display(date, published_at)
    return (
        f"נושא כללי: {subject}\n"
        f"כותרת: {title}\n"
        f"מקור: {source}\n"
        + (f"{publish_line}\n" if publish_line else f"תאריך: {date}\n")
        + f"תקציר: {snippet or 'לא סופק תקציר'}\n\n"
        f"כתוב סיכום חדשותי בעברית בין {MIN_SUMMARY_WORDS} ל-{MAX_SUMMARY_WORDS} מילים."
    )


def _summarize_rss_item(
    item: dict,
    subject: str,
    *,
    summary_model: str = MODEL,
    vendor: str | None = None,
) -> tuple[Article | None, TokenUsage]:
    """Summarize one RSS item into an Article (for parallel execution)."""
    summary, tokens = _summarize_with_nano(
        item["title"],
        item["source"],
        item["snippet"],
        subject,
        item["date"],
        item.get("published_at", ""),
        summary_model=summary_model,
        vendor=vendor,
    )
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
            published_at=item.get("published_at", ""),
            url=item.get("url", ""),
        ),
        tokens,
    )


def _merge_token_usage(total: TokenUsage, tokens: TokenUsage) -> None:
    total.input_tokens += tokens.input_tokens
    total.output_tokens += tokens.output_tokens
    total.total_tokens += tokens.total_tokens


def _followup_context(article: Article, question: str) -> str:
    return (
        f"מקור: {article.source}\n"
        f"תאריך: {article.date}\n"
        f"כותרת: {article.title}\n"
        f"סיכום הכתבה:\n{article.summary}\n\n"
        f"שאלת המשך: {question.strip()}"
    )


def _wants_deep_analysis(question: str) -> bool:
    """Detect when the user asks for in-depth / detailed follow-up."""
    lower = question.casefold()
    return any(hint.casefold() in lower for hint in DEEP_QUESTION_HINTS)


def _followup_config(question: str) -> tuple[str, str, int]:
    """Return model, system prompt, and max output tokens for this follow-up."""
    if _wants_deep_analysis(question):
        return FOLLOWUP_DEEP_MODEL, FOLLOWUP_DEEP_SYSTEM_PROMPT, 4096
    return FOLLOWUP_MODEL, FOLLOWUP_SYSTEM_PROMPT, 2048


def ask_followup(article: Article, question: str) -> tuple[str, TokenUsage, str]:
    """Answer a follow-up; uses a stronger model when depth is requested."""
    question = question.strip()
    if not question:
        return "נא להזין שאלה.", TokenUsage(), FOLLOWUP_MODEL

    if not os.getenv("OPENAI_API_KEY"):
        return "מפתח OPENAI_API_KEY לא מוגדר בקובץ .env.", TokenUsage(), FOLLOWUP_MODEL

    model, system_prompt, max_tokens = _followup_config(question)
    context = _followup_context(article, question)
    tokens = TokenUsage()

    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            input=[
                {"role": "developer", "content": system_prompt},
                {"role": "user", "content": context},
            ],
        )
        if response.usage:
            tokens = TokenUsage(
                input_tokens=response.usage.input_tokens or 0,
                output_tokens=response.usage.output_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )
        answer = (response.output_text or "").strip()
        if answer:
            return answer, tokens, model
    except Exception:
        pass

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context},
        ],
        temperature=0.3,
        max_tokens=max_tokens,
    )
    usage = response.usage
    tokens = TokenUsage(
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
        total_tokens=usage.total_tokens if usage else 0,
    )
    answer = (response.choices[0].message.content or "לא התקבלה תשובה.").strip()
    return answer, tokens, model


def _pending_entry(item: dict, index: int) -> dict:
    return {
        "index": index,
        "status": "pending",
        "title": item["title"],
        "date": item["date"],
        "source": item["source"],
        "snippet": item.get("snippet", ""),
    }


def _ready_entry(article: Article, index: int) -> dict:
    data = article.model_dump()
    data["index"] = index
    data["status"] = "ready"
    return data


def _entry_to_article(entry: dict) -> Article:
    return Article(
        title=entry["title"],
        date=entry["date"],
        source=entry["source"],
        summary=entry["summary"],
        word_count=entry.get("word_count", 0),
        input_tokens=entry.get("input_tokens", 0),
        output_tokens=entry.get("output_tokens", 0),
        published_at=entry.get("published_at", ""),
    )


def fetch_articles(
    subject: str,
    *,
    max_articles: int = MAX_ARTICLES,
    summary_model: str = MODEL,
    vendor: str | None = None,
) -> tuple[list[Article], TokenUsage]:
    """Fetch today's Israeli RSS headlines and summarize (sequential for Gemini rate limits)."""
    from functools import partial

    from llm_providers import LLMVendor, resolve_vendor

    rss_items = _fetch_israel_rss_items(subject, max_articles=max_articles)
    if not rss_items:
        raise ValueError(f"No Israeli news from today ({TODAY}) was found for this subject")

    articles: list[Article] = []
    total_tokens = TokenUsage()
    summarize = partial(
        _summarize_rss_item,
        subject=subject,
        summary_model=summary_model,
        vendor=vendor,
    )
    resolved_vendor = resolve_vendor(vendor)
    workers = 1 if resolved_vendor is LLMVendor.GEMINI else len(rss_items)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for article, tokens in executor.map(summarize, rss_items):
            _merge_token_usage(total_tokens, tokens)
            if article:
                articles.append(article)

    if not articles:
        raise ValueError("No valid Israeli news articles were returned for today")
    return articles, total_tokens


def _format_summary_paragraphs(text: str) -> str:
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not parts and text.strip():
        parts = [text.strip()]
    if not parts:
        return ""
    return "".join(f"<p>{html.escape(part)}</p>" for part in parts)


def _format_article_block(article: Article, index: int) -> str:
    token_line = ""
    if article.input_tokens or article.output_tokens:
        token_line = (
            f" · קלט: {article.input_tokens:,} · פלט: {article.output_tokens:,} טוקנים"
        )
    paragraphs = _format_summary_paragraphs(article.summary)

    return f"""
      <article class="news-article news-article--ready" data-article-id="{index}">
        <div class="article-meta">
          <span class="article-index">{index}</span>
          <span class="article-date">{html.escape(format_publish_date_display(article.date, article.published_at) or article.date)}</span>
          <span class="article-source">מקור: {html.escape(article.source)}</span>
        </div>
        <h2 class="article-title">{html.escape(article.title)}</h2>
        <div class="article-summary">{paragraphs}</div>
        <div class="article-stats">{article.word_count} מילים{token_line}</div>
      </article>"""


def _format_pending_article_block(item: dict, index: int) -> str:
    return f"""
      <article class="news-article news-article--loading">
        <div class="article-meta">
          <span class="article-index">{index}</span>
          <span class="article-date">{html.escape(item["date"])}</span>
          <span class="article-source">מקור: {html.escape(item["source"])}</span>
        </div>
        <h2 class="article-title">{html.escape(item["title"])}</h2>
        <div class="article-loading">
          <span class="loading-spinner"></span>
          <span>מסכם כתבה…</span>
        </div>
      </article>"""


def _hebrew_date_display(d: date | None = None) -> str:
    d = d or date.today()
    days = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]
    months = [
        "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
        "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
    ]
    day_name = days[(d.weekday() + 1) % 7]
    return f"יום {day_name}, {d.day} ב{months[d.month - 1]} {d.year}"


def _format_email_summary_paragraphs(text: str) -> str:
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not parts and text.strip():
        parts = [text.strip()]
    if not parts:
        return ""
    style = "margin:0 0 14px;font-size:16px;line-height:1.75;color:#374151;"
    return "".join(f'<p style="{style}">{html.escape(part)}</p>' for part in parts)


def _format_email_article_block(article: Article, index: int, *, last: bool = False) -> str:
    paragraphs = _format_email_summary_paragraphs(article.summary)
    border = "" if last else "border-bottom:1px solid #e8ecf1;"
    safe_title = html.escape(article.title)
    safe_date = html.escape(format_publish_date_display(article.date, article.published_at))
    safe_source = html.escape(article.source)
    date_line = (
        f'<span style="color:#374151;font-weight:600;">{safe_date}</span>'
        f'&nbsp;&nbsp;|&nbsp;&nbsp;<span style="font-style:italic;">{safe_source}</span>'
        if safe_date
        else f'<span style="font-style:italic;">{safe_source}</span>'
    )
    importance_html = ""
    if article.importance_note:
        importance_html = (
            f'<p style="margin:0 0 14px;padding:10px 14px;background-color:#eff6ff;'
            f'border-radius:8px;font-size:14px;line-height:1.55;color:#1e40af;">'
            f"<strong>למה זה חשוב:</strong> {html.escape(article.importance_note)}</p>"
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
                      style="width:32px;height:32px;background-color:#2563eb;border-radius:16px;
                             color:#ffffff;font-family:Segoe UI,Arial,sans-serif;font-size:15px;font-weight:700;">
                    {index}
                  </td>
                </tr>
              </table>
            </td>
            <td valign="top" style="font-family:Segoe UI,Arial,sans-serif;direction:rtl;text-align:right;">
              <p style="margin:0 0 10px;font-size:13px;line-height:1.4;color:#6b7280;">
                {date_line}
              </p>
              <h2 style="margin:0 0 14px;font-size:20px;line-height:1.35;color:#1e3a5f;font-weight:700;">
                {safe_title}
              </h2>
              {importance_html}
              <div style="font-size:16px;line-height:1.75;color:#374151;text-align:right;">
                {paragraphs}
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>"""


def format_outlook_newsletter_html(
    *,
    title: str,
    subject: str,
    articles: list[Article],
    total_words: int,
    summary_model: str = EMAIL_SUMMARY_MODEL,
    schedule_time: str = "08:00",
    stats_label: str | None = None,
    subtitle: str | None = None,
    footer_note: str | None = None,
    ai_provider_label: str | None = None,
    ai_provider_footer_label: str | None = None,
) -> str:
    """Outlook-friendly HTML newsletter (table layout + inline styles)."""
    safe_subject = html.escape(subject)
    display_date = html.escape(subtitle or _hebrew_date_display())
    safe_title = html.escape(title)
    article_count = len(articles)
    header_provider = html.escape(ai_provider_label) if ai_provider_label else ""
    footer_provider = html.escape(
        ai_provider_footer_label or ai_provider_label or ""
    )
    badge_label = html.escape(stats_label or f"{article_count} כתבות מרכזיות")
    footnote = html.escape(
        footer_note or "מקורות: אתרי חדשות ישראליים מובילים"
    )
    footer_line = f"{footer_provider} · {footnote}" if footer_provider else footnote

    if articles:
        article_rows = "".join(
            _format_email_article_block(article, index, last=(index == article_count))
            for index, article in enumerate(articles, start=1)
        )
        preheader = html.escape(
            f"{articles[0].title} — ועוד {article_count - 1} כתבות מהיום"
            if article_count > 1
            else articles[0].title
        )
    else:
        article_rows = """
    <tr>
      <td style="padding:40px 36px;font-family:Segoe UI,Arial,sans-serif;font-size:16px;
                 color:#6b7280;text-align:center;direction:rtl;">
        לא נמצאו כתבות ישראליות מהיום עבור נושא זה.
      </td>
    </tr>"""
        preheader = html.escape("סיכום חדשות יומי")

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>{safe_title}</title>
  <!--[if mso]>
  <style type="text/css">
    body, table, td, p, h1, h2 {{ font-family: Segoe UI, Arial, sans-serif !important; }}
  </style>
  <![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#eef2f7;direction:rtl;-webkit-text-size-adjust:100%;">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#eef2f7;">
    {preheader}
  </div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
         style="background-color:#eef2f7;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="620" cellspacing="0" cellpadding="0" border="0"
               style="max-width:620px;width:100%;background-color:#ffffff;border-radius:16px;
                      border:1px solid #dde3ea;overflow:hidden;">
          <tr>
            <td style="background-color:#1e3a5f;padding:0;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td style="padding:36px 36px 32px;direction:rtl;text-align:right;
                             background:linear-gradient(135deg,#1e3a5f 0%,#2563eb 100%);">
                    <p style="margin:0 0 8px;font-family:Segoe UI,Arial,sans-serif;font-size:13px;
                              letter-spacing:0.06em;text-transform:uppercase;color:rgba(255,255,255,0.85);">
                      בוקר טוב
                    </p>
                    <h1 style="margin:0 0 12px;font-family:Segoe UI,Arial,sans-serif;font-size:28px;
                               line-height:1.25;color:#ffffff;font-weight:700;">
                      {safe_title}
                    </h1>
                    <p style="margin:0 0 6px;font-family:Segoe UI,Arial,sans-serif;font-size:16px;
                              line-height:1.5;color:rgba(255,255,255,0.95);">
                      נושא: <strong style="font-weight:600;">{safe_subject}</strong>
                    </p>
                    <p style="margin:0;font-family:Segoe UI,Arial,sans-serif;font-size:14px;
                              line-height:1.5;color:rgba(255,255,255,0.8);">
                      {display_date}
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:14px 36px;background-color:#f8fafc;border-bottom:1px solid #e8ecf1;direction:rtl;text-align:right;">
              <p style="margin:0;font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#64748b;">
                <span style="display:inline-block;background-color:#dbeafe;color:#1d4ed8;
                             padding:4px 12px;border-radius:999px;font-weight:600;margin-left:8px;">
                  {badge_label}
                </span>
                ~{max(1, total_words // 200)} דקות קריאה
              </p>
              <p style="margin:8px 0 0;font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#64748b;">
                {header_provider if header_provider else "&nbsp;"}
              </p>
            </td>
          </tr>
          {article_rows}
          <tr>
            <td style="padding:24px 36px;background-color:#f8fafc;border-top:1px solid #e8ecf1;text-align:center;direction:rtl;">
              <p style="margin:0 0 6px;font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#94a3b8;">
                נשלח אוטומטית כל בוקר בשעה {html.escape(schedule_time)}
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


def format_articles_email_html(
    subject: str,
    articles: list[Article],
    total_words: int,
    *,
    summary_model: str = EMAIL_SUMMARY_MODEL,
    ai_provider_label: str | None = None,
    ai_provider_footer_label: str | None = None,
) -> str:
    """Outlook-friendly HTML newsletter for daily Israeli news."""
    return format_outlook_newsletter_html(
        title="חדשות ישראל מהיום",
        subject=subject,
        articles=articles,
        total_words=total_words,
        summary_model=summary_model,
        schedule_time="08:00",
        ai_provider_label=ai_provider_label,
        ai_provider_footer_label=ai_provider_footer_label,
    )


def format_articles_html(
    subject: str,
    articles: list[Article],
    total_words: int,
    token_usage: TokenUsage | None = None,
    cost_usage: CostUsage | None = None,
    rss_items: list[dict] | None = None,
    completed_by_index: dict[int, Article] | None = None,
    status: str | None = None,
    expected_count: int | None = None,
    chrome_only: bool = False,
    progress_done: int | None = None,
    progress_total: int | None = None,
) -> str:
    safe_subject = html.escape(subject)
    usage = token_usage or TokenUsage()
    cost = cost_usage or _calculate_cost(usage)
    token_panel = _token_cost_panel_html(usage, cost)

    if rss_items is not None:
        completed = completed_by_index or {}
        article_blocks = []
        for index, item in enumerate(rss_items):
            display_index = index + 1
            if index in completed:
                article_blocks.append(_format_article_block(completed[index], display_index))
            else:
                article_blocks.append(_format_pending_article_block(item, display_index))
        done_count = len(completed)
        total_expected = expected_count if expected_count is not None else len(rss_items)
    else:
        article_blocks = [
            _format_article_block(article, index) for index, article in enumerate(articles, start=1)
        ]
        done_count = len(articles)
        total_expected = expected_count if expected_count is not None else len(articles)

    if article_blocks:
        articles_html = "\n".join(article_blocks)
    elif chrome_only:
        articles_html = ""
    elif status:
        articles_html = f'<p class="empty status-message">{html.escape(status)}</p>'
    else:
        articles_html = '<p class="empty">לא נמצאו כתבות ישראליות מהיום עבור נושא זה.</p>'

    if progress_total:
        footer_stats = (
            f"{progress_done or 0} מתוך {progress_total} כתבות · {total_words} מילים · "
            f"{_format_usd(cost.total_cost_usd)}"
        )
    elif rss_items is not None and total_expected:
        footer_stats = (
            f"{done_count} מתוך {total_expected} כתבות · {total_words} מילים · "
            f"{_format_usd(cost.total_cost_usd)}"
        )
    elif done_count:
        footer_stats = f"{done_count} כתבות · {total_words} מילים · {_format_usd(cost.total_cost_usd)}"
    else:
        footer_stats = html.escape(status or "טוען…")

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8">
  <style>
    body {{
      font-family: "Segoe UI", "Arial", "Rubik", sans-serif;
      background: #eef2f7;
      color: #1a1a1a;
      margin: 0;
      padding: 1.25rem;
      direction: rtl;
    }}
    .page-wrap {{
      position: relative;
      max-width: 820px;
      margin: 0 auto;
      padding-top: 0.25rem;
    }}
    .token-cost-panel {{
      position: fixed;
      top: 1rem;
      left: 1rem;
      z-index: 1000;
      width: 220px;
      padding: 0.75rem 0.85rem;
      background: #ffffff;
      border: 1px solid #cbd5e1;
      border-radius: 10px;
      box-shadow: 0 4px 18px rgba(0, 0, 0, 0.12);
      direction: rtl;
      text-align: right;
    }}
    .token-cost-panel .panel-title {{
      font-size: 0.78rem;
      font-weight: 700;
      color: #1e3a5f;
      margin-bottom: 0.55rem;
      padding-bottom: 0.45rem;
      border-bottom: 1px solid #e5e7eb;
    }}
    .token-cost-panel .panel-row {{
      display: flex;
      justify-content: space-between;
      gap: 0.5rem;
      margin: 0.35rem 0;
      font-size: 0.76rem;
    }}
    .token-cost-panel .panel-label {{
      color: #6b7280;
      white-space: nowrap;
    }}
    .token-cost-panel .panel-metrics {{
      color: #111827;
      font-weight: 600;
      text-align: left;
      direction: ltr;
    }}
    .token-cost-panel .panel-total {{
      margin-top: 0.45rem;
      padding-top: 0.45rem;
      border-top: 1px dashed #e5e7eb;
    }}
    .token-cost-panel .panel-total .panel-label,
    .token-cost-panel .panel-total .panel-metrics {{
      color: #1d4ed8;
      font-weight: 700;
    }}
    .token-cost-panel .panel-note {{
      margin-top: 0.5rem;
      font-size: 0.68rem;
      color: #9ca3af;
      direction: ltr;
      text-align: left;
    }}
    .news-card {{
      max-width: 820px;
      margin: 0 auto;
      background: #fff;
      border-radius: 14px;
      box-shadow: 0 6px 28px rgba(0, 0, 0, 0.08);
      overflow: hidden;
    }}
    .news-header {{
      background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%);
      color: #fff;
      padding: 1.4rem 1.6rem;
      text-align: right;
    }}
    .news-header h1 {{
      margin: 0 0 0.4rem;
      font-size: 1.5rem;
      font-weight: 700;
    }}
    .news-header p {{
      margin: 0.2rem 0 0;
      opacity: 0.92;
      font-size: 0.95rem;
    }}
    .news-body {{
      padding: 1.2rem 1.6rem 1.4rem;
    }}
    .news-article {{
      border-bottom: 1px solid #e5e7eb;
      padding: 1.25rem 0;
    }}
    .news-article:last-child {{
      border-bottom: none;
    }}
    .article-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
      margin-bottom: 0.6rem;
      font-size: 0.85rem;
      color: #6b7280;
    }}
    .article-index {{
      background: #2563eb;
      color: #fff;
      border-radius: 999px;
      padding: 0.15rem 0.55rem;
      font-weight: 600;
    }}
    .article-date {{
      font-weight: 600;
      color: #374151;
    }}
    .article-source {{
      font-style: italic;
    }}
    .article-title {{
      margin: 0 0 0.85rem;
      font-size: 1.25rem;
      line-height: 1.4;
      color: #111827;
      text-align: right;
    }}
    .article-summary p {{
      margin: 0 0 0.85rem;
      line-height: 1.75;
      font-size: 1rem;
      text-align: right;
      color: #1f2937;
    }}
    .news-article--ready {{
      animation: article-reveal 0.35s ease;
    }}
    @keyframes article-reveal {{
      from {{ opacity: 0.4; transform: translateY(6px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    .article-stats {{
      font-size: 0.8rem;
      color: #9ca3af;
      text-align: left;
    }}
    .news-article--loading {{
      opacity: 0.85;
    }}
    .article-loading {{
      display: flex;
      align-items: center;
      gap: 0.65rem;
      color: #6b7280;
      font-size: 0.95rem;
      padding: 0.5rem 0 1rem;
    }}
    .loading-spinner {{
      width: 1rem;
      height: 1rem;
      border: 2px solid #dbeafe;
      border-top-color: #2563eb;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      flex-shrink: 0;
    }}
    @keyframes spin {{
      to {{ transform: rotate(360deg); }}
    }}
    .status-message {{
      font-size: 1rem;
    }}
    .followup-panel {{
      margin-top: 0.75rem;
      padding: 0.85rem 1rem;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
    }}
    .followup-answer {{
      margin-top: 0.5rem;
      padding: 0.75rem;
      background: #fff;
      border-radius: 8px;
      border: 1px solid #e5e7eb;
    }}
    .news-footer {{
      border-top: 1px solid #e5e7eb;
      padding: 0.85rem 1.6rem;
      font-size: 0.82rem;
      color: #6b7280;
      background: #fafafa;
      text-align: right;
    }}
    .empty {{
      text-align: center;
      color: #6b7280;
      padding: 2rem 0;
    }}
    .error {{
      max-width: 820px;
      margin: 0 auto;
      padding: 1rem 1.25rem;
      background: #fef2f2;
      border: 1px solid #fecaca;
      border-radius: 8px;
      color: #991b1b;
      direction: rtl;
      text-align: right;
    }}
  </style>
</head>
<body>
  <div class="page-wrap">
    {token_panel}
    <div class="news-card">
      <header class="news-header">
        <h1>חדשות ישראל מהיום</h1>
        <p>נושא: {safe_subject}</p>
        <p>תאריך: {html.escape(TODAY)} · מודל: GPT-4.1 Nano</p>
      </header>
      <div class="news-body">
        {articles_html}
      </div>
      <footer class="news-footer">
        {footer_stats}
      </footer>
    </div>
  </div>
</body>
</html>"""


def format_error_html(message: str) -> str:
    safe_message = html.escape(message)
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head><meta charset="utf-8"></head>
<body><div class="error" style="font-family:Segoe UI,Arial,sans-serif;max-width:820px;margin:1rem auto;
padding:1rem;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;color:#991b1b;direction:rtl;text-align:right;">
{safe_message}</div></body></html>"""


def _headlines_for_subject(subject: str) -> HeadlinesResponse:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set in .env")

    try:
        articles, token_usage = fetch_articles(subject)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI request failed: {exc}") from exc

    total_words = sum(article.word_count for article in articles)
    cost_usage = _calculate_cost(token_usage)
    return HeadlinesResponse(
        subject=subject.strip(),
        articles=articles,
        html=format_articles_html(subject.strip(), articles, total_words, token_usage, cost_usage),
        article_count=len(articles),
        total_word_count=total_words,
        token_usage=token_usage,
        cost_usage=cost_usage,
    )


def _format_chrome_html(
    subject: str,
    total_words: int,
    token_usage: TokenUsage | None = None,
    done_count: int = 0,
    expected_count: int = 0,
    status: str | None = None,
) -> str:
    return format_articles_html(
        subject,
        [],
        total_words,
        token_usage=token_usage,
        status=status,
        chrome_only=True,
        progress_done=done_count,
        progress_total=expected_count or None,
    )


def _ask_followup_ui(entry: dict, question: str) -> str:
    if entry.get("status") != "ready":
        return "הכתבה עדיין לא מוכנה."
    answer, tokens, model = ask_followup(_entry_to_article(entry), question)
    footer = f"\n\n---\n*מודל: {model}*"
    if tokens.total_tokens:
        footer += f" · *טוקנים: {tokens.total_tokens:,}*"
    return answer + footer


def headlines_for_prompt(subject: str):
    """Gradio handler — header chrome + article list state for per-article follow-up."""
    subject = subject.strip() or DEFAULT_SUBJECT
    if not os.getenv("OPENAI_API_KEY"):
        yield _format_chrome_html(subject, 0, status=""), []
        return

    yield (
        _format_chrome_html(subject, 0, status="טוען כותרות מהיום…"),
        [],
    )

    try:
        rss_items = _fetch_israel_rss_items(subject)
    except Exception as exc:
        yield format_error_html(str(exc)), []
        return

    if not rss_items:
        yield (
            format_error_html(f"לא נמצאו כתבות ישראליות מהיום ({TODAY}) עבור נושא זה."),
            [],
        )
        return

    entries = [_pending_entry(item, index) for index, item in enumerate(rss_items)]
    completed_by_index: dict[int, Article] = {}
    total_tokens = TokenUsage()
    total_words = 0
    expected_count = len(rss_items)

    yield (
        _format_chrome_html(
            subject,
            total_words,
            token_usage=total_tokens,
            done_count=0,
            expected_count=expected_count,
        ),
        entries,
    )

    with ThreadPoolExecutor(max_workers=len(rss_items)) as executor:
        futures = {
            executor.submit(_summarize_rss_item, item, subject): index
            for index, item in enumerate(rss_items)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                article, tokens = future.result()
            except Exception as exc:
                yield format_error_html(f"שגיאה בסיכום כתבה {index + 1}: {exc}"), entries
                return

            _merge_token_usage(total_tokens, tokens)
            if article:
                completed_by_index[index] = article
                entries[index] = _ready_entry(article, index)
                total_words += article.word_count

            yield (
                _format_chrome_html(
                    subject,
                    total_words,
                    token_usage=total_tokens,
                    done_count=len(completed_by_index),
                    expected_count=expected_count,
                ),
                list(entries),
            )

    if not completed_by_index:
        yield format_error_html("לא נמצאו כתבות תקינות מהיום."), entries


@app.get("/health")
def health():
    return {"status": "ok", "today": TODAY}


@app.get("/headlines", response_model=HeadlinesResponse)
def get_headlines(subject: str = Query(..., min_length=1, max_length=200)):
    return _headlines_for_subject(subject)


@app.post("/headlines", response_model=HeadlinesResponse)
def post_headlines(body: HeadlinesRequest):
    return _headlines_for_subject(body.subject)


@app.post("/followup", response_model=FollowupResponse)
def post_followup(body: FollowupRequest):
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set in .env")

    article = Article(
        title=body.title,
        date=body.date,
        source=body.source,
        summary=body.summary,
        word_count=_word_count(body.summary),
    )
    try:
        answer, token_usage, model_used = ask_followup(article, body.question)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI request failed: {exc}") from exc

    return FollowupResponse(answer=answer, model=model_used, token_usage=token_usage)


GRADIO_ARTICLE_CSS = """
.news-article { border-bottom: 1px solid #e5e7eb; padding: 1.25rem 0; max-width: 820px; margin: 0 auto; direction: rtl; }
.article-meta { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 0.6rem; font-size: 0.85rem; color: #6b7280; }
.article-index { background: #2563eb; color: #fff; border-radius: 999px; padding: 0.15rem 0.55rem; font-weight: 600; }
.article-date { font-weight: 600; color: #374151; }
.article-source { font-style: italic; }
.article-title { margin: 0 0 0.85rem; font-size: 1.25rem; line-height: 1.4; color: #111827; text-align: right; }
.article-summary p { margin: 0 0 0.85rem; line-height: 1.75; font-size: 1rem; text-align: right; color: #1f2937; }
.article-stats { font-size: 0.8rem; color: #9ca3af; text-align: left; }
.article-loading { display: flex; align-items: center; gap: 0.65rem; color: #6b7280; font-size: 0.95rem; padding: 0.5rem 0 1rem; }
.loading-spinner { width: 1rem; height: 1rem; border: 2px solid #dbeafe; border-top-color: #2563eb; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.followup-panel { direction: rtl; text-align: right; max-width: 820px; margin: 0 auto 1rem; }
"""


def launch_ui():
    import gradio as gr

    loading_chrome = _format_chrome_html(
        DEFAULT_SUBJECT,
        0,
        status="טוען חדשות ישראל מהיום…",
    )

    with gr.Blocks(title="חדשות ישראל", css=GRADIO_ARTICLE_CSS) as demo:
        gr.Markdown(
            "## חדשות ישראל מהיום\n"
            f"עד {MAX_ARTICLES} כתבות מישראל ({TODAY}) · סיכום של {MIN_SUMMARY_WORDS}-{MAX_SUMMARY_WORDS} מילים · בעברית · GPT-4.1 Nano\n\n"
            f"שאלות המשך: **{FOLLOWUP_MODEL}** (רגיל) · **{FOLLOWUP_DEEP_MODEL}** (מעמיק) + חיפוש באינטרנט"
        )
        subject = gr.Textbox(
            label="נושא",
            value=DEFAULT_SUBJECT,
            placeholder="הזינו נושא ישראלי, למשל: ביטחון, כלכלה, טכנולוגיה, פוליטיקה…",
            lines=1,
        )
        fetch_btn = gr.Button("רענן חדשות", variant="primary")
        report_html = gr.HTML(label="דוח חדשות", value=loading_chrome)
        articles_state = gr.State([])

        @gr.render(inputs=articles_state)
        def render_articles(entries: list[dict]):
            if not entries:
                return
            for entry in entries:
                display_index = entry["index"] + 1
                if entry.get("status") == "pending":
                    gr.HTML(_format_pending_article_block(entry, display_index))
                    continue

                article = _entry_to_article(entry)
                gr.HTML(_format_article_block(article, display_index))
                with gr.Group(elem_classes=["followup-panel"]):
                    gr.Markdown(
                        f"**שאלת המשך על הכתבה** · מקור: {article.source}\n\n"
                        f"שאלה רגילה → {FOLLOWUP_MODEL} · "
                        f"הוסף «מעמיק» / «בפירוט» / «ניתוח מלא» → {FOLLOWUP_DEEP_MODEL}"
                    )
                    question = gr.Textbox(
                        label="השאלה שלך",
                        placeholder="למשל: מה ההשלכות על ישראל? / נתח לעומק את ההשלכות…",
                        lines=2,
                    )
                    ask_btn = gr.Button("שאל", variant="secondary", size="sm")
                    answer = gr.Markdown(elem_classes=["followup-answer"])

                    def make_handler(entry_data: dict):
                        def handler(q: str) -> str:
                            return _ask_followup_ui(entry_data, q)

                        return handler

                    ask_btn.click(make_handler(entry), inputs=question, outputs=answer)

        fetch_btn.click(
            headlines_for_prompt,
            inputs=subject,
            outputs=[report_html, articles_state],
        )
        demo.load(
            headlines_for_prompt,
            inputs=subject,
            outputs=[report_html, articles_state],
        )

    demo.launch(inbrowser=True)


if __name__ == "__main__":
    import sys

    if "--api" in sys.argv:
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8000)
    else:
        launch_ui()
