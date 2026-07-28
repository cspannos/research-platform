"""Phase A transit geometry + diagnostic plots from cached light curves."""

from __future__ import annotations

from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
from scipy.signal import lombscargle

from projects.exoplanet.pipelines.cache_manager import (
    VETTING_PLOT_NAMES,
    list_available_plots,
    resolve_plot_file,
    target_cache_path,
    vetting_dir,
)
from research_platform.core.logging import get_logger

logger = get_logger(__name__)

# Back-compat aliases for API/tests
PLOT_NAMES = VETTING_PLOT_NAMES
_MAX_PLOT_POINTS = 8000

__all__ = [
    "PLOT_NAMES",
    "TransitGeometry",
    "estimate_baseline_depth_ppm",
    "estimate_transit_geometry",
    "generate_vetting_plots",
    "list_available_plots",
    "load_cached_lc",
    "resolve_plot_file",
]


@dataclass(frozen=True)
class TransitGeometry:
    t0: float | None
    duration_hours: float | None
    odd_depth_ppm: float | None
    even_depth_ppm: float | None
    odd_even_delta_ppm: float | None
    note: str
    # Continuum-normalized transit depth: (F_out - F_in) / F_out × 1e6
    depth_ppm: float | None = None

    @property
    def ok(self) -> bool:
        return self.t0 is not None and "unavailable" not in self.note.lower()


def estimate_baseline_depth_ppm(
    time: np.ndarray,
    flux: np.ndarray,
    period_days: float,
    *,
    t0: float | None = None,
) -> float | None:
    """
    Baseline-normalized transit depth in ppm.

    Uses out-of-transit median as continuum and in-transit mean near phase 0.
    Returns None when the fold cannot be formed.
    """
    if len(time) < 20 or period_days <= 0 or not np.isfinite(period_days):
        return None

    continuum = float(np.median(flux))
    if not np.isfinite(continuum) or continuum == 0:
        return None

    if t0 is None or not np.isfinite(t0):
        order = np.argsort(flux)[: max(5, len(flux) // 50)]
        t0 = float(np.median(time[order]))

    phase = _phase_fold(time, period_days, float(t0))
    in_transit = np.abs(phase) < 0.05
    out_transit = np.abs(phase) > 0.15
    if not np.any(in_transit) or not np.any(out_transit):
        return None

    f_out = float(np.median(flux[out_transit]))
    f_in = float(np.mean(flux[in_transit]))
    if not np.isfinite(f_out) or f_out == 0:
        f_out = continuum
    depth = (f_out - f_in) / abs(f_out) * 1e6
    if not np.isfinite(depth):
        return None
    return float(depth)


def _downsample(time: np.ndarray, flux: np.ndarray, max_points: int = _MAX_PLOT_POINTS):
    n = len(time)
    if n <= max_points:
        return time, flux
    idx = np.linspace(0, n - 1, max_points).astype(int)
    return time[idx], flux[idx]


def _phase_fold(time: np.ndarray, period: float, t0: float) -> np.ndarray:
    """Return phases in [-0.5, 0.5) relative to epoch t0."""
    phase = ((time - t0) / period + 0.5) % 1.0 - 0.5
    return phase


def _binned_mean(phase: np.ndarray, flux: np.ndarray, n_bins: int = 80) -> tuple[np.ndarray, np.ndarray]:
    edges = np.linspace(-0.5, 0.5, n_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    means = np.full(n_bins, np.nan)
    for i in range(n_bins):
        mask = (phase >= edges[i]) & (phase < edges[i + 1])
        if np.any(mask):
            means[i] = float(np.mean(flux[mask]))
    return centers, means


def estimate_transit_geometry(
    time: np.ndarray,
    flux: np.ndarray,
    period_days: float,
) -> TransitGeometry:
    """
    Estimate epoch (t0), duration, and odd/even depths from a phase fold.

    Uses the deepest phase bin as mid-transit. Gracefully returns null metrics
    with an explanatory note when the curve is too short or empty.
    """
    if len(time) < 20 or period_days <= 0 or not np.isfinite(period_days):
        return TransitGeometry(
            t0=None,
            duration_hours=None,
            odd_depth_ppm=None,
            even_depth_ppm=None,
            odd_even_delta_ppm=None,
            note="unavailable: insufficient points or invalid period",
        )

    # Rough global depth reference from continuum
    continuum = float(np.median(flux))
    if continuum == 0 or not np.isfinite(continuum):
        continuum = 1.0

    # Find t0: sample candidate epochs near deepest points
    order = np.argsort(flux)[: max(5, len(flux) // 50)]
    t0_seed = float(np.median(time[order]))
    phase = _phase_fold(time, period_days, t0_seed)
    centers, means = _binned_mean(phase, flux, n_bins=100)
    valid = np.isfinite(means)
    if not np.any(valid):
        return TransitGeometry(
            t0=None,
            duration_hours=None,
            odd_depth_ppm=None,
            even_depth_ppm=None,
            odd_even_delta_ppm=None,
            note="unavailable: phase fold empty",
        )

    mid_phase = float(centers[valid][np.argmin(means[valid])])
    t0 = t0_seed + mid_phase * period_days

    # Refine fold at t0
    phase = _phase_fold(time, period_days, t0)
    centers, means = _binned_mean(phase, flux, n_bins=100)
    valid = np.isfinite(means)
    in_depth = means[valid] < continuum - 0.5 * (continuum - float(np.nanmin(means[valid])))
    duration_hours: float | None
    if np.any(in_depth):
        # Contiguous run of deep bins around phase 0
        deep_phases = centers[valid][in_depth]
        near = deep_phases[np.abs(deep_phases) < 0.25]
        if len(near) >= 1:
            duration_hours = float((near.max() - near.min()) * period_days * 24.0)
            if duration_hours <= 0:
                duration_hours = float(0.02 * period_days * 24.0)
        else:
            duration_hours = float(0.05 * period_days * 24.0)
    else:
        duration_hours = None

    # Odd/even depths: alternate transit epochs
    transit_half = 0.05  # phase half-width for in-transit average
    epoch_idx = np.floor((time - t0) / period_days + 0.5).astype(int)
    in_transit = np.abs(phase) < transit_half
    odd_depths: list[float] = []
    even_depths: list[float] = []
    for k in np.unique(epoch_idx[in_transit]):
        mask = in_transit & (epoch_idx == k)
        if not np.any(mask):
            continue
        depth_ppm = float((continuum - np.mean(flux[mask])) * 1e6)
        if k % 2 == 0:
            even_depths.append(depth_ppm)
        else:
            odd_depths.append(depth_ppm)

    odd_depth = float(np.mean(odd_depths)) if odd_depths else None
    even_depth = float(np.mean(even_depths)) if even_depths else None
    delta = (
        abs(odd_depth - even_depth)
        if odd_depth is not None and even_depth is not None
        else None
    )

    if odd_depth is None or even_depth is None:
        note = "partial: t0 estimated; odd/even needs more transits"
    else:
        note = "ok"

    depth_ppm = estimate_baseline_depth_ppm(time, flux, period_days, t0=t0)
    if depth_ppm is None and odd_depth is not None and even_depth is not None:
        depth_ppm = float(0.5 * (odd_depth + even_depth))

    return TransitGeometry(
        t0=float(t0),
        duration_hours=duration_hours,
        odd_depth_ppm=odd_depth,
        even_depth_ppm=even_depth,
        odd_even_delta_ppm=delta,
        note=note,
        depth_ppm=depth_ppm,
    )


def _periodogram(time: np.ndarray, flux: np.ndarray, min_p: float, max_p: float):
    x = np.arange(len(flux))
    coef = np.polyfit(x, flux, 2)
    detrended = flux - np.polyval(coef, x)
    freqs = np.linspace(1.0 / max_p, 1.0 / min_p, 2000)
    power = lombscargle(time, detrended, freqs, normalize=True)
    return freqs, power, detrended


def generate_vetting_plots(
    candidate_id: int,
    time: np.ndarray,
    flux: np.ndarray,
    period_days: float,
    *,
    t0: float | None,
    min_period_days: float = 0.5,
    max_period_days: float = 20.0,
) -> dict[str, object]:
    """
    Write phase-fold, odd/even, and periodogram PNGs under the vetting cache.

    Returns {"ok": bool, "plots": list[str], "reason": str|None}.
    """
    if len(time) < 10:
        return {"ok": False, "plots": [], "reason": "insufficient_points"}

    out_dir = vetting_dir(candidate_id, create=True)
    written: list[str] = []
    t_plot, f_plot = _downsample(time, flux)
    epoch = float(t0) if t0 is not None and np.isfinite(t0) else float(np.median(time))

    try:
        # Phase-fold
        phase = _phase_fold(t_plot, period_days, epoch)
        order = np.argsort(phase)
        fig, ax = plt.subplots(figsize=(6.5, 3.8), dpi=110)
        ax.plot(phase[order], f_plot[order], ".", ms=2, alpha=0.45, color="#6ea8fe")
        centers, means = _binned_mean(phase, f_plot, n_bins=60)
        ax.plot(centers, means, "-", color="#ffcc66", lw=1.6, label="binned")
        ax.set_xlabel("Phase")
        ax.set_ylabel("Flux")
        ax.set_title(f"Phase fold · P={period_days:.4f} d · t0={epoch:.4f}")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.25)
        path = out_dir / "phase_fold.png"
        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)
        written.append("phase_fold.png")

        # Odd / even
        phase_full = _phase_fold(time, period_days, epoch)
        epoch_idx = np.floor((time - epoch) / period_days + 0.5).astype(int)
        odd = epoch_idx % 2 != 0
        even = ~odd
        fig, ax = plt.subplots(figsize=(6.5, 3.8), dpi=110)
        if np.any(odd):
            po = phase_full[odd]
            fo = flux[odd]
            po, fo = _downsample(po, fo, 4000)
            ax.plot(po, fo, ".", ms=2, alpha=0.4, color="#6ea8fe", label="odd")
        if np.any(even):
            pe = phase_full[even]
            fe = flux[even]
            pe, fe = _downsample(pe, fe, 4000)
            ax.plot(pe, fe, ".", ms=2, alpha=0.4, color="#ff7b7b", label="even")
        ax.set_xlim(-0.2, 0.2)
        ax.set_xlabel("Phase (zoom)")
        ax.set_ylabel("Flux")
        ax.set_title("Odd vs even transits")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.25)
        path = out_dir / "odd_even.png"
        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)
        written.append("odd_even.png")

        # Periodogram
        freqs, power, _ = _periodogram(time, flux, min_period_days, max_period_days)
        periods = 1.0 / freqs
        fig, ax = plt.subplots(figsize=(6.5, 3.8), dpi=110)
        ax.plot(periods, power, color="#3ddc97", lw=1.0)
        ax.axvline(period_days, color="#ffcc66", ls="--", lw=1.2, label=f"P={period_days:.3f}d")
        ax.set_xlabel("Period (days)")
        ax.set_ylabel("Normalized power")
        ax.set_title("Lomb–Scargle periodogram")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.25)
        path = out_dir / "periodogram.png"
        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)
        written.append("periodogram.png")
    except Exception as exc:  # noqa: BLE001
        plt.close("all")
        logger.warning("vetting_plots_failed", candidate_id=candidate_id, error=str(exc))
        return {"ok": False, "plots": written, "reason": str(exc)}

    logger.info("vetting_plots_written", candidate_id=candidate_id, plots=written, dir=str(out_dir))
    return {"ok": len(written) >= 2, "plots": written, "reason": None}


def load_cached_lc(slug: str) -> tuple[np.ndarray, np.ndarray] | None:
    path = target_cache_path(slug)
    if not path.exists():
        return None
    data = np.load(path)
    return data["time"], data["flux"]
