import numpy as np

from projects.exoplanet.pipelines.analysis import analyze_lightcurve
from projects.exoplanet.pipelines.mast_client import _synthetic_lightcurve
from projects.exoplanet.settings import TargetSpec, load_targets


def test_load_targets_has_curated_list() -> None:
    targets = load_targets("projects/exoplanet/config/targets.yaml")
    assert len(targets) >= 2
    assert any(t.id == "toi-715" for t in targets)


def test_synthetic_lightcurve_has_points() -> None:
    spec = TargetSpec(id="test", name="Test", mission="TESS", tic_id="123")
    curve = _synthetic_lightcurve(spec, n_points=500)
    assert len(curve.time) == 500
    assert len(curve.flux) == 500


def test_analyze_detects_periodic_signal() -> None:
    spec = TargetSpec(id="toi-715", name="TOI-715", mission="TESS", tic_id="260128064")
    curve = _synthetic_lightcurve(spec, n_points=3000)
    result = analyze_lightcurve(curve.time, curve.flux)
    assert result.period_days > 0
    assert result.snr > 0
    # Must be continuum-normalized (not residual min→1e6 nonsense)
    assert result.depth_ppm > 0
    assert result.depth_ppm < 100_000
    assert "baseline-normalized" in result.flag_reason
