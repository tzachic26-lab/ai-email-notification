"""Shared email send helper for daily agents (Outlook or Gmail)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from outlook_mcp_env import outlook_mcp_dir, outlook_python

OUTLOOK_MCP_DIR = outlook_mcp_dir()
OUTLOOK_PYTHON = outlook_python()
SEND_TIMEOUT_SECONDS = 180
DEFAULT_RETRY_DELAY_SECONDS = 10 * 60
DEFAULT_MAX_ATTEMPTS = 3  # initial run + 2 retries


def retry_delay_seconds() -> int:
    raw = os.getenv("DAILY_EMAIL_RETRY_DELAY_SECONDS", str(DEFAULT_RETRY_DELAY_SECONDS))
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_RETRY_DELAY_SECONDS


def max_attempts() -> int:
    raw = os.getenv("DAILY_EMAIL_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def run_with_scheduled_retry(run_once: Callable[[], int], *, logger, label: str) -> int:
    """Run a daily email job; on failure wait and retry (default: 2 retries, 10 min apart)."""
    attempts = max_attempts()
    delay = retry_delay_seconds()

    for attempt in range(1, attempts + 1):
        logger.info("Starting %s (attempt %s/%s)", label, attempt, attempts)
        exit_code = run_once()
        if exit_code == 0:
            return 0

        if attempt < attempts and delay > 0:
            logger.warning(
                "%s failed on attempt %s/%s — retrying in %s minutes",
                label,
                attempt,
                attempts,
                delay // 60,
            )
            time.sleep(delay)
        elif attempt < attempts:
            logger.warning(
                "%s failed on attempt %s/%s — retrying immediately",
                label,
                attempt,
                attempts,
            )

    logger.error("%s failed after %s attempt(s)", label, attempts)
    return exit_code


_PLACEHOLDER_RECIPIENT = "you@example.com"


def resolve_daily_recipient(*env_keys: str) -> str:
    """First non-empty env var, then GMAIL_TO, then placeholder."""
    for key in env_keys:
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    gmail_to = (os.getenv("GMAIL_TO") or "").strip()
    if gmail_to:
        return gmail_to
    return _PLACEHOLDER_RECIPIENT


def reject_placeholder_recipients(recipients: str, *, logger, label: str) -> None:
    parts = [
        p.strip().lower()
        for p in recipients.replace(";", ",").split(",")
        if p.strip()
    ]
    if _PLACEHOLDER_RECIPIENT in parts:
        raise RuntimeError(
            f"{label}: recipient is {_PLACEHOLDER_RECIPIENT}. "
            "Set DAILY_NEWS_RECIPIENT (or agent-specific recipient) or GMAIL_TO in .env"
        )


def configure_scheduled_outlook_env() -> None:
    """Scheduled Task runs have no TTY — never block on browser auth."""
    if not sys.stdin.isatty():
        os.environ.setdefault("OUTLOOK_SEND_NON_INTERACTIVE", "1")


def email_send_provider() -> str:
    return os.getenv("EMAIL_SEND_PROVIDER", "outlook").strip().lower()


def send_html_email(
    *,
    send_helper: Path,
    log_dir: Path,
    recipients_arg: str,
    subject: str,
    body_html: str,
    logger,
    bcc_arg: str | None = None,
    to_recipients: list[str] | None = None,
    bcc_recipients: list[str] | None = None,
) -> None:
    """Send HTML email via Gmail or Outlook based on EMAIL_SEND_PROVIDER."""
    if to_recipients is None:
        to_recipients = [p.strip() for p in recipients_arg.split(",") if p.strip()]
    reject_placeholder_recipients(",".join(to_recipients), logger=logger, label="send_html_email")
    if bcc_recipients is None and bcc_arg:
        bcc_recipients = [p.strip() for p in bcc_arg.split(",") if p.strip()]

    primary = email_send_provider()
    fallback = os.getenv("EMAIL_SEND_FALLBACK_PROVIDER", "").strip().lower()
    if not fallback:
        fallback = "outlook" if primary == "gmail" else ""

    providers = [primary]
    if fallback and fallback != primary:
        providers.append(fallback)

    last_error: Exception | None = None
    for idx, provider in enumerate(providers):
        try:
            if provider == "gmail":
                from gmail_send import send_gmail_html_email

                send_gmail_html_email(
                    subject=subject,
                    body_html=body_html,
                    logger=logger,
                    to_recipients=to_recipients,
                    bcc_recipients=bcc_recipients,
                )
            else:
                send_outlook_html_email(
                    send_helper=send_helper,
                    log_dir=log_dir,
                    recipients_arg=",".join(to_recipients),
                    bcc_arg=",".join(bcc_recipients) if bcc_recipients else None,
                    subject=subject,
                    body_html=body_html,
                    logger=logger,
                )
            return
        except Exception as exc:
            last_error = exc
            if idx < len(providers) - 1:
                logger.warning(
                    "%s send failed (%s) — trying %s",
                    provider,
                    exc,
                    providers[idx + 1],
                )
                continue
            raise
    if last_error is not None:
        raise last_error


def send_outlook_html_email(
    *,
    send_helper: Path,
    log_dir: Path,
    recipients_arg: str,
    subject: str,
    body_html: str,
    logger,
    bcc_arg: str | None = None,
) -> None:
    if not OUTLOOK_PYTHON.is_file():
        raise RuntimeError(f"Outlook MCP Python not found: {OUTLOOK_PYTHON}")
    if not send_helper.is_file():
        raise RuntimeError(f"Send helper not found: {send_helper}")

    configure_scheduled_outlook_env()

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".html",
        delete=False,
        dir=log_dir,
    ) as tmp:
        tmp.write(body_html)
        html_path = tmp.name

    from network_env import apply_http_proxy_to_env

    env = os.environ.copy()
    apply_http_proxy_to_env(env)

    cmd = [str(OUTLOOK_PYTHON), str(send_helper)]
    if env.get("OUTLOOK_SEND_NON_INTERACTIVE", "").lower() in ("1", "true", "yes"):
        cmd.append("--non-interactive")
    cmd.extend([recipients_arg, subject, html_path])
    if bcc_arg:
        cmd.append(bcc_arg)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=SEND_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise RuntimeError(detail)
        logger.info(result.stdout.strip())
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Outlook send timed out after {SEND_TIMEOUT_SECONDS}s "
            "(likely waiting for auth — sign in interactively once)"
        ) from exc
    finally:
        Path(html_path).unlink(missing_ok=True)
