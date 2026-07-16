"""
Daily agent: fetch today's AI/ML tech news and email via Outlook.

Model cascade: Gemini (summary 3.1 Lite) → ChatGPT.

Run manually:
    uv run python daily_tech_news_email_agent.py

Dry run (no email):
    uv run python daily_tech_news_email_agent.py --dry-run

Schedule (8:15 AM daily):
    powershell -ExecutionPolicy Bypass -File setup_daily_tech_news_task.ps1
    (from c:\amdocs\ai_email_notification)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path

from daily_email_send import (
    configure_scheduled_outlook_env,
    resolve_daily_recipient,
    run_with_scheduled_retry,
    send_html_email,
)
from daily_email_vendor import (
    VendorEmailMeta,
    build_with_model_tier_fallback,
    require_api_keys_for_daily_emails,
    vendor_email_label,
    vendor_email_footer_label,
)

import truststore

truststore.inject_into_ssl()

APP_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(APP_DIR))

from dotenv import load_dotenv

load_dotenv(APP_DIR / ".env", override=True)

from news_headlines_api import EMAIL_MAX_TECH_ARTICLES  # noqa: E402
from tech_ai_news_api import (  # noqa: E402
    DEFAULT_SUBJECT,
    fetch_tech_ai_articles,
    format_tech_ai_email_html,
)

RECIPIENT = resolve_daily_recipient("DAILY_TECH_NEWS_RECIPIENT", "DAILY_NEWS_RECIPIENT")
NEWS_TOPIC = os.getenv("DAILY_TECH_NEWS_TOPIC", DEFAULT_SUBJECT)
SEND_HELPER = APP_DIR / "outlook_send_helper.py"
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "daily_tech_news_email.log"

configure_scheduled_outlook_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("daily_tech_news_email")

from network_env import configure_http_proxy  # noqa: E402

configure_http_proxy(log=logger)


async def send_outlook_email(subject: str, body_html: str) -> None:
    send_html_email(
        send_helper=SEND_HELPER,
        log_dir=LOG_DIR,
        recipients_arg=RECIPIENT,
        subject=subject,
        body_html=body_html,
        logger=logger,
    )


def _build_for_tier(
    vendor: str,
    summary_model: str,
    meta: VendorEmailMeta,
) -> tuple[str, str, int]:
    provider_label = vendor_email_label(meta)
    footer_label = vendor_email_footer_label(meta)
    logger.info(
        "Fetching AI/ML tech articles for topic: %s (vendor=%s, model=%s)",
        NEWS_TOPIC,
        vendor,
        summary_model,
    )
    articles, _token_usage = fetch_tech_ai_articles(
        max_articles=EMAIL_MAX_TECH_ARTICLES,
        summary_model=summary_model,
        vendor=vendor,
    )
    total_words = sum(article.word_count for article in articles)
    report_html = format_tech_ai_email_html(
        NEWS_TOPIC,
        articles,
        total_words,
        summary_model=summary_model,
        ai_provider_label=provider_label,
        ai_provider_footer_label=footer_label,
    )
    today = date.today().isoformat()
    email_subject = f"חדשות AI וטכנולוגיה — {today}"
    logger.info("Prepared %s articles (%s words) via %s", len(articles), total_words, vendor)
    return email_subject, report_html, len(articles)


def build_report() -> tuple[str, str, int, VendorEmailMeta]:
    def build(vendor: str, summary_model: str, meta: VendorEmailMeta) -> tuple[str, str, int]:
        return _build_for_tier(vendor, summary_model, meta)

    (email_subject, report_html, count), meta = build_with_model_tier_fallback(
        build,
        logger=logger,
        label="daily_tech_news_email",
    )
    logger.info(
        "Email built with %s (%s)%s",
        meta.vendor.value,
        meta.model,
        f" [fallback: {meta.fallback_tier}]" if meta.fallback_tier else "",
    )
    return email_subject, report_html, count, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Send daily AI/ML tech news summary email.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and build the report without sending email.",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="Do not retry after failure (useful for manual testing).",
    )
    parser.add_argument(
        "--vendor",
        choices=("gemini", "openai"),
        help="Force vendor for this run (overrides .env LLM_VENDOR_PRIMARY).",
    )
    args = parser.parse_args()

    if args.vendor:
        os.environ["LLM_VENDOR_PRIMARY"] = args.vendor

    key_error = require_api_keys_for_daily_emails()
    if key_error:
        logger.error("%s", key_error)
        return 1

    def run_once() -> int:
        try:
            email_subject, report_html, count, meta = build_report()
        except Exception as exc:
            logger.exception("Failed to build tech news report: %s", exc)
            return 1

        if args.dry_run:
            preview = LOG_DIR / "daily_tech_news_preview.html"
            preview.write_text(report_html, encoding="utf-8")
            logger.info(
                "Dry run OK — %s articles, vendor=%s, preview: %s",
                count,
                meta.vendor.value,
                preview,
            )
            return 0

        try:
            asyncio.run(send_outlook_email(email_subject, report_html))
        except Exception as exc:
            logger.exception("Failed to send email: %s", exc)
            return 1

        logger.info(
            "Email sent to %s — subject: %s — provider: %s",
            RECIPIENT,
            email_subject,
            vendor_email_label(meta),
        )
        return 0

    if args.dry_run or args.no_retry:
        return run_once()

    return run_with_scheduled_retry(run_once, logger=logger, label="daily_tech_news_email")


if __name__ == "__main__":
    raise SystemExit(main())
