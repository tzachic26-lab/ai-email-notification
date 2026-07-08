"""Send HTML email via Gmail SMTP (App Password)."""
from __future__ import annotations

import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr


def _parse_recipients(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;]+", raw) if part.strip()]


def gmail_config() -> tuple[str, str, list[str], str | None]:
    address = (os.getenv("GMAIL_ADDRESS") or "").strip()
    app_password = (os.getenv("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    to_raw = (os.getenv("GMAIL_TO") or address).strip()
    from_name = (os.getenv("GMAIL_FROM_NAME") or "").strip() or None

    if not address:
        raise RuntimeError("GMAIL_ADDRESS is not set in .env")
    if not app_password:
        raise RuntimeError("GMAIL_APP_PASSWORD is not set in .env")

    to_addrs = _parse_recipients(to_raw)
    if not to_addrs:
        raise RuntimeError("GMAIL_TO is empty and GMAIL_ADDRESS is invalid")

    return address, app_password, to_addrs, from_name


def send_gmail_html_email(
    *,
    subject: str,
    body_html: str,
    logger,
    to_recipients: list[str] | None = None,
    bcc_recipients: list[str] | None = None,
) -> None:
    """Send HTML email. In Gmail mode, defaults to GMAIL_TO only (no BCC unless forced)."""
    address, app_password, default_to, from_name = gmail_config()

    gmail_self_only = os.getenv("GMAIL_SELF_ONLY", "1").lower() in ("1", "true", "yes")
    if to_recipients is not None:
        to_addrs = to_recipients
        bcc_addrs = list(bcc_recipients or [])
    elif gmail_self_only:
        to_addrs = default_to
        bcc_addrs = []
    else:
        to_addrs = to_recipients or default_to
        bcc_addrs = bcc_recipients or []

    if not to_addrs:
        raise RuntimeError("No Gmail recipients configured")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, address)) if from_name else address
    msg["To"] = ", ".join(to_addrs)
    if bcc_addrs:
        msg["Bcc"] = ", ".join(bcc_addrs)

    msg.attach(MIMEText(body_html, "html", "utf-8"))

    all_recipients = list(dict.fromkeys(to_addrs + bcc_addrs))

    port_raw = (os.getenv("GMAIL_SMTP_PORT") or "587").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 587

    if port == 465:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
            smtp.login(address, app_password)
            smtp.sendmail(address, all_recipients, msg.as_string())
    else:
        with smtplib.SMTP("smtp.gmail.com", port, timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(address, app_password)
            smtp.sendmail(address, all_recipients, msg.as_string())

    logger.info(
        "Gmail sent to %s%s (subject: %s)",
        ", ".join(to_addrs),
        f" (+{len(bcc_addrs)} BCC)" if bcc_addrs else "",
        subject,
    )
