from __future__ import annotations

from projects.exoplanet.db.models import Candidate, ReviewSummary, Target, get_db_session, init_db
from projects.exoplanet.pipelines.llm import generate_llm_summary
from projects.exoplanet.review.queries import upsert_summary
from projects.exoplanet.settings import get_exoplanet_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


def _build_summary_text(candidate: Candidate, target: Target) -> str:
    return (
        f"Candidate #{candidate.id} — {target.name} ({target.mission})\n"
        f"Period: {candidate.period_days:.3f} days\n"
        f"Depth: ~{candidate.depth_ppm:.0f} ppm | SNR: {candidate.snr:.2f}\n"
        f"Reason: {candidate.flag_reason}\n"
        f"Status: {candidate.status}\n"
        f"Review at /review/candidates/{candidate.id}"
    )


def generate_summaries_for_pending(limit: int = 10) -> dict[str, object]:
    init_db()
    settings = get_exoplanet_settings()
    session = get_db_session()
    to_create: list[tuple[Candidate, Target]] = []
    try:
        pending = (
            session.query(Candidate)
            .filter(Candidate.status == "pending")
            .order_by(Candidate.created_at.desc())
            .limit(limit)
            .all()
        )
        for candidate in pending:
            existing = (
                session.query(ReviewSummary)
                .filter_by(candidate_id=candidate.id)
                .one_or_none()
            )
            if existing:
                continue
            target = session.query(Target).filter_by(id=candidate.target_id).one()
            to_create.append((candidate, target))
    finally:
        session.close()

    created = []
    for candidate, target in to_create:
        if settings.llm_summaries:
            text, source = generate_llm_summary(candidate, target)
        else:
            text, source = _build_summary_text(candidate, target), "template"
        upsert_summary(candidate.id, text, source=source)
        created.append({"candidate_id": candidate.id, "source": source})

    logger.info("review_summaries_generated", count=len(created))
    return {"generated": created}


def format_telegram_digest(limit: int = 5) -> str:
    init_db()
    session = get_db_session()
    try:
        rows = (
            session.query(Candidate, Target, ReviewSummary)
            .join(Target, Candidate.target_id == Target.id)
            .outerjoin(ReviewSummary, ReviewSummary.candidate_id == Candidate.id)
            .filter(Candidate.status == "pending")
            .order_by(Candidate.snr.desc())
            .limit(limit)
            .all()
        )
        if not rows:
            return "No pending exoplanet candidates."

        lines = ["Exoplanet review digest:"]
        for candidate, target, summary in rows:
            snippet = summary.summary_text.split("\n")[0] if summary else candidate.flag_reason
            lines.append(f"• #{candidate.id} {target.name}: {snippet}")
        return "\n".join(lines)
    finally:
        session.close()
