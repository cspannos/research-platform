from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from projects.exoplanet.db.models import LightCurve, Target, get_db_session, init_db
from projects.exoplanet.pipelines.cache_manager import enforce_retention, target_cache_path
from projects.exoplanet.pipelines.mast_client import fetch_lightcurve
from projects.exoplanet.settings import TargetSpec, get_exoplanet_settings, load_targets
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


def _upsert_target(session, spec: TargetSpec) -> Target:
    external_id = spec.tic_id or spec.kic_id or spec.name
    row = session.query(Target).filter_by(slug=spec.id).one_or_none()
    if row is None:
        row = Target(
            slug=spec.id,
            name=spec.name,
            mission=spec.mission.upper(),
            external_id=external_id,
            notes=spec.notes,
            active=True,
        )
        session.add(row)
        session.flush()
    else:
        row.name = spec.name
        row.notes = spec.notes
        row.external_id = external_id
    if spec.ra is not None:
        row.ra = float(spec.ra)
    if spec.dec is not None:
        row.dec = float(spec.dec)
    return row


def ingest_target(spec: TargetSpec) -> dict[str, str | int]:
    init_db()
    curve = fetch_lightcurve(spec)
    cache_path = target_cache_path(spec.id)
    np.savez_compressed(
        cache_path,
        time=curve.time,
        flux=curve.flux,
        flux_err=curve.flux_err,
        source=curve.source,
    )

    session = get_db_session()
    try:
        target = _upsert_target(session, spec)
        lc = LightCurve(
            target_id=target.id,
            mission=spec.mission.upper(),
            cache_path=str(cache_path),
            n_points=len(curve.time),
            downloaded_at=datetime.now(timezone.utc),
            source=curve.source,
        )
        session.add(lc)
        session.commit()
        return {
            "target": spec.id,
            "n_points": len(curve.time),
            "cache_path": str(cache_path),
            "source": curve.source,
        }
    finally:
        session.close()


def ingest_all_targets() -> dict[str, object]:
    settings = get_exoplanet_settings()
    results = []
    for spec in load_targets():
        results.append(ingest_target(spec))
    retention = enforce_retention()
    logger.info("ingest_all_completed", targets=len(results), **retention)
    return {"ingested": results, "retention": retention, "cache_dir": settings.cache_dir}
