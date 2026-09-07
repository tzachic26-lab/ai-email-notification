"""Send HTML email via Resend API."""
from __future__ import annotations

import os
import re

import requests


def _parse_recipients(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;]+", raw) if part.strip()]


def resend_config() -> tuple[str, str, list[str]]:
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    from_address = (os.getenv("RESEND_FROM_EMAIL") or os.getenv("GMAIL_ADDRESS") or "").strip()
    to_raw = (os.getenv("RESEND_TO") or os.getenv("GMAIL_TO") or from_address).strip()

    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not set in .env")
    if not from_address:
        raise RuntimeError("RESEND_FROM_EMAIL (or GMAIL_ADDRESS) is not set in .env")
    if not to_raw:
        raise RuntimeError("RESEND_TO is empty and no default recipient is configured")

    to_addrs = _parse_recipients(to_raw)
    if not to_addrs:
        raise RuntimeError("No Resend recipients configured")

    return api_key, from_address, to_addrs


def send_resend_html_email(
    *,
    subject: str,
    body_html: str,
    logger,
    to_recipients: list[str] | None = None,
    bcc_recipients: list[str] | None = None,
) -> None:
    """Send HTML email via Resend API."""
    api_key, from_address, default_to = resend_config()

    to_addrs = to_recipients or default_to
    bcc_addrs = list(bcc_recipients or [])

    if not to_addrs:
        raise RuntimeError("No Resend recipients configured")

    # Resend supports multiple To, Cc, Bcc recipients as lists
    payload = {
        "from": from_address,
        "to": to_addrs,
        "subject": subject,
        "html": body_html,
    }
    if bcc_addrs:
        payload["bcc"] = bcc_addrs

    response = requests.post(
        "https://api.resend.com/emails",
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=60,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Resend API error {response.status_code}: {response.text}")

    logger.info(
        "Resend sent to %s%s (subject: %s)",
        ", ".join(to_addrs),
        f" (+{len(bcc_addrs)} BCC)" if bcc_addrs else "",
        subject,
    )
