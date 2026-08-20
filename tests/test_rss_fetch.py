"""Unit tests for rss_fetch retry behaviour."""

from __future__ import annotations

import logging

import feedparser
import pytest

import rss_fetch


def _feed(entries: list[dict], *, bozo: int = 0) -> feedparser.FeedParserDict:
    feed = feedparser.FeedParserDict()
    feed["entries"] = entries
    feed["bozo"] = bozo
    return feed


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(rss_fetch.time, "sleep", slept.append)
    for key in ("RSS_FETCH_ATTEMPTS", "RSS_FETCH_RETRY_DELAY_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    return slept


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", 1), ("5", 5), ("0", 1), ("-3", 1), ("nope", 3)],
)
def test_fetch_attempts(monkeypatch, value, expected):
    monkeypatch.setenv("RSS_FETCH_ATTEMPTS", value)
    assert rss_fetch.fetch_attempts() == expected


def test_fetch_attempts_default(monkeypatch):
    monkeypatch.delenv("RSS_FETCH_ATTEMPTS", raising=False)
    assert rss_fetch.fetch_attempts() == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", 0.0), ("2.5", 2.5), ("-4", 0.0), ("nope", 15.0)],
)
def test_fetch_retry_delay_seconds(monkeypatch, value, expected):
    monkeypatch.setenv("RSS_FETCH_RETRY_DELAY_SECONDS", value)
    assert rss_fetch.fetch_retry_delay_seconds() == expected


def test_fetch_retry_delay_default(monkeypatch):
    monkeypatch.delenv("RSS_FETCH_RETRY_DELAY_SECONDS", raising=False)
    assert rss_fetch.fetch_retry_delay_seconds() == 15.0


def test_parse_feed_returns_first_non_empty_result(monkeypatch, _no_sleep):
    calls: list[str] = []

    def fake_parse(url):
        calls.append(url)
        return _feed([{"title": "story"}])

    monkeypatch.setattr(rss_fetch.feedparser, "parse", fake_parse)
    feed = rss_fetch.parse_feed("https://news.example.com/rss")
    assert len(feed.entries) == 1
    assert calls == ["https://news.example.com/rss"]
    assert _no_sleep == []


def test_parse_feed_retries_until_entries_arrive(monkeypatch, _no_sleep):
    monkeypatch.setenv("RSS_FETCH_RETRY_DELAY_SECONDS", "7")
    responses = [_feed([]), _feed([{"title": "story"}])]
    monkeypatch.setattr(rss_fetch.feedparser, "parse", lambda url: responses.pop(0))

    feed = rss_fetch.parse_feed("https://news.example.com/rss")
    assert len(feed.entries) == 1
    assert _no_sleep == [7.0]
    assert responses == []


def test_parse_feed_returns_last_empty_feed_after_all_attempts(monkeypatch, _no_sleep, caplog):
    monkeypatch.setenv("RSS_FETCH_ATTEMPTS", "3")
    monkeypatch.setenv("RSS_FETCH_RETRY_DELAY_SECONDS", "1")
    attempts: list[int] = []

    def fake_parse(url):
        attempts.append(1)
        return _feed([], bozo=1)

    monkeypatch.setattr(rss_fetch.feedparser, "parse", fake_parse)
    with caplog.at_level(logging.WARNING):
        feed = rss_fetch.parse_feed("https://news.example.com/rss")

    assert feed.entries == []
    assert len(attempts) == 3
    assert _no_sleep == [1.0, 1.0]
    assert "still empty after 3 attempts" in caplog.text


def test_parse_feed_skips_sleep_when_delay_is_zero(monkeypatch, _no_sleep):
    monkeypatch.setenv("RSS_FETCH_ATTEMPTS", "2")
    monkeypatch.setenv("RSS_FETCH_RETRY_DELAY_SECONDS", "0")
    monkeypatch.setattr(rss_fetch.feedparser, "parse", lambda url: _feed([]))
    rss_fetch.parse_feed("https://news.example.com/rss")
    assert _no_sleep == []


def test_parse_feed_single_attempt_does_not_retry(monkeypatch, _no_sleep):
    monkeypatch.setenv("RSS_FETCH_ATTEMPTS", "1")
    calls: list[str] = []
    monkeypatch.setattr(
        rss_fetch.feedparser, "parse", lambda url: calls.append(url) or _feed([])
    )
    rss_fetch.parse_feed("https://news.example.com/rss")
    assert len(calls) == 1
    assert _no_sleep == []
