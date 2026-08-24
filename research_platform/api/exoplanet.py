from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from projects.exoplanet.pipelines.enrich import enrich_candidate_summary
from projects.exoplanet.review.queries import (
    add_review_comment,
    get_candidate_row,
    list_candidate_rows,
    update_candidate_status,
)

router = APIRouter(prefix="/exoplanet", tags=["exoplanet"])


class CommentOut(BaseModel):
    id: int
    author: str
    body: str
    created_at: str


class ChecklistItemOut(BaseModel):
    id: str
    label: str
    status: str
    detail: str
    next_action: str


class CandidateOut(BaseModel):
    id: int
    target_slug: str
    target_name: str
    mission: str
    period_days: float
    depth_ppm: float
    snr: float
    flag_reason: str
    status: str
    created_at: str
    summary: str | None = None
    summary_source: str | None = None
    comments: list[CommentOut] = Field(default_factory=list)
    t0: float | None = None
    duration_hours: float | None = None
    odd_depth_ppm: float | None = None
    even_depth_ppm: float | None = None
    odd_even_delta_ppm: float | None = None
    geometry_note: str | None = None
    plots_ready: bool = False
    available_plots: list[str] = Field(default_factory=list)
    checklist: list[ChecklistItemOut] = Field(default_factory=list)
    checklist_next_action: str | None = None
    neighbours: dict | None = None
    centroid: dict | None = None


class CandidateStatusUpdate(BaseModel):
    status: Literal["pending", "approved", "rejected"]


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    author: str = Field(default="reviewer", max_length=64)


def _serialize(row) -> CandidateOut:
    return CandidateOut(
        id=row.id,
        target_slug=row.target_slug,
        target_name=row.target_name,
        mission=row.mission,
        period_days=row.period_days,
        depth_ppm=row.depth_ppm,
        snr=row.snr,
        flag_reason=row.flag_reason,
        status=row.status,
        created_at=row.created_at.isoformat(),
        summary=row.summary,
        summary_source=row.summary_source,
        comments=[
            CommentOut(
                id=c.id,
                author=c.author,
                body=c.body,
                created_at=c.created_at.isoformat(),
            )
            for c in row.comments
        ],
        t0=row.t0,
        duration_hours=row.duration_hours,
        odd_depth_ppm=row.odd_depth_ppm,
        even_depth_ppm=row.even_depth_ppm,
        odd_even_delta_ppm=row.odd_even_delta_ppm,
        geometry_note=row.geometry_note,
        plots_ready=row.plots_ready,
        available_plots=list(row.available_plots or []),
        checklist=[
            ChecklistItemOut(
                id=i.id,
                label=i.label,
                status=i.status,
                detail=i.detail,
                next_action=i.next_action,
            )
            for i in (row.checklist or [])
        ],
        checklist_next_action=row.checklist_next_action,
        neighbours=row.neighbours,
        centroid=row.centroid,
    )


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(status: str | None = None, limit: int = 50) -> list[CandidateOut]:
    return [_serialize(r) for r in list_candidate_rows(status=status, limit=limit)]


@router.get("/candidates/{candidate_id}", response_model=CandidateOut)
def get_candidate(candidate_id: int) -> CandidateOut:
    row = get_candidate_row(candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return _serialize(row)


@router.patch("/candidates/{candidate_id}")
def patch_candidate_status(candidate_id: int, body: CandidateStatusUpdate) -> dict[str, str]:
    if not update_candidate_status(candidate_id, body.status):
        raise HTTPException(status_code=404, detail="candidate not found")
    return {"id": str(candidate_id), "status": body.status}


@router.post("/candidates/{candidate_id}/comments", response_model=CommentOut)
def post_comment(candidate_id: int, body: CommentCreate) -> CommentOut:
    comment = add_review_comment(candidate_id, body.body, author=body.author)
    if comment is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return CommentOut(
        id=comment.id,
        author=comment.author,
        body=comment.body,
        created_at=comment.created_at.isoformat(),
    )


@router.post("/candidates/{candidate_id}/enrich-summary")
def post_enrich_summary(candidate_id: int) -> dict[str, object]:
    result = enrich_candidate_summary(candidate_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="candidate not found")
    return result


@router.get("/targets")
def list_targets() -> list[dict[str, str]]:
    from projects.exoplanet.settings import load_targets

    return [
        {
            "id": t.id,
            "name": t.name,
            "mission": t.mission,
            "notes": t.notes,
            "ra": str(t.ra) if t.ra is not None else "",
            "dec": str(t.dec) if t.dec is not None else "",
        }
        for t in load_targets()
    ]
