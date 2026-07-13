from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    platform_env: str = Field(default="development", alias="PLATFORM_ENV")
    platform_domain: str = Field(default="localhost", alias="PLATFORM_DOMAIN")
    platform_admin_token: str = Field(default="dev-token", alias="PLATFORM_ADMIN_TOKEN")
    review_base_url: str = Field(default="http://10.66.66.1:8001", alias="REVIEW_BASE_URL")

    service_name: str = Field(default="platform", alias="SERVICE_NAME")
    tenant_id: str = Field(default="platform", alias="TENANT_ID")

    postgres_user: str = Field(default="platform", alias="POSTGRES_USER")
    postgres_password: str = Field(default="platform", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="platform_meta", alias="POSTGRES_DB")
    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    redis_password: str = Field(default="", alias="REDIS_PASSWORD")
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_user_ids: str = Field(default="", alias="TELEGRAM_ALLOWED_USER_IDS")
    telegram_mode: str = Field(default="polling", alias="TELEGRAM_MODE")

    worker_concurrency: int = Field(default=1, alias="WORKER_CONCURRENCY")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def tenant_postgres_dsn(self) -> str:
        db_name = f"tenant_{self.tenant_id}"
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{db_name}"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}"

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.telegram_allowed_user_ids.strip():
            return set()
        return {int(x.strip()) for x in self.telegram_allowed_user_ids.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def tenant_env_prefix(tenant_id: str) -> str:
    return tenant_id.upper().replace("-", "_")


def load_tenant_settings(tenant_id: str) -> Settings:
    prefix = tenant_env_prefix(tenant_id)
    overrides = {
        "TENANT_ID": tenant_id,
        "TELEGRAM_BOT_TOKEN": os.getenv(f"{prefix}_TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_ALLOWED_USER_IDS": os.getenv(f"{prefix}_TELEGRAM_ALLOWED_USER_IDS", ""),
        "TELEGRAM_MODE": os.getenv(f"{prefix}_TELEGRAM_MODE", "polling"),
    }
    return Settings(**{k: v for k, v in overrides.items() if v is not None})
