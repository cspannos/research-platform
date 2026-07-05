from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from scipy.signal import lombscargle

from projects.exoplanet.db.models import Candidate, Target, get_db_session, init_db
from projects.exoplanet.pipelines.cache_manager import target_cache_path
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
    settings = get_exoplanet_settings()
    detrended = _detrend(flux)
    dt = np.median(np.diff(time)) if len(time) > 1 else 1.0
    if dt <= 0:
        dt = 1.0

    min_f = 1.0 / settings.max_period_days
    max_f = 1.0 / settings.min_period_days
    freqs = np.linspace(min_f, max_f, 4000)
    power = lombscargle(time, detrended, freqs, normalize=True)
    peak_idx = int(np.argmax(power))
    peak_power = float(power[peak_idx])
    period_days = float(1.0 / freqs[peak_idx])

    depth = float((1.0 - np.min(detrended)) * 1e6)
    noise = float(np.std(detrended)) or 1e-9
    snr = float(peak_power / noise)

    is_interesting = snr >= settings.snr_threshold and settings.min_period_days <= period_days <= settings.max_period_days
    reason = (
        f"Lomb-Scargle peak period={period_days:.3f}d, SNR={snr:.2f}, depth~{depth:.0f}ppm"
        if is_interesting
        else f"No strong periodic signal (SNR={snr:.2f})"
    )

    return AnalysisResult(
        period_days=period_days,
        depth_ppm=depth,
        snr=snr,
        flag_reason=reason,
        is_interesting=is_interesting,
    )


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
            session.commit()

        logger.info(
            "target_analyzed",
            slug=slug,
            interesting=result.is_interesting,
            period_days=result.period_days,
            snr=result.snr,
        )
        return {
            "target": slug,
            "interesting": result.is_interesting,
            "candidate_id": candidate_id,
            "period_days": result.period_days,
            "depth_ppm": result.depth_ppm,
            "snr": result.snr,
            "flag_reason": result.flag_reason,
        }
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
