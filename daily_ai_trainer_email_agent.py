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
import os

from daily_agent import (
    LOG_DIR,
    add_common_agent_args,
    deliver_email,
    parse_recipients,
    require_vendor_api_key,
    setup_agent_logging,
)
from daily_email_send import run_with_scheduled_retry

from ai_trainer_api import (
    exercise_from_record,
    format_trainer_email_html,
    generate_trainer_exercise,
    model_display_label,
    trainer_vendor,
)
from ai_trainer_store import (
    ExerciseRecord,
    append_exercise,
    format_exercise_markdown,
    get_record_for_date,
    history_context_for_llm,
    load_history,
    today_iso,
)

DEFAULT_TO = "you@example.com"
DEFAULT_BCC = ""

TO_ARG = ",".join(parse_recipients(os.getenv("AI_TRAINER_TO", DEFAULT_TO)))
BCC_RECIPIENTS = parse_recipients(os.getenv("AI_TRAINER_BCC", DEFAULT_BCC))
BCC_ARG = ",".join(BCC_RECIPIENTS) if BCC_RECIPIENTS else None
PREVIEW_PATH = LOG_DIR / "daily_ai_trainer_preview.html"
LABEL = "daily_ai_trainer_email"

logger = setup_agent_logging(LABEL, "daily_ai_trainer_email.log")


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
    add_common_agent_args(parser)
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

    if not args.resend_today and not require_vendor_api_key(trainer_vendor(), logger):
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

        return deliver_email(
            dry_run=args.dry_run,
            preview_path=PREVIEW_PATH,
            subject=email_subject,
            body_html=report_html,
            logger=logger,
            recipients_arg=TO_ARG,
            bcc_arg=BCC_ARG,
            preview_detail=f"exercise: {title}",
        )

    if args.dry_run or args.no_retry:
        return run_once()

    return run_with_scheduled_retry(run_once, logger=logger, label=LABEL)


if __name__ == "__main__":
    raise SystemExit(main())
