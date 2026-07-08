"""Gemini-first daily email builds with Gemini → OpenAI fallback."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from llm_providers import LLMVendor, email_summary_model, resolve_vendor, top_news_rank_model
from llm_providers import (
    GeminiTierEscalationError,
    reset_gemini_escalation_state,
)

T = TypeVar("T")


@dataclass
class VendorEmailMeta:
    vendor: LLMVendor
    model: str
    used_fallback: bool
    primary_vendor: LLMVendor
    rank_model: str | None = None
    fallback_tier: str | None = None  # None | "openai"


def primary_vendor() -> LLMVendor:
    return resolve_vendor(os.getenv("LLM_VENDOR_PRIMARY", os.getenv("LLM_VENDOR", "gemini")))


def fallback_vendor() -> LLMVendor:
    return resolve_vendor(os.getenv("LLM_VENDOR_FALLBACK", "openai"))


def fallback_enabled() -> bool:
    return os.getenv("LLM_VENDOR_FALLBACK_ENABLED", "1").lower() in ("1", "true", "yes")


def gemini_summary_model() -> str:
    return os.getenv("GEMINI_SUMMARY_MODEL", "gemini-3.1-flash-lite")


def gemini_rank_model() -> str:
    return os.getenv(
        "GEMINI_TOP_NEWS_RANK_MODEL",
        os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash"),
    )


def gemini_lite_model() -> str:
    """Summary model — used for within-build Lite→Flash escalation."""
    return os.getenv("GEMINI_LITE_MODEL", gemini_summary_model())


def gemini_flash_model() -> str:
    """Escalation target when summary Lite hits repeated 503/429."""
    return os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash")


def vendor_brand_name(vendor: LLMVendor) -> str:
    return "Gemini" if vendor is LLMVendor.GEMINI else "ChatGPT"


def vendor_email_label(meta: VendorEmailMeta) -> str:
    return vendor_brand_name(meta.vendor)


def vendor_email_footer_label(meta: VendorEmailMeta) -> str:
    """Footer: brand + summary model only."""
    return f"{vendor_brand_name(meta.vendor)} ({meta.model})"


def vendor_top_news_email_label(meta: VendorEmailMeta) -> str:
    """Header for 24h top-news — Gemini or ChatGPT only."""
    return vendor_brand_name(meta.vendor)


def vendor_top_news_footer_label(meta: VendorEmailMeta) -> str:
    """Footer for 24h top-news — brand + rank/summary models used."""
    brand = vendor_brand_name(meta.vendor)
    rank_model = meta.rank_model or meta.model
    summary_model = meta.model
    if rank_model != summary_model:
        return f"{brand} ({rank_model}, {summary_model})"
    return f"{brand} ({summary_model})"


def vendor_badge_text(meta: VendorEmailMeta) -> str:
    return vendor_brand_name(meta.vendor)


@dataclass(frozen=True)
class ModelBuildTier:
    vendor: str
    summary_model: str
    tier_name: str
    fallback_tier: str | None = None
    rank_model: str | None = None


def model_build_tiers() -> list[ModelBuildTier]:
    """Morning emails: Gemini (rank Flash + summary Lite) → OpenAI."""
    primary = primary_vendor()
    if primary is LLMVendor.OPENAI:
        openai_summary = email_summary_model(LLMVendor.OPENAI)
        openai_rank = top_news_rank_model(LLMVendor.OPENAI)
        return [
            ModelBuildTier(
                "openai",
                openai_summary,
                "openai",
                rank_model=openai_rank,
            )
        ]

    tiers = [
        ModelBuildTier(
            "gemini",
            gemini_summary_model(),
            "gemini",
            rank_model=gemini_rank_model(),
        ),
    ]
    if fallback_enabled() and fallback_vendor() is LLMVendor.OPENAI:
        tiers.append(
            ModelBuildTier(
                "openai",
                email_summary_model(LLMVendor.OPENAI),
                "openai",
                "openai",
                rank_model=top_news_rank_model(LLMVendor.OPENAI),
            )
        )
    return tiers


def _run_model_tiers(
    build_fn: Callable[[ModelBuildTier, VendorEmailMeta], T],
    *,
    logger: logging.Logger,
    label: str,
) -> tuple[T, VendorEmailMeta]:
    primary = primary_vendor()
    tiers = model_build_tiers()
    last_exc: Exception | None = None

    for index, tier in enumerate(tiers):
        if tier.tier_name == "gemini":
            reset_gemini_escalation_state()
        logger.info(
            "%s: trying tier %s (vendor=%s, summary=%s%s)",
            label,
            tier.tier_name,
            tier.vendor,
            tier.summary_model,
            f", rank={tier.rank_model}" if tier.rank_model else "",
        )
        resolved = resolve_vendor(tier.vendor)
        meta = VendorEmailMeta(
            vendor=resolved,
            model=tier.summary_model,
            rank_model=tier.rank_model,
            used_fallback=tier.fallback_tier is not None,
            primary_vendor=primary,
            fallback_tier=tier.fallback_tier,
        )
        try:
            return build_fn(tier, meta), meta
        except GeminiTierEscalationError as exc:
            last_exc = exc
            if index < len(tiers) - 1:
                logger.warning("%s: tier %s aborted — %s", label, tier.tier_name, exc)
            else:
                raise
        except Exception as exc:
            last_exc = exc
            if index < len(tiers) - 1:
                logger.warning(
                    "%s: tier %s failed (%s) — trying next tier",
                    label,
                    tier.tier_name,
                    exc,
                )
            else:
                raise

    raise RuntimeError(f"{label}: all tiers failed") from last_exc


def build_with_model_tier_fallback(
    build_fn: Callable[[str, str, VendorEmailMeta], T],
    *,
    logger: logging.Logger,
    label: str,
) -> tuple[T, VendorEmailMeta]:
    """Try Gemini, then OpenAI (daily news)."""

    def _wrap(tier: ModelBuildTier, meta: VendorEmailMeta) -> T:
        return build_fn(tier.vendor, tier.summary_model, meta)

    return _run_model_tiers(_wrap, logger=logger, label=label)


def build_with_top_news_tier_fallback(
    build_fn: Callable[[str, str, str, VendorEmailMeta], T],
    *,
    logger: logging.Logger,
    label: str,
) -> tuple[T, VendorEmailMeta]:
    """Try Gemini (rank Flash + summary Lite), then OpenAI (24h top-news)."""

    def _wrap(tier: ModelBuildTier, meta: VendorEmailMeta) -> T:
        rank_model = tier.rank_model or tier.summary_model
        return build_fn(tier.vendor, rank_model, tier.summary_model, meta)

    return _run_model_tiers(_wrap, logger=logger, label=label)


def require_api_keys_for_daily_emails() -> str | None:
    """Return error message if required keys are missing."""
    primary = primary_vendor()
    fallback = fallback_vendor()
    needs_openai = primary is LLMVendor.OPENAI or (
        fallback_enabled() and fallback is LLMVendor.OPENAI
    )
    needs_gemini = primary is LLMVendor.GEMINI or (
        fallback_enabled() and fallback is LLMVendor.GEMINI
    )
    if needs_openai and not os.getenv("OPENAI_API_KEY"):
        return "OPENAI_API_KEY is not set in .env"
    if needs_gemini and not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        return "GOOGLE_API_KEY is not set in .env"
    return None
