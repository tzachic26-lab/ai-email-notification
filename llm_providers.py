"""Unified LLM providers for Hebrew news summarization (OpenAI + Gemini)."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from openai import OpenAI

if TYPE_CHECKING:
    from news_headlines_api import TokenUsage

logger = logging.getLogger(__name__)

_openai_client: OpenAI | None = None
_gemini_client = None

OPENAI_EMAIL_MODEL = "gpt-4.1-mini"
GEMINI_EMAIL_MODEL = os.getenv("GEMINI_SUMMARY_MODEL", "gemini-2.5-flash")


class GeminiTierEscalationError(RuntimeError):
    """Too many transient Gemini failures — abort tier and restart on the next tier."""

    def __init__(self, next_tier: str, message: str):
        self.next_tier = next_tier
        super().__init__(message)


# Count 503/429 hits during one tier build; abort tier and restart on next model tier.
_gemini_lite_transient_failures = 0
_gemini_flash_transient_failures = 0


class LLMVendor(str, Enum):
    OPENAI = "openai"
    GEMINI = "gemini"


@dataclass
class CompletionResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    vendor: str = ""
    latency_ms: int = 0


def get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        insecure = os.getenv("OPENAI_INSECURE_TLS", "").strip().lower() in ("1", "true", "yes")
        if insecure:
            import httpx

            _openai_client = OpenAI(http_client=httpx.Client(verify=False))
        else:
            _openai_client = OpenAI()
    return _openai_client


def get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai

        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY (or GEMINI_API_KEY) is not set in .env")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def resolve_vendor(raw: str | None = None) -> LLMVendor:
    value = (raw or os.getenv("LLM_VENDOR", "openai")).strip().lower()
    if value in ("gemini", "google"):
        return LLMVendor.GEMINI
    return LLMVendor.OPENAI


def gemini_grounding_enabled() -> bool:
    return os.getenv("GEMINI_GROUNDING", "1").lower() in ("1", "true", "yes")


GEMINI_GROUNDING_PROMPT_ADDENDUM = """
Use Google Search to find the latest verified facts about this specific story.
Ground every claim in search results and the provided snippet. If search and snippet disagree, prefer the snippet and note uncertainty.
Do not invent names, titles, numbers, or quotes not supported by search or the snippet."""


def email_summary_model(vendor: LLMVendor) -> str:
    if vendor is LLMVendor.GEMINI:
        return os.getenv("GEMINI_SUMMARY_MODEL", GEMINI_EMAIL_MODEL)
    return os.getenv("OPENAI_EMAIL_SUMMARY_MODEL", OPENAI_EMAIL_MODEL)


def top_news_rank_model(vendor: LLMVendor) -> str:
    if vendor is LLMVendor.GEMINI:
        return os.getenv(
            "GEMINI_TOP_NEWS_RANK_MODEL",
            os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash"),
        )
    return os.getenv("OPENAI_TOP_NEWS_RANK_MODEL", "gpt-4.1")


def vendor_display_name(vendor: LLMVendor) -> str:
    return vendor_brand_name(vendor)


def vendor_brand_name(vendor: LLMVendor) -> str:
    return "Gemini" if vendor is LLMVendor.GEMINI else "ChatGPT"


def _token_usage_from_openai(usage) -> tuple[int, int, int]:
    if not usage:
        return 0, 0, 0
    return usage.prompt_tokens or 0, usage.completion_tokens or 0, usage.total_tokens or 0


def _complete_openai(
    *,
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    assistant_message: str | None = None,
    retry_user_message: str | None = None,
    json_response: bool = False,
) -> CompletionResult:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    if assistant_message and retry_user_message:
        messages.extend(
            [
                {"role": "assistant", "content": assistant_message},
                {"role": "user", "content": retry_user_message},
            ]
        )

    started = time.perf_counter()
    create_kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_response:
        create_kwargs["response_format"] = {"type": "json_object"}
    response = get_openai_client().chat.completions.create(**create_kwargs)
    latency_ms = int((time.perf_counter() - started) * 1000)
    text = (response.choices[0].message.content or "").strip()
    inp, out, total = _token_usage_from_openai(response.usage)
    return CompletionResult(
        text=text,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
        model=model,
        vendor=LLMVendor.OPENAI.value,
        latency_ms=latency_ms,
    )


def _complete_gemini(
    *,
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    assistant_message: str | None = None,
    retry_user_message: str | None = None,
    use_grounding: bool = False,
    json_response: bool = False,
) -> CompletionResult:
    from google.genai import types

    client = get_gemini_client()
    contents: list = [
        types.Content(
            role="user",
            parts=[types.Part(text=f"{system_prompt}\n\n{user_message}")],
        )
    ]
    if assistant_message and retry_user_message:
        contents.append(
            types.Content(role="model", parts=[types.Part(text=assistant_message)])
        )
        contents.append(
            types.Content(role="user", parts=[types.Part(text=retry_user_message)])
        )

    config_kwargs: dict = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "thinking_config": types.ThinkingConfig(thinking_budget=0),
    }
    if use_grounding:
        config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    if json_response:
        config_kwargs["response_mime_type"] = "application/json"

    config = types.GenerateContentConfig(**config_kwargs)

    started = time.perf_counter()
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    text = (response.text or "").strip()

    usage = getattr(response, "usage_metadata", None)
    inp = getattr(usage, "prompt_token_count", 0) or 0
    out = getattr(usage, "candidates_token_count", 0) or 0
    total = getattr(usage, "total_token_count", 0) or (inp + out)

    return CompletionResult(
        text=text,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
        model=model,
        vendor=LLMVendor.GEMINI.value,
        latency_ms=latency_ms,
    )


def gemini_flash_model_name() -> str:
    return os.getenv(
        "GEMINI_FLASH_MODEL",
        os.getenv("GEMINI_TOP_NEWS_FLASH_MODEL", "gemini-2.5-flash"),
    )


def _is_lite_model(model: str) -> bool:
    return "flash-lite" in model.lower() or model.lower().endswith("-lite")


def _is_flash_model(model: str) -> bool:
    return "flash" in model.lower() and not _is_lite_model(model)


def lite_escalation_threshold() -> int:
    return max(1, int(os.getenv("GEMINI_LITE_MAX_503_BEFORE_FLASH", "5")))


def flash_escalation_threshold() -> int:
    return max(1, int(os.getenv("GEMINI_FLASH_MAX_503_BEFORE_OPENAI", "5")))


def reset_gemini_escalation_state() -> None:
    """Reset all Gemini escalation counters (start of Lite-tier build)."""
    global _gemini_lite_transient_failures, _gemini_flash_transient_failures
    _gemini_lite_transient_failures = 0
    _gemini_flash_transient_failures = 0


def reset_gemini_lite_escalation() -> None:
    """Reset Lite 503 counter (call at the start of each Lite-tier email build)."""
    reset_gemini_escalation_state()


def reset_gemini_flash_escalation() -> None:
    """Reset Flash 503 counter (start of Flash-tier build)."""
    global _gemini_flash_transient_failures
    _gemini_flash_transient_failures = 0


def _record_lite_transient_failure(model: str) -> None:
    global _gemini_lite_transient_failures
    if not _is_lite_model(model):
        return
    _gemini_lite_transient_failures += 1
    threshold = lite_escalation_threshold()
    if _gemini_lite_transient_failures >= threshold:
        raise GeminiTierEscalationError(
            "flash",
            f"Gemini Lite hit {threshold} transient failures (503/429); "
            f"restarting from scratch with Flash tier ({gemini_flash_model_name()})",
        )


def _record_flash_transient_failure(model: str) -> None:
    global _gemini_flash_transient_failures
    if not _is_flash_model(model):
        return
    _gemini_flash_transient_failures += 1
    threshold = flash_escalation_threshold()
    if _gemini_flash_transient_failures >= threshold:
        raise GeminiTierEscalationError(
            "openai",
            f"Gemini Flash hit {threshold} transient failures (503/429); "
            "restarting from scratch with OpenAI tier",
        )


def gemini_retry_delay_seconds() -> int:
    return max(1, int(os.getenv("GEMINI_RETRY_DELAY_SECONDS", "5")))


def _complete_gemini_with_fallback(**kwargs) -> CompletionResult:
    model = kwargs.pop("model")
    max_attempts = max(1, int(os.getenv("GEMINI_MAX_RETRY_ATTEMPTS", "2")))
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return _complete_gemini(model=model, **kwargs)
        except GeminiTierEscalationError:
            raise
        except Exception as exc:
            last_error = exc
            msg = str(exc).lower()
            if "429" in msg or "resource_exhausted" in msg or "503" in msg:
                _record_lite_transient_failure(model)
                _record_flash_transient_failure(model)
                if attempt + 1 < max_attempts:
                    time.sleep(gemini_retry_delay_seconds())
                continue
            break
    raise RuntimeError(f"Gemini model {model} failed: {last_error}") from last_error


def complete_chat(
    *,
    vendor: LLMVendor,
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    assistant_message: str | None = None,
    retry_user_message: str | None = None,
    use_grounding: bool = False,
    json_response: bool = False,
) -> CompletionResult:
    resolved_model = model or email_summary_model(vendor)
    if vendor is LLMVendor.GEMINI:
        return _complete_gemini_with_fallback(
            system_prompt=system_prompt,
            user_message=user_message,
            model=resolved_model,
            max_tokens=max_tokens,
            temperature=temperature,
            assistant_message=assistant_message,
            retry_user_message=retry_user_message,
            use_grounding=use_grounding,
            json_response=json_response,
        )
    return _complete_openai(
        system_prompt=system_prompt,
        user_message=user_message,
        model=resolved_model,
        max_tokens=max_tokens,
        temperature=temperature,
        assistant_message=assistant_message,
        retry_user_message=retry_user_message,
        json_response=json_response,
    )


def summarize_with_vendor(
    *,
    vendor: LLMVendor,
    system_prompt: str,
    user_message: str,
    strip_urls_fn,
    word_count_fn,
    min_words: int,
    max_words: int,
    model: str | None = None,
    use_grounding: bool = False,
) -> tuple[str, "TokenUsage", CompletionResult]:
    from news_headlines_api import TokenUsage

    if vendor is LLMVendor.GEMINI:
        time.sleep(int(os.getenv("GEMINI_CALL_DELAY_SECONDS", "5")))

    result = complete_chat(
        vendor=vendor,
        system_prompt=system_prompt,
        user_message=user_message,
        model=model,
        use_grounding=use_grounding and vendor is LLMVendor.GEMINI,
    )
    summary = strip_urls_fn(result.text)
    tokens = TokenUsage(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
    )

    word_count = word_count_fn(summary)
    if summary and (word_count < min_words or word_count > max_words):
        retry = complete_chat(
            vendor=vendor,
            system_prompt=system_prompt,
            user_message=user_message,
            model=result.model,
            assistant_message=summary,
            retry_user_message=(
                f"הסיכום הנוכחי הוא {word_count} מילים. "
                f"כתוב מחדש בעברית בין {min_words} ל-{max_words} מילים. "
                "היה מדויק, מפורט, צמוד לתוכן המקור — ללא רכילות, סנסציות או מילוי."
            ),
            use_grounding=use_grounding,
        )
        retry_summary = strip_urls_fn(retry.text)
        if retry_summary:
            summary = retry_summary
            word_count = word_count_fn(summary)
        tokens = TokenUsage(
            input_tokens=tokens.input_tokens + retry.input_tokens,
            output_tokens=tokens.output_tokens + retry.output_tokens,
            total_tokens=tokens.total_tokens + retry.total_tokens,
        )
        result.latency_ms += retry.latency_ms

    return summary, tokens, result


_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def hebrew_ratio(text: str) -> float:
    if not text:
        return 0.0
    hebrew = len(_HEBREW_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    denom = hebrew + latin
    return hebrew / denom if denom else 0.0


def objective_quality_score(
    *,
    summary: str,
    title: str,
    snippet: str,
    min_words: int,
    max_words: int,
    word_count_fn,
) -> dict[str, float | int | str]:
    word_count = word_count_fn(summary)
    in_range = min_words <= word_count <= max_words

    title_tokens = {t for t in re.findall(r"[\u0590-\u05FF]{3,}", title) if len(t) >= 3}
    snippet_tokens = {t for t in re.findall(r"[\u0590-\u05FF]{3,}", snippet) if len(t) >= 3}
    summary_tokens = set(re.findall(r"[\u0590-\u05FF]{3,}", summary))
    ref_tokens = title_tokens | snippet_tokens
    overlap = len(ref_tokens & summary_tokens) / max(len(ref_tokens), 1)

    heb = hebrew_ratio(summary)
    has_fluff = bool(
        re.search(
            r"רכילות|סנסצי|לא תאמינו|ויראל|clickbait|gossip",
            summary,
            re.IGNORECASE,
        )
    )

    score = 0.0
    score += 25 if in_range else max(0, 25 - abs(word_count - (min_words + max_words) // 2) * 0.15)
    score += min(25, heb * 25)
    score += min(25, overlap * 35)
    score += 15 if not has_fluff else 0
    score += 10 if word_count >= min_words * 0.85 else 0

    return {
        "total": round(min(100, score), 1),
        "word_count": word_count,
        "in_word_range": in_range,
        "hebrew_ratio": round(heb, 3),
        "source_overlap": round(overlap, 3),
        "no_fluff": not has_fluff,
    }
