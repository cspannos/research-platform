from __future__ import annotations

from projects.exoplanet.db.models import Candidate, Target
from projects.exoplanet.pipelines.expert import generate_review_summary


def generate_llm_summary(candidate: Candidate, target: Target) -> tuple[str, str]:
    """Compatibility wrapper — prefer projects.exoplanet.pipelines.expert."""
    return generate_review_summary(candidate, target)
