"""Environment configuration for the application foundation."""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr
    database_pool_size: int = Field(default=5, ge=1)
    database_pool_timeout: float = Field(default=5, gt=0)
    database_connect_timeout: float = Field(default=5, gt=0)
    database_command_timeout: float = Field(default=10, gt=0)
    jwt_secret: SecretStr | None = None
    jwt_issuer: str = Field(default="chunking-pdf", min_length=1)
    jwt_audience: str = Field(default="chunking-pdf-api", min_length=1)
    jwt_access_ttl_seconds: int = Field(default=900, gt=0)
    auth_rate_window_seconds: int = Field(default=60, gt=0)
    auth_ip_limit: int = Field(default=60, gt=0)
    auth_identity_limit: int = Field(default=10, gt=0)
    pdf_storage_dir: Path = Path("data/pdfs")
    upload_max_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    upload_timeout_seconds: float = Field(default=120, gt=0)
    pdf_max_pages: int = Field(default=500, gt=0)
    pdf_max_chunks: int = Field(default=10000, gt=0)
    ocr_engine: str = "tesseract"
    tokenizer_name: str = "cl100k_base"
    tesseract_command: str = "tesseract"
    tesseract_data_dir: Path | None = None
    ocr_languages: str = Field(default="vie+eng", pattern=r"^[a-zA-Z0-9_+]+$")
    ocr_dpi: int = Field(default=200, ge=72, le=400)
    ocr_timeout_seconds: int = Field(default=120, gt=0)
    ocr_min_confidence: float = Field(default=60, ge=0, le=100)
    pdf_max_render_pixels: int = Field(default=25000000, gt=0)
    pdf_max_text_chars: int = Field(default=5000000, gt=0)
    worker_max_memory_mb: int = Field(default=1024, ge=128)
    worker_poll_seconds: float = Field(default=2, gt=0)
    retry_owner_limit: int = Field(default=10, gt=0)
    chunk_min_tokens: int = Field(default=100, gt=0)
    chunk_target_tokens: int = Field(default=500, gt=0)
    chunk_max_tokens: int = Field(default=800, gt=0)
    chunk_overlap_tokens: int = Field(default=50, ge=0)
    chunk_max_repair_rounds: int = Field(default=2, ge=0)
    processing_config_version: str = Field(default="1", min_length=1)
    worker_concurrency: int = Field(default=1, gt=0)
    worker_lease_seconds: int = Field(default=60, gt=0)
    worker_heartbeat_seconds: int = Field(default=15, gt=0)
    worker_max_attempts: int = Field(default=3, gt=0)
    worker_retry_backoff_seconds: int = Field(default=30, gt=0)
    worker_deadline_seconds: int = Field(default=900, gt=0)
    source_completed_retention_seconds: int = Field(default=86400, gt=0)
    source_failed_retention_seconds: int = Field(default=604800, gt=0)
    orphan_grace_seconds: int = Field(default=3600, gt=0)

    @model_validator(mode="after")
    def validate_processing(self) -> "Settings":
        if self.ocr_engine != "tesseract" or self.tokenizer_name != "cl100k_base":
            raise ValueError("Supported local processing: tesseract and cl100k_base")
        if not self.chunk_min_tokens <= self.chunk_target_tokens <= self.chunk_max_tokens:
            raise ValueError("Chunk budgets must satisfy min <= target <= max")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("Chunk overlap must be smaller than target")
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds:
            raise ValueError("Heartbeat must be shorter than lease")
        if self.orphan_grace_seconds <= self.upload_timeout_seconds:
            raise ValueError("Orphan grace must exceed upload timeout")
        return self

    def processing_snapshot(self) -> dict:
        return self.model_dump(
            mode="json",
            include={
                "processing_config_version",
                "ocr_engine",
                "tokenizer_name",
                "chunk_min_tokens",
                "chunk_target_tokens",
                "chunk_max_tokens",
                "chunk_overlap_tokens",
                "chunk_max_repair_rounds",
                "pdf_max_pages",
                "pdf_max_chunks",
                "ocr_languages",
                "ocr_dpi",
                "ocr_timeout_seconds",
                "ocr_min_confidence",
                "pdf_max_render_pixels",
                "pdf_max_text_chars",
            },
        )

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 bytes")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError:
            raise ValueError("DATABASE_URL must be a PostgreSQL asyncpg URL") from None
        if url.drivername != "postgresql+asyncpg" or not url.host or not url.database:
            raise ValueError("DATABASE_URL requires postgresql+asyncpg, host and database")
        return value
