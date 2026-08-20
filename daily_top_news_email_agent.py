"""
Daily agent: LLM-ranked top 5 Israeli news stories from the last 24 hours.

Model cascade (24h only): Gemini (rank 2.5 Flash + summary 3.1 Lite) → ChatGPT.

Run manually:
    uv run python daily_top_news_email_agent.py

Dry run:
    uv run python daily_top_news_email_agent.py --dry-run

Schedule (8:30 AM daily):
    powershell -ExecutionPolicy Bypass -File setup_daily_top_news_task.ps1
    (from c:\amdocs\ai_email_notification)
"""

from __future__ import annotations

import argparse
from datetime import date

from daily_agent import (
    LOG_DIR,
    add_common_agent_args,
    apply_vendor_override,
    deliver_email,
    setup_agent_logging,
)
from daily_email_send import resolve_daily_recipient, run_with_scheduled_retry
from daily_email_vendor import (
    VendorEmailMeta,
    build_with_top_news_tier_fallback,
    log_build_meta,
    require_api_keys_for_daily_emails,
    vendor_top_news_email_label,
    vendor_top_news_footer_label,
)
from israel_top_news_api import (
    DEFAULT_SUBJECT,
    fetch_top_israel_articles,
    format_top_news_email_html,
    top_news_count,
    top_news_headline,
)

RECIPIENT = resolve_daily_recipient("DAILY_TOP_NEWS_RECIPIENT", "DAILY_NEWS_RECIPIENT")
PREVIEW_PATH = LOG_DIR / "daily_top_news_preview.html"
LABEL = "daily_top_news_email"

logger = setup_agent_logging(LABEL, "daily_top_news_email.log")


def _build_for_tier(
    vendor: str,
    rank_model: str,
    summary_model: str,
    meta: VendorEmailMeta,
) -> tuple[str, str, int]:
    logger.info(
        "Fetching top Israeli news (last 24h, vendor=%s, rank=%s, summary=%s)",
        vendor,
        rank_model,
        summary_model,
    )
    articles, _tokens = fetch_top_israel_articles(
        DEFAULT_SUBJECT,
        count=top_news_count(),
        vendor=vendor,
        rank_model=rank_model,
        summary_model=summary_model,
    )
    total_words = sum(a.word_count for a in articles)
    report_html = format_top_news_email_html(
        articles,
        total_words,
        summary_model=summary_model,
        ai_provider_label=vendor_top_news_email_label(meta),
        ai_provider_footer_label=vendor_top_news_footer_label(meta),
    )
    today = date.today().isoformat()
    email_subject = f"{top_news_headline()} — 24 שעות — {today}"
    logger.info("Prepared %s articles (%s words) via %s", len(articles), total_words, vendor)
    for index, article in enumerate(articles, start=1):
        note = article.importance_note or "(no note)"
        logger.info("  #%s [%s] %s — %s", index, article.source, article.title[:60], note[:80])
    return email_subject, report_html, len(articles)


def build_report() -> tuple[str, str, int, VendorEmailMeta]:
    (email_subject, report_html, count), meta = build_with_top_news_tier_fallback(
        _build_for_tier,
        logger=logger,
        label=LABEL,
    )
    log_build_meta(logger, meta, include_rank=True)
    return email_subject, report_html, count, meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send daily top-5 Israeli news (24h, LLM-ranked) email."
    )
    add_common_agent_args(parser, vendor=True)
    args = parser.parse_args()
    apply_vendor_override(args)

    key_error = require_api_keys_for_daily_emails()
    if key_error:
        logger.error("%s", key_error)
        return 1

    def run_once() -> int:
        try:
            email_subject, report_html, count, meta = build_report()
        except Exception as exc:
            logger.exception("Failed to build top news report: %s", exc)
            return 1

        return deliver_email(
            dry_run=args.dry_run,
            preview_path=PREVIEW_PATH,
            subject=email_subject,
            body_html=report_html,
            logger=logger,
            recipients_arg=RECIPIENT,
            preview_detail=f"{count} articles, vendor={meta.vendor.value}",
            sent_detail=f"provider: {vendor_top_news_email_label(meta)}",
        )

    if args.dry_run or args.no_retry:
        return run_once()

    return run_with_scheduled_retry(run_once, logger=logger, label=LABEL)


if __name__ == "__main__":
    raise SystemExit(main())
