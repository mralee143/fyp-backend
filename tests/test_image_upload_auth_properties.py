"""
Property-based tests for image upload authentication.

Feature: image-upload-minio
Tests authentication requirements for image upload endpoints.
"""

import pytest
from hypothesis import given, strategies as st, settings as hypothesis_settings, HealthCheck
from fastapi.testclient import TestClient
from prisma import Prisma
from main import app
from services.database import set_prisma
from services.minio_client import MinIOClient
from routes.images import set_minio_client
from config import settings as app_settings
from io import BytesIO


# Test constants
TEST_IMAGE_CONTENT = b"fake image content for testing"
TEST_IMAGE_FILENAME = "test_image.jpg"
TEST_IMAGE_MIME_TYPE = "image/jpeg"
UPLOAD_ENDPOINT = "/images/images/upload"


def create_test_image_file(filename=TEST_IMAGE_FILENAME, content=TEST_IMAGE_CONTENT, mime_type=TEST_IMAGE_MIME_TYPE):
    """Create a test image file for upload testing."""
    return {"file": (filename, BytesIO(content), mime_type)}


@pytest.fixture
async def test_client_with_minio(test_db: Prisma):
    """
    Create a test client with database and MinIO client configured.
    
    This fixture sets up both the Prisma client and MinIO client for testing
    image upload endpoints.
    """
    set_prisma(test_db)
    
    try:
        minio_client = MinIOClient(
            endpoint=app_settings.minio_endpoint,
            access_key=app_settings.minio_access_key,
            secret_key=app_settings.minio_secret_key,
            bucket_name=app_settings.minio_bucket_name,
            secure=app_settings.minio_secure
        )
        minio_client.ensure_bucket_exists()
        set_minio_client(minio_client)
    except Exception:
        # MinIO unavailable - endpoint will return 503 (acceptable for auth tests)
        set_minio_client(None)
    
    with TestClient(app) as client:
        yield client


# Feature: image-upload-minio, Property 1: Unauthenticated Request Rejection
@given(
    invalid_token=st.one_of(
        st.text(min_size=1, max_size=100, alphabet=st.characters(
            blacklist_categories=("Cs",), blacklist_characters="\x00"
        )).filter(lambda x: not x.startswith("eyJ")),
        st.just(""),
        st.just("invalid.token.here"),
        st.just("Bearer invalid"),
        st.just("malformed_token"),
        st.just("random_string_12345"),
    )
)
@hypothesis_settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None
)
@pytest.mark.asyncio
async def test_unauthenticated_upload_request_rejection(invalid_token, test_client_with_minio):
    """
    Property 1: Unauthenticated Request Rejection
    Validates: Requirements 1.1, 1.3
    
    For any request to the image upload endpoint without a valid JWT token,
    the system should reject the request with a 401 Unauthorized status.
    """
    files = create_test_image_file()
    
    headers = {}
    if invalid_token and invalid_token.strip():
        headers["Authorization"] = (
            invalid_token if invalid_token.startswith("Bearer ") 
            else f"Bearer {invalid_token}"
        )
    
    response = test_client_with_minio.post(UPLOAD_ENDPOINT, files=files, headers=headers)
    
    assert response.status_code == 401, (
        f"Upload with invalid token should return 401, got {response.status_code}. "
        f"Token: '{invalid_token[:50]}...', Response: {response.text}"
    )
    
    response_json = response.json()
    assert "detail" in response_json
    
    detail_lower = response_json["detail"].lower()
    assert any(keyword in detail_lower for keyword in [
        "credential", "authenticate", "authorization", "token", "unauthorized"
    ]), f"Error detail should mention authentication issue, got: {response_json['detail']}"


@pytest.mark.asyncio
async def test_upload_without_authorization_header(test_client_with_minio):
    """Verify upload without Authorization header is rejected with 401."""
    response = test_client_with_minio.post(UPLOAD_ENDPOINT, files=create_test_image_file())
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_upload_with_empty_bearer_token(test_client_with_minio):
    """Verify upload with empty Bearer token is rejected with 401."""
    response = test_client_with_minio.post(
        UPLOAD_ENDPOINT,
        files=create_test_image_file(),
        headers={"Authorization": "Bearer "}
    )
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_upload_with_malformed_authorization_header(test_client_with_minio):
    """Verify upload with malformed Authorization headers are rejected with 401."""
    malformed_headers = [
        "InvalidFormat token123",
        "Basic sometoken",
        "token_without_bearer",
        "Bearer",
    ]
    
    for auth_header in malformed_headers:
        response = test_client_with_minio.post(
            UPLOAD_ENDPOINT,
            files=create_test_image_file(),
            headers={"Authorization": auth_header}
        )
        assert response.status_code == 401, (
            f"Malformed header '{auth_header}' should return 401, got {response.status_code}"
        )
        assert "detail" in response.json()


# Feature: image-upload-minio, Property 2: Authenticated Request Processing
@given(
    email_local=st.text(min_size=1, max_size=20, alphabet=st.characters(
        whitelist_categories=("Ll", "Nd"), min_codepoint=97, max_codepoint=122
    )),
    email_domain=st.text(min_size=1, max_size=10, alphabet=st.characters(
        whitelist_categories=("Ll",), min_codepoint=97, max_codepoint=122
    )),
    password=st.text(min_size=8, max_size=100, alphabet=st.characters(
        blacklist_categories=("Cs",), blacklist_characters="\x00"
    )),
    unique_id=st.integers(min_value=1, max_value=999999999)
)
@hypothesis_settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None
)
@pytest.mark.asyncio
async def test_authenticated_upload_request_processing(
    email_local, email_domain, password, unique_id, test_db: Prisma, test_client_with_minio
):
    """
    Property 2: Authenticated Request Processing
    Validates: Requirements 1.2
    
    For any request to the upload endpoint with a valid JWT token,
    the system should extract the user identity and process the request.
    
    Note: This test verifies that authentication succeeds and the request
    is processed (not rejected with 401). The actual upload may fail due to
    MinIO unavailability (503) or validation errors (400), but those are
    different from authentication failures.
    """
    from services.auth import create_access_token, get_password_hash
    from datetime import timedelta
    import time
    
    # Create a unique email to avoid constraint violations
    # Use timestamp + unique_id to ensure uniqueness across test runs
    timestamp = int(time.time() * 1000)
    email = f"{email_local}{unique_id}{timestamp}@{email_domain}.com"
    
    # Create a test user in the database
    user = await test_db.user.create(
        data={
            "email": email,
            "hashedPassword": get_password_hash(password),
            "isActive": True
        }
    )
    
    # Create a valid JWT token for the user
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=30)
    )
    
    # Make upload request with valid authentication
    files = create_test_image_file()
    headers = {"Authorization": f"Bearer {access_token}"}
    
    response = test_client_with_minio.post(UPLOAD_ENDPOINT, files=files, headers=headers)
    
    # Authenticated requests should not return 401 (authentication failure)
    # Valid status codes: 201 (success), 400 (validation), 503 (MinIO unavailable), 500 (server error)
    assert response.status_code in [201, 400, 503, 500], (
        f"Authenticated request should not return 401. "
        f"Got {response.status_code}. Response: {response.text}"
    )
