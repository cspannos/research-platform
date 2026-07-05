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
    summary = _latest_summary(session, candidate.id)
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
