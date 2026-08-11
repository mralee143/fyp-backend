"""
Property-based tests for sensitive data exclusion from API responses.

Feature: fastapi-prisma-migration
Property 13: Sensitive Data Exclusion from Responses
Validates: Requirements 6.3
"""

import pytest
import uuid
from hypothesis import given, strategies as st, settings, HealthCheck
from services.auth import get_password_hash
from schemas.user import UserOut


# Shared strategy for password generation
PASSWORD_STRATEGY = st.text(
    min_size=8, 
    max_size=72, 
    alphabet=st.characters(
        blacklist_categories=('Cs',), 
        blacklist_characters='\x00'
    )
)


def assert_no_sensitive_data(user_response, hashed_password, plain_password, context=""):
    """
    Verify that a UserOut response contains no sensitive data.
    
    Args:
        user_response: UserOut instance to check
        hashed_password: The hashed password that should not appear
        plain_password: The plain password that should not appear
        context: Optional context string for error messages (e.g., "User 0")
    """
    prefix = f"{context}: " if context else ""
    
    # Check attributes don't exist
    assert not hasattr(user_response, 'hashed_password'), \
        f"{prefix}Response should not have hashed_password attribute"
    assert not hasattr(user_response, 'hashedPassword'), \
        f"{prefix}Response should not have hashedPassword attribute"
    
    # Check serialized dict doesn't contain password fields
    response_dict = user_response.model_dump()
    assert 'hashed_password' not in response_dict, \
        f"{prefix}Serialized response should not contain hashed_password"
    assert 'hashedPassword' not in response_dict, \
        f"{prefix}Serialized response should not contain hashedPassword"
    assert 'password' not in response_dict, \
        f"{prefix}Serialized response should not contain password"
    
    # Check JSON string doesn't contain actual password values
    response_json = user_response.model_dump_json()
    assert hashed_password not in response_json, \
        f"{prefix}JSON should not contain password hash"
    assert plain_password not in response_json, \
        f"{prefix}JSON should not contain plain password"


# Feature: fastapi-prisma-migration, Property 13: Sensitive Data Exclusion from Responses
@given(password=PASSWORD_STRATEGY)
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None
)
@pytest.mark.asyncio
async def test_sensitive_data_exclusion_from_responses(password, test_db):
    """
    Property 13: Sensitive Data Exclusion from Responses
    Validates: Requirements 6.3
    
    For any API response containing user data, the response should never include
    the hashedPassword field or any other password-related data.
    
    This test verifies that when user data is converted to UserOut schema
    (which is used for all API responses), sensitive fields are excluded.
    """
    # Generate a unique email for this test run
    test_run_id = uuid.uuid4().hex[:12]
    email = f"test_{test_run_id}@example.com"
    
    # Create a test user with a hashed password
    hashed_password = get_password_hash(password)
    user = await test_db.user.create(
        data={
            "email": email,
            "hashedPassword": hashed_password,
            "isActive": True
        }
    )
    
    # Convert the database user to UserOut schema (simulating API response)
    user_response = UserOut.model_validate(user)
    
    # Verify that the response contains expected fields
    assert user_response.id == user.id, "Response should include user ID"
    assert user_response.email == user.email, "Response should include email"
    assert user_response.is_active == user.isActive, "Response should include active status"
    assert user_response.created_at is not None, "Response should include created timestamp"
    assert user_response.updated_at is not None, "Response should include updated timestamp"
    
    # Verify that sensitive data is NOT included in the response
    assert_no_sensitive_data(user_response, hashed_password, password)


# Feature: fastapi-prisma-migration, Property 13: Sensitive Data Exclusion (Multiple Users)
@given(passwords=st.lists(PASSWORD_STRATEGY, min_size=1, max_size=5))
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None
)
@pytest.mark.asyncio
async def test_sensitive_data_exclusion_multiple_users(passwords, test_db):
    """
    Property 13: Sensitive Data Exclusion from Responses (Multiple Users)
    Validates: Requirements 6.3
    
    For any collection of user responses, none should contain sensitive data.
    This tests that the exclusion property holds across multiple users.
    """
    # Create multiple users with different passwords
    users_data = []
    for i, password in enumerate(passwords):
        test_run_id = uuid.uuid4().hex[:12]
        email = f"test_{test_run_id}_{i}@example.com"
        hashed_password = get_password_hash(password)
        
        user = await test_db.user.create(
            data={
                "email": email,
                "hashedPassword": hashed_password,
                "isActive": True
            }
        )
        users_data.append((user, password, hashed_password))
    
    # Verify that none of the responses contain sensitive data
    for i, (user, plain_password, hashed_password) in enumerate(users_data):
        user_response = UserOut.model_validate(user)
        
        # Verify expected fields are present
        assert user_response.id == user.id, f"User {i}: Response should include user ID"
        assert user_response.email == user.email, f"User {i}: Response should include email"
        
        # Verify sensitive data is excluded
        assert_no_sensitive_data(user_response, hashed_password, plain_password, f"User {i}")
