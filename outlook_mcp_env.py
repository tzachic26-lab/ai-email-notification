"""Resolve Outlook MCP server paths from environment."""

from __future__ import annotations

import os
from pathlib import Path

_LEGACY_OUTLOOK_MCP_DIR = Path(r"C:\amdocs\mcp-servers\outlook-mcp-server-v4")


def outlook_mcp_dir() -> Path:
    raw = (os.getenv("OUTLOOK_MCP_DIR") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_dir():
            raise RuntimeError(f"OUTLOOK_MCP_DIR does not exist: {path}")
        return path
    if _LEGACY_OUTLOOK_MCP_DIR.is_dir():
        return _LEGACY_OUTLOOK_MCP_DIR
    raise RuntimeError(
        "OUTLOOK_MCP_DIR is not set and the default Outlook MCP path was not found. "
        "Set OUTLOOK_MCP_DIR in .env to your outlook-mcp-server directory."
    )


def outlook_python() -> Path:
    return outlook_mcp_dir() / ".venv" / "Scripts" / "python.exe"
