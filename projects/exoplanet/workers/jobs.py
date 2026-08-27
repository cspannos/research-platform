from __future__ import annotations

import os

from projects.exoplanet.pipelines.analysis import (
    analyze_target_slug,
    scan_all_cached_targets,
    vet_candidate,
)
from projects.exoplanet.pipelines.neighbours import vet_neighbours
from projects.exoplanet.pipelines.validate import vet_validate
from projects.exoplanet.pipelines.ingest import ingest_all_targets, ingest_target
from projects.exoplanet.pipelines.summaries import format_telegram_digest, generate_summaries_for_pending
from projects.exoplanet.settings import load_targets
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


def exoplanet_ingest_job() -> dict[str, object]:
    return ingest_all_targets()


def exoplanet_scan_job() -> dict[str, object]:
    return scan_all_cached_targets()


def exoplanet_analyze_target_job(slug: str) -> dict[str, object]:
    return analyze_target_slug(slug)


def exoplanet_vet_candidate_job(candidate_id: int) -> dict[str, object]:
    """Regenerate Phase A geometry + diagnostic plots for one candidate."""
    return vet_candidate(candidate_id)


def exoplanet_vet_neighbours_job(candidate_id: int, force: bool = False) -> dict[str, object]:
    """Phase B: Gaia cone + dilution + optional TPF centroid (idempotent)."""
    return vet_neighbours(candidate_id, force=force)


def exoplanet_vet_validate_job(candidate_id: int, force: bool = False) -> dict[str, object]:
    """Phase C: optional FPP/NFPP on exoplanet-validate queue. Never used by /scan."""
    return vet_validate(candidate_id, force=force)


def exoplanet_review_summary_job() -> dict[str, object]:
    return generate_summaries_for_pending()


def exoplanet_enrich_summary_job(candidate_id: int) -> dict[str, object]:
    from projects.exoplanet.pipelines.enrich import enrich_candidate_summary

    return enrich_candidate_summary(candidate_id)


def exoplanet_telegram_digest_job() -> dict[str, str]:
    digest = format_telegram_digest()
    logger.info("exoplanet_digest_ready", length=len(digest))
    return {"digest": digest}


def exoplanet_notify_telegram_job(chat_ids: list[int] | None = None) -> dict[str, object]:
    """Send digest to configured Telegram users (called after review summary job)."""
    import asyncio

    from telegram import Bot

    token = os.getenv("EXOPLANET_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    allowed = os.getenv("EXOPLANET_TELEGRAM_ALLOWED_USER_IDS") or os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    recipients = chat_ids or [int(x.strip()) for x in allowed.split(",") if x.strip()]

    if not token or not recipients:
        return {"sent": 0, "reason": "telegram not configured"}

    digest = format_telegram_digest()
    bot = Bot(token=token)

    async def _send_all() -> int:
        count = 0
        for chat_id in recipients:
            await bot.send_message(chat_id=chat_id, text=digest)
            count += 1
        return count

    sent = asyncio.run(_send_all())
    return {"sent": sent, "digest_preview": digest[:200]}
