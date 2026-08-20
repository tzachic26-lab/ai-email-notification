"""
Daily agent: fetch today's Israeli news summaries and email via Outlook.

Model cascade: Gemini (summary 3.1 Lite) → ChatGPT.

Run manually:
    uv run python daily_news_email_agent.py

Dry run (no email):
    uv run python daily_news_email_agent.py --dry-run

Schedule (8:00 AM daily):
    powershell -ExecutionPolicy Bypass -File setup_daily_news_task.ps1
    (from c:\amdocs\ai_email_notification)
"""

from __future__ import annotations

import argparse
import os
from datetime import date

from daily_agent import (
    LOG_DIR,
    add_common_agent_args,
    deliver_email,
    parse_recipients,
    setup_agent_logging,
)
from daily_email_send import resolve_daily_recipient, run_with_scheduled_retry
from daily_email_vendor import (
    VendorEmailMeta,
    build_with_model_tier_fallback,
    log_build_meta,
    require_api_keys_for_daily_emails,
    vendor_email_footer_label,
    vendor_email_label,
)
from news_headlines_api import (
    DEFAULT_SUBJECT,
    EMAIL_MAX_ARTICLES,
    fetch_articles,
    format_articles_email_html,
)

RECIPIENTS_ARG = ",".join(parse_recipients(resolve_daily_recipient("DAILY_NEWS_RECIPIENT")))
NEWS_TOPIC = os.getenv("DAILY_NEWS_TOPIC", DEFAULT_SUBJECT)
PREVIEW_PATH = LOG_DIR / "daily_news_preview.html"
LABEL = "daily_news_email"

logger = setup_agent_logging(LABEL, "daily_news_email.log")


def _build_for_tier(
    vendor: str,
    summary_model: str,
    meta: VendorEmailMeta,
) -> tuple[str, str, int]:
    logger.info(
        "Fetching articles for topic: %s (vendor=%s, model=%s)",
        NEWS_TOPIC,
        vendor,
        summary_model,
    )
    articles, _token_usage = fetch_articles(
        NEWS_TOPIC,
        max_articles=EMAIL_MAX_ARTICLES,
        summary_model=summary_model,
        vendor=vendor,
    )
    total_words = sum(article.word_count for article in articles)
    report_html = format_articles_email_html(
        NEWS_TOPIC,
        articles,
        total_words,
        summary_model=summary_model,
        ai_provider_label=vendor_email_label(meta),
        ai_provider_footer_label=vendor_email_footer_label(meta),
    )
    today = date.today().isoformat()
    email_subject = f"חדשות ישראל מהיום — {today}"
    logger.info("Prepared %s articles (%s words) via %s", len(articles), total_words, vendor)
    return email_subject, report_html, len(articles)


def build_report() -> tuple[str, str, int, VendorEmailMeta]:
    (email_subject, report_html, count), meta = build_with_model_tier_fallback(
        _build_for_tier,
        logger=logger,
        label=LABEL,
    )
    log_build_meta(logger, meta)
    return email_subject, report_html, count, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Send daily Israeli news summary email.")
    add_common_agent_args(parser)
    args = parser.parse_args()

    key_error = require_api_keys_for_daily_emails()
    if key_error:
        logger.error("%s", key_error)
        return 1

    def run_once() -> int:
        try:
            email_subject, report_html, count, meta = build_report()
        except Exception as exc:
            logger.exception("Failed to build news report: %s", exc)
            return 1

        return deliver_email(
            dry_run=args.dry_run,
            preview_path=PREVIEW_PATH,
            subject=email_subject,
            body_html=report_html,
            logger=logger,
            recipients_arg=RECIPIENTS_ARG,
            preview_detail=f"{count} articles, vendor={meta.vendor.value}",
            sent_detail=f"provider: {vendor_email_label(meta)}",
        )

    if args.dry_run or args.no_retry:
        return run_once()

    return run_with_scheduled_retry(run_once, logger=logger, label=LABEL)


if __name__ == "__main__":
    raise SystemExit(main())
