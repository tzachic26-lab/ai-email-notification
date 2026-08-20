"""Shared primitives for the markdown history files used as run-to-run storage.

Both the AI trainer and the job search agents keep their history as a markdown
file of ``---`` separated entries, each starting with an ``## <iso date> | <title>``
header followed by ``**Field:** value`` lines and ``### Section`` blocks.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

ENTRY_HEADER_RE = re.compile(
    r"^##\s+(?P<iso_date>\d{4}-\d{2}-\d{2})\s+\|\s+(?P<title>.+)$",
    re.MULTILINE,
)
FIELD_RE = re.compile(r"^\*\*(?P<key>[^*]+):\*\*\s*(?P<value>.+)$", re.MULTILINE)
ENTRY_SEPARATOR_RE = re.compile(r"\n---+\n")


def today_iso() -> str:
    return date.today().isoformat()


def history_path(env_key: str, default: Path) -> Path:
    """History file location, overridable through ``env_key``."""
    raw = (os.getenv(env_key) or "").strip()
    return Path(raw) if raw else default


def ensure_history_file(target: Path, intro: str) -> Path:
    """Create the history file with its intro header when it does not exist yet."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(intro, encoding="utf-8")
    return target


def parse_fields(chunk: str) -> dict[str, str]:
    """``**Key:** value`` lines of an entry, keyed by lowercased field name."""
    return {m.group("key").strip().lower(): m.group("value").strip() for m in FIELD_RE.finditer(chunk)}


def iter_entries(text: str):
    """Yield ``(chunk, header_match)`` for every entry in a history file."""
    for chunk in ENTRY_SEPARATOR_RE.split(text):
        header = ENTRY_HEADER_RE.search(chunk)
        if header:
            yield chunk, header


def markdown_section(body: str, heading: str) -> str:
    """Body of the ``### <heading>`` section, or an empty string."""
    pattern = rf"### {re.escape(heading)}\s*\n(.*?)(?=\n### |\Z)"
    match = re.search(pattern, body, re.DOTALL)
    return match.group(1).strip() if match else ""


def section_bullets(body: str, heading: str) -> list[str]:
    """Non-placeholder bullet items of the ``### <heading>`` section."""
    items: list[str] = []
    for line in markdown_section(body, heading).splitlines():
        text = line.strip()
        if not text.startswith("-"):
            continue
        value = text.lstrip("- ").strip()
        if value and value not in ("—", "-"):
            items.append(value)
    return items


def append_entries(target: Path, entries: list[str]) -> Path:
    """Append ``---`` separated entries to an existing history file."""
    text = target.read_text(encoding="utf-8").rstrip()
    block = "\n\n".join(entry.strip() for entry in entries if entry.strip())
    if block and not block.startswith("---"):
        block = f"---\n\n{block}"
    target.write_text(f"{text}\n\n{block}\n", encoding="utf-8")
    return target
