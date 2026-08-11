"""
Configuration management using Pydantic Settings.

This module provides type-safe configuration management by loading
environment variables and validating them using Pydantic.
"""

from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field


# `.env` lives at the repo root, one level above this package, and is shared
# with docker-compose.yml and the frontend build. Resolved from __file__ rather
# than the cwd so the API, the ARQ worker and scripts/ all read the same file
# no matter where they are launched from.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    All settings are loaded from .env file or environment variables.
    Required settings will raise validation errors if missing.
    """
    
    # Database Configuration
    database_url: str = Field(
        ...,
        description="PostgreSQL database connection string"
    )
    
    # JWT Configuration
    secret_key: str = Field(
        ...,
        description="Secret key for JWT token signing (minimum 32 characters recommended)"
    )
    algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm"
    )
    access_token_expire_minutes: int = Field(
        default=30,
        description="JWT token expiration time in minutes"
    )
    
    # Application Configuration
    app_name: str = Field(
        default="Authentication API",
        description="Application name"
    )
    debug: bool = Field(
        default=False,
        description="Debug mode flag"
    )

    # CORS Configuration
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        description="Comma-separated list of allowed frontend origins for CORS"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a cleaned list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # Email / SMTP Configuration (Gmail: smtp.gmail.com:587 with an App Password)
    smtp_host: str = Field(default="smtp.gmail.com", description="SMTP server host")
    smtp_port: int = Field(default=587, description="SMTP server port (587 for STARTTLS)")
    smtp_user: str = Field(default="", description="SMTP username / sender email")
    smtp_password: str = Field(default="", description="SMTP password / Gmail App Password")
    smtp_from: str = Field(default="", description="From address (defaults to smtp_user)")

    # OTP Configuration
    otp_expiry_minutes: int = Field(default=10, description="Minutes an email OTP stays valid")

    # Gemini (LLM/VLM) video violence detection
    gemini_api_key: str = Field(default="", description="Google Gemini API key (aistudio.google.com)")
    gemini_model: str = Field(
        # A floating alias, not a pinned version: Google retires numbered models
        # and the API then 404s, which this pipeline can only report as "no
        # model reachable". gemini-2.0-flash died exactly that way.
        default="gemini-flash-latest",
        description="Gemini model id used for video analysis",
    )

    @property
    def llm_enabled(self) -> bool:
        """True when a Gemini API key is configured."""
        return bool(self.gemini_api_key)

    # Qwen chat agent (OpenAI-compatible endpoint: Ollama, vLLM, DashScope...)
    qwen_chat_base_url: str = Field(
        default="http://localhost:11434/v1",
        description=(
            "OpenAI-compatible base URL serving a Qwen chat model. Defaults to "
            "a local Ollama install (`ollama pull qwen2.5:7b`)."
        ),
    )
    qwen_chat_api_key: str = Field(
        default="ollama",
        description="API key for the Qwen chat endpoint (any value for Ollama)",
    )
    qwen_chat_model: str = Field(
        default="qwen2.5:7b",
        description="Qwen chat model id used by the agent",
    )
    qwen_chat_timeout_s: int = Field(
        default=180,
        description="Per-request timeout for the Qwen chat endpoint, in seconds",
    )
    qwen_chat_allow_local_fallback: bool = Field(
        default=True,
        description=(
            "When the chat endpoint is unreachable, fall back to running Qwen "
            "locally in `qwen_env` as a subprocess (offline but slow)."
        ),
    )

    # Admin
    admin_email: str = Field(
        default="aleehaider045@gmail.com",
        description="Email of the admin user (has access to the admin dashboard)",
    )

    @property
    def email_enabled(self) -> bool:
        """True when SMTP credentials are configured; otherwise OTPs are logged only."""
        return bool(self.smtp_user and self.smtp_password)

    @property
    def email_from(self) -> str:
        """Resolved From address."""
        return self.smtp_from or self.smtp_user
    
    # Redis — ARQ job broker, cache backend and SSE pub/sub channel
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL used by the queue, cache and pub/sub",
    )

    # Caching
    cache_enabled: bool = Field(default=True, description="Enable the Redis cache layer")
    cache_ttl_seconds: int = Field(
        default=300, description="Default TTL for cached API responses"
    )
    presigned_url_ttl_seconds: int = Field(
        default=3600, description="Lifetime of presigned MinIO URLs handed to the browser"
    )

    # Worker / pipeline
    frame_sample_count: int = Field(
        default=12,
        description="Frames extracted and streamed to the client before detection runs",
    )
    incident_frame_count: int = Field(
        default=5,
        description=(
            "Stills captured across each flagged incident, so the chat agent can "
            "name the exact frame the incident peaks on"
        ),
    )
    job_max_tries: int = Field(default=2, description="ARQ retries per analysis job")
    job_timeout_seconds: int = Field(
        default=1800, description="Hard timeout for one analysis job"
    )

    # Webhooks
    webhook_timeout_seconds: float = Field(
        default=10.0, description="HTTP timeout for one webhook delivery attempt"
    )
    webhook_max_attempts: int = Field(
        default=5, description="Delivery attempts before a webhook is marked FAILED"
    )

    # Chat agent
    chat_model: str = Field(
        default="gemini-flash-latest",
        description="Gemini model backing the per-video chat agent",
    )
    chat_history_turns: int = Field(
        default=12, description="Previous turns replayed into the chat prompt"
    )
    chat_max_frames: int = Field(
        default=6,
        description="Frame images attached to a chat prompt so the agent can look at the scene",
    )

    # MinIO Configuration
    minio_endpoint: str = Field(
        ...,
        description="MinIO server endpoint (e.g., 'localhost:9000')"
    )
    minio_access_key: str = Field(
        ...,
        description="MinIO access key"
    )
    minio_secret_key: str = Field(
        ...,
        description="MinIO secret key"
    )
    minio_bucket_name: str = Field(
        default="user-images",
        description="MinIO bucket name for storing images"
    )
    minio_media_bucket: str = Field(
        default="vision-media",
        description="MinIO bucket for videos, extracted frames and incident clips",
    )
    minio_secure: bool = Field(
        default=False,
        description="Use HTTPS for MinIO connection"
    )
    minio_public_endpoint: str = Field(
        default="",
        description=(
            "Browser-facing MinIO host used only when signing presigned URLs. "
            "Set this when the API reaches MinIO under a different name than "
            "the browser does — in Docker the API talks to 'minio:9000' while "
            "the browser can only resolve 'localhost:9000'. A presigned URL "
            "signs the Host header, so the signing host must be the one the "
            "browser will request. Empty means both are the same."
        ),
    )
    
    model_config = {
        "env_file": str(ENV_FILE),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }


# Global settings instance
settings = Settings()
