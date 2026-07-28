from projects.exoplanet.pipelines.checklist import (
    build_vetting_checklist,
    checklist_blocking_action,
)
from projects.exoplanet.pipelines.mast_client import _synthetic_lightcurve
from projects.exoplanet.pipelines.vetting import estimate_baseline_depth_ppm
from projects.exoplanet.settings import TargetSpec


def test_baseline_depth_is_plausible_for_synthetic() -> None:
    spec = TargetSpec(id="toi-715", name="TOI-715", mission="TESS", tic_id="1")
    curve = _synthetic_lightcurve(spec, n_points=3000)
    depth = estimate_baseline_depth_ppm(curve.time, curve.flux, period_days=3.7)
    assert depth is not None
    # Synthetic TESS depth is 0.0025 → ~2500 ppm
    assert 500 < depth < 10_000


def test_checklist_fails_absurd_depth() -> None:
    items = build_vetting_checklist(depth_ppm=1_500_000.0, plots_ready=True, available_plots=["a.png", "b.png"])
    depth = next(i for i in items if i.id == "depth")
    assert depth.status == "fail"
    assert checklist_blocking_action(items)


def test_checklist_fails_odd_even_asymmetry() -> None:
    items = build_vetting_checklist(
        depth_ppm=2500.0,
        odd_depth_ppm=11.2,
        even_depth_ppm=116.2,
        odd_even_delta_ppm=105.0,
        plots_ready=True,
        available_plots=["phase_fold.png", "odd_even.png"],
    )
    odd_even = next(i for i in items if i.id == "odd_even")
    assert odd_even.status == "fail"
    assert "blend" in odd_even.detail.lower() or "red flag" in odd_even.detail.lower()
    neighbours = next(i for i in items if i.id == "neighbours")
    assert neighbours.status == "unavailable"


def test_checklist_passes_sane_metrics() -> None:
    items = build_vetting_checklist(
        depth_ppm=2500.0,
        odd_depth_ppm=2400.0,
        even_depth_ppm=2550.0,
        odd_even_delta_ppm=150.0,
        plots_ready=True,
        available_plots=["phase_fold.png", "odd_even.png", "periodogram.png"],
    )
    by_id = {i.id: i for i in items}
    assert by_id["depth"].status == "pass"
    assert by_id["odd_even"].status == "pass"
    assert by_id["plots"].status == "pass"
