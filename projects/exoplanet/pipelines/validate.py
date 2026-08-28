"""Phase C: optional statistical validation (TRICERATOPS or bounded equivalent).

Never called from /scan. Enable with EXOPLANET_TRICERATOPS=true.

Runtime:
- Flag off / gate fail: milliseconds.
- Equivalent FPP (default when the package is not installed): < 1 s from stored metrics.
- Real TRICERATOPS: typically 5–30 min on 1 CPU; job timeout EXOPLANET_VALIDATE_TIMEOUT_S
  (default 900 s). Uses N=20_000 MC draws instead of the package default 1e6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from projects.exoplanet.db.models import Candidate, Target, get_db_session, init_db
from projects.exoplanet.pipelines.centroid import is_synthetic_source, load_lightcurve_source
from projects.exoplanet.pipelines.neighbours import dumps_payload, loads_payload, spec_from_target
from projects.exoplanet.settings import get_exoplanet_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)

VALIDATE_QUEUE_NAME = "exoplanet-validate"

# Giacalone et al. 2021 classification cuts (quoted on /review).
FPP_VALIDATED = 0.015
FPP_LIKELY = 0.5
NFPP_VALIDATED = 1e-3
NFPP_NEARBY_FP = 0.1

_TRICERATOPS_N_DRAWS = 20_000

# stev.oapd.inaf.it (TRILEGAL, used by TRICERATOPS) omits this ZeroSSL intermediate.
_TRILEGAL_AIA_CA = "http://crt.sectigo.com/ZeroSSLRSADVSSLCA2.crt"


def _ensure_trilegal_tls() -> str | None:
    """Extend the CA bundle so TRICERATOPS can fetch TRILEGAL over TLS.

    Does not disable verification. Returns the bundle path, or None if skipped.
    """
    import os
    import ssl
    import urllib.request
    from pathlib import Path

    try:
        import certifi
    except Exception:
        return None

    cache = Path(os.getenv("EXOPLANET_CACHE_DIR") or "/tmp")
    dest = cache / "ca-bundle-trilegal.pem"
    if not dest.exists() or dest.stat().st_size < 1000:
        try:
            cache.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(_TRILEGAL_AIA_CA, timeout=20) as resp:
                der = resp.read()
            pem = ssl.DER_cert_to_PEM_cert(der)
            dest.write_text(Path(certifi.where()).read_text(encoding="utf-8") + pem, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("trilegal_ca_bundle_failed", error=str(exc)[:200])
            return None
    path = str(dest)
    os.environ["SSL_CERT_FILE"] = path
    os.environ["REQUESTS_CA_BUNDLE"] = path
    os.environ["CURL_CA_BUNDLE"] = path
    return path


def _ensure_triceratops_numpy_shims() -> None:
    """pytransit 2.2 still imports numpy.int and scipy.integrate.trapz (removed)."""
    aliases = {"int": int, "float": float, "bool": bool, "complex": complex}
    for name, value in aliases.items():
        if name not in np.__dict__:
            setattr(np, name, value)
    if "trapz" not in np.__dict__ and hasattr(np, "trapezoid"):
        np.trapz = np.trapezoid  # type: ignore[attr-defined]
    import scipy.integrate as _sci_integrate

    if not hasattr(_sci_integrate, "trapz") and hasattr(_sci_integrate, "trapezoid"):
        _sci_integrate.trapz = _sci_integrate.trapezoid  # type: ignore[attr-defined]
    _ensure_trilegal_tls()


def _ensure_triceratops_numpy_shims() -> None:
    """pytransit 2.2 still imports numpy.int and scipy.integrate.trapz (removed)."""
    aliases = {"int": int, "float": float, "bool": bool, "complex": complex}
    for name, value in aliases.items():
        if name not in np.__dict__:
            setattr(np, name, value)
    if "trapz" not in np.__dict__ and hasattr(np, "trapezoid"):
        np.trapz = np.trapezoid  # type: ignore[attr-defined]
    import scipy.integrate as _sci_integrate

    if not hasattr(_sci_integrate, "trapz") and hasattr(_sci_integrate, "trapezoid"):
        _sci_integrate.trapz = _sci_integrate.trapezoid  # type: ignore[attr-defined]


@dataclass(frozen=True)
class ValidationInput:
    mission: str
    snr: float
    period_days: float
    tic_id: str | None
    lc_source: str | None
    odd_depth_ppm: float | None
    even_depth_ppm: float | None
    dilution: float | None
    brightest_delta_mag: float | None
    centroid_pass: bool | None
    centroid_pvalue: float | None


def unavailable_validation(reason: str, **extra: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "unavailable",
        "method": None,
        "fpp": None,
        "nfpp": None,
        "prob_eb": None,
        "prob_nearby_eb": None,
        "reason": reason,
        "error": extra.pop("error", None),
    }
    payload.update(extra)
    return payload


def _clip01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number


def equivalent_fpp(
    *,
    odd_depth_ppm: float | None,
    even_depth_ppm: float | None,
    dilution: float | None,
    brightest_delta_mag: float | None,
    centroid_pass: bool | None,
    centroid_pvalue: float | None,
) -> dict[str, Any]:
    """Bounded FPP/NFPP from Phase A/B metrics — not a TRICERATOPS posterior.

    Combines odd–even EB risk, Gaia dilution / bright neighbour, and centroid shift.
    """
    p_eb = 0.08
    if odd_depth_ppm is not None and even_depth_ppm is not None:
        scale = max(abs(odd_depth_ppm), abs(even_depth_ppm), 1.0)
        p_eb = _clip01(abs(odd_depth_ppm - even_depth_ppm) / scale)

    p_blend = 0.08
    if dilution is not None:
        p_blend = _clip01(1.0 - dilution)
    if brightest_delta_mag is not None and brightest_delta_mag < 1.0:
        p_blend = _clip01(max(p_blend, 0.45))
    elif brightest_delta_mag is not None and brightest_delta_mag < 2.5:
        p_blend = _clip01(max(p_blend, 0.2))

    p_bg = 0.08
    if centroid_pass is False:
        p_bg = 0.7
    elif centroid_pass is True:
        p_bg = 0.01
    elif centroid_pvalue is not None and centroid_pvalue < 0.05:
        p_bg = 0.6

    fpp = _clip01(1.0 - (1.0 - p_eb) * (1.0 - p_blend) * (1.0 - p_bg))
    nfpp = _clip01(1.0 - (1.0 - p_blend) * (1.0 - p_bg))
    return {
        "status": "ok",
        "method": "equivalent",
        "fpp": fpp,
        "nfpp": nfpp,
        "prob_eb": p_eb,
        "prob_nearby_eb": nfpp,
        "reason": None,
        "error": None,
        "components": {
            "p_eb": p_eb,
            "p_blend": p_blend,
            "p_background": p_bg,
        },
    }


def try_triceratops_fpp(
    *,
    tic_id: str,
    period_days: float,
    time: np.ndarray,
    flux: np.ndarray,
    flux_err: np.ndarray | None,
    sectors: np.ndarray | None = None,
    t0: float | None = None,
) -> dict[str, Any]:
    """Call TRICERATOPS if installed. Raises on failure so the job can record a snippet."""
    _ensure_triceratops_numpy_shims()
    from triceratops.triceratops import target as TrTarget

    from projects.exoplanet.pipelines.vetting import _phase_fold

    tid = int(str(tic_id).replace("TIC", "").replace("tic", "").strip())
    sec = np.asarray(sectors if sectors is not None else [1], dtype=int)
    tgt = TrTarget(ID=tid, sectors=sec)
    err = float(np.nanmedian(flux_err)) if flux_err is not None and len(flux_err) else 1e-4
    if not np.isfinite(err) or err <= 0:
        err = 1e-4
    time_arr = np.asarray(time, dtype=float)
    if t0 is not None and np.isfinite(t0) and period_days > 0:
        # TRICERATOPS wants days from transit midpoint on a phase-folded curve.
        time_arr = _phase_fold(time_arr, float(period_days), float(t0)) * float(period_days)
    tgt.calc_probs(
        time=time_arr,
        flux_0=np.asarray(flux, dtype=float),
        flux_err_0=err,
        P_orb=float(period_days),
        N=_TRICERATOPS_N_DRAWS,
        parallel=False,
        verbose=0,
    )
    fpp = float(getattr(tgt, "FPP"))
    nfpp = float(getattr(tgt, "NFPP"))
    return {
        "status": "ok",
        "method": "triceratops",
        "fpp": fpp,
        "nfpp": nfpp,
        "prob_eb": None,
        "prob_nearby_eb": nfpp,
        "reason": None,
        "error": None,
        "n_draws": _TRICERATOPS_N_DRAWS,
    }


def _is_retryable(payload: dict[str, Any] | None) -> bool:
    if not payload or not payload.get("status"):
        return True
    reason = str(payload.get("reason") or "").lower()
    return any(token in reason for token in ("queued", "timeout", "no_network", "triceratops_error"))


def compute_validation_payload(
    inp: ValidationInput,
    *,
    enabled: bool,
    min_snr: float,
    min_period_days: float = 0.5,
    max_period_days: float = 20.0,
    triceratops_runner=None,
) -> dict[str, Any]:
    """Pure gating + FPP. triceratops_runner(inp) -> dict | None for tests."""
    gate = {
        "enabled": enabled,
        "min_snr": min_snr,
        "snr": inp.snr,
        "mission": inp.mission,
        "min_period_days": min_period_days,
        "max_period_days": max_period_days,
        "period_days": inp.period_days,
    }
    if not enabled:
        return unavailable_validation("triceratops_disabled", gate=gate)
    mission = (inp.mission or "").upper()
    if mission != "TESS":
        return unavailable_validation("mission_not_tess", gate=gate)
    if inp.snr < min_snr:
        return unavailable_validation("below_snr_gate", gate=gate)
    if inp.period_days < min_period_days or inp.period_days > max_period_days:
        return unavailable_validation("period_out_of_gate", gate=gate)
    if is_synthetic_source(inp.lc_source):
        return unavailable_validation("synthetic_lc", gate=gate)
    if not inp.tic_id:
        return unavailable_validation("no_tic_id", gate=gate)

    runner = triceratops_runner
    try:
        tri = runner(inp) if runner is not None else None
    except Exception as exc:  # noqa: BLE001
        return unavailable_validation(
            "triceratops_error",
            gate=gate,
            error=str(exc)[:300],
        )
    if tri and tri.get("status") == "ok":
        tri = dict(tri)
        tri["gate"] = gate
        return tri

    payload = equivalent_fpp(
        odd_depth_ppm=inp.odd_depth_ppm,
        even_depth_ppm=inp.even_depth_ppm,
        dilution=inp.dilution,
        brightest_delta_mag=inp.brightest_delta_mag,
        centroid_pass=inp.centroid_pass,
        centroid_pvalue=inp.centroid_pvalue,
    )
    payload["gate"] = gate
    if runner is not None and tri is None:
        payload["note"] = "triceratops_not_installed; used equivalent FPP"
    return payload


def _input_from_row(candidate: Candidate, target: Target) -> ValidationInput:
    neighbours = loads_payload(getattr(candidate, "neighbours_json", None)) or loads_payload(
        getattr(target, "neighbours_json", None)
    )
    centroid = loads_payload(getattr(candidate, "centroid_json", None)) or {}
    spec = spec_from_target(target)
    centroid_pass = centroid.get("pass") if isinstance(centroid, dict) else None
    if centroid_pass is not None:
        centroid_pass = bool(centroid_pass)
    return ValidationInput(
        mission=target.mission or spec.mission,
        snr=float(candidate.snr or 0.0),
        period_days=float(candidate.period_days or 0.0),
        tic_id=spec.tic_id or (target.external_id if (target.mission or "").upper() == "TESS" else None),
        lc_source=load_lightcurve_source(target.slug),
        odd_depth_ppm=_as_float(getattr(candidate, "odd_depth_ppm", None)),
        even_depth_ppm=_as_float(getattr(candidate, "even_depth_ppm", None)),
        dilution=_as_float((neighbours or {}).get("dilution") if neighbours else None),
        brightest_delta_mag=_as_float((neighbours or {}).get("brightest_delta_mag") if neighbours else None),
        centroid_pass=centroid_pass,
        centroid_pvalue=_as_float((centroid or {}).get("pvalue") if centroid else None),
    )


def _load_lc_arrays(slug: str) -> tuple[np.ndarray, np.ndarray, np.ndarray | None] | None:
    from projects.exoplanet.pipelines.cache_manager import target_cache_path

    path = target_cache_path(slug)
    if not path.exists():
        return None
    data = np.load(path)
    time = np.asarray(data["time"], dtype=float)
    flux = np.asarray(data["flux"], dtype=float)
    flux_err = np.asarray(data["flux_err"], dtype=float) if "flux_err" in data.files else None
    return time, flux, flux_err


def _guess_sectors(slug: str) -> np.ndarray:
    import re

    from projects.exoplanet.pipelines.cache_manager import existing_tpf

    path = existing_tpf(slug)
    if path is not None:
        match = re.search(r"s(\d{4})", path.stem, re.I)
        if match:
            return np.array([int(match.group(1))])
    return np.array([1])


def vet_validate(candidate_id: int, *, force: bool = False) -> dict[str, Any]:
    """Idempotent Phase C job. Never invoked by scan/analyze."""
    init_db()
    settings = get_exoplanet_settings()
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
        existing = loads_payload(getattr(candidate, "validation_json", None))
        if not force and existing and not _is_retryable(existing):
            return {"ok": True, "candidate_id": candidate_id, "validation": existing, "cached": True}

        inp = _input_from_row(candidate, target)

        def _runner(payload_inp: ValidationInput) -> dict[str, Any] | None:
            try:
                _ensure_triceratops_numpy_shims()
                import triceratops.triceratops  # noqa: F401
            except Exception as exc:  # noqa: BLE001
                logger.warning("triceratops_import_failed", error=str(exc)[:300])
                return None
            arrays = _load_lc_arrays(target.slug)
            if arrays is None or not payload_inp.tic_id:
                return None
            time, flux, flux_err = arrays
            return try_triceratops_fpp(
                tic_id=str(payload_inp.tic_id),
                period_days=payload_inp.period_days,
                time=time,
                flux=flux,
                flux_err=flux_err,
                sectors=_guess_sectors(target.slug),
                t0=_as_float(getattr(candidate, "t0", None)),
            )

        try:
            validation = compute_validation_payload(
                inp,
                enabled=bool(settings.triceratops),
                min_snr=float(settings.validate_min_snr),
                min_period_days=float(settings.min_period_days),
                max_period_days=float(settings.max_period_days),
                triceratops_runner=_runner,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("vet_validate_failed", candidate_id=candidate_id, error=str(exc))
            validation = unavailable_validation("triceratops_error", error=str(exc)[:300])

        candidate.validation_json = dumps_payload(validation)
        session.commit()
        return {"ok": True, "candidate_id": candidate_id, "validation": validation, "cached": False}
    finally:
        session.close()


def enqueue_validation(candidate_id: int, *, force: bool = True) -> dict[str, Any]:
    """Queue Phase C on exoplanet-validate (does not run inside /scan)."""
    init_db()
    session = get_db_session()
    try:
        candidate = session.query(Candidate).filter_by(id=candidate_id).one_or_none()
        if candidate is None:
            return {"ok": False, "reason": "not_found", "candidate_id": candidate_id}
        queued = unavailable_validation("queued")
        candidate.validation_json = dumps_payload(queued)
        session.commit()
    finally:
        session.close()

    from research_platform.workers.base import get_queue

    settings = get_exoplanet_settings()
    timeout = max(60, int(settings.validate_timeout_s))
    queue = get_queue("exoplanet", queue_name=VALIDATE_QUEUE_NAME)
    from projects.exoplanet.workers.jobs import exoplanet_vet_validate_job

    job = queue.enqueue(
        exoplanet_vet_validate_job,
        candidate_id,
        force,
        job_timeout=timeout,
        result_ttl=86400,
        failure_ttl=86400,
    )
    logger.info(
        "validation_enqueued",
        candidate_id=candidate_id,
        job_id=job.id,
        timeout_s=timeout,
        enabled=bool(settings.triceratops),
    )
    return {
        "ok": True,
        "candidate_id": candidate_id,
        "queued": True,
        "job_id": job.id,
        "timeout_s": timeout,
        "enabled": bool(settings.triceratops),
        "validation": queued,
    }
