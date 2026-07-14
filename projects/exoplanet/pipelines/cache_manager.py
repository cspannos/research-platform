from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from projects.exoplanet.settings import get_exoplanet_settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


def ensure_cache_dir(*, create: bool = True) -> Path:
    settings = get_exoplanet_settings()
    cache = Path(settings.cache_dir)
    if create:
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Read-only mounts (platform-api) still need the path for plot serving.
            if not cache.exists():
                raise
    return cache


def cache_size_gb(cache_dir: Path | None = None) -> float:
    root = cache_dir or ensure_cache_dir()
    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    return total / (1024**3)


def enforce_retention() -> dict[str, int]:
    settings = get_exoplanet_settings()
    cache = ensure_cache_dir()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    removed = 0

    for path in cache.rglob("*.npz"):
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1

    while cache_size_gb(cache) > settings.max_cache_gb:
        files = sorted(
            (f for f in cache.rglob("*.npz") if f.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        if not files:
            break
        files[0].unlink(missing_ok=True)
        removed += 1

    logger.info("cache_retention_applied", removed=removed, size_gb=round(cache_size_gb(cache), 3))
    return {"removed_files": removed, "size_gb": round(cache_size_gb(cache), 3)}


def target_cache_path(slug: str) -> Path:
    return ensure_cache_dir() / f"{slug}.npz"


def vetting_dir(candidate_id: int, *, create: bool = False) -> Path:
    """Directory for diagnostic PNGs: {cache}/vetting/{candidate_id}/.

    create=False for read paths (platform-api mounts cache read-only).
    """
    path = ensure_cache_dir() / "vetting" / str(candidate_id)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def vetting_plot_path(candidate_id: int, plot_name: str, *, create: bool = False) -> Path:
    return vetting_dir(candidate_id, create=create) / plot_name
