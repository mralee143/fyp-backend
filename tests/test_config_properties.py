"""
Property-based tests for configuration management.

Feature: fastapi-prisma-migration
Property 14: Configuration Validation on Startup
Validates: Requirements 10.4
"""

import os
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from pydantic import ValidationError
from config import Settings


# Feature: fastapi-prisma-migration, Property 14: Configuration Validation on Startup
@given(
    missing_var=st.sampled_from(['DATABASE_URL', 'SECRET_KEY'])
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=100)
def test_missing_required_env_variables_fails_startup(missing_var, monkeypatch):
    """
    For any application startup attempt with missing required environment variables
    (DATABASE_URL or SECRET_KEY), the system should fail to start with a clear
    error message indicating which variable is missing.
    
    Validates: Requirements 10.4
    """
    # Clear all environment variables
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('SECRET_KEY', raising=False)
    monkeypatch.delenv('ALGORITHM', raising=False)
    monkeypatch.delenv('ACCESS_TOKEN_EXPIRE_MINUTES', raising=False)
    monkeypatch.delenv('APP_NAME', raising=False)
    monkeypatch.delenv('DEBUG', raising=False)
    
    # Set all required variables except the one we're testing
    if missing_var != 'DATABASE_URL':
        monkeypatch.setenv('DATABASE_URL', 'postgresql://test:test@localhost:5432/test')
    if missing_var != 'SECRET_KEY':
        monkeypatch.setenv('SECRET_KEY', 'test-secret-key-at-least-32-characters-long')
    
    # Attempt to create Settings instance should fail
    # We need to prevent loading from .env file by passing _env_file=None
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    
    # Verify the error message mentions the missing field
    error_message = str(exc_info.value)
    assert missing_var.lower() in error_message.lower(), \
        f"Error message should mention missing variable {missing_var}"


def test_valid_configuration_succeeds(monkeypatch):
    """
    Test that providing all required environment variables allows successful startup.
    This is a unit test to verify the happy path.
    """
    # Set all required environment variables
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost:5432/testdb')
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key-minimum-32-chars-long')
    
    # Should not raise any exception
    settings = Settings(_env_file=None)
    
    # Verify settings are loaded correctly
    assert settings.database_url == 'postgresql://user:pass@localhost:5432/testdb'
    assert settings.secret_key == 'test-secret-key-minimum-32-chars-long'
    assert settings.algorithm == 'HS256'  # Default value
    assert settings.access_token_expire_minutes == 30  # Default value
    assert settings.app_name == 'Authentication API'  # Default value
    assert settings.debug is False  # Default value


def test_optional_settings_use_defaults(monkeypatch):
    """
    Test that optional settings use their default values when not provided.
    """
    # Set only required variables
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost:5432/testdb')
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key-minimum-32-chars-long')
    
    # Clear optional variables
    monkeypatch.delenv('ALGORITHM', raising=False)
    monkeypatch.delenv('ACCESS_TOKEN_EXPIRE_MINUTES', raising=False)
    monkeypatch.delenv('APP_NAME', raising=False)
    monkeypatch.delenv('DEBUG', raising=False)
    
    settings = Settings(_env_file=None)
    
    # Verify defaults are used
    assert settings.algorithm == 'HS256'
    assert settings.access_token_expire_minutes == 30
    assert settings.app_name == 'Authentication API'
    assert settings.debug is False


def test_custom_optional_settings_override_defaults(monkeypatch):
    """
    Test that custom values for optional settings override defaults.
    """
    # Set all variables including optional ones
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost:5432/testdb')
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key-minimum-32-chars-long')
    monkeypatch.setenv('ALGORITHM', 'HS512')
    monkeypatch.setenv('ACCESS_TOKEN_EXPIRE_MINUTES', '60')
    monkeypatch.setenv('APP_NAME', 'Custom API')
    monkeypatch.setenv('DEBUG', 'true')
    
    settings = Settings(_env_file=None)
    
    # Verify custom values are used
    assert settings.algorithm == 'HS512'
    assert settings.access_token_expire_minutes == 60
    assert settings.app_name == 'Custom API'
    assert settings.debug is True
