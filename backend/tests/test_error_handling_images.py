"""
Error handling tests for image upload system.

Tests error scenarios including descriptive error messages for upload failures
and MinIO service unavailability.

Feature: image-upload-minio
"""

import pytest
from hypothesis import given, strategies as st, settings
from services.database import set_prisma
from services.minio_client import MinIOClient
from routes.images import set_minio_client
from unittest.mock import Mock
import io


# Helper functions for DRY principle

def create_test_file(filename: str, content: bytes = b"fake image content", mime_type: str = "image/jpeg"):
    """Create test file data for upload."""
    return {"file": (filename, io.BytesIO(content), mime_type)}


async def upload_image(client, token: str, filename: str, content: bytes = b"fake image content", mime_type: str = "image/jpeg"):
    """Upload an image and return the response."""
    files = create_test_file(filename, content, mime_type)
    return await client.post(
        "/images/upload",
        files=files,
        headers={"Authorization": f"Bearer {token}"}
    )


def assert_error_response(response, expected_status: int, keywords: list[str]):
    """Assert error response has expected status and descriptive message."""
    assert response.status_code == expected_status, f"Expected {expected_status}, got {response.status_code}"
    error_data = response.json()
    assert "detail" in error_data
    assert isinstance(error_data["detail"], str)
    assert len(error_data["detail"]) > 0
    detail_lower = error_data["detail"].lower()
    assert any(keyword in detail_lower for keyword in keywords), \
        f"Expected one of {keywords} in error message: {error_data['detail']}"


# Property 16: Descriptive Error Messages
# Validates: Requirements 9.1

@pytest.mark.asyncio
@pytest.mark.property_test
@given(filename=st.text(min_size=1, max_size=50).filter(lambda x: '.' not in x))
@settings(max_examples=100, deadline=None)
async def test_upload_without_extension_returns_descriptive_error(
    filename, test_db, authenticated_client_factory
):
    """
    Property 16: Descriptive Error Messages - No Extension
    Validates: Requirements 9.1
    """
    set_prisma(test_db)
    client, token = await authenticated_client_factory()
    
    response = await upload_image(client, token, filename)
    assert_error_response(response, 400, ["extension", "format", "file"])


@pytest.mark.asyncio
@pytest.mark.property_test
@given(
    extension=st.text(min_size=1, max_size=10).filter(
        lambda x: x.lower() not in {'jpg', 'jpeg', 'png', 'svg', 'bmp', 'webp', 'gif', 'tiff', 'ico'}
        and '/' not in x and '\\' not in x
    )
)
@settings(max_examples=100, deadline=None)
async def test_upload_with_invalid_extension_returns_descriptive_error(
    extension, test_db, authenticated_client_factory
):
    """
    Property 16: Descriptive Error Messages - Invalid Extension
    Validates: Requirements 9.1
    """
    set_prisma(test_db)
    client, token = await authenticated_client_factory()
    
    filename = f"test_file.{extension}"
    response = await upload_image(client, token, filename)
    assert_error_response(response, 400, ["extension", "allowed", "format", "not allowed"])


@pytest.mark.asyncio
async def test_upload_oversized_file_returns_descriptive_error(
    test_db, authenticated_client_factory
):
    """
    Property 16: Descriptive Error Messages - File Too Large
    Validates: Requirements 9.1
    """
    set_prisma(test_db)
    client, token = await authenticated_client_factory()
    
    # Create a file larger than 50MB
    file_size = 51 * 1024 * 1024  # 51MB
    file_content = b"x" * file_size
    
    response = await upload_image(client, token, "large_image.jpg", file_content)
    assert_error_response(response, 400, ["size", "limit", "large", "exceeds", "50"])


@pytest.mark.asyncio
async def test_upload_with_mime_mismatch_returns_descriptive_error(
    test_db, authenticated_client_factory
):
    """
    Property 16: Descriptive Error Messages - MIME Type Mismatch
    Validates: Requirements 9.1
    """
    set_prisma(test_db)
    client, token = await authenticated_client_factory()
    
    # Create a .jpg file but claim it's a PNG
    response = await upload_image(client, token, "test_image.jpg", mime_type="image/png")
    assert_error_response(response, 400, ["mime", "type", "content", "match"])


# MinIO Service Unavailability Tests
# Validates: Requirements 9.2

@pytest.mark.asyncio
async def test_upload_when_minio_unavailable_returns_503(
    test_db, authenticated_client_factory
):
    """
    MinIO Unavailability - Upload
    Validates: Requirements 9.2
    """
    set_prisma(test_db)
    client, token = await authenticated_client_factory()
    
    # Mock MinIO client to simulate unavailability
    mock_minio = Mock(spec=MinIOClient)
    mock_minio.upload_file.side_effect = Exception("Connection refused")
    set_minio_client(mock_minio)
    
    response = await upload_image(client, token, "test_image.jpg")
    assert_error_response(response, 503, ["unavailable", "service", "storage"])


@pytest.mark.asyncio
async def test_retrieve_when_minio_unavailable_returns_503(
    test_db, authenticated_client_factory
):
    """
    MinIO Unavailability - Retrieval
    Validates: Requirements 9.2
    """
    set_prisma(test_db)
    client, token = await authenticated_client_factory()
    
    # Get user ID and create image metadata
    user_data = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    user_id = user_data.json()["id"]
    
    image = await test_db.image.create(
        data={
            "userId": user_id,
            "objectKey": "user_test/test_image.jpg",
            "originalFilename": "test_image.jpg",
            "generatedFilename": "test_image_123456.jpg",
            "mimeType": "image/jpeg",
            "fileSize": 1024
        }
    )
    
    # Mock MinIO client to simulate unavailability
    mock_minio = Mock(spec=MinIOClient)
    mock_minio.download_file.side_effect = Exception("Connection refused")
    set_minio_client(mock_minio)
    
    response = await client.get(f"/images/{image.id}", headers={"Authorization": f"Bearer {token}"})
    assert_error_response(response, 503, ["unavailable", "service", "storage"])


@pytest.mark.asyncio
async def test_minio_client_not_initialized_returns_503(
    test_db, authenticated_client_factory
):
    """
    MinIO Not Initialized
    Validates: Requirements 9.2
    """
    set_prisma(test_db)
    client, token = await authenticated_client_factory()
    
    # Set MinIO client to None to simulate uninitialized state
    set_minio_client(None)
    
    response = await upload_image(client, token, "test_image.jpg")
    assert_error_response(response, 503, ["unavailable", "service", "storage", "not available"])
