from __future__ import annotations

from datetime import UTC, datetime

from research_platform.core.logging import get_logger

logger = get_logger(__name__)


def ping_job(message: str = "pong") -> dict[str, str]:
    """Example worker job: validate queue plumbing and structured logging."""
    payload = {
        "message": message,
        "processed_at": datetime.now(UTC).isoformat(),
    }
    logger.info("ping_job_completed", **payload)
    return payload


def summarize_text_job(tenant_id: str, subject: str, body: str) -> dict[str, str]:
    """Placeholder for LLM-backed summaries. Wire to API provider in Phase 2."""
    summary = f"[{tenant_id}] {subject}: {body[:240]}"
    logger.info("summary_job_completed", tenant=tenant_id, subject=subject)
    return {"tenant_id": tenant_id, "subject": subject, "summary": summary}
