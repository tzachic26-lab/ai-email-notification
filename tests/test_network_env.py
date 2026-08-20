"""Unit tests for network_env proxy resolution."""

from __future__ import annotations

import logging
import os
import socket

import pytest

import network_env as ne


@pytest.fixture
def unreachable(monkeypatch):
    """Treat every proxy URL as unresolvable (no DNS lookups in tests)."""
    monkeypatch.setattr(ne, "_proxy_url_reachable", lambda url, timeout=None: False)


@pytest.fixture
def reachable(monkeypatch):
    monkeypatch.setattr(ne, "_proxy_url_reachable", lambda url, timeout=None: True)


def test_proxy_url_reachable_true_when_dns_resolves(monkeypatch):
    calls = {}

    def fake_getaddrinfo(host, port, **kwargs):
        calls["host"] = host
        calls["port"] = port
        return [("family", "socktype", "proto", "canonname", (host, port))]

    monkeypatch.setattr(ne.socket, "getaddrinfo", fake_getaddrinfo)
    assert ne._proxy_url_reachable("http://proxy.example.com:8080") is True
    assert calls == {"host": "proxy.example.com", "port": 8080}


def test_proxy_url_reachable_false_on_dns_error(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no such host")

    monkeypatch.setattr(ne.socket, "getaddrinfo", boom)
    assert ne._proxy_url_reachable("http://proxy.example.com:8080") is False


def test_proxy_url_reachable_false_without_host():
    assert ne._proxy_url_reachable("not-a-url") is False


def test_proxy_url_reachable_defaults_port_by_scheme(monkeypatch):
    seen: list[int] = []

    def fake_getaddrinfo(host, port, **kwargs):
        seen.append(port)
        return []

    monkeypatch.setattr(ne.socket, "getaddrinfo", fake_getaddrinfo)
    ne._proxy_url_reachable("http://proxy.example.com")
    ne._proxy_url_reachable("https://proxy.example.com")
    assert seen == [8080, 443]


def test_proxy_url_reachable_restores_default_timeout(monkeypatch):
    monkeypatch.setattr(ne.socket, "getaddrinfo", lambda *a, **k: [])
    previous = socket.getdefaulttimeout()
    ne._proxy_url_reachable("http://proxy.example.com:8080", timeout=1.0)
    assert socket.getdefaulttimeout() == previous


def test_corp_proxy_reachable_delegates_to_url_check(monkeypatch):
    seen = {}

    def fake_reachable(url, timeout=None):
        seen["url"] = url
        return True

    monkeypatch.setattr(ne, "_proxy_url_reachable", fake_reachable)
    assert ne.corp_proxy_reachable() is True
    assert seen["url"] == ne.CORP_PROXY_URL


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), (" 1 ", True), ("0", False), ("no", False)],
)
def test_auto_proxy_enabled_parses_env(monkeypatch, value, expected):
    monkeypatch.setenv("AUTO_HTTP_PROXY", value)
    assert ne._auto_proxy_enabled() is expected


def test_auto_proxy_enabled_defaults_to_true(monkeypatch):
    monkeypatch.delenv("AUTO_HTTP_PROXY", raising=False)
    assert ne._auto_proxy_enabled() is True


def test_strip_proxy_vars_removes_all_casings():
    env = {"HTTP_PROXY": "a", "https_proxy": "b", "KEEP": "c"}
    ne._strip_proxy_vars(env)
    assert env == {"KEEP": "c"}


def test_set_direct_marks_no_proxy_default():
    env = {"HTTP_PROXY": "a"}
    ne._set_direct(env)
    assert env == {"NO_PROXY_DEFAULT": "1"}


def test_apply_proxy_sets_both_schemes_and_clears_direct_flag():
    env = {"NO_PROXY_DEFAULT": "1"}
    ne._apply_proxy(env, "http://p:8080")
    assert env == {"HTTP_PROXY": "http://p:8080", "HTTPS_PROXY": "http://p:8080"}


def test_ensure_no_proxy_only_sets_default_when_missing():
    env: dict[str, str] = {}
    ne._ensure_no_proxy(env)
    assert env["NO_PROXY"] == ne.DEFAULT_NO_PROXY

    env = {"no_proxy": "example.com"}
    ne._ensure_no_proxy(env)
    assert "NO_PROXY" not in env


def test_proxy_vars_present_and_configured_url_precedence():
    assert ne._proxy_vars_present({"http_proxy": ""}) is True
    assert ne._proxy_vars_present({"NO_PROXY": "x"}) is False
    assert ne._configured_proxy_url({"HTTPS_PROXY": " https://b "}) == "https://b"
    assert ne._configured_proxy_url({"HTTP_PROXY": "http://a", "HTTPS_PROXY": "https://b"}) == "http://a"
    assert ne._configured_proxy_url({}) == ""


def test_resolve_proxy_explicit_empty_means_direct(unreachable):
    env = {"HTTP_PROXY": "   "}
    assert ne._resolve_proxy_for_env(env) == "direct (explicit empty)"
    assert env == {"NO_PROXY": ne.DEFAULT_NO_PROXY, "NO_PROXY_DEFAULT": "1"}


def test_resolve_proxy_keeps_reachable_configured_proxy(reachable):
    env = {"HTTPS_PROXY": "http://corp:8080"}
    assert ne._resolve_proxy_for_env(env) == "proxy (configured)"
    assert env["HTTP_PROXY"] == "http://corp:8080"
    assert env["HTTPS_PROXY"] == "http://corp:8080"


def test_resolve_proxy_falls_back_to_direct_when_unreachable(unreachable, caplog):
    env = {"HTTP_PROXY": "http://dead:8080"}
    with caplog.at_level(logging.WARNING):
        assert ne._resolve_proxy_for_env(env) == "direct (proxy unreachable)"
    assert "HTTP_PROXY" not in env
    assert env["NO_PROXY_DEFAULT"] == "1"
    assert "unreachable" in caplog.text


def test_resolve_proxy_auto_detects_corp_proxy(monkeypatch):
    monkeypatch.delenv("AUTO_HTTP_PROXY", raising=False)
    monkeypatch.setattr(ne, "corp_proxy_reachable", lambda: True)
    env: dict[str, str] = {}
    assert ne._resolve_proxy_for_env(env) == "corp proxy (auto-detected)"
    assert env["HTTP_PROXY"] == ne.CORP_PROXY_URL


def test_resolve_proxy_direct_when_auto_detection_disabled(monkeypatch):
    monkeypatch.setenv("AUTO_HTTP_PROXY", "0")
    monkeypatch.setattr(ne, "corp_proxy_reachable", lambda: True)
    env: dict[str, str] = {}
    assert ne._resolve_proxy_for_env(env) == "direct"
    assert env["NO_PROXY_DEFAULT"] == "1"


def test_resolve_proxy_uses_provided_logger(monkeypatch):
    monkeypatch.setenv("AUTO_HTTP_PROXY", "0")
    log = logging.getLogger("test_network_env.custom")
    records: list[str] = []
    monkeypatch.setattr(log, "info", lambda msg, *args: records.append(msg % args))
    ne._resolve_proxy_for_env({}, log=log)
    assert records == ["Network: direct"]


def test_configure_http_proxy_mutates_process_env(monkeypatch):
    monkeypatch.setenv("AUTO_HTTP_PROXY", "0")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(key, raising=False)

    assert ne.configure_http_proxy() == "direct"
    assert os.environ["NO_PROXY_DEFAULT"] == "1"
    assert os.environ["NO_PROXY"] == ne.DEFAULT_NO_PROXY


def test_apply_http_proxy_to_env_copies_process_settings(monkeypatch):
    monkeypatch.setenv("AUTO_HTTP_PROXY", "0")
    monkeypatch.setenv("NO_PROXY", "example.com")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(key, raising=False)

    env: dict[str, str] = {}
    assert ne.apply_http_proxy_to_env(env) == "direct"
    assert env["NO_PROXY"] == "example.com"


def test_apply_http_proxy_to_env_validates_inherited_proxy(monkeypatch, unreachable):
    monkeypatch.setenv("HTTPS_PROXY", "http://dead:8080")
    env: dict[str, str] = {}
    assert ne.apply_http_proxy_to_env(env) == "direct (proxy unreachable)"
    assert "HTTPS_PROXY" not in env
