"""
Daily AI Trainer agent: generate a hands-on AI exercise and email via Outlook.

Run manually:
    uv run python daily_ai_trainer_email_agent.py

Resend today's exercise (no new LLM call):
    uv run python daily_ai_trainer_email_agent.py --resend-today --no-retry

Schedule (9:00 AM daily):
    powershell -ExecutionPolicy Bypass -File setup_daily_ai_trainer_task.ps1
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from daily_email_send import (
    configure_scheduled_outlook_env,
    run_with_scheduled_retry,
    send_html_email,
)

import truststore

truststore.inject_into_ssl()

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from dotenv import load_dotenv

load_dotenv(APP_DIR / ".env", override=True)

from ai_trainer_api import (  # noqa: E402
    exercise_from_record,
    format_trainer_email_html,
    generate_trainer_exercise,
    model_display_label,
    trainer_vendor,
)
from ai_trainer_store import (  # noqa: E402
    ExerciseRecord,
    append_exercise,
    format_exercise_markdown,
    get_record_for_date,
    history_context_for_llm,
    load_history,
    today_iso,
)
from llm_providers import LLMVendor  # noqa: E402

DEFAULT_TO = "you@example.com"
DEFAULT_BCC = ""


def _parse_recipients(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;]+", raw) if part.strip()]


TO_RECIPIENTS = _parse_recipients(os.getenv("AI_TRAINER_TO", DEFAULT_TO))
BCC_RECIPIENTS = _parse_recipients(os.getenv("AI_TRAINER_BCC", DEFAULT_BCC))
TO_ARG = ",".join(TO_RECIPIENTS)
BCC_ARG = ",".join(BCC_RECIPIENTS) if BCC_RECIPIENTS else None
SEND_HELPER = APP_DIR / "outlook_send_helper.py"
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "daily_ai_trainer_email.log"

configure_scheduled_outlook_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("daily_ai_trainer_email")

from network_env import configure_http_proxy  # noqa: E402

configure_http_proxy(log=logger)


async def send_outlook_email(subject: str, body_html: str) -> None:
    send_html_email(
        send_helper=SEND_HELPER,
        log_dir=LOG_DIR,
        recipients_arg=TO_ARG,
        bcc_arg=BCC_ARG,
        subject=subject,
        body_html=body_html,
        logger=logger,
    )


def _html_for_exercise(
    *,
    iso_date: str,
    exercise,
    records: list[ExerciseRecord],
) -> tuple[str, str]:
    report_html = format_trainer_email_html(
        iso_date=iso_date,
        exercise=exercise,
        history_records=records,
    )
    email_subject = f"AI Trainer — {exercise.title} — {iso_date}"
    return email_subject, report_html


def build_resend_report(iso_date: str | None = None) -> tuple[str, str, str]:
    target_date = iso_date or today_iso()
    _, records = load_history()
    record = get_record_for_date(target_date)
    if not record:
        raise RuntimeError(f"No saved exercise found for {target_date}")

    exercise = exercise_from_record(record)
    email_subject, report_html = _html_for_exercise(
        iso_date=target_date,
        exercise=exercise,
        records=records,
    )
    logger.info(
        "Resending exercise: %s [%s] — TO: %s, BCC: %s",
        exercise.title,
        target_date,
        TO_ARG,
        BCC_ARG or "(none)",
    )
    return email_subject, report_html, exercise.title


def build_report(*, save: bool = True, force: bool = False) -> tuple[str, str, str]:
    iso_date = today_iso()
    _, records = load_history()

    if not force and any(r.iso_date == iso_date for r in records):
        raise RuntimeError(
            f"Exercise for {iso_date} already exists in history. "
            "Use --resend-today to resend it, --force to generate a new one."
        )

    existing_ids = {r.exercise_id.lower() for r in records if r.exercise_id}
    existing_titles = {r.title.lower() for r in records if r.title}
    history_ctx = history_context_for_llm(records)

    vendor = trainer_vendor()
    logger.info(
        "Generating AI trainer exercise (date=%s, vendor=%s, prior_sessions=%s)",
        iso_date,
        vendor.value,
        len(records),
    )

    exercise = generate_trainer_exercise(
        iso_date=iso_date,
        history_context=history_ctx,
        existing_ids=existing_ids,
        existing_titles=existing_titles,
    )

    model_label = model_display_label(exercise)
    md_entry = format_exercise_markdown(
        iso_date=iso_date,
        exercise_id=exercise.id,
        title=exercise.title,
        category=exercise.category,
        difficulty=exercise.difficulty,
        estimated_minutes=exercise.estimated_minutes,
        tools=exercise.tools,
        trend_context=exercise.trend_context,
        exercise_steps=exercise.exercise_steps,
        deliverable=exercise.deliverable,
        success_criteria=exercise.success_criteria,
        stretch_goal=exercise.stretch_goal,
        resources=exercise.resources,
        skills_built=exercise.skills_built,
        model_label=model_label,
    )

    if save:
        path = append_exercise(md_entry)
        logger.info("Saved exercise to %s", path)

    all_records = records + [
        ExerciseRecord(
            iso_date=iso_date,
            title=exercise.title,
            exercise_id=exercise.id,
            category=exercise.category,
            difficulty=exercise.difficulty,
            markdown_body=md_entry,
        )
    ]

    email_subject, report_html = _html_for_exercise(
        iso_date=iso_date,
        exercise=exercise,
        records=all_records,
    )
    logger.info("Prepared exercise: %s [%s]", exercise.title, exercise.category)
    return email_subject, report_html, exercise.title


def main() -> int:
    parser = argparse.ArgumentParser(description="Send daily AI trainer exercise email.")
    parser.add_argument("--dry-run", action="store_true", help="Build without sending email.")
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="Do not retry after failure (useful for manual testing).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not append to history file (dry testing only).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Generate even if today already has an exercise in history.",
    )
    parser.add_argument(
        "--resend-today",
        action="store_true",
        help="Resend today's saved exercise without calling the LLM.",
    )
    args = parser.parse_args()

    if not args.resend_today:
        vendor = trainer_vendor()
        if vendor is LLMVendor.OPENAI and not os.getenv("OPENAI_API_KEY"):
            logger.error("OPENAI_API_KEY is not set in %s", APP_DIR / ".env")
            return 1
        if vendor is LLMVendor.GEMINI and not (
            os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        ):
            logger.error("GOOGLE_API_KEY is not set in %s", APP_DIR / ".env")
            return 1

    def run_once() -> int:
        try:
            if args.resend_today:
                email_subject, report_html, title = build_resend_report()
            else:
                email_subject, report_html, title = build_report(
                    save=not args.no_save,
                    force=args.force,
                )
        except Exception as exc:
            logger.exception("Failed to build AI trainer report: %s", exc)
            return 1

        if args.dry_run:
            preview = LOG_DIR / "daily_ai_trainer_preview.html"
            preview.write_text(report_html, encoding="utf-8")
            logger.info("Dry run OK — exercise: %s, preview: %s", title, preview)
            return 0

        try:
            asyncio.run(send_outlook_email(email_subject, report_html))
        except Exception as exc:
            logger.exception("Failed to send email: %s", exc)
            return 1

        logger.info(
            "Email sent — TO: %s%s — subject: %s",
            TO_ARG,
            f", BCC: {BCC_ARG}" if BCC_ARG else "",
            email_subject,
        )
        return 0

    if args.dry_run or args.no_retry:
        return run_once()

    return run_with_scheduled_retry(run_once, logger=logger, label="daily_ai_trainer_email")


if __name__ == "__main__":
    raise SystemExit(main())
