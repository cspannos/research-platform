from types import SimpleNamespace

import numpy as np

from projects.exoplanet.pipelines.analysis import (
    CANDIDATE_PERIOD_RTOL,
    analyze_lightcurve,
    find_matching_candidate,
)
from projects.exoplanet.pipelines.mast_client import _synthetic_lightcurve
from projects.exoplanet.settings import TargetSpec, load_targets


class _StubQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _StubSession:
    """Stands in for the id-descending candidate query."""

    def __init__(self, rows):
        self._rows = rows

    def query(self, *args, **kwargs):
        return _StubQuery(self._rows)


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


def test_daily_rescan_reuses_candidate_for_a_wobbling_period() -> None:
    """These are real consecutive periods the scheduler recovered for one signal."""
    session = _StubSession([SimpleNamespace(id=121, period_days=0.5964, status="pending")])
    for redetected in (0.5964, 0.5949, 0.5926, 0.5906, 0.5914):
        match = find_matching_candidate(session, 1, redetected)
        assert match is not None, f"period {redetected} should reuse candidate 121"
        assert match.id == 121


def test_distinct_periods_get_separate_candidates() -> None:
    session = _StubSession([SimpleNamespace(id=5, period_days=3.36, status="pending")])
    # TOI-270 c at 5.66 d must not collapse into b at 3.36 d.
    assert find_matching_candidate(session, 1, 5.66) is None
    # Just outside the tolerance.
    assert find_matching_candidate(session, 1, 3.36 * (1 + 2 * CANDIDATE_PERIOD_RTOL)) is None


def test_rejected_candidate_is_matched_so_it_is_not_resurrected() -> None:
    session = _StubSession([SimpleNamespace(id=9, period_days=1.5, status="rejected")])
    match = find_matching_candidate(session, 1, 1.5)
    assert match is not None
    assert match.status == "rejected"


def test_newest_matching_candidate_wins() -> None:
    session = _StubSession(
        [
            SimpleNamespace(id=30, period_days=1.50, status="pending"),
            SimpleNamespace(id=10, period_days=1.51, status="pending"),
        ]
    )
    assert find_matching_candidate(session, 1, 1.5).id == 30


def test_unusable_periods_never_match() -> None:
    session = _StubSession([SimpleNamespace(id=1, period_days=1.5, status="pending")])
    assert find_matching_candidate(session, 1, float("nan")) is None
    assert find_matching_candidate(session, 1, 0.0) is None

    bad_rows = _StubSession(
        [
            SimpleNamespace(id=2, period_days=None, status="pending"),
            SimpleNamespace(id=1, period_days=float("nan"), status="pending"),
        ]
    )
    assert find_matching_candidate(bad_rows, 1, 1.5) is None
