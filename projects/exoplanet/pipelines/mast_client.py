from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
import numpy as np
from astropy.io import fits

from projects.exoplanet.settings import TargetSpec, get_exoplanet_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)

MAST_INVOKE_URL = "https://mast.stsci.edu/api/v0/invoke"
MAST_DOWNLOAD_URL = "https://mast.stsci.edu/api/v0.1/Download/file"
MAX_LIGHTCURVE_POINTS = 25_000


@dataclass(frozen=True)
class LightCurveData:
    time: np.ndarray
    flux: np.ndarray
    flux_err: np.ndarray
    source: str


def _mast_headers(token: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _mast_invoke(request: dict, token: str, *, timeout: float = 60.0) -> dict:
    """Call MAST mashup invoke with form-encoded `request=` (required by the API)."""
    payload = "request=" + quote(json.dumps(request))
    with httpx.Client(timeout=timeout) as client:
        response = client.post(MAST_INVOKE_URL, content=payload, headers=_mast_headers(token))
    if response.status_code != 200:
        logger.warning(
            "mast_invoke_failed",
            status=response.status_code,
            service=request.get("service"),
            body=response.text[:200],
        )
        return {}
    try:
        return response.json()
    except json.JSONDecodeError:
        # Some MAST responses are a JSON string embedded in text.
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            logger.warning("mast_invoke_invalid_json", service=request.get("service"))
            return {}


def _target_search_names(target: TargetSpec) -> list[str]:
    names: list[str] = []
    if target.tic_id:
        names.extend([f"TIC {target.tic_id}", target.tic_id, f"TIC{target.tic_id}"])
    if target.kic_id:
        names.extend([f"KIC {target.kic_id}", target.kic_id, f"KIC{target.kic_id}"])
    names.append(target.name)
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _query_mast_observations(target: TargetSpec, token: str) -> list[dict]:
    mission = target.mission.upper()
    for name in _target_search_names(target):
        request = {
            "service": "Mast.Caom.Filtered",
            "format": "json",
            "params": {
                "columns": "*",
                "filters": [
                    {"paramName": "obs_collection", "values": [mission]},
                    {"paramName": "dataproduct_type", "values": ["timeseries"]},
                    {"paramName": "target_name", "values": [name]},
                ],
            },
            "pagesize": 20,
            "page": 1,
        }
        data = _mast_invoke(request, token)
        rows = data.get("data") or []
        if rows:
            logger.info("mast_observations_found", target=target.id, count=len(rows), query=name)
            return rows
    return []


def _query_mast_products(obsid: str, token: str) -> list[dict]:
    request = {
        "service": "Mast.Caom.Products",
        "format": "json",
        "params": {"obsid": str(obsid)},
        "pagesize": 200,
        "page": 1,
    }
    data = _mast_invoke(request, token, timeout=90.0)
    return data.get("data") or []


def _is_lightcurve_product(product: dict) -> bool:
    filename = str(product.get("productFilename") or "").lower()
    description = str(product.get("description") or "").lower()
    subgroup = str(product.get("productSubGroupDescription") or "").upper()
    if subgroup in {"LC", "LLC", "SLC"}:
        return True
    if "lc.fits" in filename or filename.endswith("_lc.fits.gz"):
        return True
    if "light curve" in description or "lightcurve" in description:
        return True
    return False


def _pick_lightcurve_product(products: list[dict]) -> dict | None:
    candidates = [p for p in products if _is_lightcurve_product(p) and p.get("dataURI")]
    if not candidates:
        return None

    def score(product: dict) -> tuple[int, int, float]:
        filename = str(product.get("productFilename") or "").lower()
        # Prefer PDC SAP / science products and smaller files.
        prefer = 0
        if "pdcsap" in filename or "lc.fits" in filename:
            prefer += 2
        if str(product.get("productType") or "").upper() == "SCIENCE":
            prefer += 1
        size = int(float(product.get("size") or 0))
        # Prefer calibrated higher levels when present.
        calib = int(float(product.get("calib_level") or 0))
        return (prefer, calib, -size if size else 0)

    return sorted(candidates, key=score, reverse=True)[0]


def _download_product_bytes(data_uri: str, token: str) -> bytes:
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        response = client.get(MAST_DOWNLOAD_URL, params={"uri": data_uri}, headers=headers)
    response.raise_for_status()
    content = response.content
    if len(content) < 100:
        raise RuntimeError("MAST download returned empty/too-small payload")
    # MAST sometimes returns an HTML error page.
    if content[:20].lstrip().lower().startswith(b"<!doctype") or content[:6].lstrip().lower().startswith(
        b"<html"
    ):
        raise RuntimeError("MAST download returned HTML instead of FITS")
    return content


def _first_present(names: list[str], available: set[str]) -> str | None:
    for name in names:
        if name in available:
            return name
    # Case-insensitive fallback.
    lower_map = {n.lower(): n for n in available}
    for name in names:
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return None


def _extract_lightcurve_from_fits(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with fits.open(path, memmap=False) as hdul:
        table = None
        for hdu in hdul:
            if getattr(hdu, "data", None) is None:
                continue
            names = set(getattr(hdu, "columns", None).names if getattr(hdu, "columns", None) else [])
            if not names:
                continue
            time_col = _first_present(["TIME", "BTJD", "BJD", "JD"], names)
            flux_col = _first_present(
                ["PDCSAP_FLUX", "SAP_FLUX", "FLUX", "KSPSAP_FLUX", "DET_FLUX"],
                names,
            )
            if time_col and flux_col:
                table = hdu.data
                time = np.asarray(table[time_col], dtype=float)
                flux = np.asarray(table[flux_col], dtype=float)
                err_col = _first_present(
                    ["PDCSAP_FLUX_ERR", "SAP_FLUX_ERR", "FLUX_ERR", "ERROR"],
                    names,
                )
                flux_err = (
                    np.asarray(table[err_col], dtype=float)
                    if err_col
                    else np.full_like(flux, np.nanmedian(np.abs(np.diff(flux))) or 1e-4)
                )
                break
        if table is None:
            raise RuntimeError("No TIME/FLUX columns found in FITS product")

    mask = np.isfinite(time) & np.isfinite(flux)
    time, flux, flux_err = time[mask], flux[mask], flux_err[mask]
    if len(time) < 50:
        raise RuntimeError(f"Too few finite light-curve points ({len(time)})")

    # Normalize flux around unity when values look absolute.
    med = float(np.nanmedian(flux))
    if med != 0 and abs(med - 1.0) > 0.05:
        flux = flux / med
        flux_err = flux_err / abs(med)

    if len(time) > MAX_LIGHTCURVE_POINTS:
        idx = np.linspace(0, len(time) - 1, MAX_LIGHTCURVE_POINTS).astype(int)
        time, flux, flux_err = time[idx], flux[idx], flux_err[idx]

    order = np.argsort(time)
    return time[order], flux[order], flux_err[order]


def _fetch_mast_lightcurve(target: TargetSpec, token: str) -> LightCurveData | None:
    observations = _query_mast_observations(target, token)
    if not observations:
        logger.warning("mast_no_observations", target=target.id)
        return None

    # Prefer longer / more recent timeseries when available.
    def obs_score(row: dict) -> tuple[float, float]:
        return (float(row.get("t_exptime") or 0), float(row.get("t_max") or 0))

    for obs in sorted(observations, key=obs_score, reverse=True)[:8]:
        obsid = obs.get("obsid") or obs.get("obsID")
        if not obsid:
            continue
        products = _query_mast_products(str(obsid), token)
        product = _pick_lightcurve_product(products)
        if product is None:
            continue
        data_uri = product["dataURI"]
        filename = product.get("productFilename") or "lightcurve.fits"
        try:
            payload = _download_product_bytes(data_uri, token)
            suffix = ".fits.gz" if filename.endswith(".gz") else ".fits"
            with tempfile.NamedTemporaryFile(prefix="mast-", suffix=suffix, delete=True) as tmp:
                tmp.write(payload)
                tmp.flush()
                time, flux, flux_err = _extract_lightcurve_from_fits(Path(tmp.name))
            source = f"mast:{filename}"
            logger.info(
                "mast_lightcurve_downloaded",
                target=target.id,
                n_points=len(time),
                source=source,
                obsid=obsid,
            )
            return LightCurveData(time=time, flux=flux, flux_err=flux_err, source=source)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mast_product_failed",
                target=target.id,
                obsid=obsid,
                uri=data_uri,
                error=str(exc),
            )
            continue
    return None


def _synthetic_lightcurve(target: TargetSpec, n_points: int = 2000) -> LightCurveData:
    """Deterministic synthetic curve for offline/dev when MAST is unavailable."""
    rng = np.random.default_rng(abs(hash(target.id)) % (2**32))
    time = np.sort(rng.uniform(0, 30, n_points))
    flux = np.ones_like(time)
    period = 3.7 if "715" in target.id else 5.2
    phase = (time % period) / period
    transit_mask = phase < 0.05
    depth = 0.0025 if target.mission.upper() == "TESS" else 0.0018
    flux[transit_mask] -= depth
    flux += rng.normal(0, 0.0004, size=n_points)
    flux_err = np.full(n_points, 0.0004)
    return LightCurveData(time=time, flux=flux, flux_err=flux_err, source="synthetic")


def fetch_lightcurve(target: TargetSpec, *, allow_synthetic: bool | None = None) -> LightCurveData:
    """
    Fetch a real targeted light curve via MAST (observation → LC product → FITS).

    Falls back to synthetic data when MAST fails and allow_synthetic is true
    (default from EXOPLANET_ALLOW_SYNTHETIC, True).
    """
    settings = get_exoplanet_settings()
    if allow_synthetic is None:
        allow_synthetic = settings.allow_synthetic

    token = settings.mast_api_token
    if token:
        curve = _fetch_mast_lightcurve(target, token)
        if curve is not None:
            return curve
        logger.warning("mast_real_fetch_failed", target=target.id)
    else:
        logger.warning("mast_token_missing", target=target.id)

    if allow_synthetic:
        logger.info("mast_fallback_synthetic", target=target.id)
        return _synthetic_lightcurve(target)

    raise RuntimeError(
        f"No real MAST light curve for {target.id}. "
        "Set MAST_API_TOKEN and ensure the target has public LC products, "
        "or set EXOPLANET_ALLOW_SYNTHETIC=true."
    )
