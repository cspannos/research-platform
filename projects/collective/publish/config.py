from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CollectivePublishSettings(BaseSettings):
    """Publish credentials loaded only by collective services — not shared tenants."""

    model_config = SettingsConfigDict(
        env_file=("secrets/collective/collective.env", ".env"),
        extra="ignore",
        populate_by_name=True,
    )

    github_repo: str = Field(default="", alias="COLLECTIVE_GITHUB_REPO")
    git_user_name: str = Field(default="", alias="COLLECTIVE_GIT_USER_NAME")
    git_user_email: str = Field(default="", alias="COLLECTIVE_GIT_USER_EMAIL")
    github_token: str = Field(default="", alias="COLLECTIVE_GITHUB_TOKEN")
    publish_enabled: bool = Field(default=False, alias="COLLECTIVE_PUBLISH_ENABLED")
    openrouter_api_key: str = Field(default="", alias="COLLECTIVE_OPENROUTER_API_KEY")
    export_dir: str = Field(default="/var/lib/collective/exports", alias="COLLECTIVE_EXPORT_DIR")

    def validate_for_publish(self) -> None:
        missing = []
        if not self.github_repo.strip():
            missing.append("COLLECTIVE_GITHUB_REPO")
        if not self.git_user_name.strip():
            missing.append("COLLECTIVE_GIT_USER_NAME")
        if not self.git_user_email.strip():
            missing.append("COLLECTIVE_GIT_USER_EMAIL")
        if not self.github_token.strip():
            missing.append("COLLECTIVE_GITHUB_TOKEN")
        if missing:
            raise ValueError(f"Collective publish misconfigured: {', '.join(missing)}")
        if "users.noreply.github.com" not in self.git_user_email and "@" in self.git_user_email:
            # Soft warning encoded as validation note — personal email is a correlation risk.
            pass


@lru_cache
def get_collective_settings() -> CollectivePublishSettings:
    return CollectivePublishSettings()
