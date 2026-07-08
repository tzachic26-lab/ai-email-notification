"""Shared RSS fetch with retry (handles corp proxy / network warm-up at Windows logon)."""
from __future__ import annotations

import logging
import os
import time

import feedparser

logger = logging.getLogger(__name__)


def fetch_attempts() -> int:
    raw = os.getenv("RSS_FETCH_ATTEMPTS", "3")
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def fetch_retry_delay_seconds() -> float:
    raw = os.getenv("RSS_FETCH_RETRY_DELAY_SECONDS", "15")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 15.0


def parse_feed(url: str) -> feedparser.FeedParserDict:
    """Fetch one RSS feed with short retries when the response has no entries."""
    attempts = fetch_attempts()
    delay = fetch_retry_delay_seconds()
    last_feed: feedparser.FeedParserDict | None = None

    for attempt in range(1, attempts + 1):
        feed = feedparser.parse(url)
        last_feed = feed
        if feed.entries:
            return feed
        bozo_exc = getattr(feed, "bozo_exception", None)
        if attempt < attempts:
            logger.warning(
                "RSS empty (attempt %s/%s) url=%s bozo=%s err=%s — retry in %ss",
                attempt,
                attempts,
                url[:90],
                feed.bozo,
                bozo_exc,
                delay,
            )
            if delay:
                time.sleep(delay)
        else:
            logger.warning(
                "RSS still empty after %s attempts: url=%s bozo=%s err=%s",
                attempts,
                url[:90],
                feed.bozo,
                bozo_exc,
            )

    return last_feed  # type: ignore[return-value]
