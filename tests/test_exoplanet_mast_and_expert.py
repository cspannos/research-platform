from unittest.mock import MagicMock, patch

import numpy as np

from projects.exoplanet.pipelines.mast_client import (
    LightCurveData,
    _extract_lightcurve_from_fits,
    _pick_lightcurve_product,
    _synthetic_lightcurve,
    fetch_lightcurve,
)
from projects.exoplanet.settings import TargetSpec
from research_platform.bots.exoplanet_bot import _parse_ask_args


def test_parse_ask_args_with_candidate_id() -> None:
    cid, q = _parse_ask_args(["8", "Is", "this", "an", "EB?"])
    assert cid == 8
    assert q == "Is this an EB?"


def test_parse_ask_args_without_id() -> None:
    cid, q = _parse_ask_args(["What", "is", "SNR?"])
    assert cid is None
    assert q == "What is SNR?"


def test_pick_lightcurve_product_prefers_lc_fits() -> None:
    products = [
        {"productFilename": "thumb.png", "dataURI": "mast:x/thumb.png", "size": 10},
        {
            "productFilename": "hlsp_tess_lc.fits",
            "dataURI": "mast:x/lc.fits",
            "productType": "SCIENCE",
            "calib_level": 3,
            "size": 1000,
        },
    ]
    picked = _pick_lightcurve_product(products)
    assert picked is not None
    assert "lc.fits" in picked["productFilename"]


def test_fetch_falls_back_to_synthetic_without_token(monkeypatch) -> None:
    monkeypatch.setenv("MAST_API_TOKEN", "")
    monkeypatch.setenv("EXOPLANET_ALLOW_SYNTHETIC", "true")
    from projects.exoplanet import settings as exo_settings

    exo_settings.get_exoplanet_settings.cache_clear()
    spec = TargetSpec(id="toi-715", name="TOI-715", mission="TESS", tic_id="260128064")
    curve = fetch_lightcurve(spec)
    assert isinstance(curve, LightCurveData)
    assert curve.source == "synthetic"
    exo_settings.get_exoplanet_settings.cache_clear()


def test_extract_lightcurve_from_minimal_fits(tmp_path) -> None:
    from astropy.io import fits
    from astropy.table import Table

    time = np.linspace(0, 10, 200)
    flux = np.ones(200)
    flux[20:30] -= 0.002
    table = Table([time, flux], names=("TIME", "PDCSAP_FLUX"))
    path = tmp_path / "sample_lc.fits"
    fits.BinTableHDU(table).writeto(path)

    t, f, e = _extract_lightcurve_from_fits(path)
    assert len(t) == 200
    assert np.isfinite(f).all()
    assert len(e) == 200


def test_expert_template_when_no_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    from projects.exoplanet import settings as exo_settings
    from research_platform.core import config as platform_config

    exo_settings.get_exoplanet_settings.cache_clear()
    platform_config.get_settings.cache_clear()

    candidate = MagicMock(
        id=1,
        period_days=1.2,
        depth_ppm=500.0,
        snr=8.0,
        flag_reason="test",
        status="pending",
    )
    target = MagicMock(name="TOI-715", mission="TESS", slug="toi-715", external_id="1", notes="")
    # MagicMock name attribute is special; set explicitly
    target.name = "TOI-715"

    from projects.exoplanet.pipelines.expert import generate_review_summary

    text, source = generate_review_summary(candidate, target)
    assert source == "template"
    assert "TOI-715" in text

    exo_settings.get_exoplanet_settings.cache_clear()
    platform_config.get_settings.cache_clear()


def test_ask_without_key_explains_missing_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    from projects.exoplanet import settings as exo_settings
    from research_platform.core import config as platform_config

    exo_settings.get_exoplanet_settings.cache_clear()
    platform_config.get_settings.cache_clear()

    from projects.exoplanet.pipelines.expert import answer_exoplanet_question

    result = answer_exoplanet_question("What is SNR?")
    assert result["ok"] is False
    assert result["reason"] == "no_key"
    assert "OPENROUTER_API_KEY" in str(result.get("hint"))

    exo_settings.get_exoplanet_settings.cache_clear()
    platform_config.get_settings.cache_clear()


def test_ask_credits_error_is_not_missing_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from projects.exoplanet import settings as exo_settings
    from research_platform.core import config as platform_config

    exo_settings.get_exoplanet_settings.cache_clear()
    platform_config.get_settings.cache_clear()

    class _Resp:
        status_code = 402
        text = '{"error":{"message":"This request requires more credits"}}'

    from projects.exoplanet.pipelines import expert as expert_mod

    with patch.object(expert_mod.httpx, "Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _Resp()
        result = expert_mod.answer_exoplanet_question("What is SNR?")
    assert result["ok"] is False
    assert result["reason"] == "credits"
    assert "credits" in str(result.get("hint")).lower()

    exo_settings.get_exoplanet_settings.cache_clear()
    platform_config.get_settings.cache_clear()
