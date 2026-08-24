"""Lightkurve-equivalent photocenter test from a cached TPF (no synthetic centroids)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits
from scipy import stats

from projects.exoplanet.pipelines.cache_manager import existing_tpf, target_cache_path
from projects.exoplanet.settings import TargetSpec, get_exoplanet_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)

_P_FAIL = 0.05
_MAX_CADENCES = 15_000


def load_lightcurve_source(slug: str) -> str | None:
    path = target_cache_path(slug)
    if not path.exists():
        return None
    try:
        data = np.load(path, allow_pickle=True)
    except OSError:
        return None
    if "source" not in data.files:
        return None
    raw = data["source"]
    if getattr(raw, "shape", ()) == ():
        return str(raw.item())
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def is_synthetic_source(source: str | None) -> bool:
    if not source:
        return False
    return source.lower().startswith("synthetic")


def _phase_fold(time: np.ndarray, period: float, t0: float) -> np.ndarray:
    return ((time - t0) / period + 0.5) % 1.0 - 0.5


def flux_weighted_centroids(flux_cube: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized photocenter (pixels) for a (n, ny, nx) flux cube."""
    cube = np.asarray(flux_cube, dtype=float)
    if cube.ndim != 3:
        raise ValueError(f"expected 3D FLUX cube, got shape {cube.shape}")
    pos = np.clip(np.nan_to_num(cube, nan=0.0), 0.0, None)
    n, ny, nx = pos.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    tot = pos.sum(axis=(1, 2))
    cx = (pos * xx).sum(axis=(1, 2)) / np.where(tot > 0, tot, np.nan)
    cy = (pos * yy).sum(axis=(1, 2)) / np.where(tot > 0, tot, np.nan)
    return cx, cy


def load_tpf_centroids(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    try:
        with fits.open(path, memmap=False) as hdul:
            table = None
            for hdu in hdul[1:]:
                data = getattr(hdu, "data", None)
                cols = getattr(hdu, "columns", None)
                names = set(cols.names) if cols is not None else set()
                if not names:
                    continue
                time_col = "TIME" if "TIME" in names else None
                flux_col = "FLUX" if "FLUX" in names else None
                if time_col and flux_col:
                    table = data
                    break
            if table is None:
                return None
            time = np.asarray(table["TIME"], dtype=float)
            flux = np.asarray(table["FLUX"], dtype=float)
            col_names = set()
            if getattr(table, "columns", None) is not None:
                col_names = set(table.columns.names)
            elif getattr(table, "dtype", None) is not None and table.dtype.names:
                col_names = set(table.dtype.names)
            quality = None
            for qname in ("QUALITY", "SAP_QUALITY"):
                if qname in col_names:
                    quality = np.asarray(table[qname], dtype=int)
                    break
    except Exception as exc:  # noqa: BLE001
        logger.warning("tpf_read_failed", path=str(path), error=str(exc))
        return None

    if flux.ndim != 3 or len(time) < 20:
        return None
    mask = np.isfinite(time)
    if quality is not None:
        mask &= quality == 0
    time, flux = time[mask], flux[mask]
    if len(time) < 20:
        return None
    if len(time) > _MAX_CADENCES:
        idx = np.linspace(0, len(time) - 1, _MAX_CADENCES).astype(int)
        time, flux = time[idx], flux[idx]
    try:
        cx, cy = flux_weighted_centroids(flux)
    except ValueError:
        return None
    finite = np.isfinite(time) & np.isfinite(cx) & np.isfinite(cy)
    if int(np.count_nonzero(finite)) < 20:
        return None
    return time[finite], cx[finite], cy[finite]


def centroid_shift_test(
    time: np.ndarray,
    cx: np.ndarray,
    cy: np.ndarray,
    period_days: float,
    t0: float,
    duration_hours: float | None,
) -> dict[str, object]:
    """Two-sample Hotelling T² on in- vs out-of-transit photocenter."""
    if period_days <= 0 or not np.isfinite(period_days) or not np.isfinite(t0):
        return {"status": "unavailable", "reason": "invalid_ephemeris", "pass": None}

    phase = _phase_fold(time, period_days, t0)
    half = 0.05
    if duration_hours is not None and duration_hours > 0:
        half = max(0.02, min(0.12, (duration_hours / 24.0) / period_days / 2.0))
    in_tr = np.abs(phase) < half
    out_tr = np.abs(phase) > 0.15
    xin = np.column_stack([cx[in_tr], cy[in_tr]])
    xout = np.column_stack([cx[out_tr], cy[out_tr]])
    xin = xin[np.isfinite(xin).all(axis=1)]
    xout = xout[np.isfinite(xout).all(axis=1)]
    nin, nout = len(xin), len(xout)
    if nin < 5 or nout < 10:
        return {
            "status": "unavailable",
            "reason": "too_few_cadences",
            "pass": None,
            "n_in": nin,
            "n_out": nout,
        }

    mean_in = xin.mean(axis=0)
    mean_out = xout.mean(axis=0)
    diff = mean_in - mean_out
    offset = float(np.hypot(diff[0], diff[1]))
    vin = np.cov(xin, rowvar=False)
    vout = np.cov(xout, rowvar=False)
    pooled = ((nin - 1) * vin + (nout - 1) * vout) / max(nin + nout - 2, 1)
    try:
        inv = np.linalg.pinv(np.atleast_2d(pooled))
    except np.linalg.LinAlgError:
        return {
            "status": "unavailable",
            "reason": "singular_covariance",
            "pass": None,
            "offset_pix": offset,
        }

    p_dim = 2
    t2 = float((nin * nout) / (nin + nout) * diff @ inv @ diff)
    df2 = nin + nout - p_dim - 1
    if df2 <= 0 or not np.isfinite(t2):
        return {
            "status": "unavailable",
            "reason": "invalid_hotelling",
            "pass": None,
            "offset_pix": offset,
        }
    f_stat = t2 * df2 / ((nin + nout - 2) * p_dim)
    pvalue = float(stats.f.sf(f_stat, p_dim, df2))
    passed = bool(pvalue >= _P_FAIL)
    return {
        "status": "pass" if passed else "fail",
        "pass": passed,
        "reason": None,
        "pvalue": pvalue,
        "offset_pix": offset,
        "n_in": nin,
        "n_out": nout,
        "method": "hotelling_t2_photocenter",
    }


def unavailable_centroid(reason: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "unavailable",
        "pass": None,
        "pvalue": None,
        "offset_pix": None,
        "reason": reason,
        "method": "hotelling_t2_photocenter",
    }
    payload.update(extra)
    return payload


def run_centroid_test(
    spec: TargetSpec,
    *,
    period_days: float,
    t0: float | None,
    duration_hours: float | None,
    lc_source: str | None,
    force: bool = False,
) -> dict[str, object]:
    """
    Centroid offset test for one target/candidate.

    Synthetic light curves never claim a real centroid, even if a TPF exists.
    TPF is cached: one file per target; downloads are skipped on cache hit.
    """
    if is_synthetic_source(lc_source):
        return unavailable_centroid("synthetic_lc")

    settings = get_exoplanet_settings()
    cached = existing_tpf(spec.id)
    tpf_path = cached
    if tpf_path is None:
        if not settings.fetch_tpf and not force:
            return unavailable_centroid("tpf_disabled")
        from projects.exoplanet.pipelines.mast_client import fetch_target_pixel_file

        try:
            tpf_path = fetch_target_pixel_file(spec)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tpf_fetch_error", target=spec.id, error=str(exc))
            return unavailable_centroid(f"no_network:{exc}")

    if tpf_path is None:
        return unavailable_centroid("no_tpf")

    if t0 is None or not np.isfinite(float(t0)):
        return unavailable_centroid("no_t0", tpf_path=str(tpf_path))

    loaded = load_tpf_centroids(tpf_path)
    if loaded is None:
        return unavailable_centroid("tpf_unreadable", tpf_path=str(tpf_path))

    time, cx, cy = loaded
    result = centroid_shift_test(time, cx, cy, period_days, float(t0), duration_hours)
    result["tpf_path"] = str(tpf_path)
    result["tpf_label"] = tpf_path.stem
    return result
