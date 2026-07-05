from __future__ import annotations

import httpx

from projects.exoplanet.db.models import Candidate, Target
from projects.exoplanet.settings import get_exoplanet_settings
from research_platform.core.config import get_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-3.5-haiku"


def _build_prompt(candidate: Candidate, target: Target) -> str:
    return (
        "You are assisting a citizen-science exoplanet review workflow. "
        "Write a concise, plain-language summary for a human reviewer.\n\n"
        f"Target: {target.name} ({target.mission})\n"
        f"Period (days): {candidate.period_days:.4f}\n"
        f"Transit depth (ppm): {candidate.depth_ppm:.1f}\n"
        f"SNR: {candidate.snr:.2f}\n"
        f"Detection note: {candidate.flag_reason}\n\n"
        "Explain what was flagged, plausible astrophysical interpretations "
        "(planet candidate vs stellar variability vs instrument/systematic), "
        "and 2-3 concrete follow-up checks. Keep under 220 words."
    )


def _fallback_summary(candidate: Candidate, target: Target) -> str:
    return (
        f"{target.name} ({target.mission}) shows a periodic signal at "
        f"{candidate.period_days:.3f} d (SNR {candidate.snr:.1f}). "
        f"{candidate.flag_reason} "
        "Follow up with vetting plots, neighbor checks, and archive metadata."
    )


def generate_llm_summary(candidate: Candidate, target: Target) -> tuple[str, str]:
    """
    Return (summary_text, source) where source is 'llm' or 'template'.
    """
    platform = get_settings()
    exo = get_exoplanet_settings()
    api_key = exo.openrouter_api_key or platform.openrouter_api_key

    if not api_key:
        logger.info("llm_summary_skipped", reason="no_openrouter_key")
        return _fallback_summary(candidate, target), "template"

    payload = {
        "model": DEFAULT_MODEL,
        "messages": [{"role": "user", "content": _build_prompt(candidate, target)}],
        "max_tokens": 400,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://research-platform.local/exoplanet",
        "X-Title": "Research Platform Exoplanet Review",
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(OPENROUTER_URL, json=payload, headers=headers)
        if response.status_code != 200:
            logger.warning("llm_summary_failed", status=response.status_code, body=response.text[:200])
            return _fallback_summary(candidate, target), "template"
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        if not content:
            return _fallback_summary(candidate, target), "template"
        return content, "llm"
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_summary_error", error=str(exc))
        return _fallback_summary(candidate, target), "template"
