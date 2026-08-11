"""
Property-based tests for authenticated profile access.

Feature: fastapi-prisma-migration
Property 11: Authenticated Profile Access
Validates: Requirements 6.1
"""

import pytest
import uuid
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import timedelta
from services.auth import create_access_token, get_password_hash
from middleware.auth import get_current_active_user, get_current_user
from services.database import set_prisma
from schemas.user import UserOut


# Feature: fastapi-prisma-migration, Property 11: Authenticated Profile Access
@given(
    password=st.text(min_size=8, max_size=50, alphabet=st.characters(blacklist_categories=('Cs',), blacklist_characters='\x00'))
)
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None
)
@pytest.mark.asyncio
async def test_authenticated_profile_access(password, test_db):
    """
    Property 11: Authenticated Profile Access
    Validates: Requirements 6.1
    
    For any authenticated user with a valid JWT token, requesting their profile
    should return their user information (id, email, isActive, timestamps).
    """
    # Set up the Prisma client for the middleware
    set_prisma(test_db)
    
    # Generate a unique email for this test run
    test_run_id = uuid.uuid4().hex[:12]
    email = f"test_{test_run_id}@example.com"
    
    # Create a test user
    hashed_password = get_password_hash(password)
    user = await test_db.user.create(
        data={
            "email": email,
            "hashedPassword": hashed_password,
            "isActive": True
        }
    )
    
    # Create a valid JWT token for the user
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=30)
    )
    
    # Use the middleware to get the current user with the valid token
    current_user = await get_current_user(token=access_token, prisma=test_db)
    
    # Verify the returned user data matches the created user
    assert current_user is not None, "Current user should not be None"
    assert isinstance(current_user, UserOut), "Current user should be a UserOut instance"
    assert current_user.id == user.id, f"User ID should match: expected {user.id}, got {current_user.id}"
    assert current_user.email == user.email, f"Email should match: expected {user.email}, got {current_user.email}"
    assert current_user.is_active == user.isActive, f"Active status should match: expected {user.isActive}, got {current_user.is_active}"
    
    # Verify timestamps are present
    assert current_user.created_at is not None, "Created timestamp should be present"
    assert current_user.updated_at is not None, "Updated timestamp should be present"
    
    # Verify the user can access protected endpoints (using get_current_active_user)
    active_user = await get_current_active_user(current_user=current_user)
    assert active_user is not None, "Active user should not be None"
    assert active_user.id == user.id, "Active user ID should match"
    assert active_user.email == user.email, "Active user email should match"


# Feature: fastapi-prisma-migration, Property 11: Authenticated Profile Access (Inactive User)
@given(
    password=st.text(min_size=8, max_size=50, alphabet=st.characters(blacklist_categories=('Cs',), blacklist_characters='\x00'))
)
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None
)
@pytest.mark.asyncio
async def test_inactive_user_profile_access_rejection(password, test_db):
    """
    Property 11: Authenticated Profile Access (Inactive User Edge Case)
    Validates: Requirements 6.1
    
    For any authenticated user with a valid JWT token but inactive status,
    requesting their profile should be rejected.
    """
    # Set up the Prisma client for the middleware
    set_prisma(test_db)
    
    # Generate a unique email for this test run
    test_run_id = uuid.uuid4().hex[:12]
    email = f"test_{test_run_id}@example.com"
    
    # Create an inactive test user
    hashed_password = get_password_hash(password)
    user = await test_db.user.create(
        data={
            "email": email,
            "hashedPassword": hashed_password,
            "isActive": False  # Inactive user
        }
    )
    
    # Create a valid JWT token for the user
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=30)
    )
    
    # Use the middleware to get the current user with the valid token
    current_user = await get_current_user(token=access_token, prisma=test_db)
    
    # Verify the user data is retrieved (authentication succeeded)
    assert current_user is not None
    assert current_user.email == user.email
    assert current_user.is_active == False
    
    # Attempting to access protected endpoints should fail for inactive users
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await get_current_active_user(current_user=current_user)
    
    # Verify it's a 400 Bad Request error for inactive user
    assert exc_info.value.status_code == 400
    assert "Inactive user" in exc_info.value.detail
