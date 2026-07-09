"""
Configuration management using Pydantic Settings.

This module provides type-safe configuration management by loading
environment variables and validating them using Pydantic.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


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

    @property
    def email_enabled(self) -> bool:
        """True when SMTP credentials are configured; otherwise OTPs are logged only."""
        return bool(self.smtp_user and self.smtp_password)

    @property
    def email_from(self) -> str:
        """Resolved From address."""
        return self.smtp_from or self.smtp_user
    
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
    minio_secure: bool = Field(
        default=False,
        description="Use HTTPS for MinIO connection"
    )
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }


# Global settings instance
settings = Settings()
