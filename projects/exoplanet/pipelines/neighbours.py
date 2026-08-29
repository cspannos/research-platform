"""Phase B: resolve coordinates, Gaia cone (~1'), dilution, optional TPF centroid."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from astropy.coordinates import SkyCoord
from astropy import units as u

from projects.exoplanet.db.models import Candidate, Target, get_db_session, init_db
from projects.exoplanet.pipelines.centroid import load_lightcurve_source, run_centroid_test
from projects.exoplanet.settings import TargetSpec, load_targets
from research_platform.core.logging import get_logger

logger = get_logger(__name__)

CONE_RADIUS_ARCMIN = 1.0
TESS_APERTURE_ARCSEC = 21.0
KEPLER_APERTURE_ARCSEC = 4.0
TARGET_MATCH_ARCSEC = 1.5
MAX_NEIGHBOURS_STORED = 20
GAIA_ROW_LIMIT = 80
GAIA_TIMEOUT_S = 30.0

# Dilution / bright-neighbour gates for the checklist (used here as flags).
DILUTION_FAIL = 0.80
DILUTION_UNCLEAR = 0.95
BRIGHT_FAIL_DMAG = 1.0
BRIGHT_UNCLEAR_DMAG = 2.5


def dumps_payload(payload: dict[str, Any]) -> str:
    def _default(obj: object) -> object:
        if isinstance(obj, (np.floating, np.integer)):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        raise TypeError(f"unserializable {type(obj)}")

    return json.dumps(payload, default=_default)


def loads_payload(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def aperture_arcsec_for_mission(mission: str) -> float:
    if str(mission).upper() == "KEPLER":
        return KEPLER_APERTURE_ARCSEC
    return TESS_APERTURE_ARCSEC


def spec_from_target(target: Target) -> TargetSpec:
    for spec in load_targets():
        if spec.id == target.slug:
            ra = spec.ra if spec.ra is not None else getattr(target, "ra", None)
            dec = spec.dec if spec.dec is not None else getattr(target, "dec", None)
            return spec.model_copy(update={"ra": ra, "dec": dec})
    mission = (target.mission or "").upper()
    external = target.external_id or ""
    return TargetSpec(
        id=target.slug,
        name=target.name,
        mission=mission or "TESS",
        tic_id=external if mission == "TESS" else None,
        kic_id=external if mission == "KEPLER" else None,
        ra=getattr(target, "ra", None),
        dec=getattr(target, "dec", None),
        notes=target.notes or "",
    )


def _finite_pair(ra: object, dec: object) -> tuple[float, float] | None:
    try:
        ra_f = float(ra)  # type: ignore[arg-type]
        dec_f = float(dec)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not np.isfinite(ra_f) or not np.isfinite(dec_f):
        return None
    return ra_f, dec_f


def resolve_coordinates(spec: TargetSpec) -> tuple[float, float, str] | None:
    """RA/Dec from yaml, then MAST TIC/KIC via astroquery (graceful on network fail)."""
    pair = _finite_pair(spec.ra, spec.dec)
    if pair is not None:
        return pair[0], pair[1], "targets.yaml"

    try:
        from astroquery.mast import Catalogs
    except Exception as exc:  # noqa: BLE001
        logger.info("coord_resolve_skipped", target=spec.id, error=str(exc))
        return None

    queries: list[tuple[str, dict[str, object]]] = []
    if spec.tic_id:
        try:
            queries.append(("tic", {"catalog": "TIC", "ID": int(spec.tic_id)}))
        except ValueError:
            queries.append(("tic", {"catalog": "TIC", "ID": spec.tic_id}))
    if spec.kic_id:
        try:
            queries.append(("kic", {"catalog": "Kepler", "kepid": int(spec.kic_id)}))
        except ValueError:
            pass
    queries.append(("name", {"catalog": "TIC" if spec.mission.upper() == "TESS" else "Kepler"}))

    for source, kwargs in queries:
        try:
            if source == "name":
                table = Catalogs.query_object(spec.name, **kwargs)
            else:
                table = Catalogs.query_criteria(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("coord_catalog_failed", target=spec.id, source=source, error=str(exc))
            continue
        if table is None or len(table) == 0:
            continue
        row = table[0]
        keys = {str(k).lower(): k for k in row.colnames}
        ra_key = keys.get("ra")
        dec_key = keys.get("dec") or keys.get("declination")
        if ra_key is None or dec_key is None:
            continue
        pair = _finite_pair(row[ra_key], row[dec_key])
        if pair is not None:
            return pair[0], pair[1], source
    return None


def query_gaia_cone(ra: float, dec: float, radius_arcmin: float = CONE_RADIUS_ARCMIN) -> list[dict[str, Any]]:
    """Gaia DR3 cone via astroquery. Raises on hard failures; caller maps to unavailable."""
    from astroquery.gaia import Gaia

    Gaia.ROW_LIMIT = GAIA_ROW_LIMIT
    try:
        Gaia.TIMEOUT = GAIA_TIMEOUT_S
    except Exception:  # noqa: BLE001
        pass
    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    # astroquery >= 0.4.11 makes radius keyword-only.
    job = Gaia.cone_search_async(coord, radius=radius_arcmin * u.arcmin)
    table = job.get_results()
    rows: list[dict[str, Any]] = []
    if table is None:
        return rows
    colnames = {str(c).lower(): c for c in table.colnames}
    sid_k = colnames.get("source_id")
    ra_k = colnames.get("ra")
    dec_k = colnames.get("dec")
    mag_k = colnames.get("phot_g_mean_mag")
    for row in table:
        try:
            source_id = str(row[sid_k]) if sid_k else ""
            ra_i = float(row[ra_k])
            dec_i = float(row[dec_k])
        except (TypeError, ValueError, KeyError):
            continue
        gmag = None
        if mag_k is not None:
            try:
                mag = float(row[mag_k])
                if np.isfinite(mag):
                    gmag = mag
            except (TypeError, ValueError):
                gmag = None
        rows.append(
            {
                "source_id": source_id,
                "ra": ra_i,
                "dec": dec_i,
                "gmag": gmag,
            }
        )
    return rows


def _mag_to_flux(mag: float) -> float:
    return float(10 ** (-0.4 * mag))


def summarise_neighbours(
    rows: list[dict[str, Any]],
    *,
    ra: float,
    dec: float,
    aperture_arcsec: float,
    radius_arcmin: float = CONE_RADIUS_ARCMIN,
    coord_source: str = "unknown",
) -> dict[str, Any]:
    """Build a neighbour summary dict from Gaia-like rows (ra/dec/gmag)."""
    origin = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    enriched: list[dict[str, Any]] = []
    for row in rows:
        pair = _finite_pair(row.get("ra"), row.get("dec"))
        if pair is None:
            continue
        sep = float(origin.separation(SkyCoord(ra=pair[0] * u.deg, dec=pair[1] * u.deg)).arcsec)
        gmag = row.get("gmag")
        try:
            gmag_f = float(gmag) if gmag is not None else None
            if gmag_f is not None and not np.isfinite(gmag_f):
                gmag_f = None
        except (TypeError, ValueError):
            gmag_f = None
        enriched.append(
            {
                "source_id": str(row.get("source_id") or ""),
                "ra": pair[0],
                "dec": pair[1],
                "gmag": gmag_f,
                "sep_arcsec": sep,
            }
        )
    enriched.sort(key=lambda r: r["sep_arcsec"])

    target_row = next((r for r in enriched if r["sep_arcsec"] <= TARGET_MATCH_ARCSEC), None)
    target_gmag = target_row["gmag"] if target_row else None
    neighbours = [r for r in enriched if r["sep_arcsec"] > TARGET_MATCH_ARCSEC]

    for item in neighbours:
        if target_gmag is not None and item["gmag"] is not None:
            item["delta_mag"] = float(item["gmag"] - target_gmag)
        else:
            item["delta_mag"] = None
        item["in_aperture"] = bool(item["sep_arcsec"] <= aperture_arcsec)

    in_ap = [n for n in neighbours if n["in_aperture"]]
    mag_neighbours = [n for n in neighbours if n["gmag"] is not None]
    brightest_delta = None
    if target_gmag is not None and mag_neighbours:
        brightest_delta = min(float(n["gmag"] - target_gmag) for n in mag_neighbours)

    dilution = None
    if target_gmag is not None:
        f_t = _mag_to_flux(target_gmag)
        f_n = 0.0
        for n in in_ap:
            if n["gmag"] is not None:
                f_n += _mag_to_flux(float(n["gmag"]))
        denom = f_t + f_n
        if denom > 0:
            dilution = float(f_t / denom)

    return {
        "status": "ok",
        "reason": None,
        "ra": ra,
        "dec": dec,
        "coord_source": coord_source,
        "radius_arcmin": radius_arcmin,
        "aperture_arcsec": aperture_arcsec,
        "n_gaia": len(enriched),
        "n_neighbours": len(neighbours),
        "n_in_aperture": len(in_ap),
        "target_gmag": target_gmag,
        "brightest_delta_mag": brightest_delta,
        "dilution": dilution,
        "neighbours": neighbours[:MAX_NEIGHBOURS_STORED],
    }


def unavailable_neighbours(reason: str, **extra: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "unavailable",
        "reason": reason,
        "n_neighbours": None,
        "n_in_aperture": None,
        "brightest_delta_mag": None,
        "dilution": None,
        "neighbours": [],
    }
    payload.update(extra)
    return payload


def _is_retryable(payload: dict[str, Any] | None) -> bool:
    if not payload or not payload.get("status"):
        return True
    reason = str(payload.get("reason") or "").lower()
    return "no_network" in reason or "timeout" in reason


def run_gaia_neighbours(
    spec: TargetSpec,
    *,
    cached_ra: float | None = None,
    cached_dec: float | None = None,
) -> tuple[dict[str, Any], float | None, float | None]:
    """Resolve coords + Gaia cone. Returns (payload, ra, dec)."""
    resolved = None
    pair = _finite_pair(spec.ra, spec.dec)
    source = "targets.yaml"
    if pair is None:
        pair = _finite_pair(cached_ra, cached_dec)
        source = "db"
    if pair is None:
        resolved = resolve_coordinates(spec)
        if resolved is None:
            return unavailable_neighbours("no_coords"), None, None
        ra, dec, source = resolved
    else:
        ra, dec = pair
        # Prefer yaml/db; still record source.
        if spec.ra is not None:
            source = "targets.yaml"

    try:
        rows = query_gaia_cone(ra, dec, CONE_RADIUS_ARCMIN)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gaia_cone_failed", target=spec.id, error=str(exc))
        err = str(exc).lower()
        reason = "no_network" if any(k in err for k in ("timeout", "network", "connect", "resolve")) else f"gaia_error:{exc}"
        return unavailable_neighbours(reason, ra=ra, dec=dec, coord_source=source), ra, dec

    aperture = aperture_arcsec_for_mission(spec.mission)
    summary = summarise_neighbours(
        rows,
        ra=ra,
        dec=dec,
        aperture_arcsec=aperture,
        coord_source=source,
    )
    return summary, ra, dec


def apply_neighbour_vetting(
    candidate: Candidate,
    target: Target,
    spec: TargetSpec | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Mutate candidate/target with neighbour + centroid JSON. Caller commits."""
    spec = spec or spec_from_target(target)
    existing_nb = loads_payload(getattr(candidate, "neighbours_json", None)) or loads_payload(
        getattr(target, "neighbours_json", None)
    )
    existing_cent = loads_payload(getattr(candidate, "centroid_json", None))

    if force or _is_retryable(existing_nb):
        neighbours, ra, dec = run_gaia_neighbours(
            spec,
            cached_ra=getattr(target, "ra", None),
            cached_dec=getattr(target, "dec", None),
        )
        if ra is not None:
            target.ra = float(ra)
        if dec is not None:
            target.dec = float(dec)
        target.neighbours_json = dumps_payload(neighbours)
        candidate.neighbours_json = target.neighbours_json
    else:
        neighbours = existing_nb or unavailable_neighbours("cached")
        candidate.neighbours_json = dumps_payload(neighbours)
        if getattr(target, "neighbours_json", None) in (None, ""):
            target.neighbours_json = candidate.neighbours_json

    lc_source = load_lightcurve_source(target.slug)
    if not force and not _is_retryable(existing_cent) and existing_cent:
        centroid = existing_cent
    else:
        centroid = run_centroid_test(
            spec,
            period_days=float(candidate.period_days),
            t0=getattr(candidate, "t0", None),
            duration_hours=getattr(candidate, "duration_hours", None),
            lc_source=lc_source,
            force=force,
        )
    candidate.centroid_json = dumps_payload(centroid)
    return {"neighbours": neighbours, "centroid": centroid}


def vet_neighbours(candidate_id: int, *, force: bool = False) -> dict[str, Any]:
    """Idempotent Phase B job: Gaia neighbours + optional TPF centroid."""
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
        result = apply_neighbour_vetting(candidate, target, force=force)
        session.commit()
        return {"ok": True, "candidate_id": candidate_id, **result}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("vet_neighbours_failed", candidate_id=candidate_id, error=str(exc))
        return {"ok": False, "reason": str(exc), "candidate_id": candidate_id}
    finally:
        session.close()


def neighbour_risk_flags(neighbours: dict[str, Any] | None) -> str | None:
    """Short contamination flag for expert context, or None."""
    if not neighbours or neighbours.get("status") != "ok":
        return None
    bits: list[str] = []
    dmag = neighbours.get("brightest_delta_mag")
    dil = neighbours.get("dilution")
    if isinstance(dmag, (int, float)) and dmag < BRIGHT_FAIL_DMAG:
        bits.append(f"bright neighbour ΔG={dmag:.2f}")
    elif isinstance(dmag, (int, float)) and dmag < BRIGHT_UNCLEAR_DMAG:
        bits.append(f"nearby star ΔG={dmag:.2f}")
    if isinstance(dil, (int, float)) and dil < DILUTION_FAIL:
        bits.append(f"high dilution ({dil:.2f})")
    elif isinstance(dil, (int, float)) and dil < DILUTION_UNCLEAR:
        bits.append(f"moderate dilution ({dil:.2f})")
    return "; ".join(bits) if bits else None
