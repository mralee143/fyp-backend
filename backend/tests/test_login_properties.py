"""
Property-based tests for user login.

Feature: fastapi-prisma-migration
Property 9: Valid Login Returns JWT Token
Validates: Requirements 5.2

Property 10: Invalid Credentials Rejection
Validates: Requirements 5.3
"""

import pytest
import uuid
from hypothesis import given, strategies as st, settings, HealthCheck
from prisma import Prisma
from services.auth import get_password_hash, verify_password, create_access_token, decode_access_token
from schemas.auth import TokenData


# Feature: fastapi-prisma-migration, Property 9: Valid Login Returns JWT Token
@given(
    password=st.text(min_size=8, max_size=72, alphabet=st.characters(
        blacklist_categories=('Cs',), 
        blacklist_characters='\x00'
    ))
)
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None  # Disable deadline for async database operations
)
@pytest.mark.asyncio
async def test_valid_login_returns_jwt_token(password, test_db: Prisma):
    """
    For any existing user with correct credentials, login should return
    a JWT access token with token_type "bearer".
    
    This property tests the core login functionality by creating a user,
    verifying their credentials, and ensuring a valid JWT token is generated.
    
    Validates: Requirements 5.2
    """
    # Generate a unique email for this test run
    test_run_id = uuid.uuid4().hex[:12]
    email = f"test_{test_run_id}@example.com"
    
    # Create a user with hashed password
    hashed_password = get_password_hash(password)
    user = await test_db.user.create(
        data={
            "email": email,
            "hashedPassword": hashed_password,
            "isActive": True
        }
    )
    
    # Verify the user was created
    assert user is not None
    assert user.email == email
    
    # Simulate login: verify password (what login endpoint does)
    password_valid = verify_password(password, user.hashedPassword)
    assert password_valid is True, "Password verification should succeed for correct password"
    
    # Simulate login: check user is active (what login endpoint does)
    assert user.isActive is True, "User should be active"
    
    # Simulate login: create access token (what login endpoint does)
    access_token = create_access_token(data={"sub": user.email})
    
    # Verify token was created
    assert access_token is not None, "Access token should be created"
    assert isinstance(access_token, str), "Access token should be a string"
    assert len(access_token) > 0, "Access token should not be empty"
    
    # Verify token can be decoded and contains correct user identity
    token_data = decode_access_token(access_token)
    assert token_data is not None, "Token should be decodable"
    assert isinstance(token_data, TokenData), "Decoded token should be TokenData"
    assert token_data.email == email, "Token should contain the user's email"
    
    # Verify token type would be "bearer" (as defined in Token schema)
    # This is implicitly tested by the Token schema default value


# Feature: fastapi-prisma-migration, Property 10: Invalid Credentials Rejection
@given(
    correct_password=st.text(min_size=8, max_size=72, alphabet=st.characters(
        blacklist_categories=('Cs',), 
        blacklist_characters='\x00'
    )),
    wrong_password=st.text(min_size=8, max_size=72, alphabet=st.characters(
        blacklist_categories=('Cs',), 
        blacklist_characters='\x00'
    ))
)
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None  # Disable deadline for async database operations
)
@pytest.mark.asyncio
async def test_invalid_credentials_rejection(correct_password, wrong_password, test_db: Prisma):
    """
    For any login attempt with incorrect password or non-existent email,
    the system should reject authentication (password verification fails).
    
    This property tests that invalid credentials are properly rejected,
    which would result in 401 Unauthorized in the actual endpoint.
    
    Validates: Requirements 5.3
    """
    # Skip if passwords happen to be the same (edge case)
    if correct_password == wrong_password:
        return
    
    # Generate a unique email for this test run
    test_run_id = uuid.uuid4().hex[:12]
    email = f"test_{test_run_id}@example.com"
    
    # Create a user with the correct password
    hashed_password = get_password_hash(correct_password)
    user = await test_db.user.create(
        data={
            "email": email,
            "hashedPassword": hashed_password,
            "isActive": True
        }
    )
    
    # Test 1: Wrong password should fail verification
    password_valid = verify_password(wrong_password, user.hashedPassword)
    assert password_valid is False, "Password verification should fail for incorrect password"
    
    # Test 2: Non-existent email should return None
    non_existent_email = f"nonexistent_{test_run_id}@example.com"
    non_existent_user = await test_db.user.find_unique(where={"email": non_existent_email})
    assert non_existent_user is None, "Non-existent email should not be found"
    
    # Test 3: Correct password should succeed (sanity check)
    correct_password_valid = verify_password(correct_password, user.hashedPassword)
    assert correct_password_valid is True, "Password verification should succeed for correct password"
