import pytest
from fastapi.testclient import TestClient

from projects.exoplanet.db.models import Candidate, Target
from projects.exoplanet.pipelines.llm import generate_llm_summary
from research_platform.api.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_TOKEN", "test-admin-token")
    return TestClient(app)


def test_review_dashboard_requires_token(client: TestClient) -> None:
    response = client.get("/review/")
    assert response.status_code == 401


def test_review_dashboard_loads_with_token(client: TestClient) -> None:
    from unittest.mock import patch

    with patch("research_platform.api.review_dashboard.list_candidate_rows", return_value=[]):
        response = client.get("/review/?token=test-admin-token")
    assert response.status_code == 200
    assert "Exoplanet Candidate Review" in response.text


def test_review_host_root_redirects(client: TestClient) -> None:
    response = client.get("/", headers={"Host": "review.example.com"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/review/")


def test_llm_summary_fallback_without_api_key(monkeypatch) -> None:
    candidate = Candidate(
        id=1,
        target_id=1,
        period_days=3.7,
        depth_ppm=2500,
        snr=8.5,
        flag_reason="test",
        status="pending",
    )
    target = Target(
        id=1,
        slug="toi-715",
        name="TOI-715",
        mission="TESS",
        external_id="260128064",
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    from projects.exoplanet import settings as exo_settings
    from research_platform.core import config as platform_config

    exo_settings.get_exoplanet_settings.cache_clear()
    platform_config.get_settings.cache_clear()
    text, source = generate_llm_summary(candidate, target)
    assert source == "template"
    assert "TOI-715" in text
    exo_settings.get_exoplanet_settings.cache_clear()
    platform_config.get_settings.cache_clear()
