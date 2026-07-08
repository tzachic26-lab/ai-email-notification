"""Send an HTML email via Outlook (run with Outlook MCP venv Python)."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

from dotenv import load_dotenv

load_dotenv(APP_DIR / ".env", override=True)

from outlook_mcp_env import outlook_mcp_dir  # noqa: E402

OUTLOOK_MCP_DIR = outlook_mcp_dir()
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(OUTLOOK_MCP_DIR))

from network_env import configure_http_proxy  # noqa: E402

from outlook_client import OutlookClient  # noqa: E402
from secure_config import get_config  # noqa: E402
from token_store import get_tokens  # noqa: E402

logger = logging.getLogger("outlook_send_helper")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

configure_http_proxy(log=logger)

AUTH_WAIT_SECONDS = 120
FLASK_STARTUP_SECONDS = 2
NON_INTERACTIVE_FLAG = "--non-interactive"


def _non_interactive_mode(argv: list[str] | None = None) -> bool:
    argv = argv if argv is not None else sys.argv
    if NON_INTERACTIVE_FLAG in argv:
        return True
    if os.environ.get("OUTLOOK_SEND_NON_INTERACTIVE", "").lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("OUTLOOK_SEND_ALLOW_BROWSER", "").lower() in ("1", "true", "yes"):
        return False
    return not sys.stdin.isatty()


def _strip_helper_flags(argv: list[str]) -> list[str]:
    return [arg for arg in argv if arg != NON_INTERACTIVE_FLAG]


def _auth_server_running(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _start_flask_server() -> None:
    cfg = get_config()
    if _auth_server_running(cfg.flask_port):
        logger.info("Outlook auth server already running on port %s", cfg.flask_port)
        return

    from app import app as flask_app

    def run() -> None:
        import os as _os

        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        with open(_os.devnull, "w") as devnull:
            old_stdout = sys.stdout
            sys.stdout = devnull
            try:
                flask_app.run(
                    host=cfg.flask_host,
                    port=cfg.flask_port,
                    debug=False,
                    use_reloader=False,
                )
            finally:
                sys.stdout = old_stdout

    threading.Thread(target=run, daemon=True).start()
    time.sleep(FLASK_STARTUP_SECONDS)


def _wait_for_new_tokens(previous_access: str | None, timeout: int = AUTH_WAIT_SECONDS) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        tokens = get_tokens()
        access = tokens.get("access_token") if tokens else None
        if access and access != previous_access:
            return True
        time.sleep(1)
    return False


async def _verify_token(client: OutlookClient) -> bool:
    if not client.is_authenticated():
        client.load_cached_tokens()
    if not client.access_token:
        return False
    try:
        user = await client.get_current_user()
        return bool(user.get("mail") or user.get("userPrincipalName"))
    except Exception as exc:
        logger.info("Token verification failed: %s", exc)
        return False


async def _refresh_or_clear(client: OutlookClient) -> bool:
    """Try refresh token; clear cache if refresh fails."""
    client.load_cached_tokens()
    if client.refresh_token:
        refreshed = client.auth_manager.refresh_access_token()
        if refreshed:
            logger.info("Outlook access token refreshed")
            return True
        logger.warning("Refresh token invalid — clearing cached tokens")
        client.auth_manager.clear_tokens()
        return False
    if client.access_token:
        client.auth_manager.clear_tokens()
    return False


async def ensure_outlook_authenticated(
    client: OutlookClient,
    *,
    allow_browser: bool | None = None,
) -> bool:
    """Load, refresh, or recreate Outlook tokens before sending."""
    cfg = get_config()
    allow_browser = not _non_interactive_mode() if allow_browser is None else allow_browser

    client.load_cached_tokens()
    if client.refresh_token:
        client.auth_manager.refresh_access_token()
    if await _verify_token(client):
        return True

    if await _refresh_or_clear(client) and await _verify_token(client):
        return True

    if not allow_browser:
        logger.error(
            "Outlook not authenticated in non-interactive mode (scheduled task). "
            "Sign in once from a terminal: open %s/signin after running "
            "week1\\start_outlook_auth_server.ps1, then retry.",
            cfg.flask_url,
        )
        print(
            "Outlook not authenticated — run interactive sign-in once, then scheduled sends "
            f"will use the cached refresh token. Auth URL: {cfg.flask_url}/signin",
            file=sys.stderr,
        )
        return False

    logger.info("Starting browser sign-in at %s/signin", cfg.flask_url)
    previous = get_tokens()
    previous_access = previous.get("access_token") if previous else None
    _start_flask_server()
    webbrowser.open(f"{cfg.flask_url}/signin")

    if not _wait_for_new_tokens(previous_access):
        print(
            f"Outlook sign-in timed out after {AUTH_WAIT_SECONDS}s. "
            f"Open {cfg.flask_url}/signin manually.",
            file=sys.stderr,
        )
        return False

    client.load_cached_tokens()
    if client.refresh_token:
        client.auth_manager.refresh_access_token()

    if await _verify_token(client):
        logger.info("Outlook re-authenticated successfully")
        return True

    print("Outlook sign-in completed but token verification failed", file=sys.stderr)
    return False


def _parse_recipients(raw: str) -> list[str]:
    recipients = [part.strip() for part in re.split(r"[,;]+", raw) if part.strip()]
    if not recipients:
        raise ValueError("At least one recipient is required")
    return recipients


async def main() -> int:
    argv = _strip_helper_flags(sys.argv)
    if len(argv) not in (4, 5):
        print(
            "Usage: outlook_send_helper.py [--non-interactive] <to[,to2...]> <subject> <html_file> [bcc[,bcc2...]]",
            file=sys.stderr,
        )
        return 1

    recipients_raw, subject, html_path = argv[1], argv[2], argv[3]
    bcc_raw = argv[4] if len(argv) == 5 else ""
    try:
        recipients = _parse_recipients(recipients_raw)
        bcc_recipients = _parse_recipients(bcc_raw) if bcc_raw.strip() else []
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    body_html = Path(html_path).read_text(encoding="utf-8")

    cfg = get_config()
    client = OutlookClient(cfg.tenant_id, cfg.client_id, cfg.client_secret, cfg.authority)
    await client.initialize()
    try:
        if not await ensure_outlook_authenticated(client):
            print("Outlook not authenticated", file=sys.stderr)
            return 1

        result = await client.send_mail(
            to_recipients=recipients,
            subject=subject,
            body_html=body_html,
            bcc_recipients=bcc_recipients or None,
        )
        if result.get("error"):
            error = str(result["error"])
            if "401" in error or "Token expired" in error or "re-authenticate" in error.lower():
                logger.info("Send failed with auth error — retrying after token refresh")
                if await _refresh_or_clear(client) and await _verify_token(client):
                    result = await client.send_mail(
                        to_recipients=recipients,
                        subject=subject,
                        body_html=body_html,
                        bcc_recipients=bcc_recipients or None,
                    )
            if result.get("error"):
                print(result["error"], file=sys.stderr)
                return 1

        sent_to = f"Sent to {', '.join(recipients)}"
        if bcc_recipients:
            sent_to += f" (BCC: {', '.join(bcc_recipients)})"
        print(sent_to)
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
