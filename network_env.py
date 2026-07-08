"""HTTP proxy configuration for corp network vs direct internet (home / off VPN)."""
from __future__ import annotations

import logging
import os
import socket
from typing import MutableMapping
from urllib.parse import urlparse

CORP_PROXY_URL = "http://genproxy.corp.amdocs.com:8080"
CORP_PROXY_HOST = "genproxy.corp.amdocs.com"
CORP_PROXY_PORT = 8080
DEFAULT_NO_PROXY = "localhost,127.0.0.1,::1"
CORP_PROXY_DETECT_TIMEOUT_SECONDS = 2.0

logger = logging.getLogger(__name__)


def _proxy_url_reachable(proxy_url: str, *, timeout: float = CORP_PROXY_DETECT_TIMEOUT_SECONDS) -> bool:
    parsed = urlparse(proxy_url)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (8080 if parsed.scheme == "http" else 443)
    prev = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(prev)


def corp_proxy_reachable(*, timeout: float = CORP_PROXY_DETECT_TIMEOUT_SECONDS) -> bool:
    """True when the corporate proxy hostname resolves (on VPN / corp network)."""
    return _proxy_url_reachable(CORP_PROXY_URL, timeout=timeout)


def _auto_proxy_enabled() -> bool:
    return os.getenv("AUTO_HTTP_PROXY", "1").strip().lower() in ("1", "true", "yes")


def _strip_proxy_vars(env: MutableMapping[str, str]) -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)


def _set_direct(env: MutableMapping[str, str]) -> None:
    """Direct internet — tell Outlook MCP not to use its corp proxy default."""
    _strip_proxy_vars(env)
    env["NO_PROXY_DEFAULT"] = "1"


def _apply_proxy(env: MutableMapping[str, str], proxy_url: str) -> None:
    env["HTTP_PROXY"] = proxy_url
    env["HTTPS_PROXY"] = proxy_url
    env.pop("NO_PROXY_DEFAULT", None)


def _ensure_no_proxy(env: MutableMapping[str, str]) -> None:
    if not env.get("NO_PROXY") and not env.get("no_proxy"):
        env["NO_PROXY"] = DEFAULT_NO_PROXY


def _proxy_vars_present(env: MutableMapping[str, str]) -> bool:
    return any(key in env for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"))


def _configured_proxy_url(env: MutableMapping[str, str]) -> str:
    return (env.get("HTTP_PROXY") or env.get("http_proxy") or env.get("HTTPS_PROXY") or env.get("https_proxy") or "").strip()


def _resolve_proxy_for_env(env: MutableMapping[str, str], *, log: logging.Logger | None = None) -> str:
    log = log or logger
    _ensure_no_proxy(env)

    if _proxy_vars_present(env):
        requested = _configured_proxy_url(env)
        if not requested:
            _set_direct(env)
            mode = "direct (explicit empty)"
            log.info("Network: %s", mode)
            return mode
        if _proxy_url_reachable(requested):
            _apply_proxy(env, requested)
            mode = "proxy (configured)"
            log.info("Network: %s — %s", mode, requested)
            return mode
        log.warning("Network: configured proxy unreachable (%s) — using direct", requested)
        _set_direct(env)
        mode = "direct (proxy unreachable)"
        log.info("Network: %s", mode)
        return mode

    if _auto_proxy_enabled() and corp_proxy_reachable():
        _apply_proxy(env, CORP_PROXY_URL)
        mode = "corp proxy (auto-detected)"
        log.info("Network: %s", mode)
        return mode

    _set_direct(env)
    mode = "direct"
    log.info("Network: %s", mode)
    return mode


def configure_http_proxy(*, log: logging.Logger | None = None) -> str:
    """Configure HTTP(S)_PROXY for the current process."""
    return _resolve_proxy_for_env(os.environ, log=log)


def apply_http_proxy_to_env(env: MutableMapping[str, str]) -> str:
    """Apply validated proxy settings into a subprocess env dict (e.g. Outlook send)."""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
        if key in os.environ:
            env[key] = os.environ[key]
    return _resolve_proxy_for_env(env)
