from __future__ import annotations

import httpx

from projects.exoplanet.db.models import Candidate, Target, get_db_session, init_db
from projects.exoplanet.settings import get_exoplanet_settings
from research_platform.core.config import get_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"

EXPERT_SYSTEM_PROMPT = (
    "You are an expert exoplanet astrophysicist and citizen-science mentor. "
    "You help review transit/periodogram candidates from TESS and Kepler. "
    "Be precise, skeptical of false positives (EB blends, systematics, stellar "
    "variability), and give actionable follow-up checks. "
    "Use plain language suitable for a skilled non-specialist reviewer. "
    "When data is labeled synthetic, say so explicitly and do not pretend it is "
    "flight photometry."
)


def _openrouter_api_key() -> str:
    exo = get_exoplanet_settings()
    platform = get_settings()
    return exo.openrouter_api_key or platform.openrouter_api_key


def _model_name() -> str:
    return get_exoplanet_settings().llm_model or DEFAULT_MODEL


def chat_exoplanet_expert(
    user_content: str,
    *,
    max_tokens: int = 500,
    temperature: float = 0.2,
) -> tuple[str | None, str]:
    """
    Call the shared exoplanet expert via OpenRouter.

    Returns (content, source) where source is 'llm' or 'unavailable'.
    """
    api_key = _openrouter_api_key()
    if not api_key:
        logger.info("exoplanet_expert_skipped", reason="no_openrouter_key")
        return None, "unavailable"

    payload = {
        "model": _model_name(),
        "messages": [
            {"role": "system", "content": EXPERT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://research-platform.local/exoplanet",
        "X-Title": "Research Platform Exoplanet Expert",
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(OPENROUTER_URL, json=payload, headers=headers)
        if response.status_code != 200:
            logger.warning(
                "exoplanet_expert_failed",
                status=response.status_code,
                body=response.text[:200],
                model=_model_name(),
            )
            return None, "unavailable"
        content = response.json()["choices"][0]["message"]["content"].strip()
        if not content:
            return None, "unavailable"
        return content, "llm"
    except Exception as exc:  # noqa: BLE001
        logger.warning("exoplanet_expert_error", error=str(exc))
        return None, "unavailable"


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fmt_opt(value: object, fmt: str) -> str:
    number = _as_float(value)
    if number is None:
        return "unavailable"
    return format(number, fmt)


def _candidate_context(candidate: Candidate, target: Target) -> str:
    from projects.exoplanet.pipelines.checklist import (
        checklist_from_candidate_like,
        format_checklist_for_prompt,
    )

    note_raw = getattr(candidate, "geometry_note", None)
    geometry_note = note_raw if isinstance(note_raw, str) and note_raw else "n/a"
    plots_ready = getattr(candidate, "plots_ready", False)
    plots = "yes" if plots_ready is True else "no"
    checklist = format_checklist_for_prompt(checklist_from_candidate_like(candidate))
    return (
        f"Target: {target.name} (slug={target.slug}, mission={target.mission}, "
        f"external_id={target.external_id})\n"
        f"Notes: {target.notes or 'n/a'}\n"
        f"Candidate #{candidate.id}\n"
        f"Period (days): {candidate.period_days:.4f}\n"
        f"Transit depth (ppm, baseline-normalized): {candidate.depth_ppm:.1f}\n"
        f"SNR: {candidate.snr:.2f}\n"
        f"Epoch t0: {_fmt_opt(getattr(candidate, 't0', None), '.6f')}\n"
        f"Duration (hours): {_fmt_opt(getattr(candidate, 'duration_hours', None), '.3f')}\n"
        f"Odd depth (ppm): {_fmt_opt(getattr(candidate, 'odd_depth_ppm', None), '.1f')}\n"
        f"Even depth (ppm): {_fmt_opt(getattr(candidate, 'even_depth_ppm', None), '.1f')}\n"
        f"Odd-even delta (ppm): {_fmt_opt(getattr(candidate, 'odd_even_delta_ppm', None), '.1f')}\n"
        f"Geometry note: {geometry_note}\n"
        f"Vetting plots ready: {plots}\n"
        f"{checklist}\n"
        f"Status: {candidate.status}\n"
        f"Detection note: {candidate.flag_reason}"
    )


def build_review_summary_prompt(candidate: Candidate, target: Target) -> str:
    return (
        "Write a concise review summary for a human citizen-science reviewer.\n\n"
        f"{_candidate_context(candidate, target)}\n\n"
        "Explain what was flagged, plausible interpretations "
        "(planet candidate vs stellar variability vs instrument/systematic), "
        "and 2-3 concrete follow-up checks. Keep under 220 words."
    )


def template_review_summary(candidate: Candidate, target: Target) -> str:
    from projects.exoplanet.pipelines.checklist import (
        checklist_blocking_action,
        checklist_from_candidate_like,
    )

    geom = ""
    t0 = _as_float(getattr(candidate, "t0", None))
    if t0 is not None:
        odd = _as_float(getattr(candidate, "odd_depth_ppm", None))
        even = _as_float(getattr(candidate, "even_depth_ppm", None))
        dur = _as_float(getattr(candidate, "duration_hours", None))
        geom = f" Epoch t0≈{t0:.4f}"
        if dur is not None:
            geom += f", duration≈{dur:.2f} h"
        if odd is not None and even is not None:
            geom += f", odd/even depths {odd:.0f}/{even:.0f} ppm"
        geom += "."
    items = checklist_from_candidate_like(candidate)
    fails = [i for i in items if i.status == "fail"]
    next_action = checklist_blocking_action(items)
    flags = ""
    if fails:
        flags = " Checklist fails: " + "; ".join(f"{i.label} ({i.status})" for i in fails) + "."
    if next_action:
        flags += f" Next: {next_action}"
    return (
        f"{target.name} ({target.mission}) shows a periodic signal at "
        f"{candidate.period_days:.3f} d (SNR {candidate.snr:.1f}, "
        f"depth {candidate.depth_ppm:.0f} ppm baseline-normalized). "
        f"{candidate.flag_reason}.{geom}{flags}"
    )


def generate_review_summary(candidate: Candidate, target: Target) -> tuple[str, str]:
    """Return (summary_text, source) where source is 'llm' or 'template'."""
    text, source = chat_exoplanet_expert(build_review_summary_prompt(candidate, target))
    if text and source == "llm":
        return text, "llm"
    return template_review_summary(candidate, target), "template"


def _load_candidate_pair(candidate_id: int) -> tuple[Candidate, Target] | None:
    init_db()
    session = get_db_session()
    try:
        row = (
            session.query(Candidate, Target)
            .join(Target, Candidate.target_id == Target.id)
            .filter(Candidate.id == candidate_id)
            .one_or_none()
        )
        if row is None:
            return None
        return row
    finally:
        session.close()


def answer_exoplanet_question(
    question: str,
    *,
    candidate_id: int | None = None,
) -> dict[str, object]:
    """Answer a free-form exoplanet question, optionally grounded on one candidate."""
    q = question.strip()
    if not q:
        return {"ok": False, "reason": "empty_question"}

    context = ""
    if candidate_id is not None:
        pair = _load_candidate_pair(candidate_id)
        if pair is None:
            return {"ok": False, "reason": "candidate_not_found", "candidate_id": candidate_id}
        candidate, target = pair
        context = _candidate_context(candidate, target)

    if context:
        prompt = (
            "Answer the reviewer's question using the candidate context below. "
            "If the question cannot be answered from this data, say what else is needed.\n\n"
            f"CONTEXT:\n{context}\n\nQUESTION:\n{q}"
        )
    else:
        prompt = (
            "Answer the reviewer's exoplanet / transit-vetting question. "
            "If they refer to a specific candidate id, ask them to include it "
            "(e.g. /ask 8 Is the period realistic?).\n\n"
            f"QUESTION:\n{q}"
        )

    text, source = chat_exoplanet_expert(prompt, max_tokens=700, temperature=0.25)
    if not text:
        return {
            "ok": False,
            "reason": "llm_unavailable",
            "hint": "Set OPENROUTER_API_KEY in .env and recreate platform-api / bot-exoplanet.",
        }
    return {"ok": True, "source": source, "answer": text, "candidate_id": candidate_id}
