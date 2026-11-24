from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://songscribe:songscribe@postgres:5432/songscribe"
    )

    # Queue / Redis
    queue_backend: Literal["celery", "local"] = Field(default="celery")
    redis_url: str = Field(default="redis://redis:6379/0")
    celery_broker_url: str = Field(default="")
    celery_result_backend: str = Field(default="")

    # Storage
    storage_backend: Literal["s3", "local"] = Field(default="s3")
    local_storage_dir: str = Field(default="./local_data/storage")
    s3_endpoint_url: str = Field(default="http://minio:9000")
    s3_public_endpoint_url: str = Field(default="http://localhost:9000")
    s3_bucket: str = Field(default="songscribe")
    s3_access_key: str = Field(default="minioadmin")
    s3_secret_key: str = Field(default="minioadmin123")
    s3_region: str = Field(default="us-east-1")
    s3_use_ssl: bool = Field(default=False)

    # Compute & ML
    device: str = Field(default="auto")
    model_cache_dir: str = Field(default="/models")
    piano_model_path: str = Field(default="")
    demucs_model: str = Field(default="htdemucs")

    # API
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"
    )
    max_upload_mb: int = Field(default=100)

    @property
    def effective_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def effective_result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def local_storage_path(self) -> Path:
        return Path(self.local_storage_dir).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
