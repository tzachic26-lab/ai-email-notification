"""Shared helpers for reading configuration from environment variables."""

from __future__ import annotations

import os

TRUE_VALUES = ("1", "true", "yes")


def env_text(name: str, default: str = "", *fallback_names: str) -> str:
    """Stripped value of ``name``, then of each fallback name, else ``default``."""
    for key in (name, *fallback_names):
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return default


def env_flag(name: str, default: bool = False) -> bool:
    """True when the variable is set to 1/true/yes (case-insensitive)."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in TRUE_VALUES


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Integer value of the variable, clamped to the given bounds.

    Falls back to ``default`` when unset or not a valid integer.
    """
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value
