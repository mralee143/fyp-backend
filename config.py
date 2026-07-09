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
