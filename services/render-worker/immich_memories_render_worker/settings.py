"""Worker-owned settings; requests cannot choose local paths or hardware policy."""

from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    # WHY: the app's own `render` section produces IMMICH_MEMORIES_RENDER__WORKER_TOKEN.
    # On the shorter prefix the two differ by one underscore and neither errors.
    model_config = SettingsConfigDict(env_prefix="IMMICH_MEMORIES_RENDER_WORKER_", extra="forbid")
    host: str = "127.0.0.1"
    port: int = Field(default=8093, ge=1, le=65535)
    token: SecretStr
    immich_url: AnyHttpUrl
    directory: Path
    max_jobs: int = Field(default=4, ge=1, le=32)
    retention_seconds: int = Field(default=3600, ge=60, le=86400)
    job_timeout_seconds: int = Field(default=3600, ge=30, le=86400)

    @field_validator("token")
    @classmethod
    def require_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("a worker bearer token is required")
        return value
