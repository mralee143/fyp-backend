"""
Property-based tests for User model.

Feature: fastapi-prisma-migration
Property 15: Auto-Incrementing User IDs
Validates: Requirements 4.1

Property 6: Duplicate Email Registration Rejection
Validates: Requirements 5.4, 4.2
"""

import pytest
import uuid
from hypothesis import given, strategies as st, settings, HealthCheck
from prisma import Prisma
from prisma.errors import UniqueViolationError


# Feature: fastapi-prisma-migration, Property 15: Auto-Incrementing User IDs
@given(
    num_users=st.integers(min_value=2, max_value=10)
)
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None  # Disable deadline for async database operations
)
@pytest.mark.asyncio
async def test_auto_incrementing_user_ids(num_users, test_db: Prisma):
    """
    For any sequence of user registrations, each new user should receive an ID
    that is greater than all previously assigned IDs.
    
    Validates: Requirements 4.1
    """
    created_ids = []
    
    # Generate a unique prefix for this test run to avoid email collisions
    test_run_id = uuid.uuid4().hex[:8]
    
    # Create multiple users sequentially
    for i in range(num_users):
        user = await test_db.user.create(
            data={
                "email": f"test_{test_run_id}_{i}@example.com",
                "hashedPassword": f"hashed_password_{i}",
                "isActive": True
            }
        )
        created_ids.append(user.id)
    
    # Verify that IDs are strictly increasing
    for i in range(1, len(created_ids)):
        assert created_ids[i] > created_ids[i-1], \
            f"User ID {created_ids[i]} should be greater than previous ID {created_ids[i-1]}"
    
    # Verify all IDs are unique
    assert len(created_ids) == len(set(created_ids)), \
        "All user IDs should be unique"
    
    # Verify all IDs are positive integers
    for user_id in created_ids:
        assert isinstance(user_id, int), f"User ID {user_id} should be an integer"
        assert user_id > 0, f"User ID {user_id} should be positive"



# Feature: fastapi-prisma-migration, Property 6: Duplicate Email Registration Rejection
@given(
    password1=st.text(min_size=8, max_size=50, alphabet=st.characters(blacklist_categories=('Cs',), blacklist_characters='\x00')),
    password2=st.text(min_size=8, max_size=50, alphabet=st.characters(blacklist_categories=('Cs',), blacklist_characters='\x00'))
)
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100,
    deadline=None  # Disable deadline for async database operations
)
@pytest.mark.asyncio
async def test_duplicate_email_registration_rejection(password1, password2, test_db: Prisma):
    """
    For any email address that already exists in the database, attempting to
    register a new user with that email should fail with a unique constraint violation.
    
    Validates: Requirements 5.4, 4.2
    """
    # Generate a unique email for this test run to avoid collisions between hypothesis examples
    test_run_id = uuid.uuid4().hex[:12]
    email = f"test_{test_run_id}@example.com"
    
    # Create the first user with the email
    first_user = await test_db.user.create(
        data={
            "email": email,
            "hashedPassword": password1,
            "isActive": True
        }
    )
    
    # Verify the first user was created successfully
    assert first_user is not None
    assert first_user.email == email
    
    # Attempt to create a second user with the same email should fail
    with pytest.raises(UniqueViolationError) as exc_info:
        await test_db.user.create(
            data={
                "email": email,
                "hashedPassword": password2,
                "isActive": True
            }
        )
    
    # Verify the error is related to the email unique constraint
    error_message = str(exc_info.value)
    assert "email" in error_message.lower() or "unique" in error_message.lower(), \
        "Error should indicate unique constraint violation on email field"
    
    # Verify only one user exists with this email
    users_with_email = await test_db.user.find_many(
        where={"email": email}
    )
    assert len(users_with_email) == 1, \
        f"Only one user should exist with email {email}, found {len(users_with_email)}"
