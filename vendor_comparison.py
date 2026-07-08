"""
Side-by-side vendor comparison: OpenAI vs Gemini on the same Israeli news articles.

Run:
    uv run python vendor_comparison.py
    uv run python vendor_comparison.py --articles 3

Output:
    logs/vendor_comparison.html
    logs/vendor_comparison.json
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import truststore

truststore.inject_into_ssl()

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from dotenv import load_dotenv

load_dotenv(APP_DIR / ".env", override=True)

from network_env import configure_http_proxy  # noqa: E402

configure_http_proxy()
    LLMVendor,
    complete_chat,
    email_summary_model,
    objective_quality_score,
    summarize_with_vendor,
    vendor_display_name,
)
from news_headlines_api import (  # noqa: E402
    DEFAULT_SUBJECT,
    EMAIL_SUMMARY_MODEL,
    MAX_SUMMARY_WORDS,
    MIN_SUMMARY_WORDS,
    SUMMARY_PROMPT,
    _fetch_israel_rss_items,
    _summary_user_message,
    _word_count,
    _strip_urls,
)

LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("vendor_comparison")


@dataclass
class VendorRun:
    vendor: str
    model: str
    summary: str
    word_count: int
    latency_ms: int
    tokens_total: int
    quality: dict


@dataclass
class ArticleComparison:
    title: str
    source: str
    snippet: str
    openai: VendorRun
    gemini: VendorRun
    winner: str
    winner_reason: str


JUDGE_PROMPT = """You are an impartial Hebrew news editor evaluating two summaries of the same article.
Score each summary from 1 to 10 on:
- Hebrew fluency and natural Israeli style
- Factual accuracy relative to the source snippet (no invented facts)
- Depth and usefulness for an informed reader
- Absence of gossip, fluff, or sensationalism

Return JSON only:
{"openai_score": <int>, "gemini_score": <int>, "winner": "openai"|"gemini"|"tie", "reason_he": "<one Hebrew sentence>"}"""


def _judge_summaries(
    *,
    title: str,
    source: str,
    snippet: str,
    openai_summary: str,
    gemini_summary: str,
) -> dict:
    user = (
        f"כותרת: {title}\nמקור: {source}\nתקציר מקור: {snippet}\n\n"
        f"--- סיכום OpenAI ---\n{openai_summary}\n\n"
        f"--- סיכום Gemini ---\n{gemini_summary}"
    )
    try:
        result = complete_chat(
            vendor=LLMVendor.OPENAI,
            system_prompt=JUDGE_PROMPT,
            user_message=user,
            model="gpt-4.1-mini",
            temperature=0.1,
            max_tokens=512,
        )
        raw = result.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Judge failed: %s", exc)
        return {}


def _run_vendor(
    *,
    vendor: LLMVendor,
    item: dict,
    subject: str,
) -> VendorRun:
    user_message = _summary_user_message(
        item["title"],
        item["source"],
        item["snippet"],
        subject,
        item["date"],
        item.get("published_at", ""),
    )
    model = email_summary_model(vendor)
    summary, tokens, result = summarize_with_vendor(
        vendor=vendor,
        system_prompt=SUMMARY_PROMPT,
        user_message=user_message,
        strip_urls_fn=_strip_urls,
        word_count_fn=_word_count,
        min_words=MIN_SUMMARY_WORDS,
        max_words=MAX_SUMMARY_WORDS,
        model=model if vendor is LLMVendor.OPENAI else None,
    )
    quality = objective_quality_score(
        summary=summary,
        title=item["title"],
        snippet=item["snippet"],
        min_words=MIN_SUMMARY_WORDS,
        max_words=MAX_SUMMARY_WORDS,
        word_count_fn=_word_count,
    )
    return VendorRun(
        vendor=vendor.value,
        model=result.model,
        summary=summary,
        word_count=_word_count(summary),
        latency_ms=result.latency_ms,
        tokens_total=tokens.total_tokens,
        quality=quality,
    )


def compare_articles(*, subject: str, max_articles: int, use_judge: bool) -> list[ArticleComparison]:
    items = _fetch_israel_rss_items(subject, max_articles=max_articles)
    if not items:
        raise ValueError(f"No articles found for subject: {subject}")

    comparisons: list[ArticleComparison] = []
    for index, item in enumerate(items, start=1):
        logger.info("[%s/%s] Comparing: %s", index, len(items), item["title"][:70])
        openai_run = _run_vendor(vendor=LLMVendor.OPENAI, item=item, subject=subject)
        time.sleep(5)
        gemini_run = _run_vendor(vendor=LLMVendor.GEMINI, item=item, subject=subject)
        time.sleep(3)

        openai_obj = float(openai_run.quality["total"])
        gemini_obj = float(gemini_run.quality["total"])
        winner = "tie"
        reason = "ציון אובייקטיבי שווה"

        if use_judge:
            judge = _judge_summaries(
                title=item["title"],
                source=item["source"],
                snippet=item["snippet"],
                openai_summary=openai_run.summary,
                gemini_summary=gemini_run.summary,
            )
            if judge:
                o_score = int(judge.get("openai_score", 0))
                g_score = int(judge.get("gemini_score", 0))
                winner = judge.get("winner", "tie")
                reason = judge.get("reason_he", reason)
                openai_obj = o_score * 10
                gemini_obj = g_score * 10

        if not use_judge or winner == "tie":
            if openai_obj > gemini_obj + 2:
                winner = "openai"
                reason = f"ציון אובייקטיבי גבוה יותר ({openai_obj} מול {gemini_obj})"
            elif gemini_obj > openai_obj + 2:
                winner = "gemini"
                reason = f"ציון אובייקטיבי גבוה יותר ({gemini_obj} מול {openai_obj})"

        comparisons.append(
            ArticleComparison(
                title=item["title"],
                source=item["source"],
                snippet=item["snippet"],
                openai=openai_run,
                gemini=gemini_run,
                winner=winner,
                winner_reason=reason,
            )
        )
    return comparisons


def _aggregate(comparisons: list[ArticleComparison]) -> dict:
    wins = {"openai": 0, "gemini": 0, "tie": 0}
    for c in comparisons:
        wins[c.winner] = wins.get(c.winner, 0) + 1

    def avg(vendor: str, field: str) -> float:
        runs = [getattr(c, vendor) for c in comparisons]
        values = [float(r.quality["total"]) for r in runs]
        return round(sum(values) / len(values), 1) if values else 0.0

    openai_lat = round(sum(c.openai.latency_ms for c in comparisons) / len(comparisons))
    gemini_lat = round(sum(c.gemini.latency_ms for c in comparisons) / len(comparisons))
    openai_tok = round(sum(c.openai.tokens_total for c in comparisons) / len(comparisons))
    gemini_tok = round(sum(c.gemini.tokens_total for c in comparisons) / len(comparisons))

    recommendation = "openai"
    if wins["gemini"] > wins["openai"]:
        recommendation = "gemini"
    elif wins["openai"] == wins["gemini"]:
        if avg("gemini", "total") > avg("openai", "total"):
            recommendation = "gemini"

    return {
        "wins": wins,
        "avg_objective_openai": avg("openai", "total"),
        "avg_objective_gemini": avg("gemini", "total"),
        "avg_latency_ms": {"openai": openai_lat, "gemini": gemini_lat},
        "avg_tokens": {"openai": openai_tok, "gemini": gemini_tok},
        "recommendation": recommendation,
        "openai_model": comparisons[0].openai.model if comparisons else EMAIL_SUMMARY_MODEL,
        "gemini_model": comparisons[0].gemini.model if comparisons else "",
    }


def _format_html(comparisons: list[ArticleComparison], summary: dict) -> str:
    rec = summary["recommendation"]
    rec_label = vendor_display_name(LLMVendor(rec))
    wins = summary["wins"]

    article_blocks = []
    for i, c in enumerate(comparisons, start=1):
        def col(run: VendorRun, vendor_key: str) -> str:
            win_badge = " 🏆" if c.winner == vendor_key else ""
            q = run.quality
            return f"""
            <div class="col {'winner' if c.winner == vendor_key else ''}">
              <h3>{vendor_display_name(LLMVendor(vendor_key))}{win_badge}</h3>
              <p class="meta">{html.escape(run.model)} · {run.word_count} מילים · {run.latency_ms}ms · {run.tokens_total} tokens</p>
              <p class="scores">ציון: {q['total']} · עברית: {q['hebrew_ratio']} · חפיפה מקור: {q['source_overlap']}</p>
              <div class="summary">{html.escape(run.summary)}</div>
            </div>"""

        article_blocks.append(f"""
        <section class="article">
          <h2>{i}. {html.escape(c.title)}</h2>
          <p class="meta">מקור: {html.escape(c.source)} · מנצח: <strong>{html.escape(c.winner)}</strong> — {html.escape(c.winner_reason)}</p>
          <p class="snippet">תקציר RSS: {html.escape(c.snippet[:300])}</p>
          <div class="cols">{col(c.openai, 'openai')}{col(c.gemini, 'gemini')}</div>
        </section>""")

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8">
  <title>השוואת OpenAI מול Gemini</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; background: #f1f5f9; margin: 0; padding: 24px; }}
    .wrap {{ max-width: 1100px; margin: 0 auto; }}
    h1 {{ color: #1e3a5f; }}
    .summary-box {{ background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
    .rec {{ font-size: 1.2rem; color: #166534; }}
    .article {{ background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
    .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .col {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; }}
    .col.winner {{ border-color: #22c55e; background: #f0fdf4; }}
    .meta, .scores, .snippet {{ color: #64748b; font-size: 0.9rem; }}
    .summary {{ line-height: 1.7; color: #334155; white-space: pre-wrap; margin-top: 10px; }}
    @media (max-width: 800px) {{ .cols {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>השוואת ספקי LLM — חדשות ישראל</h1>
    <div class="summary-box">
      <p class="rec"><strong>המלצה:</strong> {html.escape(rec_label)} ({html.escape(rec)})</p>
      <p>OpenAI ({html.escape(summary['openai_model'])}): ניצחונות {wins.get('openai', 0)} · ממוצע ציון {summary['avg_objective_openai']} · latency {summary['avg_latency_ms']['openai']}ms</p>
      <p>Gemini ({html.escape(summary['gemini_model'])}): ניצחונות {wins.get('gemini', 0)} · ממוצע ציון {summary['avg_objective_gemini']} · latency {summary['avg_latency_ms']['gemini']}ms</p>
      <p>תיקו: {wins.get('tie', 0)} · נוצר: {datetime.now().isoformat(timespec='seconds')}</p>
    </div>
    {''.join(article_blocks)}
  </div>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare OpenAI vs Gemini news summary quality.")
    parser.add_argument("--articles", type=int, default=3, help="Number of articles to compare (default: 3)")
    parser.add_argument("--subject", default=DEFAULT_SUBJECT, help="News topic")
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM judge (objective scores only)")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set")
        return 1
    if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        logger.error("GOOGLE_API_KEY is not set")
        return 1

    try:
        comparisons = compare_articles(
            subject=args.subject,
            max_articles=args.articles,
            use_judge=not args.no_judge,
        )
    except Exception as exc:
        logger.exception("Comparison failed: %s", exc)
        return 1

    summary = _aggregate(comparisons)
    payload = {
        "summary": summary,
        "comparisons": [
            {
                "title": c.title,
                "source": c.source,
                "winner": c.winner,
                "winner_reason": c.winner_reason,
                "openai": asdict(c.openai),
                "gemini": asdict(c.gemini),
            }
            for c in comparisons
        ],
    }

    json_path = LOG_DIR / "vendor_comparison.json"
    html_path = LOG_DIR / "vendor_comparison.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_format_html(comparisons, summary), encoding="utf-8")

    logger.info("Recommendation: %s", summary["recommendation"])
    logger.info("Wins: %s", summary["wins"])
    logger.info("Report: %s", html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
