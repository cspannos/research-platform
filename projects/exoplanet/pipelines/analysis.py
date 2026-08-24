from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from scipy.signal import lombscargle

from projects.exoplanet.db.models import Candidate, Target, get_db_session, init_db
from projects.exoplanet.pipelines.cache_manager import target_cache_path
from projects.exoplanet.pipelines.neighbours import apply_neighbour_vetting
from projects.exoplanet.pipelines.vetting import (
    estimate_baseline_depth_ppm,
    estimate_transit_geometry,
    generate_vetting_plots,
    list_available_plots,
    load_cached_lc,
)
from projects.exoplanet.settings import get_exoplanet_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AnalysisResult:
    period_days: float
    depth_ppm: float
    snr: float
    flag_reason: str
    is_interesting: bool


def _load_cached_curve(slug: str) -> tuple[np.ndarray, np.ndarray]:
    path = target_cache_path(slug)
    if not path.exists():
        raise FileNotFoundError(f"No cached light curve for {slug}. Run ingest first.")
    data = np.load(path)
    return data["time"], data["flux"]


def _detrend(flux: np.ndarray) -> np.ndarray:
    x = np.arange(len(flux))
    coef = np.polyfit(x, flux, 2)
    return flux - np.polyval(coef, x)


def analyze_lightcurve(time: np.ndarray, flux: np.ndarray) -> AnalysisResult:
    """Period search on detrended flux; depth from continuum-normalized fold."""
    settings = get_exoplanet_settings()
    continuum = float(np.median(flux))
    if not np.isfinite(continuum) or continuum == 0:
        continuum = 1.0
    normalized = flux / continuum
    detrended = _detrend(normalized)

    min_f = 1.0 / settings.max_period_days
    max_f = 1.0 / settings.min_period_days
    freqs = np.linspace(min_f, max_f, 4000)
    power = lombscargle(time, detrended, freqs, normalize=True)
    peak_idx = int(np.argmax(power))
    peak_power = float(power[peak_idx])
    period_days = float(1.0 / freqs[peak_idx])

    depth = estimate_baseline_depth_ppm(time, flux, period_days)
    if depth is None:
        # Last-resort: in-transit vs continuum on normalized series (still baseline-relative).
        depth = float(max(0.0, (1.0 - float(np.min(normalized))) * 1e6))
    noise = float(np.std(detrended)) or 1e-9
    snr = float(peak_power / noise)

    is_interesting = (
        snr >= settings.snr_threshold
        and settings.min_period_days <= period_days <= settings.max_period_days
    )
    reason = (
        f"Lomb-Scargle peak period={period_days:.3f}d, SNR={snr:.2f}, "
        f"depth={depth:.0f}ppm (baseline-normalized)"
        if is_interesting
        else f"No strong periodic signal (SNR={snr:.2f})"
    )

    return AnalysisResult(
        period_days=period_days,
        depth_ppm=float(depth),
        snr=snr,
        flag_reason=reason,
        is_interesting=is_interesting,
    )


def _apply_geometry_and_plots(
    candidate: Candidate,
    slug: str,
    time: np.ndarray,
    flux: np.ndarray,
) -> dict[str, object]:
    """Compute geometry metrics + PNGs; mutate candidate fields in-session."""
    settings = get_exoplanet_settings()
    geometry = estimate_transit_geometry(time, flux, candidate.period_days)
    candidate.t0 = geometry.t0
    candidate.duration_hours = geometry.duration_hours
    candidate.odd_depth_ppm = geometry.odd_depth_ppm
    candidate.even_depth_ppm = geometry.even_depth_ppm
    candidate.odd_even_delta_ppm = geometry.odd_even_delta_ppm
    candidate.geometry_note = geometry.note
    if geometry.depth_ppm is not None:
        candidate.depth_ppm = float(geometry.depth_ppm)

    plot_result = generate_vetting_plots(
        candidate.id,
        time,
        flux,
        candidate.period_days,
        t0=geometry.t0,
        min_period_days=settings.min_period_days,
        max_period_days=settings.max_period_days,
    )
    candidate.plots_ready = bool(plot_result.get("ok"))
    if not candidate.plots_ready and plot_result.get("reason"):
        extra = f"plots unavailable: {plot_result['reason']}"
        if candidate.geometry_note:
            candidate.geometry_note = f"{candidate.geometry_note}; {extra}"
        else:
            candidate.geometry_note = extra

    return {
        "t0": candidate.t0,
        "duration_hours": candidate.duration_hours,
        "odd_depth_ppm": candidate.odd_depth_ppm,
        "even_depth_ppm": candidate.even_depth_ppm,
        "odd_even_delta_ppm": candidate.odd_even_delta_ppm,
        "geometry_note": candidate.geometry_note,
        "depth_ppm": candidate.depth_ppm,
        "plots_ready": candidate.plots_ready,
        "plots": list_available_plots(candidate.id),
        "slug": slug,
    }


def _apply_phase_b(candidate: Candidate, target: Target) -> dict[str, object]:
    """Gaia neighbours + optional TPF centroid; never raises to the caller."""
    try:
        return apply_neighbour_vetting(candidate, target)
    except Exception as exc:  # noqa: BLE001
        logger.warning("phase_b_failed", candidate_id=candidate.id, error=str(exc))
        return {
            "neighbours": {"status": "unavailable", "reason": str(exc)},
            "centroid": {"status": "unavailable", "reason": str(exc), "pass": None},
        }


def analyze_target_slug(slug: str) -> dict[str, object]:
    init_db()
    time, flux = _load_cached_curve(slug)
    result = analyze_lightcurve(time, flux)

    session = get_db_session()
    try:
        target = session.query(Target).filter_by(slug=slug).one_or_none()
        if target is None:
            raise ValueError(f"Unknown target slug: {slug}")

        candidate_id = None
        vetting: dict[str, object] = {}
        if result.is_interesting:
            candidate = Candidate(
                target_id=target.id,
                period_days=result.period_days,
                depth_ppm=result.depth_ppm,
                snr=result.snr,
                flag_reason=result.flag_reason,
                status="pending",
                created_at=datetime.now(timezone.utc),
            )
            session.add(candidate)
            session.flush()
            candidate_id = candidate.id
            vetting = _apply_geometry_and_plots(candidate, slug, time, flux)
            vetting.update(_apply_phase_b(candidate, target))
            session.commit()

        logger.info(
            "target_analyzed",
            slug=slug,
            interesting=result.is_interesting,
            period_days=result.period_days,
            snr=result.snr,
            depth_ppm=result.depth_ppm,
            candidate_id=candidate_id,
            plots_ready=vetting.get("plots_ready"),
        )
        payload: dict[str, object] = {
            "target": slug,
            "interesting": result.is_interesting,
            "candidate_id": candidate_id,
            "period_days": result.period_days,
            "depth_ppm": vetting.get("depth_ppm", result.depth_ppm),
            "snr": result.snr,
            "flag_reason": result.flag_reason,
        }
        payload.update(vetting)
        return payload
    finally:
        session.close()


def vet_candidate(candidate_id: int) -> dict[str, object]:
    """
    Recompute geometry + diagnostic plots (+ refreshed depth) for an existing candidate.

    Idempotent; used when LC cache appears after detection or to refresh Phase A artifacts.
    """
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
            return {"ok": False, "reason": "not_found", "candidate_id": candidate_id}
        candidate, target = row
        cached = load_cached_lc(target.slug)
        if cached is None:
            candidate.geometry_note = "unavailable: no light-curve cache"
            candidate.plots_ready = False
            candidate.t0 = None
            candidate.duration_hours = None
            candidate.odd_depth_ppm = None
            candidate.even_depth_ppm = None
            candidate.odd_even_delta_ppm = None
            phase_b = _apply_phase_b(candidate, target)
            session.commit()
            return {
                "ok": False,
                "reason": "no_cache",
                "candidate_id": candidate_id,
                "geometry_note": candidate.geometry_note,
                "plots_ready": False,
                **phase_b,
            }

        time, flux = cached
        vetting = _apply_geometry_and_plots(candidate, target.slug, time, flux)
        vetting.update(_apply_phase_b(candidate, target))
        session.commit()
        return {"ok": True, "candidate_id": candidate_id, **vetting}
    finally:
        session.close()


def scan_all_cached_targets() -> dict[str, object]:
    init_db()
    session = get_db_session()
    try:
        slugs = [row.slug for row in session.query(Target).filter_by(active=True).all()]
    finally:
        session.close()

    results = []
    flagged = 0
    for slug in slugs:
        if not target_cache_path(slug).exists():
            continue
        outcome = analyze_target_slug(slug)
        results.append(outcome)
        if outcome.get("interesting"):
            flagged += 1

    return {"scanned": len(results), "flagged": flagged, "results": results}
