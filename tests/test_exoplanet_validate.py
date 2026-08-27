from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from projects.exoplanet.pipelines.checklist import build_vetting_checklist
from projects.exoplanet.pipelines.expert import _candidate_context, template_review_summary
from projects.exoplanet.pipelines.validate import (
    ValidationInput,
    compute_validation_payload,
    equivalent_fpp,
    unavailable_validation,
)
from research_platform.api.main import app
from research_platform.bots.exoplanet_bot import _format_validation


def _tess_input(**overrides) -> ValidationInput:
    base = dict(
        mission="TESS",
        snr=12.0,
        period_days=3.7,
        tic_id="260128064",
        lc_source="mast:tess-lc.fits",
        odd_depth_ppm=2400.0,
        even_depth_ppm=2500.0,
        dilution=0.98,
        brightest_delta_mag=4.0,
        centroid_pass=True,
        centroid_pvalue=0.4,
    )
    base.update(overrides)
    return ValidationInput(**base)


def test_disabled_flag_is_unavailable() -> None:
    payload = compute_validation_payload(_tess_input(), enabled=False, min_snr=8.0)
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "triceratops_disabled"
    assert payload["fpp"] is None


def test_kepler_and_synthetic_and_below_snr_gated() -> None:
    kepler = compute_validation_payload(
        _tess_input(mission="Kepler"),
        enabled=True,
        min_snr=8.0,
    )
    assert kepler["reason"] == "mission_not_tess"

    synth = compute_validation_payload(
        _tess_input(lc_source="synthetic"),
        enabled=True,
        min_snr=8.0,
    )
    assert synth["reason"] == "synthetic_lc"

    low = compute_validation_payload(_tess_input(snr=5.5), enabled=True, min_snr=8.0)
    assert low["reason"] == "below_snr_gate"

    period = compute_validation_payload(
        _tess_input(period_days=40.0),
        enabled=True,
        min_snr=8.0,
    )
    assert period["reason"] == "period_out_of_gate"


def test_equivalent_fpp_clean_candidate_is_low() -> None:
    payload = equivalent_fpp(
        odd_depth_ppm=2500.0,
        even_depth_ppm=2500.0,
        dilution=1.0,
        brightest_delta_mag=5.0,
        centroid_pass=True,
        centroid_pvalue=0.8,
    )
    assert payload["status"] == "ok"
    assert payload["method"] == "equivalent"
    assert payload["fpp"] < 0.015
    assert payload["nfpp"] < 0.05


def test_equivalent_fpp_bright_blend_and_centroid_fail() -> None:
    payload = equivalent_fpp(
        odd_depth_ppm=100.0,
        even_depth_ppm=2000.0,
        dilution=0.4,
        brightest_delta_mag=0.3,
        centroid_pass=False,
        centroid_pvalue=0.001,
    )
    assert payload["fpp"] > 0.5
    assert payload["nfpp"] > 0.1


def test_triceratops_runner_success() -> None:
    def runner(_inp):
        return {
            "status": "ok",
            "method": "triceratops",
            "fpp": 0.001,
            "nfpp": 1e-4,
            "prob_eb": None,
            "prob_nearby_eb": 1e-4,
            "reason": None,
            "error": None,
        }

    payload = compute_validation_payload(
        _tess_input(),
        enabled=True,
        min_snr=8.0,
        triceratops_runner=runner,
    )
    assert payload["method"] == "triceratops"
    assert payload["fpp"] == 0.001
    assert payload["nfpp"] == 1e-4


def test_triceratops_runner_exception_records_snippet() -> None:
    def runner(_inp):
        raise RuntimeError("TESS catalog timeout xyz")

    payload = compute_validation_payload(
        _tess_input(),
        enabled=True,
        min_snr=8.0,
        triceratops_runner=runner,
    )
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "triceratops_error"
    assert payload["error"] is not None
    assert "catalog timeout" in payload["error"]


def test_missing_package_falls_back_to_equivalent() -> None:
    payload = compute_validation_payload(
        _tess_input(),
        enabled=True,
        min_snr=8.0,
        triceratops_runner=lambda _inp: None,
    )
    assert payload["method"] == "equivalent"
    assert "triceratops_not_installed" in (payload.get("note") or "")
    assert payload["fpp"] is not None


def test_scan_path_does_not_import_or_call_validate() -> None:
    src = Path("projects/exoplanet/pipelines/analysis.py").read_text(encoding="utf-8")
    assert "vet_validate" not in src
    assert "enqueue_validation" not in src
    assert "exoplanet_vet_validate" not in src
    from projects.exoplanet.pipelines import analysis

    assert not hasattr(analysis, "vet_validate")
    assert "validate" not in Path("projects/exoplanet/workers/jobs.py").read_text(
        encoding="utf-8"
    ).split("def exoplanet_scan_job")[1].split("def ")[0]


def test_checklist_fpp_statuses() -> None:
    unavailable = build_vetting_checklist(
        depth_ppm=2500.0,
        validation={"status": "unavailable", "reason": "triceratops_disabled"},
    )
    by_id = {i.id: i for i in unavailable}
    assert by_id["fpp"].status == "unavailable"

    passing = build_vetting_checklist(
        depth_ppm=2500.0,
        validation={"status": "ok", "method": "triceratops", "fpp": 0.001, "nfpp": 1e-5},
    )
    assert {i.id: i for i in passing}["fpp"].status == "pass"

    nearby = build_vetting_checklist(
        depth_ppm=2500.0,
        validation={"status": "ok", "method": "equivalent", "fpp": 0.4, "nfpp": 0.2},
    )
    assert {i.id: i for i in nearby}["fpp"].status == "fail"


def test_expert_context_includes_fpp() -> None:
    candidate = SimpleNamespace(
        id=8,
        period_days=3.7,
        depth_ppm=2500.0,
        snr=12.0,
        flag_reason="peak",
        status="pending",
        t0=1.0,
        duration_hours=2.0,
        odd_depth_ppm=2400.0,
        even_depth_ppm=2500.0,
        odd_even_delta_ppm=100.0,
        geometry_note=None,
        plots_ready=True,
        available_plots=["phase_fold.png"],
        neighbours={"status": "ok", "n_neighbours": 0, "dilution": 1.0, "brightest_delta_mag": None},
        centroid={"status": "unavailable", "reason": "synthetic_lc"},
        validation={"status": "ok", "method": "equivalent", "fpp": 0.012, "nfpp": 0.01},
    )
    target = SimpleNamespace(
        name="TOI-715",
        slug="toi-715",
        mission="TESS",
        external_id="260128064",
        notes="",
    )
    ctx = _candidate_context(candidate, target)
    assert "FPP=0.012" in ctx
    assert "NFPP=" in ctx
    summary = template_review_summary(candidate, target)
    assert "FPP=0.012" in summary


def test_format_validation_ok_and_error() -> None:
    ok = _format_validation(
        {
            "ok": True,
            "candidate_id": 8,
            "validation": {
                "status": "ok",
                "method": "equivalent",
                "fpp": 0.02,
                "nfpp": 0.01,
            },
        }
    )
    assert "Validation #8" in ok
    assert "FPP=0.02" in ok
    failed = _format_validation(
        {
            "ok": True,
            "candidate_id": 3,
            "validation": {
                "status": "unavailable",
                "reason": "triceratops_error",
                "error": "TESS catalog timeout",
            },
        }
    )
    assert "triceratops_error" in failed
    assert "catalog timeout" in failed


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_TOKEN", "test-admin-token")
    return TestClient(app)


def test_review_post_validate_rerenders_panel(client: TestClient) -> None:
    from datetime import datetime, timezone

    from projects.exoplanet.review.queries import CandidateRow

    row = CandidateRow(
        id=8,
        target_slug="toi-715",
        target_name="TOI-715",
        mission="TESS",
        period_days=3.7,
        depth_ppm=2500.0,
        snr=12.0,
        flag_reason="test",
        status="pending",
        created_at=datetime.now(timezone.utc),
        summary=None,
        summary_source=None,
        comments=[],
        validation={"status": "unavailable", "reason": "queued"},
    )
    queued = {
        "ok": True,
        "candidate_id": 8,
        "queued": True,
        "job_id": "job-1",
        "timeout_s": 900,
        "enabled": True,
        "validation": unavailable_validation("queued"),
    }
    with (
        patch(
            "projects.exoplanet.pipelines.validate.enqueue_validation",
            return_value=queued,
        ) as enq,
        patch(
            "research_platform.api.review_dashboard.get_candidate_row",
            return_value=row,
        ),
    ):
        response = client.post("/review/candidates/8/validate?token=test-admin-token")
    assert response.status_code == 200
    enq.assert_called_once()
    assert "Run validation" in response.text
    assert "FPP" in response.text
    assert "queued" in response.text.lower()


def test_unavailable_validation_keeps_error_snippet() -> None:
    payload = unavailable_validation("triceratops_error", error="boom 123")
    assert payload["status"] == "unavailable"
    assert payload["error"] == "boom 123"
    assert payload["fpp"] is None
