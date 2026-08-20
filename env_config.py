"""Numeric environment variable parsing that reports bad configuration."""
from __future__ import annotations

import logging
import os
from typing import TypeVar

_Number = TypeVar("_Number", int, float)

logger = logging.getLogger(__name__)


def env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """Read an int env var; log a warning and use the default when unparsable."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(float(raw.strip()))
        except ValueError:
            logger.warning("%s=%r is not a number — using %s", name, raw, default)
            value = default
    return _clamp(value, minimum, maximum)


def env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Read a float env var; log a warning and use the default when unparsable."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = float(raw.strip())
        except ValueError:
            logger.warning("%s=%r is not a number — using %s", name, raw, default)
            value = default
    return _clamp(value, minimum, maximum)


def _clamp(value: _Number, minimum: _Number | None, maximum: _Number | None) -> _Number:
    if minimum is not None and value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value
