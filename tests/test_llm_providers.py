"""Unit tests for llm_providers (vendor selection, retries, quality scoring)."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

import llm_providers as lp


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "LLM_VENDOR",
        "GEMINI_GROUNDING",
        "GEMINI_SUMMARY_MODEL",
        "OPENAI_EMAIL_SUMMARY_MODEL",
        "GEMINI_TOP_NEWS_RANK_MODEL",
        "OPENAI_TOP_NEWS_RANK_MODEL",
        "GEMINI_FLASH_MODEL",
        "GEMINI_TOP_NEWS_FLASH_MODEL",
        "GEMINI_LITE_MAX_503_BEFORE_FLASH",
        "GEMINI_FLASH_MAX_503_BEFORE_OPENAI",
        "GEMINI_RETRY_DELAY_SECONDS",
        "GEMINI_MAX_RETRY_ATTEMPTS",
        "GEMINI_CALL_DELAY_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    lp.reset_gemini_escalation_state()
    monkeypatch.setattr(lp.time, "sleep", lambda seconds: None)


def _word_count(text: str) -> int:
    return len(text.split())


# --- vendor resolution ---------------------------------------------------


@pytest.mark.parametrize("raw", ["gemini", "GEMINI", " google "])
def test_resolve_vendor_gemini(raw):
    assert lp.resolve_vendor(raw) is lp.LLMVendor.GEMINI


@pytest.mark.parametrize("raw", ["openai", "anything-else", ""])
def test_resolve_vendor_defaults_to_openai(raw):
    assert lp.resolve_vendor(raw) is lp.LLMVendor.OPENAI


def test_resolve_vendor_reads_env(monkeypatch):
    monkeypatch.setenv("LLM_VENDOR", "gemini")
    assert lp.resolve_vendor() is lp.LLMVendor.GEMINI
    monkeypatch.delenv("LLM_VENDOR")
    assert lp.resolve_vendor() is lp.LLMVendor.OPENAI


def test_gemini_grounding_enabled(monkeypatch):
    assert lp.gemini_grounding_enabled() is True
    monkeypatch.setenv("GEMINI_GROUNDING", "0")
    assert lp.gemini_grounding_enabled() is False


def test_email_summary_model_defaults_and_overrides(monkeypatch):
    assert lp.email_summary_model(lp.LLMVendor.OPENAI) == lp.OPENAI_EMAIL_MODEL
    assert lp.email_summary_model(lp.LLMVendor.GEMINI) == lp.GEMINI_EMAIL_MODEL
    monkeypatch.setenv("OPENAI_EMAIL_SUMMARY_MODEL", "gpt-custom")
    monkeypatch.setenv("GEMINI_SUMMARY_MODEL", "gemini-custom")
    assert lp.email_summary_model(lp.LLMVendor.OPENAI) == "gpt-custom"
    assert lp.email_summary_model(lp.LLMVendor.GEMINI) == "gemini-custom"


def test_top_news_rank_model(monkeypatch):
    assert lp.top_news_rank_model(lp.LLMVendor.OPENAI) == "gpt-4.1"
    assert lp.top_news_rank_model(lp.LLMVendor.GEMINI) == "gemini-2.5-flash"
    monkeypatch.setenv("GEMINI_FLASH_MODEL", "gemini-flash-x")
    assert lp.top_news_rank_model(lp.LLMVendor.GEMINI) == "gemini-flash-x"
    monkeypatch.setenv("GEMINI_TOP_NEWS_RANK_MODEL", "gemini-rank")
    assert lp.top_news_rank_model(lp.LLMVendor.GEMINI) == "gemini-rank"


def test_vendor_display_names():
    assert lp.vendor_display_name(lp.LLMVendor.GEMINI) == "Gemini"
    assert lp.vendor_display_name(lp.LLMVendor.OPENAI) == "ChatGPT"
    assert lp.vendor_brand_name(lp.LLMVendor.GEMINI) == "Gemini"


def test_gemini_flash_model_name(monkeypatch):
    assert lp.gemini_flash_model_name() == "gemini-2.5-flash"
    monkeypatch.setenv("GEMINI_TOP_NEWS_FLASH_MODEL", "flash-fallback")
    assert lp.gemini_flash_model_name() == "flash-fallback"
    monkeypatch.setenv("GEMINI_FLASH_MODEL", "flash-primary")
    assert lp.gemini_flash_model_name() == "flash-primary"


# --- model tier detection ------------------------------------------------


@pytest.mark.parametrize("model", ["gemini-2.5-flash-lite", "GEMINI-2.0-LITE"])
def test_is_lite_model(model):
    assert lp._is_lite_model(model) is True
    assert lp._is_flash_model(model) is False


def test_is_flash_model():
    assert lp._is_flash_model("gemini-2.5-flash") is True
    assert lp._is_flash_model("gpt-4.1-mini") is False


def test_escalation_thresholds(monkeypatch):
    assert lp.lite_escalation_threshold() == 5
    assert lp.flash_escalation_threshold() == 5
    monkeypatch.setenv("GEMINI_LITE_MAX_503_BEFORE_FLASH", "0")
    monkeypatch.setenv("GEMINI_FLASH_MAX_503_BEFORE_OPENAI", "2")
    assert lp.lite_escalation_threshold() == 1
    assert lp.flash_escalation_threshold() == 2


def test_gemini_retry_delay_seconds(monkeypatch):
    assert lp.gemini_retry_delay_seconds() == 5
    monkeypatch.setenv("GEMINI_RETRY_DELAY_SECONDS", "0")
    assert lp.gemini_retry_delay_seconds() == 1


# --- escalation counters -------------------------------------------------


def test_record_lite_transient_failure_escalates_at_threshold(monkeypatch):
    monkeypatch.setenv("GEMINI_LITE_MAX_503_BEFORE_FLASH", "2")
    lp._record_lite_transient_failure("gemini-2.5-flash-lite")
    with pytest.raises(lp.GeminiTierEscalationError) as excinfo:
        lp._record_lite_transient_failure("gemini-2.5-flash-lite")
    assert excinfo.value.next_tier == "flash"


def test_record_lite_transient_failure_ignores_other_models(monkeypatch):
    monkeypatch.setenv("GEMINI_LITE_MAX_503_BEFORE_FLASH", "1")
    lp._record_lite_transient_failure("gemini-2.5-flash")  # not a lite model
    lp._record_lite_transient_failure("gpt-4.1-mini")


def test_record_flash_transient_failure_escalates_to_openai(monkeypatch):
    monkeypatch.setenv("GEMINI_FLASH_MAX_503_BEFORE_OPENAI", "1")
    with pytest.raises(lp.GeminiTierEscalationError) as excinfo:
        lp._record_flash_transient_failure("gemini-2.5-flash")
    assert excinfo.value.next_tier == "openai"


def test_reset_helpers_clear_counters(monkeypatch):
    monkeypatch.setenv("GEMINI_FLASH_MAX_503_BEFORE_OPENAI", "2")
    lp._record_flash_transient_failure("gemini-2.5-flash")
    lp.reset_gemini_flash_escalation()
    lp._record_flash_transient_failure("gemini-2.5-flash")  # counter restarted

    monkeypatch.setenv("GEMINI_LITE_MAX_503_BEFORE_FLASH", "2")
    lp._record_lite_transient_failure("gemini-2.5-flash-lite")
    lp.reset_gemini_lite_escalation()
    lp._record_lite_transient_failure("gemini-2.5-flash-lite")


# --- completion plumbing -------------------------------------------------


class FakeUsage:
    def __init__(self, prompt=11, completion=22, total=33):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class FakeOpenAIClient:
    def __init__(self, text="summary text", usage=None):
        self.calls: list[dict] = []
        self._text = text
        self._usage = usage if usage is not None else FakeUsage()
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = types.SimpleNamespace(content=self._text)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=message)], usage=self._usage
        )


def test_token_usage_from_openai():
    assert lp._token_usage_from_openai(None) == (0, 0, 0)
    assert lp._token_usage_from_openai(FakeUsage(1, 2, 3)) == (1, 2, 3)
    assert lp._token_usage_from_openai(FakeUsage(None, None, None)) == (0, 0, 0)


def test_complete_openai_builds_messages_and_result(monkeypatch):
    client = FakeOpenAIClient(text="  hello  ")
    monkeypatch.setattr(lp, "get_openai_client", lambda: client)

    result = lp._complete_openai(
        system_prompt="sys",
        user_message="user",
        model="gpt-test",
        json_response=True,
    )

    assert result.text == "hello"
    assert result.model == "gpt-test"
    assert result.vendor == "openai"
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (11, 22, 33)
    assert result.latency_ms >= 0
    kwargs = client.calls[0]
    assert kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]
    assert kwargs["response_format"] == {"type": "json_object"}


def test_complete_openai_appends_retry_turns(monkeypatch):
    client = FakeOpenAIClient()
    monkeypatch.setattr(lp, "get_openai_client", lambda: client)
    lp._complete_openai(
        system_prompt="sys",
        user_message="user",
        model="gpt-test",
        assistant_message="draft",
        retry_user_message="rewrite",
    )
    roles = [m["role"] for m in client.calls[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_complete_chat_routes_to_openai(monkeypatch):
    seen = {}

    def fake_openai(**kwargs):
        seen.update(kwargs)
        return lp.CompletionResult(text="ok", model=kwargs["model"])

    monkeypatch.setattr(lp, "_complete_openai", fake_openai)
    result = lp.complete_chat(
        vendor=lp.LLMVendor.OPENAI, system_prompt="s", user_message="u"
    )
    assert result.text == "ok"
    assert seen["model"] == lp.OPENAI_EMAIL_MODEL


def test_complete_chat_routes_to_gemini_with_fallback(monkeypatch):
    seen = {}

    def fake_gemini(**kwargs):
        seen.update(kwargs)
        return lp.CompletionResult(text="ok", model=kwargs["model"])

    monkeypatch.setattr(lp, "_complete_gemini_with_fallback", fake_gemini)
    lp.complete_chat(
        vendor=lp.LLMVendor.GEMINI,
        system_prompt="s",
        user_message="u",
        model="gemini-x",
        use_grounding=True,
    )
    assert seen["model"] == "gemini-x"
    assert seen["use_grounding"] is True


def test_complete_gemini_with_fallback_retries_transient_errors(monkeypatch):
    attempts = {"n": 0}

    def flaky(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("503 service unavailable")
        return lp.CompletionResult(text="ok", model=kwargs["model"])

    monkeypatch.setattr(lp, "_complete_gemini", flaky)
    result = lp._complete_gemini_with_fallback(
        system_prompt="s", user_message="u", model="gemini-2.5-pro"
    )
    assert result.text == "ok"
    assert attempts["n"] == 2


def test_complete_gemini_with_fallback_raises_after_attempts(monkeypatch):
    def always_429(**kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(lp, "_complete_gemini", always_429)
    with pytest.raises(RuntimeError, match="gemini-2.5-pro failed"):
        lp._complete_gemini_with_fallback(
            system_prompt="s", user_message="u", model="gemini-2.5-pro"
        )


def test_complete_gemini_with_fallback_does_not_retry_other_errors(monkeypatch):
    attempts = {"n": 0}

    def bad_request(**kwargs):
        attempts["n"] += 1
        raise ValueError("400 invalid argument")

    monkeypatch.setattr(lp, "_complete_gemini", bad_request)
    with pytest.raises(RuntimeError):
        lp._complete_gemini_with_fallback(
            system_prompt="s", user_message="u", model="gemini-2.5-pro"
        )
    assert attempts["n"] == 1


def test_complete_gemini_with_fallback_propagates_escalation(monkeypatch):
    monkeypatch.setenv("GEMINI_LITE_MAX_503_BEFORE_FLASH", "1")

    def unavailable(**kwargs):
        raise RuntimeError("503 unavailable")

    monkeypatch.setattr(lp, "_complete_gemini", unavailable)
    with pytest.raises(lp.GeminiTierEscalationError):
        lp._complete_gemini_with_fallback(
            system_prompt="s", user_message="u", model="gemini-2.5-flash-lite"
        )


# --- summarize_with_vendor ----------------------------------------------


@pytest.fixture
def fake_news_module(monkeypatch):
    """summarize_with_vendor imports TokenUsage from the heavy news module."""

    @dataclass
    class TokenUsage:
        input_tokens: int = 0
        output_tokens: int = 0
        total_tokens: int = 0

    module = types.ModuleType("news_headlines_api")
    module.TokenUsage = TokenUsage
    monkeypatch.setitem(sys.modules, "news_headlines_api", module)
    return module


def test_summarize_with_vendor_accepts_in_range_summary(monkeypatch, fake_news_module):
    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        return lp.CompletionResult(
            text="one two three four five",
            input_tokens=5,
            output_tokens=6,
            total_tokens=11,
            model="gpt-test",
        )

    monkeypatch.setattr(lp, "complete_chat", fake_complete)
    summary, tokens, result = lp.summarize_with_vendor(
        vendor=lp.LLMVendor.OPENAI,
        system_prompt="s",
        user_message="u",
        strip_urls_fn=lambda text: text,
        word_count_fn=_word_count,
        min_words=3,
        max_words=10,
    )
    assert summary == "one two three four five"
    assert tokens.total_tokens == 11
    assert result.model == "gpt-test"
    assert len(calls) == 1


def test_summarize_with_vendor_retries_when_out_of_range(monkeypatch, fake_news_module):
    responses = [
        lp.CompletionResult(text="too short", total_tokens=5, model="gpt-test", latency_ms=100),
        lp.CompletionResult(
            text="one two three four five", total_tokens=7, model="gpt-test", latency_ms=50
        ),
    ]
    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr(lp, "complete_chat", fake_complete)
    summary, tokens, result = lp.summarize_with_vendor(
        vendor=lp.LLMVendor.OPENAI,
        system_prompt="s",
        user_message="u",
        strip_urls_fn=lambda text: text,
        word_count_fn=_word_count,
        min_words=4,
        max_words=10,
    )
    assert summary == "one two three four five"
    assert tokens.total_tokens == 12
    assert result.latency_ms == 150
    assert len(calls) == 2
    assert calls[1]["assistant_message"] == "too short"


def test_summarize_with_vendor_keeps_first_summary_when_retry_is_empty(
    monkeypatch, fake_news_module
):
    responses = [
        lp.CompletionResult(text="too short", total_tokens=5, model="gpt-test"),
        lp.CompletionResult(text="", total_tokens=1, model="gpt-test"),
    ]
    monkeypatch.setattr(lp, "complete_chat", lambda **kwargs: responses.pop(0))
    summary, tokens, _ = lp.summarize_with_vendor(
        vendor=lp.LLMVendor.OPENAI,
        system_prompt="s",
        user_message="u",
        strip_urls_fn=lambda text: text,
        word_count_fn=_word_count,
        min_words=4,
        max_words=10,
    )
    assert summary == "too short"
    assert tokens.total_tokens == 6


def test_summarize_with_vendor_grounding_only_for_gemini(monkeypatch, fake_news_module):
    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        return lp.CompletionResult(text="one two three", model="m")

    monkeypatch.setattr(lp, "complete_chat", fake_complete)
    lp.summarize_with_vendor(
        vendor=lp.LLMVendor.OPENAI,
        system_prompt="s",
        user_message="u",
        strip_urls_fn=lambda text: text,
        word_count_fn=_word_count,
        min_words=1,
        max_words=10,
        use_grounding=True,
    )
    assert calls[0]["use_grounding"] is False


# --- scoring ------------------------------------------------------------


def test_hebrew_ratio():
    assert lp.hebrew_ratio("") == 0.0
    assert lp.hebrew_ratio("12345 !!") == 0.0
    assert lp.hebrew_ratio("שלום") == 1.0
    assert lp.hebrew_ratio("abcd") == 0.0
    assert lp.hebrew_ratio("שלום abc") == pytest.approx(4 / 7)


def test_objective_quality_score_rewards_hebrew_overlap_and_length():
    title = "ראש הממשלה נפגש עם שרי האוצר"
    snippet = "הפגישה עסקה בתקציב הביטחון ובמשק"
    summary = "ראש הממשלה נפגש עם שרי האוצר ודנו בתקציב הביטחון ובמשק הישראלי לקראת ההצבעה"
    score = lp.objective_quality_score(
        summary=summary,
        title=title,
        snippet=snippet,
        min_words=8,
        max_words=20,
        word_count_fn=_word_count,
    )
    assert score["word_count"] == _word_count(summary)
    assert score["in_word_range"] is True
    assert score["hebrew_ratio"] == 1.0
    assert score["source_overlap"] > 0.5
    assert score["no_fluff"] is True
    assert score["total"] > 70


def test_objective_quality_score_penalizes_fluff_and_wrong_length():
    score = lp.objective_quality_score(
        summary="רכילות סנסציונית",
        title="ראש הממשלה נפגש עם שרי האוצר",
        snippet="הפגישה עסקה בתקציב",
        min_words=50,
        max_words=80,
        word_count_fn=_word_count,
    )
    assert score["in_word_range"] is False
    assert score["no_fluff"] is False
    assert score["total"] < 50


def test_objective_quality_score_caps_at_100():
    text = "מילה " * 30
    score = lp.objective_quality_score(
        summary=text,
        title="מילה",
        snippet="מילה",
        min_words=10,
        max_words=40,
        word_count_fn=_word_count,
    )
    assert score["total"] <= 100
