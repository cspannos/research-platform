from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from projects.exoplanet.pipelines.cache_manager import VETTING_PLOT_NAMES, resolve_plot_file
from projects.exoplanet.pipelines.enrich import enrich_candidate_summary
from projects.exoplanet.review.queries import (
    add_review_comment,
    get_candidate_row,
    list_candidate_rows,
    update_candidate_status,
)
from research_platform.core.config import get_settings

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/review", tags=["review-dashboard"])


def verify_review_access(
    request: Request,
    token: str | None = Query(default=None),
) -> None:
    settings = get_settings()
    supplied = (
        token
        or request.headers.get("X-Admin-Token")
        or request.cookies.get("admin_token")
    )
    if not supplied or supplied != settings.platform_admin_token:
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/", response_class=HTMLResponse)
def review_home(
    request: Request,
    status: str = Query(default="pending"),
    _: None = Depends(verify_review_access),
) -> Response:
    rows = list_candidate_rows(status=None if status == "all" else status, limit=100)
    return templates.TemplateResponse(
        request,
        "review/index.html",
        {"candidates": rows, "status_filter": status},
    )


@router.get("/partials/list", response_class=HTMLResponse)
def review_list_partial(
    request: Request,
    status: str = Query(default="pending"),
    _: None = Depends(verify_review_access),
) -> Response:
    rows = list_candidate_rows(status=None if status == "all" else status, limit=100)
    return templates.TemplateResponse(
        request,
        "review/partials/candidate_list.html",
        {"candidates": rows, "status_filter": status},
    )


@router.get("/candidates/{candidate_id}", response_class=HTMLResponse)
def review_candidate_detail(
    request: Request,
    candidate_id: int,
    _: None = Depends(verify_review_access),
) -> Response:
    row = get_candidate_row(candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return templates.TemplateResponse(
        request,
        "review/candidate_detail.html",
        {"candidate": row},
    )


@router.get("/candidates/{candidate_id}/plots/{plot_name}")
def review_candidate_plot(
    candidate_id: int,
    plot_name: str,
    _: None = Depends(verify_review_access),
) -> FileResponse:
    if plot_name not in VETTING_PLOT_NAMES:
        raise HTTPException(status_code=404, detail="unknown plot")
    if get_candidate_row(candidate_id) is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    path = resolve_plot_file(candidate_id, plot_name)
    if path is None:
        raise HTTPException(status_code=404, detail="plot unavailable")
    return FileResponse(path, media_type="image/png", filename=plot_name)


@router.post("/candidates/{candidate_id}/status", response_class=HTMLResponse)
def review_set_status(
    request: Request,
    candidate_id: int,
    status: str = Form(...),
    _: None = Depends(verify_review_access),
) -> Response:
    if status not in {"pending", "approved", "rejected"}:
        raise HTTPException(status_code=400, detail="invalid status")
    if not update_candidate_status(candidate_id, status):
        raise HTTPException(status_code=404, detail="candidate not found")
    row = get_candidate_row(candidate_id)
    return templates.TemplateResponse(
        request,
        "review/partials/candidate_detail_panel.html",
        {"candidate": row, "message": f"Marked as {status}."},
    )


@router.post("/candidates/{candidate_id}/comment", response_class=HTMLResponse)
def review_add_comment(
    request: Request,
    candidate_id: int,
    body: str = Form(...),
    author: str = Form(default="reviewer"),
    _: None = Depends(verify_review_access),
) -> Response:
    comment = add_review_comment(candidate_id, body, author=author)
    if comment is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    row = get_candidate_row(candidate_id)
    return templates.TemplateResponse(
        request,
        "review/partials/candidate_detail_panel.html",
        {"candidate": row, "message": "Comment saved."},
    )


@router.post("/candidates/{candidate_id}/enrich", response_class=HTMLResponse)
def review_enrich_summary(
    request: Request,
    candidate_id: int,
    _: None = Depends(verify_review_access),
) -> Response:
    result = enrich_candidate_summary(candidate_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="candidate not found")
    row = get_candidate_row(candidate_id)
    return templates.TemplateResponse(
        request,
        "review/partials/candidate_detail_panel.html",
        {
            "candidate": row,
            "message": f"Summary enriched ({result.get('source', 'unknown')}).",
        },
    )


def review_root_redirect(request: Request) -> RedirectResponse:
    token = request.query_params.get("token", "")
    url = f"/review/?token={token}" if token else "/review/"
    return RedirectResponse(url=url, status_code=302)
