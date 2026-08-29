from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient

from projects.exoplanet.pipelines.centroid import (
    centroid_shift_test,
    flux_weighted_centroids,
    is_synthetic_source,
    run_centroid_test,
)
from projects.exoplanet.pipelines.mast_client import _pick_tpf_product
from projects.exoplanet.pipelines.neighbours import (
    apply_neighbour_vetting,
    run_gaia_neighbours,
    summarise_neighbours,
    unavailable_neighbours,
)
from projects.exoplanet.settings import TargetSpec, load_targets
from research_platform.api.main import app


def test_load_targets_includes_coordinates() -> None:
    targets = load_targets("projects/exoplanet/config/targets.yaml")
    toi = next(t for t in targets if t.id == "toi-715")
    assert toi.ra is not None
    assert toi.dec is not None


def test_summarise_neighbours_dilution_and_brightest() -> None:
    rows = [
        {"source_id": "1", "ra": 10.0, "dec": 0.0, "gmag": 10.0},
        {"source_id": "2", "ra": 10.001, "dec": 0.0, "gmag": 12.0},
        {"source_id": "3", "ra": 10.02, "dec": 0.0, "gmag": 8.0},
    ]
    summary = summarise_neighbours(rows, ra=10.0, dec=0.0, aperture_arcsec=21.0)
    assert summary["status"] == "ok"
    assert summary["n_neighbours"] == 2
    assert summary["target_gmag"] == pytest.approx(10.0)
    assert summary["brightest_delta_mag"] == pytest.approx(-2.0)
    assert summary["dilution"] is not None
    assert 0 < summary["dilution"] < 1
    in_ap = [n for n in summary["neighbours"] if n["in_aperture"]]
    assert len(in_ap) >= 1


def test_gaia_neighbours_uses_yaml_coords_and_mocked_cone() -> None:
    spec = TargetSpec(
        id="toi-715",
        name="TOI-715",
        mission="TESS",
        tic_id="1",
        ra=113.85,
        dec=-73.57,
    )
    gaia_rows = [
        {"source_id": "t", "ra": 113.85, "dec": -73.57, "gmag": 14.8},
        {"source_id": "n", "ra": 113.86, "dec": -73.57, "gmag": 16.2},
    ]
    with patch(
        "projects.exoplanet.pipelines.neighbours.query_gaia_cone",
        return_value=gaia_rows,
    ) as mocked:
        payload, ra, dec = run_gaia_neighbours(spec)
    mocked.assert_called_once()
    assert ra == pytest.approx(113.85)
    assert dec == pytest.approx(-73.57)
    assert payload["status"] == "ok"
    assert payload["n_neighbours"] == 1
    assert payload["coord_source"] == "targets.yaml"


def test_gaia_unavailable_without_coords() -> None:
    spec = TargetSpec(id="x", name="X", mission="TESS", tic_id="1")
    with patch(
        "projects.exoplanet.pipelines.neighbours.resolve_coordinates",
        return_value=None,
    ):
        payload, ra, dec = run_gaia_neighbours(spec)
    assert ra is None and dec is None
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "no_coords"


def test_gaia_network_failure_is_unavailable() -> None:
    spec = TargetSpec(id="x", name="X", mission="TESS", ra=1.0, dec=2.0)
    with patch(
        "projects.exoplanet.pipelines.neighbours.query_gaia_cone",
        side_effect=TimeoutError("network timeout"),
    ):
        payload, ra, dec = run_gaia_neighbours(spec)
    assert ra == pytest.approx(1.0)
    assert payload["status"] == "unavailable"
    assert "no_network" in str(payload["reason"])


def test_gaia_cone_passes_radius_as_keyword() -> None:
    """astroquery >= 0.4.11 makes radius keyword-only; a positional call raises TypeError."""
    from projects.exoplanet.pipelines.neighbours import query_gaia_cone

    fake_gaia = MagicMock()
    table = MagicMock()
    table.colnames = ["source_id", "ra", "dec", "phot_g_mean_mag"]
    table.__len__ = lambda self: 0
    table.__iter__ = lambda self: iter(())
    fake_gaia.cone_search_async.return_value.get_results.return_value = table

    def cone_search_async(coordinate, *, radius=None, **kwargs):
        return fake_gaia.cone_search_async(coordinate, radius=radius, **kwargs)

    module = MagicMock()
    module.Gaia = MagicMock()
    module.Gaia.cone_search_async = cone_search_async

    with patch.dict("sys.modules", {"astroquery.gaia": module}):
        query_gaia_cone(113.85, -73.58, radius_arcmin=1.0)

    _, kwargs = fake_gaia.cone_search_async.call_args
    assert kwargs["radius"] is not None


def test_centroid_shift_detects_offset() -> None:
    rng = np.random.default_rng(0)
    n = 800
    time = np.linspace(0, 40, n)
    period, t0 = 4.0, 1.0
    phase = ((time - t0) / period + 0.5) % 1.0 - 0.5
    in_tr = np.abs(phase) < 0.04
    cx = rng.normal(5.0, 0.02, n)
    cy = rng.normal(5.0, 0.02, n)
    cx[in_tr] += 0.4
    cy[in_tr] += 0.3
    result = centroid_shift_test(time, cx, cy, period, t0, duration_hours=2.0)
    assert result["status"] == "fail"
    assert result["pass"] is False
    assert result["pvalue"] < 0.05
    assert result["offset_pix"] > 0.2


def test_centroid_shift_stable_passes() -> None:
    rng = np.random.default_rng(1)
    n = 800
    time = np.linspace(0, 40, n)
    cx = rng.normal(5.0, 0.01, n)
    cy = rng.normal(5.0, 0.01, n)
    result = centroid_shift_test(time, cx, cy, 4.0, 1.0, duration_hours=2.0)
    assert result["status"] == "pass"
    assert result["pvalue"] >= 0.05


def test_synthetic_lc_skips_tpf_download(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXOPLANET_CACHE_DIR", str(tmp_path))
    from projects.exoplanet import settings as exo_settings

    exo_settings.get_exoplanet_settings.cache_clear()
    spec = TargetSpec(id="toi-715", name="TOI-715", mission="TESS", tic_id="1")
    with patch(
        "projects.exoplanet.pipelines.mast_client.fetch_target_pixel_file"
    ) as fetch:
        result = run_centroid_test(
            spec,
            period_days=3.7,
            t0=10.0,
            duration_hours=2.0,
            lc_source="synthetic",
        )
    fetch.assert_not_called()
    assert result["status"] == "unavailable"
    assert result["reason"] == "synthetic_lc"
    assert result["pvalue"] is None
    exo_settings.get_exoplanet_settings.cache_clear()


def test_flux_weighted_centroids_and_cached_tpf(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXOPLANET_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("EXOPLANET_FETCH_TPF", "true")
    from projects.exoplanet import settings as exo_settings

    exo_settings.get_exoplanet_settings.cache_clear()

    n, ny, nx = 60, 5, 5
    flux = np.zeros((n, ny, nx), dtype=float)
    flux[:, 2, 2] = 100.0
    cx, cy = flux_weighted_centroids(flux)
    assert np.nanmean(cx) == pytest.approx(2.0, abs=0.05)
    assert np.nanmean(cy) == pytest.approx(2.0, abs=0.05)

    time = np.linspace(0, 20, n)
    col1 = fits.Column(name="TIME", format="D", array=time)
    col2 = fits.Column(name="FLUX", format=f"{ny*nx}E", dim=f"({nx},{ny})", array=flux)
    col3 = fits.Column(name="QUALITY", format="J", array=np.zeros(n, dtype=int))
    hdu = fits.BinTableHDU.from_columns([col1, col2, col3])
    tpf_path = tmp_path / "tpf" / "toi-715" / "s0001.fits"
    tpf_path.parent.mkdir(parents=True)
    hdu.writeto(tpf_path)

    spec = TargetSpec(id="toi-715", name="TOI-715", mission="TESS", tic_id="1")
    with patch(
        "projects.exoplanet.pipelines.mast_client.fetch_target_pixel_file"
    ) as fetch:
        result = run_centroid_test(
            spec,
            period_days=3.7,
            t0=float(np.median(time)),
            duration_hours=2.0,
            lc_source="mast:real.fits",
        )
    fetch.assert_not_called()
    assert result["status"] in {"pass", "fail", "unavailable"}
    assert result["tpf_path"] == str(tpf_path)
    exo_settings.get_exoplanet_settings.cache_clear()


def test_apply_neighbour_vetting_is_idempotent() -> None:
    cached_nb = {
        "status": "ok",
        "reason": None,
        "n_neighbours": 3,
        "brightest_delta_mag": 2.2,
        "dilution": 0.97,
        "neighbours": [],
    }
    cached_cent = {"status": "unavailable", "reason": "synthetic_lc", "pass": None}
    spec = TargetSpec(id="toi-715", name="TOI-715", mission="TESS", tic_id="1", ra=1.0, dec=2.0)
    candidate = MagicMock(
        period_days=3.7,
        t0=10.0,
        duration_hours=2.0,
        neighbours_json=json.dumps(cached_nb),
        centroid_json=json.dumps(cached_cent),
    )
    target = MagicMock(
        slug="toi-715",
        ra=1.0,
        dec=2.0,
        neighbours_json=json.dumps(cached_nb),
        mission="TESS",
        name="TOI-715",
        external_id="1",
        notes="",
    )
    with (
        patch("projects.exoplanet.pipelines.neighbours.run_gaia_neighbours") as gaia,
        patch("projects.exoplanet.pipelines.neighbours.run_centroid_test") as cent,
        patch(
            "projects.exoplanet.pipelines.neighbours.load_lightcurve_source",
            return_value="synthetic",
        ),
    ):
        out = apply_neighbour_vetting(candidate, target, spec)
    gaia.assert_not_called()
    cent.assert_not_called()
    assert out["neighbours"]["n_neighbours"] == 3
    assert out["centroid"]["reason"] == "synthetic_lc"


def test_apply_retries_no_network() -> None:
    spec = TargetSpec(id="toi-715", name="TOI-715", mission="TESS", ra=1.0, dec=2.0)
    candidate = MagicMock(
        period_days=3.7,
        t0=10.0,
        duration_hours=2.0,
        neighbours_json=json.dumps(unavailable_neighbours("no_network")),
        centroid_json=json.dumps({"status": "unavailable", "reason": "no_network:timeout"}),
    )
    target = MagicMock(
        slug="toi-715",
        ra=1.0,
        dec=2.0,
        neighbours_json=None,
        mission="TESS",
        name="TOI-715",
        external_id="1",
        notes="",
    )
    fresh = {"status": "ok", "n_neighbours": 0, "neighbours": [], "reason": None}
    with (
        patch(
            "projects.exoplanet.pipelines.neighbours.run_gaia_neighbours",
            return_value=(fresh, 1.0, 2.0),
        ) as gaia,
        patch(
            "projects.exoplanet.pipelines.neighbours.run_centroid_test",
            return_value={"status": "unavailable", "reason": "synthetic_lc"},
        ) as cent,
        patch(
            "projects.exoplanet.pipelines.neighbours.load_lightcurve_source",
            return_value="synthetic",
        ),
    ):
        out = apply_neighbour_vetting(candidate, target, spec)
    gaia.assert_called_once()
    cent.assert_called_once()
    assert out["neighbours"]["status"] == "ok"


def test_pick_tpf_product() -> None:
    products = [
        {"productFilename": "thumb.png", "dataURI": "mast:x/thumb.png"},
        {
            "productFilename": "tess-s0001-tp.fits",
            "dataURI": "mast:x/tp.fits",
            "productType": "SCIENCE",
            "productSubGroupDescription": "TP",
            "size": 5000,
        },
    ]
    picked = _pick_tpf_product(products)
    assert picked is not None
    assert "tp.fits" in picked["productFilename"]


def test_is_synthetic_source() -> None:
    assert is_synthetic_source("synthetic") is True
    assert is_synthetic_source("mast:lc.fits") is False
    assert is_synthetic_source(None) is False


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_TOKEN", "test-admin-token")
    return TestClient(app)


def test_review_list_approve_returns_list(client: TestClient) -> None:
    from datetime import datetime, timezone

    from projects.exoplanet.review.queries import CandidateRow

    row = CandidateRow(
        id=9,
        target_slug="toi-715",
        target_name="TOI-715",
        mission="TESS",
        period_days=3.7,
        depth_ppm=1000.0,
        snr=8.0,
        flag_reason="test",
        status="approved",
        created_at=datetime.now(timezone.utc),
        summary=None,
        summary_source=None,
        comments=[],
        neighbours={
            "status": "ok",
            "n_neighbours": 2,
            "dilution": 0.96,
            "brightest_delta_mag": 3.1,
        },
        centroid={"status": "unavailable", "reason": "synthetic_lc"},
    )
    with (
        patch(
            "research_platform.api.review_dashboard.update_candidate_status",
            return_value=True,
        ),
        patch(
            "research_platform.api.review_dashboard.list_candidate_rows",
            return_value=[row],
        ),
    ):
        response = client.post(
            "/review/candidates/9/status?token=test-admin-token&view=list&status_filter=all",
            data={"status": "approved"},
        )
    assert response.status_code == 200
    assert "Approve" in response.text
    assert "Reject" in response.text
    assert "Neighbours 2" in response.text
    assert "synthetic_lc" in response.text or "unavailable" in response.text
