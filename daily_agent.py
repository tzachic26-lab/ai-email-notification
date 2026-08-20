"""Shared bootstrap and plumbing for the ``daily_*_email_agent`` entry points.

Importing this module performs the setup every daily agent needs before it can
import the API modules: TLS trust store injection, ``sys.path`` and ``.env``
loading. Import it first in an agent so that ``os.getenv`` reads in the API
modules see the values from ``.env``.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

import truststore

truststore.inject_into_ssl()

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(APP_DIR / ".env", override=True)

from daily_email_send import (  # noqa: E402
    configure_scheduled_outlook_env,
    send_html_email,
)
from llm_providers import LLMVendor  # noqa: E402

SEND_HELPER = APP_DIR / "outlook_send_helper.py"
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
VENDOR_CHOICES = ("gemini", "openai")

configure_scheduled_outlook_env()


def parse_recipients(raw: str) -> list[str]:
    """Split a comma/semicolon separated recipient list."""
    return [part.strip() for part in re.split(r"[,;]+", raw or "") if part.strip()]


def setup_agent_logging(name: str, log_filename: str) -> logging.Logger:
    """Log to ``logs/<log_filename>`` and stdout, then configure the HTTP proxy."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / log_filename, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger(name)

    from network_env import configure_http_proxy

    configure_http_proxy(log=logger)
    return logger


def add_common_agent_args(parser: argparse.ArgumentParser, *, vendor: bool = False) -> None:
    """Add the CLI flags every daily agent supports."""
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
    if vendor:
        parser.add_argument(
            "--vendor",
            choices=VENDOR_CHOICES,
            help="Force vendor for this run (overrides .env LLM_VENDOR_PRIMARY).",
        )


def apply_vendor_override(args: argparse.Namespace) -> None:
    """Honour ``--vendor`` for the rest of the run."""
    if args.vendor:
        os.environ["LLM_VENDOR_PRIMARY"] = args.vendor


def require_vendor_api_key(vendor: LLMVendor, logger: logging.Logger) -> bool:
    """Log and return False when the key for a single-vendor run is missing."""
    if vendor is LLMVendor.OPENAI and not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set in %s", APP_DIR / ".env")
        return False
    if vendor is LLMVendor.GEMINI and not (
        os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    ):
        logger.error("GOOGLE_API_KEY is not set in %s", APP_DIR / ".env")
        return False
    return True


def send_agent_email(
    *,
    subject: str,
    body_html: str,
    logger: logging.Logger,
    recipients_arg: str,
    bcc_arg: str | None = None,
) -> None:
    send_html_email(
        send_helper=SEND_HELPER,
        log_dir=LOG_DIR,
        recipients_arg=recipients_arg,
        bcc_arg=bcc_arg,
        subject=subject,
        body_html=body_html,
        logger=logger,
    )


def deliver_email(
    *,
    dry_run: bool,
    preview_path: Path,
    subject: str,
    body_html: str,
    logger: logging.Logger,
    recipients_arg: str,
    bcc_arg: str | None = None,
    preview_detail: str = "",
    sent_detail: str = "",
) -> int:
    """Write the preview (dry run) or send the email; returns the process exit code."""
    if dry_run:
        preview_path.write_text(body_html, encoding="utf-8")
        logger.info(
            "Dry run OK — %spreview: %s",
            f"{preview_detail}, " if preview_detail else "",
            preview_path,
        )
        return 0

    try:
        send_agent_email(
            subject=subject,
            body_html=body_html,
            logger=logger,
            recipients_arg=recipients_arg,
            bcc_arg=bcc_arg,
        )
    except Exception as exc:
        logger.exception("Failed to send email: %s", exc)
        return 1

    logger.info(
        "Email sent to %s%s — subject: %s%s",
        recipients_arg,
        f" (BCC: {bcc_arg})" if bcc_arg else "",
        subject,
        f" — {sent_detail}" if sent_detail else "",
    )
    return 0
