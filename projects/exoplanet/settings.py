from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TargetSpec(BaseModel):
    id: str
    name: str
    mission: str
    tic_id: str | None = None
    kic_id: str | None = None
    ra: float | None = None
    dec: float | None = None
    notes: str = ""


class ExoplanetSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    mast_api_token: str = Field(default="", alias="MAST_API_TOKEN")
    cache_dir: str = Field(default="/var/cache/platform/exoplanet", alias="EXOPLANET_CACHE_DIR")
    max_cache_gb: float = Field(default=80.0, alias="EXOPLANET_MAX_CACHE_GB")
    retention_days: int = Field(default=30, alias="EXOPLANET_RETENTION_DAYS")
    targets_file: str = Field(
        default="projects/exoplanet/config/targets.yaml",
        alias="EXOPLANET_TARGETS_FILE",
    )
    min_period_days: float = Field(default=0.5, alias="EXOPLANET_MIN_PERIOD_DAYS")
    max_period_days: float = Field(default=20.0, alias="EXOPLANET_MAX_PERIOD_DAYS")
    snr_threshold: float = Field(default=5.0, alias="EXOPLANET_SNR_THRESHOLD")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    llm_summaries: bool = Field(default=False, alias="EXOPLANET_LLM_SUMMARIES")
    llm_model: str = Field(default="anthropic/claude-haiku-4.5", alias="EXOPLANET_LLM_MODEL")
    allow_synthetic: bool = Field(default=True, alias="EXOPLANET_ALLOW_SYNTHETIC")
    fetch_tpf: bool = Field(default=True, alias="EXOPLANET_FETCH_TPF")
    triceratops: bool = Field(default=False, alias="EXOPLANET_TRICERATOPS")
    validate_min_snr: float = Field(default=8.0, alias="EXOPLANET_VALIDATE_MIN_SNR")
    validate_timeout_s: int = Field(default=900, alias="EXOPLANET_VALIDATE_TIMEOUT_S")


@lru_cache
def get_exoplanet_settings() -> ExoplanetSettings:
    return ExoplanetSettings()


def load_targets(path: str | None = None) -> list[TargetSpec]:
    settings = get_exoplanet_settings()
    target_path = Path(path or settings.targets_file)
    if not target_path.exists():
        return []
    payload = yaml.safe_load(target_path.read_text(encoding="utf-8")) or {}
    return [TargetSpec.model_validate(item) for item in payload.get("targets", [])]
