"""Generate daily AI trainer exercises with a deep-reasoning LLM."""

from __future__ import annotations

import html
import json
import logging
import os
import re
from dataclasses import dataclass

from llm_providers import LLMVendor, complete_chat, resolve_vendor, vendor_brand_name

logger = logging.getLogger(__name__)

TRAINER_SYSTEM_PROMPT = """You are an elite AI technical coach and curriculum designer for a senior engineer.

Your job each day: design ONE fresh, hands-on exercise that builds real AI engineering capability — not trivia.

Design principles:
- Prefer current methodologies (2025–2026): agents, MCP/tool use, RAG evals, structured outputs, observability, guardrails, fine-tuning tradeoffs, multimodal pipelines, cost/latency optimization, prompt caching, synthetic data, etc.
- Include concrete tools/frameworks that deliver professional value (e.g. LangGraph, LlamaIndex, Cursor, OpenAI/Anthropic/Gemini APIs, LangSmith, Weights & Biases, vLLM, Ollama, Hugging Face, pydantic-ai, Instructor, etc.) — vary tools across days.
- Mix theory-with-practice: the learner must BUILD, MEASURE, or DECIDE something — not just read.
- Difficulty should challenge but fit a 30–90 minute session.
- Be specific: filenames, API calls, metrics, acceptance checks.
- English only for all content fields.

Anti-repetition (critical):
- You receive prior exercise history. Never reuse the same core task, category focus, primary tool stack, or learning objective.
- Pick a different subdomain of AI engineering than recent days.

Respond with JSON only (no markdown fences):
{
  "id": "kebab-case unique slug for this exercise",
  "title": "short compelling title",
  "category": "one of: Prompt Engineering | Agents & Tools | RAG & Retrieval | Evals & Observability | Fine-tuning & Adaptation | Production & MLOps | Multimodal | Security & Safety | Architecture & Design | Cost & Performance",
  "difficulty": "beginner | intermediate | advanced",
  "estimated_minutes": 45,
  "tools": ["tool1", "tool2"],
  "trend_context": "2-4 sentences on why this matters now and what trend it connects to",
  "exercise_steps": ["step 1", "step 2", "..."],
  "deliverable": "what the learner submits or produces",
  "success_criteria": ["measurable criterion 1", "..."],
  "stretch_goal": "optional harder extension",
  "resources": [{"title": "...", "url": "https://..."}],
  "skills_built": ["skill1", "skill2"]
}"""


@dataclass
class TrainerExercise:
    id: str
    title: str
    category: str
    difficulty: str
    estimated_minutes: int
    tools: list[str]
    trend_context: str
    exercise_steps: list[str]
    deliverable: str
    success_criteria: list[str]
    stretch_goal: str
    resources: list[dict[str, str]]
    skills_built: list[str]
    model: str
    vendor: str


def trainer_vendor() -> LLMVendor:
    raw = os.getenv("AI_TRAINER_VENDOR", "openai")
    return resolve_vendor(raw)


def trainer_fallback_vendor() -> LLMVendor | None:
    if os.getenv("AI_TRAINER_VENDOR_FALLBACK_ENABLED", "1").lower() not in ("1", "true", "yes"):
        return None
    raw = os.getenv("AI_TRAINER_VENDOR_FALLBACK", "gemini")
    fallback = resolve_vendor(raw)
    if fallback is trainer_vendor():
        return None
    return fallback


def trainer_model(vendor: LLMVendor | None = None) -> str:
    v = vendor or trainer_vendor()
    if v is LLMVendor.GEMINI:
        return os.getenv("AI_TRAINER_MODEL", "gemini-2.5-pro")
    return os.getenv("AI_TRAINER_MODEL", "gpt-4.1")


def _parse_exercise_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _to_exercise(data: dict, *, model: str, vendor: str) -> TrainerExercise:
    return TrainerExercise(
        id=str(data.get("id", "")).strip(),
        title=str(data.get("title", "")).strip(),
        category=str(data.get("category", "")).strip(),
        difficulty=str(data.get("difficulty", "intermediate")).strip(),
        estimated_minutes=int(data.get("estimated_minutes", 45)),
        tools=[str(t) for t in data.get("tools", [])],
        trend_context=str(data.get("trend_context", "")).strip(),
        exercise_steps=[str(s) for s in data.get("exercise_steps", [])],
        deliverable=str(data.get("deliverable", "")).strip(),
        success_criteria=[str(c) for c in data.get("success_criteria", [])],
        stretch_goal=str(data.get("stretch_goal", "")).strip(),
        resources=[r for r in data.get("resources", []) if isinstance(r, dict)],
        skills_built=[str(s) for s in data.get("skills_built", [])],
        model=model,
        vendor=vendor,
    )


def _is_duplicate(exercise: TrainerExercise, existing_ids: set[str], existing_titles: set[str]) -> bool:
    slug = exercise.id.lower()
    title = exercise.title.lower()
    if slug and slug in existing_ids:
        return True
    if title and title in existing_titles:
        return True
    return False


def exercise_from_record(record) -> TrainerExercise:
    from ai_trainer_store import (
        _markdown_section,
        _parse_generated_by,
        _parse_list_lines,
        _parse_resources,
        _FIELD_RE,
    )

    body = record.markdown_body
    fields = {m.group("key").strip().lower(): m.group("value").strip() for m in _FIELD_RE.finditer(body)}
    minutes_raw = fields.get("estimated time", "45 min")
    minutes_match = re.search(r"\d+", minutes_raw)
    estimated_minutes = int(minutes_match.group()) if minutes_match else 45
    tools = [t.strip() for t in fields.get("tools", "").split(",") if t.strip()]
    skills = [s.strip() for s in _markdown_section(body, "Skills built").split(",") if s.strip()]
    model, vendor = _parse_generated_by(body)

    return TrainerExercise(
        id=record.exercise_id or fields.get("id", ""),
        title=record.title,
        category=record.category,
        difficulty=record.difficulty,
        estimated_minutes=estimated_minutes,
        tools=tools,
        trend_context=_markdown_section(body, "Trend / Why now"),
        exercise_steps=_parse_list_lines(_markdown_section(body, "Exercise"), numbered=True),
        deliverable=_markdown_section(body, "Deliverable"),
        success_criteria=_parse_list_lines(_markdown_section(body, "Success criteria")),
        stretch_goal=_markdown_section(body, "Stretch goal"),
        resources=_parse_resources(_markdown_section(body, "Resources")),
        skills_built=skills,
        model=model,
        vendor=vendor,
    )


def include_history_in_email() -> bool:
    return os.getenv("AI_TRAINER_INCLUDE_HISTORY_IN_EMAIL", "0").lower() in ("1", "true", "yes")


def history_rows_in_email() -> int:
    return max(0, int(os.getenv("AI_TRAINER_HISTORY_IN_EMAIL_MAX", "5")))


def _is_transient_llm_error(exc: BaseException) -> bool:
    try:
        from openai import APIConnectionError, APITimeoutError, RateLimitError

        if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
            return True
    except ImportError:
        pass
    try:
        import httpx

        if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
            return True
    except ImportError:
        pass
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "connection error",
            "connecterror",
            "getaddrinfo failed",
            "timed out",
            "timeout",
            "503",
            "429",
            "temporarily unavailable",
        )
    )


def _complete_trainer_chat(
    *,
    vendor: LLMVendor,
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    temperature: float,
    use_grounding: bool,
    json_response: bool,
    assistant_message: str | None = None,
    retry_user_message: str | None = None,
):
    vendors: list[LLMVendor] = [vendor]
    fallback = trainer_fallback_vendor()
    if fallback is not None:
        vendors.append(fallback)

    last_error: BaseException | None = None
    for attempt_vendor in vendors:
        try:
            return complete_chat(
                vendor=attempt_vendor,
                system_prompt=system_prompt,
                user_message=user_message,
                model=trainer_model(attempt_vendor),
                max_tokens=max_tokens,
                temperature=temperature,
                assistant_message=assistant_message,
                retry_user_message=retry_user_message,
                use_grounding=use_grounding and attempt_vendor is LLMVendor.GEMINI,
                json_response=json_response,
            )
        except Exception as exc:
            if attempt_vendor is not vendors[-1] and _is_transient_llm_error(exc):
                logger.warning(
                    "AI trainer %s failed (%s) — trying %s",
                    attempt_vendor.value,
                    exc,
                    vendors[-1].value,
                )
                last_error = exc
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("AI trainer LLM call failed")


def generate_trainer_exercise(
    *,
    iso_date: str,
    history_context: str,
    existing_ids: set[str],
    existing_titles: set[str],
) -> TrainerExercise:
    vendor = trainer_vendor()
    grounding_enabled = os.getenv("AI_TRAINER_GROUNDING", "1").lower() in (
        "1",
        "true",
        "yes",
    )

    user_message = f"""Today: {iso_date}

{history_context}

Design today's exercise. It must be completely new compared to the history above.
Return valid JSON only."""

    result = _complete_trainer_chat(
        vendor=vendor,
        system_prompt=TRAINER_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=int(os.getenv("AI_TRAINER_MAX_TOKENS", "4096")),
        temperature=float(os.getenv("AI_TRAINER_TEMPERATURE", "0.65")),
        use_grounding=grounding_enabled,
        json_response=True,
    )
    exercise = _to_exercise(_parse_exercise_json(result.text), model=result.model, vendor=result.vendor)

    if _is_duplicate(exercise, existing_ids, existing_titles):
        retry = _complete_trainer_chat(
            vendor=vendor,
            system_prompt=TRAINER_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=int(os.getenv("AI_TRAINER_MAX_TOKENS", "4096")),
            temperature=0.75,
            assistant_message=result.text,
            retry_user_message=(
                "Your proposal duplicates a prior exercise (same id, title, or core focus). "
                "Pick a different category, tool stack, and learning objective. Return JSON only."
            ),
            use_grounding=grounding_enabled,
            json_response=True,
        )
        exercise = _to_exercise(_parse_exercise_json(retry.text), model=retry.model, vendor=retry.vendor)

    if not exercise.title or not exercise.exercise_steps:
        raise RuntimeError("Trainer model returned an incomplete exercise")

    return exercise


def model_display_label(exercise: TrainerExercise) -> str:
    brand = vendor_brand_name(LLMVendor(exercise.vendor))
    return f"{brand} ({exercise.model})"


def _esc(text: str) -> str:
    return html.escape(text)


def _list_items(items: list[str]) -> str:
    if not items:
        return "<li>—</li>"
    return "".join(f"<li style='margin-bottom:8px;'>{_esc(i)}</li>" for i in items)


def _resource_links(resources: list[dict[str, str]]) -> str:
    if not resources:
        return "<li>—</li>"
    parts = []
    for r in resources:
        title = _esc(r.get("title", "Link"))
        url = _esc(r.get("url", "#"))
        parts.append(
            f"<li style='margin-bottom:6px;'>"
            f"<a href='{url}' style='color:#2563eb;text-decoration:none;'>{title}</a>"
            f"</li>"
        )
    return "".join(parts)


def format_trainer_email_html(
    *,
    iso_date: str,
    exercise: TrainerExercise,
    history_records: list,
) -> str:
    model_label = _esc(model_display_label(exercise))
    tools = _esc(", ".join(exercise.tools))
    skills = _esc(", ".join(exercise.skills_built))
    steps_html = "".join(
        f"<li style='margin-bottom:10px;'>{_esc(step)}</li>" for step in exercise.exercise_steps
    ) or "<li>—</li>"
    criteria_html = _list_items(exercise.success_criteria)
    resources_html = _resource_links(exercise.resources)
    session_num = len(history_records)

    history_block = ""
    if include_history_in_email() and history_rows_in_email() > 0:
        history_rows = ""
        for rec in reversed(history_records[-history_rows_in_email() :]):
            history_rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#374151;white-space:nowrap;">{_esc(rec.iso_date)}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#6b7280;">{_esc(rec.category)}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#111827;">{_esc(rec.title)}</td>
        </tr>"""
        history_block = f"""
              <hr style="border:none;border-top:1px solid #e5e7eb;margin:32px 0;">
              <h2 style="margin:0 0 6px;font-size:18px;color:#111827;">Recent exercises</h2>
              <p style="margin:0 0 16px;font-size:13px;color:#6b7280;">Last {min(history_rows_in_email(), session_num)} sessions</p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
                <tr style="background:#f3f4f6;">
                  <th style="padding:10px 12px;text-align:left;font-size:11px;color:#6b7280;text-transform:uppercase;">Date</th>
                  <th style="padding:10px 12px;text-align:left;font-size:11px;color:#6b7280;text-transform:uppercase;">Category</th>
                  <th style="padding:10px 12px;text-align:left;font-size:11px;color:#6b7280;text-transform:uppercase;">Title</th>
                </tr>
                {history_rows}
              </table>"""
    else:
        history_block = f"""
              <p style="margin:32px 0 0;font-size:13px;color:#9ca3af;text-align:center;">
                Session {session_num} · Topics are tracked internally so each day stays fresh.
              </p>"""

    return f"""<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Trainer — { _esc(iso_date) }</title>
</head>
<body style="margin:0;padding:0;background-color:#f0fdf4;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#f0fdf4;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="640" cellspacing="0" cellpadding="0" border="0"
               style="max-width:640px;width:100%;background:#ffffff;border-radius:16px;border:1px solid #bbf7d0;overflow:hidden;">
          <tr>
            <td style="padding:36px;background:linear-gradient(135deg,#065f46 0%,#059669 100%);">
              <p style="margin:0 0 8px;font-family:Segoe UI,Arial,sans-serif;font-size:12px;letter-spacing:0.08em;text-transform:uppercase;color:rgba(255,255,255,0.85);">
                Daily AI Trainer
              </p>
              <h1 style="margin:0 0 10px;font-family:Segoe UI,Arial,sans-serif;font-size:26px;color:#ffffff;font-weight:700;">
                {_esc(exercise.title)}
              </h1>
              <p style="margin:0;font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:rgba(255,255,255,0.9);">
                { _esc(iso_date) } · {_esc(exercise.category)} · {_esc(exercise.difficulty)} · ~{exercise.estimated_minutes} min
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 32px;font-family:Segoe UI,Arial,sans-serif;">
              <p style="margin:0 0 6px;font-size:12px;font-weight:600;color:#059669;text-transform:uppercase;">Trend / Why now</p>
              <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#374151;">{_esc(exercise.trend_context)}</p>

              <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#059669;text-transform:uppercase;">Today's exercise</p>
              <ol style="margin:0 0 24px;padding-left:20px;font-size:15px;line-height:1.65;color:#111827;">
                {steps_html}
              </ol>

              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:24px;background:#f9fafb;border-radius:12px;border:1px solid #e5e7eb;">
                <tr>
                  <td style="padding:16px 20px;">
                    <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#6b7280;">DELIVERABLE</p>
                    <p style="margin:0;font-size:14px;line-height:1.55;color:#111827;">{_esc(exercise.deliverable)}</p>
                  </td>
                </tr>
              </table>

              <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#059669;text-transform:uppercase;">Success criteria</p>
              <ul style="margin:0 0 24px;padding-left:20px;font-size:14px;line-height:1.6;color:#374151;">
                {criteria_html}
              </ul>

              <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#059669;text-transform:uppercase;">Stretch goal</p>
              <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#374151;">{_esc(exercise.stretch_goal)}</p>

              <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#059669;text-transform:uppercase;">Tools & skills</p>
              <p style="margin:0 0 4px;font-size:14px;color:#374151;"><strong>Tools:</strong> {tools}</p>
              <p style="margin:0 0 24px;font-size:14px;color:#374151;"><strong>Skills built:</strong> {skills}</p>

              <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#059669;text-transform:uppercase;">Resources</p>
              <ul style="margin:0 0 32px;padding-left:20px;font-size:14px;">
                {resources_html}
              </ul>

              {history_block}

              <p style="margin:24px 0 0;font-size:12px;color:#9ca3af;text-align:center;">
                Generated by {model_label} · AI Trainer Agent
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
