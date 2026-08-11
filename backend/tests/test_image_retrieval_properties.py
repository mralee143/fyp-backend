"""
Property-based tests for image retrieval round-trip.

Feature: image-upload-minio
Tests that uploaded images can be retrieved with identical content.
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
from services.auth import create_access_token, get_password_hash
from datetime import timedelta
from io import BytesIO
import time


# Test constants
UPLOAD_ENDPOINT = "/images/upload"
ALLOWED_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']
MIME_TYPE_MAP = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'gif': 'image/gif',
    'webp': 'image/webp',
    'bmp': 'image/bmp'
}


@pytest.fixture
async def test_client_with_minio(test_db: Prisma):
    """
    Create a test client with database and MinIO client configured.
    
    This fixture sets up both the Prisma client and MinIO client for testing
    image upload and retrieval endpoints.
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
    except Exception as e:
        pytest.skip(f"MinIO unavailable: {e}")
    
    with TestClient(app) as client:
        yield client


@st.composite
def valid_image_file(draw):
    """
    Generate valid image file data for property testing.
    
    Returns a dictionary with filename, content, and content_type.
    """
    # Choose a random extension from allowed list
    extension = draw(st.sampled_from(ALLOWED_EXTENSIONS))
    
    # Generate a filename (alphanumeric with underscores and hyphens)
    filename_base = draw(st.text(
        min_size=1,
        max_size=30,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_-"
        )
    ).filter(lambda x: x and x[0].isalnum()))
    
    # Generate random binary content (1KB to 1MB for reasonable test speed)
    content_size = draw(st.integers(min_value=1024, max_value=1024*1024))
    content = draw(st.binary(min_size=content_size, max_size=content_size))
    
    return {
        'filename': f"{filename_base}.{extension}",
        'content': content,
        'content_type': MIME_TYPE_MAP[extension]
    }


# Feature: image-upload-minio, Property 12: Image Retrieval Round-Trip
@given(
    image_data=valid_image_file(),
    email_local=st.text(min_size=1, max_size=20, alphabet=st.characters(
        whitelist_categories=("Ll", "Nd"), min_codepoint=97, max_codepoint=122
    )),
    email_domain=st.text(min_size=1, max_size=10, alphabet=st.characters(
        whitelist_categories=("Ll",), min_codepoint=97, max_codepoint=122
    )),
    password=st.text(min_size=8, max_size=50, alphabet=st.characters(
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
async def test_image_retrieval_round_trip(
    image_data,
    email_local,
    email_domain,
    password,
    unique_id,
    test_db: Prisma,
    test_client_with_minio
):
    """
    Property 12: Image Retrieval Round-Trip
    Validates: Requirements 6.1
    
    For any uploaded image, retrieving it by ID should return the same
    binary content that was originally uploaded.
    
    This property verifies that:
    1. Upload succeeds and returns image metadata
    2. Retrieval using the image ID returns the exact same binary content
    3. Content-Type header matches the uploaded MIME type
    4. The round-trip preserves data integrity
    """
    # Create a unique email to avoid constraint violations
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
    
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # Step 1: Upload the image
    files = {
        "file": (
            image_data['filename'],
            BytesIO(image_data['content']),
            image_data['content_type']
        )
    }
    
    upload_response = test_client_with_minio.post(
        UPLOAD_ENDPOINT,
        files=files,
        headers=headers
    )
    
    # Verify upload succeeded
    assert upload_response.status_code == 201, (
        f"Upload should succeed with 201. Got {upload_response.status_code}. "
        f"Response: {upload_response.text}"
    )
    
    upload_json = upload_response.json()
    assert "id" in upload_json, "Upload response should include image ID"
    assert "objectKey" in upload_json, "Upload response should include object key"
    assert "mimeType" in upload_json, "Upload response should include MIME type"
    
    image_id = upload_json["id"]
    uploaded_mime_type = upload_json["mimeType"]
    
    # Step 2: Retrieve the image
    retrieve_response = test_client_with_minio.get(
        f"/images/{image_id}",
        headers=headers
    )
    
    # Verify retrieval succeeded
    assert retrieve_response.status_code == 200, (
        f"Retrieval should succeed with 200. Got {retrieve_response.status_code}. "
        f"Response: {retrieve_response.text if retrieve_response.status_code != 200 else 'binary content'}"
    )
    
    # Step 3: Verify Content-Type header matches
    assert "content-type" in retrieve_response.headers, (
        "Retrieval response should include Content-Type header"
    )
    
    retrieved_content_type = retrieve_response.headers["content-type"]
    assert retrieved_content_type == uploaded_mime_type, (
        f"Content-Type should match uploaded MIME type. "
        f"Expected: {uploaded_mime_type}, Got: {retrieved_content_type}"
    )
    
    # Step 4: Verify binary content matches exactly (round-trip property)
    retrieved_content = retrieve_response.content
    assert retrieved_content == image_data['content'], (
        f"Retrieved content should match uploaded content exactly. "
        f"Original size: {len(image_data['content'])} bytes, "
        f"Retrieved size: {len(retrieved_content)} bytes"
    )
    
    # Additional verification: file size should match
    assert upload_json["fileSize"] == len(image_data['content']), (
        f"Stored file size should match original. "
        f"Expected: {len(image_data['content'])}, Got: {upload_json['fileSize']}"
    )


@pytest.mark.asyncio
async def test_image_round_trip_with_specific_content(test_db: Prisma, test_client_with_minio):
    """
    Unit test for image round-trip with specific known content.
    
    This test verifies the round-trip property with a specific example
    to ensure the basic functionality works correctly.
    """
    from services.auth import create_access_token, get_password_hash
    from datetime import timedelta
    
    # Create a test user
    user = await test_db.user.create(
        data={
            "email": "roundtrip_test@example.com",
            "hashedPassword": get_password_hash("TestPassword123!"),
            "isActive": True
        }
    )
    
    # Create JWT token
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=30)
    )
    
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # Specific test image content
    test_content = b"This is a test image content with some binary data: \x00\x01\x02\xff\xfe"
    test_filename = "test_image.jpg"
    test_mime_type = "image/jpeg"
    
    # Upload the image
    files = {
        "file": (test_filename, BytesIO(test_content), test_mime_type)
    }
    
    upload_response = test_client_with_minio.post(
        UPLOAD_ENDPOINT,
        files=files,
        headers=headers
    )
    
    assert upload_response.status_code == 201, f"Upload failed: {upload_response.text}"
    image_id = upload_response.json()["id"]
    
    # Retrieve the image
    retrieve_response = test_client_with_minio.get(
        f"/images/{image_id}",
        headers=headers
    )
    
    assert retrieve_response.status_code == 200, f"Retrieval failed: {retrieve_response.text}"
    assert retrieve_response.content == test_content, "Content should match exactly"
    assert retrieve_response.headers["content-type"] == test_mime_type, "MIME type should match"


@pytest.mark.asyncio
async def test_image_round_trip_with_large_file(test_db: Prisma, test_client_with_minio):
    """
    Unit test for image round-trip with a larger file (near the size limit).
    
    This test verifies that larger files (up to 10MB) maintain integrity
    through the upload-retrieval cycle.
    """
    from services.auth import create_access_token, get_password_hash
    from datetime import timedelta
    
    # Create a test user
    user = await test_db.user.create(
        data={
            "email": "largefile_test@example.com",
            "hashedPassword": get_password_hash("TestPassword123!"),
            "isActive": True
        }
    )
    
    # Create JWT token
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=30)
    )
    
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # Create a 10MB test file
    large_content = b"X" * (10 * 1024 * 1024)  # 10MB
    test_filename = "large_image.png"
    test_mime_type = "image/png"
    
    # Upload the image
    files = {
        "file": (test_filename, BytesIO(large_content), test_mime_type)
    }
    
    upload_response = test_client_with_minio.post(
        UPLOAD_ENDPOINT,
        files=files,
        headers=headers
    )
    
    assert upload_response.status_code == 201, f"Upload failed: {upload_response.text}"
    image_id = upload_response.json()["id"]
    
    # Retrieve the image
    retrieve_response = test_client_with_minio.get(
        f"/images/{image_id}",
        headers=headers
    )
    
    assert retrieve_response.status_code == 200, f"Retrieval failed"
    assert len(retrieve_response.content) == len(large_content), (
        f"Content size should match. Expected: {len(large_content)}, "
        f"Got: {len(retrieve_response.content)}"
    )
    assert retrieve_response.content == large_content, "Content should match exactly"
