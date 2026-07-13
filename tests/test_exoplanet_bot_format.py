from research_platform.bots.exoplanet_bot import (
    _format_analyze,
    _format_ingest,
    _format_job_result,
    _format_scan,
)


def test_format_analyze_interesting() -> None:
    text = _format_analyze(
        {
            "target": "toi-715",
            "interesting": True,
            "candidate_id": 7,
            "period_days": 0.591,
            "snr": 87.4,
            "depth_ppm": 1200.0,
            "flag_reason": "peak",
        }
    )
    assert "toi-715" in text
    assert "Candidate #7" in text
    assert "Flagged: yes" in text


def test_format_scan() -> None:
    text = _format_scan(
        {
            "scanned": 2,
            "flagged": 1,
            "results": [
                {
                    "target": "toi-715",
                    "interesting": True,
                    "period_days": 0.5,
                    "snr": 10.0,
                    "candidate_id": 1,
                },
                {
                    "target": "kepler-442",
                    "interesting": False,
                    "period_days": 1.2,
                    "snr": 2.0,
                    "candidate_id": None,
                },
            ],
        }
    )
    assert "2 analyzed, 1 flagged" in text
    assert "toi-715" in text


def test_format_ingest() -> None:
    text = _format_ingest(
        {
            "ingested": [
                {"target": "toi-715", "n_points": 100, "source": "synthetic"},
            ]
        }
    )
    assert "1 targets" in text
    assert "synthetic" in text


def test_format_job_result_failure() -> None:
    text = _format_job_result("analyze", {"ok": False, "error": "No cached light curve"})
    assert "failed" in text
    assert "No cached light curve" in text
