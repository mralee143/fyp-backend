"""
Unit tests for API documentation endpoints.

Tests that the FastAPI automatic documentation endpoints are accessible
and return successful responses.

Requirements: 1.2, 13.1, 13.2
"""

import pytest
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.mark.asyncio
async def test_swagger_ui_docs_endpoint():
    """
    Test that /docs endpoint returns 200 OK.
    
    The /docs endpoint provides Swagger UI interactive documentation.
    This test verifies the endpoint is accessible and returns a successful response.
    
    Validates: Requirements 1.2, 13.1
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/docs")
        assert response.status_code == 200
        # Swagger UI returns HTML content
        assert "text/html" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_redoc_endpoint():
    """
    Test that /redoc endpoint returns 200 OK.
    
    The /redoc endpoint provides ReDoc alternative documentation interface.
    This test verifies the endpoint is accessible and returns a successful response.
    
    Validates: Requirements 1.2, 13.2
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/redoc")
        assert response.status_code == 200
        # ReDoc returns HTML content
        assert "text/html" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_openapi_schema_endpoint():
    """
    Test that /openapi.json endpoint returns the OpenAPI schema.
    
    FastAPI automatically generates an OpenAPI schema that powers the
    documentation interfaces. This test verifies the schema is accessible.
    
    Validates: Requirements 13.5
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        # OpenAPI schema is JSON
        assert "application/json" in response.headers.get("content-type", "")
        
        # Verify it's a valid OpenAPI schema with expected fields
        schema = response.json()
        assert "openapi" in schema
        assert "info" in schema
        assert schema["info"]["title"] == "Authentication API"
        assert schema["info"]["version"] == "2.0.0"
