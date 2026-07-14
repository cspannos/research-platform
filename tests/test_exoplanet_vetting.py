from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from projects.exoplanet.pipelines.mast_client import _synthetic_lightcurve
from projects.exoplanet.pipelines.vetting import (
    PLOT_NAMES,
    estimate_transit_geometry,
    generate_vetting_plots,
    resolve_plot_file,
)
from projects.exoplanet.settings import TargetSpec
from research_platform.api.main import app


def _transit_curve(period: float = 3.7, n_points: int = 4000):
    rng = np.random.default_rng(42)
    time = np.sort(rng.uniform(0, 40, n_points))
    flux = np.ones_like(time)
    phase = (time % period) / period
    flux[phase < 0.04] -= 0.003
    flux += rng.normal(0, 0.0003, size=n_points)
    return time, flux


def test_estimate_transit_geometry_finds_t0() -> None:
    period = 3.7
    time, flux = _transit_curve(period=period)
    geom = estimate_transit_geometry(time, flux, period)
    assert geom.t0 is not None
    assert geom.duration_hours is not None
    assert geom.duration_hours > 0
    assert geom.odd_depth_ppm is not None
    assert geom.even_depth_ppm is not None
    assert geom.odd_even_delta_ppm is not None
    assert "unavailable" not in geom.note.lower() or geom.note == "ok" or "partial" in geom.note


def test_estimate_transit_geometry_unavailable_on_empty() -> None:
    geom = estimate_transit_geometry(np.array([1.0]), np.array([1.0]), 3.0)
    assert geom.t0 is None
    assert "unavailable" in geom.note.lower()


def test_generate_vetting_plots_writes_pngs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXOPLANET_CACHE_DIR", str(tmp_path))
    from projects.exoplanet import settings as exo_settings

    exo_settings.get_exoplanet_settings.cache_clear()

    spec = TargetSpec(id="toi-715", name="TOI-715", mission="TESS", tic_id="1")
    curve = _synthetic_lightcurve(spec, n_points=2500)
    result = generate_vetting_plots(
        99,
        curve.time,
        curve.flux,
        period_days=3.7,
        t0=float(np.median(curve.time)),
    )
    assert result["ok"] is True
    assert len(result["plots"]) >= 2
    for name in result["plots"]:
        assert (tmp_path / "vetting" / "99" / name).is_file()
        assert resolve_plot_file(99, name) is not None

    exo_settings.get_exoplanet_settings.cache_clear()


def test_resolve_plot_rejects_unknown_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXOPLANET_CACHE_DIR", str(tmp_path))
    from projects.exoplanet import settings as exo_settings

    exo_settings.get_exoplanet_settings.cache_clear()
    assert resolve_plot_file(1, "not_a_plot.png") is None
    assert "phase_fold.png" in PLOT_NAMES
    exo_settings.get_exoplanet_settings.cache_clear()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_TOKEN", "test-admin-token")
    return TestClient(app)


def test_review_plot_route_requires_token(client: TestClient) -> None:
    response = client.get("/review/candidates/1/plots/phase_fold.png")
    assert response.status_code == 401


def test_review_plot_route_serves_png(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXOPLANET_CACHE_DIR", str(tmp_path))
    from projects.exoplanet import settings as exo_settings

    exo_settings.get_exoplanet_settings.cache_clear()

    plot_dir = tmp_path / "vetting" / "5"
    plot_dir.mkdir(parents=True)
    png = plot_dir / "phase_fold.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    from projects.exoplanet.review.queries import CandidateRow
    from datetime import datetime, timezone

    fake_row = CandidateRow(
        id=5,
        target_slug="toi-715",
        target_name="TOI-715",
        mission="TESS",
        period_days=3.7,
        depth_ppm=1000.0,
        snr=8.0,
        flag_reason="test",
        status="pending",
        created_at=datetime.now(timezone.utc),
        summary=None,
        summary_source=None,
        comments=[],
        plots_ready=True,
        available_plots=["phase_fold.png"],
    )

    with patch(
        "research_platform.api.review_dashboard.get_candidate_row",
        return_value=fake_row,
    ):
        response = client.get("/review/candidates/5/plots/phase_fold.png?token=test-admin-token")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    exo_settings.get_exoplanet_settings.cache_clear()


def test_review_plot_route_404_when_missing(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXOPLANET_CACHE_DIR", str(tmp_path))
    from projects.exoplanet import settings as exo_settings

    exo_settings.get_exoplanet_settings.cache_clear()
    with patch(
        "research_platform.api.review_dashboard.get_candidate_row",
        return_value=MagicMock(id=1),
    ):
        response = client.get("/review/candidates/1/plots/phase_fold.png?token=test-admin-token")
    assert response.status_code == 404
    exo_settings.get_exoplanet_settings.cache_clear()
