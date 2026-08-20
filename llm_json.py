"""Shared parsing of JSON payloads returned by LLM completions."""

from __future__ import annotations

import json
import re

_FENCE_START_RE = re.compile(r"^```(?:[a-zA-Z0-9_-]+)?\s*")
_FENCE_END_RE = re.compile(r"\s*```$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_code_fences(raw: str) -> str:
    """Remove a surrounding ```json ... ``` fence, if present."""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    text = _FENCE_START_RE.sub("", text)
    text = _FENCE_END_RE.sub("", text)
    return text.strip()


def sanitize_json_text(text: str) -> str:
    """Strip/repair content that breaks json.loads (common in grounded LLM output)."""
    text = _CONTROL_CHARS_RE.sub("", text)
    # Escape raw newlines/tabs inside JSON string literals
    out: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            out.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string:
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
        out.append(ch)
    return "".join(out)


def parse_json(raw: str) -> object:
    """Parse LLM output as JSON, tolerating code fences."""
    return json.loads(strip_code_fences(raw))


def parse_json_object(raw: str, *, what: str = "LLM response") -> dict:
    """Parse LLM output as a JSON object, tolerating code fences."""
    data = parse_json(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{what} is not a JSON object")
    return data


def parse_json_object_lenient(raw: str) -> dict:
    """Parse a JSON object from LLM output, repairing common formatting damage.

    Tries the raw text, then a sanitized version, then the first ``{...}`` blob.
    """
    text = strip_code_fences(raw)
    for candidate in (text, sanitize_json_text(text)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(sanitize_json_text(match.group(0)))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data
    raise json.JSONDecodeError("No JSON object found", text, 0)
