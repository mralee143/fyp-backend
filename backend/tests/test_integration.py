"""
End-to-end integration tests for the authentication system.

These tests verify complete user flows from signup through login to profile access,
testing the integration of all components (routes, middleware, services, database).

Feature: fastapi-prisma-migration
Validates: Requirements 1.4, 5.1, 5.2, 6.1
"""

import pytest
from fastapi.testclient import TestClient
from prisma import Prisma
from main import app
from services.database import set_prisma


@pytest.fixture
async def test_client(test_db: Prisma):
    """
    Create a test client with a clean database for integration tests.
    
    This fixture sets up the Prisma client for the application and provides
    a TestClient for making HTTP requests to the API.
    """
    # Set the test database for the application
    set_prisma(test_db)
    
    # Create test client
    with TestClient(app) as client:
        yield client


@pytest.mark.asyncio
async def test_complete_signup_login_profile_flow(test_client):
    """
    Test the complete user journey: signup → login → profile access.
    
    This integration test verifies that:
    1. A new user can successfully register
    2. The registered user can log in and receive a JWT token
    3. The user can access their profile using the JWT token
    
    Validates: Requirements 1.4, 5.1, 5.2, 6.1
    """
    # Step 1: Sign up a new user
    signup_data = {
        "email": "integration_test@example.com",
        "password": "SecurePassword123!"
    }
    
    signup_response = test_client.post("/auth/signup", json=signup_data)
    
    # Verify signup was successful
    assert signup_response.status_code == 201, f"Signup failed: {signup_response.text}"
    signup_json = signup_response.json()
    assert "id" in signup_json, "Signup response should include user ID"
    assert signup_json["email"] == signup_data["email"], "Email should match"
    assert signup_json["isActive"] is True, "New user should be active"
    assert "hashedPassword" not in signup_json, "Password should not be in response"
    assert "password" not in signup_json, "Password should not be in response"
    
    user_id = signup_json["id"]
    
    # Step 2: Log in with the created user
    login_data = {
        "username": signup_data["email"],  # OAuth2 form uses 'username' field
        "password": signup_data["password"]
    }
    
    login_response = test_client.post(
        "/auth/login",
        data=login_data,  # OAuth2PasswordRequestForm expects form data, not JSON
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    
    # Verify login was successful
    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    login_json = login_response.json()
    assert "access_token" in login_json, "Login response should include access token"
    assert "token_type" in login_json, "Login response should include token type"
    assert login_json["token_type"] == "bearer", "Token type should be 'bearer'"
    assert len(login_json["access_token"]) > 0, "Access token should not be empty"
    
    access_token = login_json["access_token"]
    
    # Step 3: Access user profile with the JWT token
    profile_response = test_client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    
    # Verify profile access was successful
    assert profile_response.status_code == 200, f"Profile access failed: {profile_response.text}"
    profile_json = profile_response.json()
    assert profile_json["id"] == user_id, "Profile should match the created user"
    assert profile_json["email"] == signup_data["email"], "Email should match"
    assert profile_json["isActive"] is True, "User should be active"
    assert "hashedPassword" not in profile_json, "Password should not be in profile"
    assert "password" not in profile_json, "Password should not be in profile"
    assert "createdAt" in profile_json, "Profile should include createdAt timestamp"
    assert "updatedAt" in profile_json, "Profile should include updatedAt timestamp"


@pytest.mark.asyncio
async def test_authentication_flow_with_invalid_tokens(test_client):
    """
    Test authentication flow with various invalid token scenarios.
    
    This integration test verifies that:
    1. Requests without tokens are rejected
    2. Requests with invalid tokens are rejected
    3. Requests with malformed tokens are rejected
    
    Validates: Requirements 6.1 (authentication requirement)
    """
    # Test 1: Access profile without any token
    response_no_token = test_client.get("/users/me")
    assert response_no_token.status_code == 401, "Request without token should be rejected"
    assert "detail" in response_no_token.json(), "Error response should include detail"
    
    # Test 2: Access profile with invalid token
    response_invalid_token = test_client.get(
        "/users/me",
        headers={"Authorization": "Bearer invalid_token_12345"}
    )
    assert response_invalid_token.status_code == 401, "Request with invalid token should be rejected"
    assert "detail" in response_invalid_token.json(), "Error response should include detail"
    
    # Test 3: Access profile with malformed authorization header
    response_malformed = test_client.get(
        "/users/me",
        headers={"Authorization": "InvalidFormat token123"}
    )
    assert response_malformed.status_code == 401, "Request with malformed auth header should be rejected"
    
    # Test 4: Access profile with empty token
    response_empty_token = test_client.get(
        "/users/me",
        headers={"Authorization": "Bearer "}
    )
    assert response_empty_token.status_code == 401, "Request with empty token should be rejected"


@pytest.mark.asyncio
async def test_duplicate_registration_flow(test_client):
    """
    Test that duplicate email registration is properly rejected.
    
    This integration test verifies that:
    1. First registration succeeds
    2. Second registration with same email fails with 409 Conflict
    3. Error message is clear and informative
    
    Validates: Requirements 5.4 (duplicate email rejection)
    """
    # Step 1: Register first user
    user_data = {
        "email": "duplicate_test@example.com",
        "password": "FirstPassword123!"
    }
    
    first_signup = test_client.post("/auth/signup", json=user_data)
    assert first_signup.status_code == 201, "First signup should succeed"
    first_user = first_signup.json()
    assert first_user["email"] == user_data["email"]
    
    # Step 2: Attempt to register with the same email
    duplicate_data = {
        "email": "duplicate_test@example.com",  # Same email
        "password": "DifferentPassword456!"  # Different password
    }
    
    duplicate_signup = test_client.post("/auth/signup", json=duplicate_data)
    
    # Verify duplicate registration is rejected
    assert duplicate_signup.status_code == 409, "Duplicate email should return 409 Conflict"
    error_response = duplicate_signup.json()
    assert "detail" in error_response, "Error response should include detail"
    assert "already registered" in error_response["detail"].lower(), "Error should mention email is already registered"
    
    # Step 3: Verify original user can still log in
    login_data = {
        "username": user_data["email"],
        "password": user_data["password"]
    }
    
    login_response = test_client.post(
        "/auth/login",
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    
    assert login_response.status_code == 200, "Original user should still be able to log in"
    assert "access_token" in login_response.json()


@pytest.mark.asyncio
async def test_login_with_invalid_credentials(test_client):
    """
    Test login attempts with various invalid credential scenarios.
    
    This integration test verifies that:
    1. Login with non-existent email fails
    2. Login with wrong password fails
    3. Both scenarios return 401 Unauthorized
    
    Validates: Requirements 5.3 (invalid credentials rejection)
    """
    # First, create a valid user
    valid_user = {
        "email": "valid_user@example.com",
        "password": "CorrectPassword123!"
    }
    
    signup_response = test_client.post("/auth/signup", json=valid_user)
    assert signup_response.status_code == 201, "User creation should succeed"
    
    # Test 1: Login with non-existent email
    login_nonexistent = test_client.post(
        "/auth/login",
        data={
            "username": "nonexistent@example.com",
            "password": "SomePassword123!"
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    
    assert login_nonexistent.status_code == 401, "Login with non-existent email should fail"
    error_response = login_nonexistent.json()
    assert "detail" in error_response
    assert "incorrect" in error_response["detail"].lower() or "invalid" in error_response["detail"].lower()
    
    # Test 2: Login with wrong password
    login_wrong_password = test_client.post(
        "/auth/login",
        data={
            "username": valid_user["email"],
            "password": "WrongPassword456!"
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    
    assert login_wrong_password.status_code == 401, "Login with wrong password should fail"
    error_response = login_wrong_password.json()
    assert "detail" in error_response
    assert "incorrect" in error_response["detail"].lower() or "invalid" in error_response["detail"].lower()
    
    # Test 3: Verify correct credentials still work
    login_correct = test_client.post(
        "/auth/login",
        data={
            "username": valid_user["email"],
            "password": valid_user["password"]
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    
    assert login_correct.status_code == 200, "Login with correct credentials should succeed"
    assert "access_token" in login_correct.json()


@pytest.mark.asyncio
async def test_inactive_user_cannot_login(test_client, test_db: Prisma):
    """
    Test that inactive users cannot log in.
    
    This integration test verifies that:
    1. User can be created and initially log in
    2. After being deactivated, login fails
    3. Error message indicates inactive user status
    
    Validates: Requirements 5.2 (active user requirement for login)
    """
    # Step 1: Create and verify a user can log in
    user_data = {
        "email": "inactive_test@example.com",
        "password": "TestPassword123!"
    }
    
    signup_response = test_client.post("/auth/signup", json=user_data)
    assert signup_response.status_code == 201
    
    # Verify initial login works
    login_data = {
        "username": user_data["email"],
        "password": user_data["password"]
    }
    
    initial_login = test_client.post(
        "/auth/login",
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert initial_login.status_code == 200, "Initial login should succeed"
    
    # Step 2: Deactivate the user directly in the database
    await test_db.user.update(
        where={"email": user_data["email"]},
        data={"isActive": False}
    )
    
    # Step 3: Attempt to log in with inactive account
    inactive_login = test_client.post(
        "/auth/login",
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    
    # Verify login is rejected for inactive user
    assert inactive_login.status_code == 400, "Inactive user login should return 400 Bad Request"
    error_response = inactive_login.json()
    assert "detail" in error_response
    assert "inactive" in error_response["detail"].lower(), "Error should mention inactive user"


@pytest.mark.asyncio
async def test_profile_access_with_expired_token(test_client, test_db: Prisma):
    """
    Test that expired tokens are rejected for profile access.
    
    This integration test verifies that:
    1. A token with very short expiration can be created
    2. After expiration, the token is rejected
    3. User must log in again to get a new token
    
    Validates: Requirements 9.5 (expired token rejection)
    """
    from datetime import timedelta
    from services.auth import create_access_token, get_password_hash
    
    # Create a user directly in the database
    user_email = "expiry_test@example.com"
    hashed_password = get_password_hash("TestPassword123!")
    
    user = await test_db.user.create(
        data={
            "email": user_email,
            "hashedPassword": hashed_password,
            "isActive": True
        }
    )
    
    # Create a token that expires immediately (negative expiration)
    expired_token = create_access_token(
        data={"sub": user_email},
        expires_delta=timedelta(seconds=-1)  # Already expired
    )
    
    # Attempt to access profile with expired token
    profile_response = test_client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {expired_token}"}
    )
    
    # Verify expired token is rejected
    assert profile_response.status_code == 401, "Expired token should be rejected"
    error_response = profile_response.json()
    assert "detail" in error_response
    
    # Verify user can log in again to get a new valid token
    login_response = test_client.post(
        "/auth/login",
        data={
            "username": user_email,
            "password": "TestPassword123!"
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    
    assert login_response.status_code == 200, "User should be able to log in again"
    new_token = login_response.json()["access_token"]
    
    # Verify new token works for profile access
    new_profile_response = test_client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {new_token}"}
    )
    
    assert new_profile_response.status_code == 200, "New token should work"
    assert new_profile_response.json()["email"] == user_email


@pytest.mark.asyncio
async def test_email_validation_in_signup(test_client):
    """
    Test that invalid email formats are rejected during signup.
    
    This integration test verifies that:
    1. Invalid email formats return 422 Validation Error
    2. Error response includes field-level details
    3. Valid email formats are accepted
    
    Validates: Requirements 5.5 (email format validation)
    """
    # Test various invalid email formats
    invalid_emails = [
        "notanemail",
        "missing@domain",
        "@nodomain.com",
        "spaces in@email.com",
        "double@@domain.com",
        ""
    ]
    
    for invalid_email in invalid_emails:
        signup_data = {
            "email": invalid_email,
            "password": "ValidPassword123!"
        }
        
        response = test_client.post("/auth/signup", json=signup_data)
        
        # Should return 422 for validation error
        assert response.status_code == 422, f"Invalid email '{invalid_email}' should return 422"
        error_response = response.json()
        assert "detail" in error_response, "Validation error should include detail"
    
    # Test that valid email format is accepted
    valid_signup = {
        "email": "valid.email@example.com",
        "password": "ValidPassword123!"
    }
    
    valid_response = test_client.post("/auth/signup", json=valid_signup)
    assert valid_response.status_code == 201, "Valid email should be accepted"


@pytest.mark.asyncio
async def test_api_documentation_endpoints(test_client):
    """
    Test that API documentation endpoints are accessible.
    
    This integration test verifies that:
    1. Swagger UI is accessible at /docs
    2. ReDoc is accessible at /redoc
    3. OpenAPI schema is accessible at /openapi.json
    
    Validates: Requirements 1.2, 13.1, 13.2
    """
    # Test Swagger UI
    docs_response = test_client.get("/docs")
    assert docs_response.status_code == 200, "Swagger UI should be accessible"
    
    # Test ReDoc
    redoc_response = test_client.get("/redoc")
    assert redoc_response.status_code == 200, "ReDoc should be accessible"
    
    # Test OpenAPI schema
    openapi_response = test_client.get("/openapi.json")
    assert openapi_response.status_code == 200, "OpenAPI schema should be accessible"
    openapi_json = openapi_response.json()
    assert "openapi" in openapi_json, "Response should be valid OpenAPI schema"
    assert "paths" in openapi_json, "Schema should include API paths"



@pytest.mark.asyncio
async def test_image_retrieval_endpoint(test_client, test_db: Prisma):
    """
    Test image retrieval endpoint functionality.
    
    This integration test verifies that:
    1. User can retrieve their own image
    2. User cannot retrieve another user's image (403)
    3. Non-existent image returns 404
    4. Unauthenticated request returns 401
    
    Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
    """
    from services.auth import get_password_hash
    from services.minio_client import MinIOClient
    from config import settings
    from routes.images import set_minio_client
    from services.image_utils import generate_hashed_user_id
    
    # Setup MinIO client for testing
    minio_client = MinIOClient(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket_name=settings.minio_bucket_name,
        secure=settings.minio_secure
    )
    minio_client.ensure_bucket_exists()
    set_minio_client(minio_client)
    
    # Create two test users
    user1_email = "image_owner@example.com"
    user2_email = "other_user@example.com"
    password = "TestPassword123!"
    hashed_password = get_password_hash(password)
    
    user1 = await test_db.user.create(
        data={
            "email": user1_email,
            "hashedPassword": hashed_password,
            "isActive": True
        }
    )
    
    user2 = await test_db.user.create(
        data={
            "email": user2_email,
            "hashedPassword": hashed_password,
            "isActive": True
        }
    )
    
    # Get tokens for both users
    login_response1 = test_client.post(
        "/auth/login",
        data={"username": user1_email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    token1 = login_response1.json()["access_token"]
    
    login_response2 = test_client.post(
        "/auth/login",
        data={"username": user2_email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    token2 = login_response2.json()["access_token"]
    
    # Manually create an image record and upload to MinIO for testing
    test_image_content = b"fake image content for testing"
    hashed_user_id = generate_hashed_user_id(user1.id)
    object_key = f"{hashed_user_id}/test_image_123456.jpg"
    
    # Upload directly to MinIO
    minio_client.upload_file(
        file_data=test_image_content,
        object_key=object_key,
        content_type="image/jpeg"
    )
    
    # Create image record in database
    image = await test_db.image.create(
        data={
            "userId": user1.id,
            "objectKey": object_key,
            "originalFilename": "test_image.jpg",
            "generatedFilename": "test_image_123456.jpg",
            "mimeType": "image/jpeg",
            "fileSize": len(test_image_content)
        }
    )
    
    # Test 1: User1 can retrieve their own image
    retrieve_response = test_client.get(
        f"/images/{image.id}",
        headers={"Authorization": f"Bearer {token1}"}
    )
    
    assert retrieve_response.status_code == 200, "Owner should be able to retrieve their image"
    assert retrieve_response.headers["content-type"] == "image/jpeg", "Content-Type should match"
    assert retrieve_response.content == test_image_content, "Image content should match"
    
    # Test 2: User2 cannot retrieve user1's image (403)
    forbidden_response = test_client.get(
        f"/images/{image.id}",
        headers={"Authorization": f"Bearer {token2}"}
    )
    
    assert forbidden_response.status_code == 403, "Other user should get 403 Forbidden"
    assert "permission" in forbidden_response.json()["detail"].lower()
    
    # Test 3: Non-existent image returns 404
    not_found_response = test_client.get(
        f"/images/99999",
        headers={"Authorization": f"Bearer {token1}"}
    )
    
    assert not_found_response.status_code == 404, "Non-existent image should return 404"
    assert "not found" in not_found_response.json()["detail"].lower()
    
    # Test 4: Unauthenticated request returns 401
    unauth_response = test_client.get(f"/images/{image.id}")
    assert unauth_response.status_code == 401, "Unauthenticated request should return 401"
    
    # Cleanup: Delete the uploaded image from MinIO
    try:
        minio_client.delete_file(object_key)
    except Exception:
        pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_image_listing_endpoint(test_client, test_db: Prisma):
    """
    Test image listing endpoint functionality.
    
    This integration test verifies that:
    1. User can list their own images
    2. Empty list is returned for users with no images
    3. Images are ordered by upload timestamp (newest first)
    4. All metadata fields are included in the response
    5. Unauthenticated request returns 401
    
    Validates: Requirements 7.1, 7.2, 7.3, 7.4
    """
    from services.auth import get_password_hash
    from services.minio_client import MinIOClient
    from config import settings
    from routes.images import set_minio_client
    from services.image_utils import generate_hashed_user_id
    import time
    
    # Setup MinIO client for testing
    minio_client = MinIOClient(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket_name=settings.minio_bucket_name,
        secure=settings.minio_secure
    )
    minio_client.ensure_bucket_exists()
    set_minio_client(minio_client)
    
    # Create two test users
    user1_email = "list_test_user1@example.com"
    user2_email = "list_test_user2@example.com"
    password = "TestPassword123!"
    hashed_password = get_password_hash(password)
    
    user1 = await test_db.user.create(
        data={
            "email": user1_email,
            "hashedPassword": hashed_password,
            "isActive": True
        }
    )
    
    user2 = await test_db.user.create(
        data={
            "email": user2_email,
            "hashedPassword": hashed_password,
            "isActive": True
        }
    )
    
    # Get tokens for both users
    login_response1 = test_client.post(
        "/auth/login",
        data={"username": user1_email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    token1 = login_response1.json()["access_token"]
    
    login_response2 = test_client.post(
        "/auth/login",
        data={"username": user2_email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    token2 = login_response2.json()["access_token"]
    
    # Test 1: Empty list for user with no images
    empty_list_response = test_client.get(
        "/images/",
        headers={"Authorization": f"Bearer {token2}"}
    )
    
    assert empty_list_response.status_code == 200, "Should return 200 for empty list"
    empty_list_data = empty_list_response.json()
    assert empty_list_data["images"] == [], "Should return empty array"
    assert empty_list_data["total"] == 0, "Total should be 0"
    
    # Create multiple images for user1
    hashed_user_id = generate_hashed_user_id(user1.id)
    uploaded_images = []
    
    for i in range(3):
        test_image_content = f"fake image content {i}".encode()
        object_key = f"{hashed_user_id}/test_image_{i}_{int(time.time() * 1000)}.jpg"
        
        # Upload to MinIO
        minio_client.upload_file(
            file_data=test_image_content,
            object_key=object_key,
            content_type="image/jpeg"
        )
        
        # Create image record in database
        image = await test_db.image.create(
            data={
                "userId": user1.id,
                "objectKey": object_key,
                "originalFilename": f"test_image_{i}.jpg",
                "generatedFilename": f"test_image_{i}_{int(time.time() * 1000)}.jpg",
                "mimeType": "image/jpeg",
                "fileSize": len(test_image_content)
            }
        )
        uploaded_images.append(image)
        time.sleep(0.01)  # Small delay to ensure different timestamps
    
    # Test 2: User1 can list their images
    list_response = test_client.get(
        "/images/",
        headers={"Authorization": f"Bearer {token1}"}
    )
    
    assert list_response.status_code == 200, "Should return 200 for image list"
    list_data = list_response.json()
    
    # Verify response structure
    assert "images" in list_data, "Response should include 'images' field"
    assert "total" in list_data, "Response should include 'total' field"
    assert list_data["total"] == 3, "Total should be 3"
    assert len(list_data["images"]) == 3, "Should return 3 images"
    
    # Test 3: Verify all metadata fields are included
    for image_data in list_data["images"]:
        assert "id" in image_data, "Should include id"
        assert "object_key" in image_data, "Should include object_key"
        assert "original_filename" in image_data, "Should include original_filename"
        assert "generated_filename" in image_data, "Should include generated_filename"
        assert "mime_type" in image_data, "Should include mime_type"
        assert "file_size" in image_data, "Should include file_size"
        assert "uploaded_at" in image_data, "Should include uploaded_at"
    
    # Test 4: Verify images are ordered by uploadedAt descending (newest first)
    timestamps = [img["uploaded_at"] for img in list_data["images"]]
    assert timestamps == sorted(timestamps, reverse=True), "Images should be ordered by uploadedAt descending"
    
    # Test 5: User2 still has empty list (isolation)
    user2_list_response = test_client.get(
        "/images/",
        headers={"Authorization": f"Bearer {token2}"}
    )
    
    assert user2_list_response.status_code == 200
    user2_list_data = user2_list_response.json()
    assert user2_list_data["total"] == 0, "User2 should have no images"
    assert len(user2_list_data["images"]) == 0, "User2 should have empty list"
    
    # Test 6: Unauthenticated request returns 401
    unauth_response = test_client.get("/images/")
    assert unauth_response.status_code == 401, "Unauthenticated request should return 401"
    
    # Cleanup: Delete uploaded images from MinIO
    for image in uploaded_images:
        try:
            minio_client.delete_file(image.objectKey)
        except Exception:
            pass  # Ignore cleanup errors
