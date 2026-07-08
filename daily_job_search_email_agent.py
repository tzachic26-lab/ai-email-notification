"""
Daily job search agent — supports multiple candidates via JSON profiles.

Run for a friend (CV + email + BCC you):
    uv run python daily_job_search_email_agent.py --profile roi_atias --dry-run

List profiles:
    uv run python daily_job_search_email_agent.py --list-profiles

Schedule (per profile):
    powershell -ExecutionPolicy Bypass -File setup_daily_job_search_profile_task.ps1 -ProfileId roi_atias
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

from job_search_api import (  # noqa: E402
    JobSearchResult,
    format_job_search_email_html,
    job_search_result_from_history,
    job_search_vendor,
    records_to_listings,
    run_job_search,
)
from job_search_profile import JobSearchProfile, list_profiles, load_profile, profile_context  # noqa: E402
from job_search_store import load_history, purge_invalid_history_entries, today_iso  # noqa: E402
from llm_providers import LLMVendor  # noqa: E402

DEFAULT_TO = os.getenv("JOB_SEARCH_TO", "you@example.com")
SEND_HELPER = APP_DIR / "outlook_send_helper.py"
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

configure_scheduled_outlook_env()


def _parse_recipients(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;]+", raw) if part.strip()]


def _subject_prefix(profile: JobSearchProfile | None) -> str:
    if profile:
        return profile.email_subject_prefix()
    name = os.getenv("JOB_SEARCH_PROFILE_NAME", "").strip()
    if name:
        return f"Job Search — {name}"
    return "Job Search"


def _configure_logging(profile: JobSearchProfile | None) -> logging.Logger:
    log_file = profile.log_file if profile else LOG_DIR / "daily_job_search_email.log"
    logger = logging.getLogger("daily_job_search_email")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.FileHandler(log_file, encoding="utf-8"))
    logger.addHandler(logging.StreamHandler(sys.stdout))
    return logger


def send_job_email(
    *,
    subject: str,
    body_html: str,
    profile: JobSearchProfile | None,
    logger: logging.Logger,
    me_only: bool = False,
) -> None:
    if me_only:
        to_list = _parse_recipients(os.getenv("JOB_SEARCH_ME_ONLY_TO", DEFAULT_TO))
        bcc_list: list[str] = []
    elif profile:
        to_list = profile.to_emails
        bcc_list = profile.bcc_emails
    else:
        to_list = _parse_recipients(os.getenv("JOB_SEARCH_TO", DEFAULT_TO))
        bcc_list = _parse_recipients(os.getenv("JOB_SEARCH_BCC", ""))

    send_html_email(
        send_helper=SEND_HELPER,
        log_dir=LOG_DIR,
        recipients_arg=",".join(to_list),
        bcc_arg=",".join(bcc_list) if bcc_list else None,
        subject=subject,
        body_html=body_html,
        logger=logger,
        to_recipients=to_list,
        bcc_recipients=bcc_list or None,
    )


def build_report(
    *,
    save: bool = True,
    profile: JobSearchProfile | None = None,
    ignore_history: bool = False,
) -> tuple[str, str, int]:
    result = run_job_search(save=save, ignore_history=ignore_history)
    report_html = format_job_search_email_html(result)
    count = len(result.new_jobs)
    email_subject = f"{_subject_prefix(profile)} — {count} new match(es) — {result.iso_date}"
    return email_subject, report_html, count


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily job search email agent (multi-profile).")
    parser.add_argument(
        "--profile",
        metavar="ID",
        help="Candidate profile id (JSON in data/job_profiles/<id>.json), e.g. roi_atias",
    )
    parser.add_argument("--list-profiles", action="store_true", help="List available profile IDs and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Search without sending email.")
    parser.add_argument("--no-retry", action="store_true", help="Do not retry after failure.")
    parser.add_argument("--no-save", action="store_true", help="Do not append new jobs to history.")
    parser.add_argument("--clean-history", action="store_true", help="Clean this profile's job history and exit.")
    parser.add_argument("--resend-today", action="store_true", help="Resend today's jobs from history.")
    parser.add_argument(
        "--ignore-history",
        action="store_true",
        help="Fresh search: skip dedup and do not tell LLM to skip tracked jobs.",
    )
    parser.add_argument(
        "--me-only",
        action="store_true",
        help="Send to your addresses only (not the profile recipient / no BCC). For preview before forwarding.",
    )
    parser.add_argument("--resend-preview", action="store_true", help="Resend last dry-run preview HTML.")
    parser.add_argument(
        "--backfill-vectors",
        action="store_true",
        help="Upload existing markdown history to Pinecone (one-time setup).",
    )
    args = parser.parse_args()

    if args.list_profiles:
        ids = list_profiles()
        if not ids:
            print("No profiles in data/job_profiles/*.json")
            return 0
        for pid in ids:
            p = load_profile(pid)
            print(f"{pid}: {p.display_name} -> {', '.join(p.to_emails)}")
            if p.bcc_emails:
                print(f"  BCC: {', '.join(p.bcc_emails)}")
            print(f"  CV: {p.cv_source}")
        return 0

    profile: JobSearchProfile | None = None
    if args.profile:
        profile = load_profile(args.profile)

    logger = _configure_logging(profile)

    from network_env import configure_http_proxy  # noqa: E402

    configure_http_proxy(log=logger)

    ctx = profile_context(profile) if profile else _null_context()
    with ctx:
        if args.clean_history:
            path, kept, removed = purge_invalid_history_entries()
            logger.info("History cleanup: kept %s, removed %s — %s", kept, removed, path)
            return 0

        if args.backfill_vectors:
            from job_search_vector import backfill_history_records, profile_namespace, vector_dedup_enabled

            if not vector_dedup_enabled():
                logger.error(
                    "Set JOB_SEARCH_VECTOR_DEDUP=1 and configure Pinecone or Chroma "
                    "(JOB_SEARCH_VECTOR_BACKEND=chroma) in .env first"
                )
                return 1
            _, records = load_history()
            count, _ = backfill_history_records(records)
            logger.info(
                "Pinecone backfill complete: %s job(s) in namespace %r",
                count,
                profile_namespace(),
            )
            return 0

        vendor = job_search_vendor()
        if vendor is LLMVendor.OPENAI and not os.getenv("OPENAI_API_KEY"):
            logger.error("OPENAI_API_KEY is not set in %s", APP_DIR / ".env")
            return 1
        if vendor is LLMVendor.GEMINI and not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
            logger.error("GOOGLE_API_KEY is not set in %s", APP_DIR / ".env")
            return 1

        preview_path = profile.preview_file if profile else LOG_DIR / "daily_job_search_preview.html"
        label = profile.log_stem if profile else "daily_job_search_email"
        me_only = args.me_only

        def run_once() -> int:
            try:
                if args.resend_today:
                    result = job_search_result_from_history()
                    if not result.new_jobs:
                        _, records = load_history()
                        valid = records_to_listings(records)
                        verified = sum(1 for j in valid if j.url_status == "verified")
                        unavailable = sum(1 for j in valid if j.url_status == "unavailable")
                        result = JobSearchResult(
                            iso_date=result.iso_date,
                            new_jobs=valid,
                            search_notes=[
                                f"Showing {len(valid)} job(s) from history "
                                f"({verified} with working links, {unavailable} without working links)."
                            ],
                            providers_used=["history resend"],
                            total_tracked=len(records),
                        )
                    report_html = format_job_search_email_html(result)
                    count = len(result.new_jobs)
                    verified_n = sum(1 for j in result.new_jobs if j.url_status == "verified")
                    email_subject = (
                        f"{_subject_prefix(profile)} — {count} match(es) "
                        f"({verified_n} with links) — {result.iso_date}"
                    )
                elif args.resend_preview:
                    if not preview_path.is_file():
                        raise RuntimeError(f"No preview file at {preview_path} — run --dry-run first")
                    report_html = preview_path.read_text(encoding="utf-8")
                    email_subject = f"{_subject_prefix(profile)} — resend — {today_iso()}"
                    count = -1
                else:
                    email_subject, report_html, count = build_report(
                        save=not args.no_save,
                        profile=profile,
                        ignore_history=args.ignore_history,
                    )
                    logger.info(
                        "Job search complete: %s new jobs (profile=%s)",
                        count,
                        profile.id if profile else "default",
                    )
            except Exception as exc:
                logger.exception("Failed to run job search: %s", exc)
                return 1

            if args.dry_run:
                preview_path.write_text(report_html, encoding="utf-8")
                logger.info("Dry run OK — %s new jobs, preview: %s", count, preview_path)
                return 0

            try:
                send_job_email(
                    subject=email_subject,
                    body_html=report_html,
                    profile=profile,
                    logger=logger,
                    me_only=me_only,
                )
            except Exception as exc:
                logger.exception("Failed to send email: %s", exc)
                return 1

            if me_only:
                to_desc = ", ".join(_parse_recipients(os.getenv("JOB_SEARCH_ME_ONLY_TO", DEFAULT_TO)))
                bcc_desc = " (me-only preview)"
            else:
                to_desc = ", ".join(profile.to_emails) if profile else os.getenv("JOB_SEARCH_TO", DEFAULT_TO)
                bcc_desc = f" BCC: {', '.join(profile.bcc_emails)}" if profile and profile.bcc_emails else ""
            logger.info("Email sent — TO: %s%s — subject: %s", to_desc, bcc_desc, email_subject)
            return 0

        if args.dry_run or args.no_retry:
            return run_once()

        return run_with_scheduled_retry(run_once, logger=logger, label=label)


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
