from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from projects.exoplanet.db.models import (
    Candidate,
    ReviewComment,
    ReviewSummary,
    Target,
    get_db_session,
    init_db,
)


@dataclass
class CommentRow:
    id: int
    author: str
    body: str
    created_at: datetime


@dataclass
class ChecklistItemRow:
    id: str
    label: str
    status: str
    detail: str
    next_action: str


@dataclass
class CandidateRow:
    id: int
    target_slug: str
    target_name: str
    mission: str
    period_days: float
    depth_ppm: float
    snr: float
    flag_reason: str
    status: str
    created_at: datetime
    summary: str | None
    summary_source: str | None
    comments: list[CommentRow]
    t0: float | None = None
    duration_hours: float | None = None
    odd_depth_ppm: float | None = None
    even_depth_ppm: float | None = None
    odd_even_delta_ppm: float | None = None
    geometry_note: str | None = None
    plots_ready: bool = False
    available_plots: list[str] | None = None
    checklist: list[ChecklistItemRow] | None = None
    checklist_next_action: str | None = None


def _latest_summary(session, candidate_id: int) -> ReviewSummary | None:
    return (
        session.query(ReviewSummary)
        .filter_by(candidate_id=candidate_id)
        .order_by(ReviewSummary.generated_at.desc())
        .first()
    )


def _comments_for(session, candidate_id: int) -> list[CommentRow]:
    rows = (
        session.query(ReviewComment)
        .filter_by(candidate_id=candidate_id)
        .order_by(ReviewComment.created_at.asc())
        .all()
    )
    return [CommentRow(id=r.id, author=r.author, body=r.body, created_at=r.created_at) for r in rows]


def _to_row(session, candidate: Candidate, target: Target) -> CandidateRow:
    from projects.exoplanet.pipelines.cache_manager import list_available_plots
    from projects.exoplanet.pipelines.checklist import (
        checklist_blocking_action,
        checklist_from_candidate_like,
    )

    summary = _latest_summary(session, candidate.id)
    plots = list_available_plots(candidate.id)
    plots_ready = bool(getattr(candidate, "plots_ready", False) or plots)
    # Temporary attrs for checklist helper
    candidate.available_plots = plots  # type: ignore[attr-defined]
    candidate.plots_ready = plots_ready
    items = checklist_from_candidate_like(candidate, available_plots=plots)
    checklist_rows = [
        ChecklistItemRow(
            id=i.id,
            label=i.label,
            status=i.status,
            detail=i.detail,
            next_action=i.next_action,
        )
        for i in items
    ]
    return CandidateRow(
        id=candidate.id,
        target_slug=target.slug,
        target_name=target.name,
        mission=target.mission,
        period_days=candidate.period_days,
        depth_ppm=candidate.depth_ppm,
        snr=candidate.snr,
        flag_reason=candidate.flag_reason,
        status=candidate.status,
        created_at=candidate.created_at,
        summary=summary.summary_text if summary else None,
        summary_source=summary.source if summary else None,
        comments=_comments_for(session, candidate.id),
        t0=getattr(candidate, "t0", None),
        duration_hours=getattr(candidate, "duration_hours", None),
        odd_depth_ppm=getattr(candidate, "odd_depth_ppm", None),
        even_depth_ppm=getattr(candidate, "even_depth_ppm", None),
        odd_even_delta_ppm=getattr(candidate, "odd_even_delta_ppm", None),
        geometry_note=getattr(candidate, "geometry_note", None),
        plots_ready=plots_ready,
        available_plots=plots,
        checklist=checklist_rows,
        checklist_next_action=checklist_blocking_action(items),
    )


def list_candidate_rows(status: str | None = None, limit: int = 50) -> list[CandidateRow]:
    init_db()
    session = get_db_session()
    try:
        query = session.query(Candidate, Target).join(Target, Candidate.target_id == Target.id)
        if status:
            query = query.filter(Candidate.status == status)
        rows = query.order_by(Candidate.created_at.desc()).limit(limit).all()
        return [_to_row(session, c, t) for c, t in rows]
    finally:
        session.close()


def get_candidate_row(candidate_id: int) -> CandidateRow | None:
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
        candidate, target = row
        return _to_row(session, candidate, target)
    finally:
        session.close()


def update_candidate_status(candidate_id: int, status: str) -> bool:
    init_db()
    session = get_db_session()
    try:
        candidate = session.query(Candidate).filter_by(id=candidate_id).one_or_none()
        if candidate is None:
            return False
        candidate.status = status
        session.commit()
        return True
    finally:
        session.close()


def add_review_comment(candidate_id: int, body: str, author: str = "reviewer") -> CommentRow | None:
    init_db()
    session = get_db_session()
    try:
        candidate = session.query(Candidate).filter_by(id=candidate_id).one_or_none()
        if candidate is None:
            return None
        comment = ReviewComment(candidate_id=candidate_id, author=author, body=body.strip())
        session.add(comment)
        session.commit()
        session.refresh(comment)
        return CommentRow(
            id=comment.id,
            author=comment.author,
            body=comment.body,
            created_at=comment.created_at,
        )
    finally:
        session.close()


def upsert_summary(candidate_id: int, text: str, source: str = "template") -> None:
    init_db()
    session = get_db_session()
    try:
        summary = (
            session.query(ReviewSummary)
            .filter_by(candidate_id=candidate_id, source=source)
            .one_or_none()
        )
        now = datetime.now(timezone.utc)
        if summary:
            summary.summary_text = text
            summary.generated_at = now
        else:
            session.add(
                ReviewSummary(
                    candidate_id=candidate_id,
                    summary_text=text,
                    source=source,
                    generated_at=now,
                )
            )
        session.commit()
    finally:
        session.close()
