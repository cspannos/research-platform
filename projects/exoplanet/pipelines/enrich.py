from __future__ import annotations

from projects.exoplanet.db.models import Candidate, Target, get_db_session, init_db
from projects.exoplanet.pipelines.expert import generate_review_summary
from projects.exoplanet.review.queries import upsert_summary
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


def enrich_candidate_summary(candidate_id: int) -> dict[str, object]:
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
            return {"ok": False, "reason": "not_found"}
        candidate, target = row
        text, source = generate_review_summary(candidate, target)
        upsert_summary(candidate_id, text, source=source)
        logger.info("candidate_summary_enriched", candidate_id=candidate_id, source=source)
        return {"ok": True, "candidate_id": candidate_id, "source": source, "summary": text}
    finally:
        session.close()
