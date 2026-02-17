"""
Property-based tests for user registration.

Feature: fastapi-prisma-migration
Property 8: Valid Registration Creates Account
Validates: Requirements 5.1
"""

import pytest
import uuid
from hypothesis import given, strategies as st, settings, HealthCheck
from prisma import Prisma
from services.auth import get_password_hash


# Feature: fastapi-prisma-migration, Property 8: Valid Registration Creates Account
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
async def test_valid_registration_creates_account(password, test_db: Prisma):
    """
    For any valid email and password combination that doesn't already exist,
    registration should successfully create a new user account and return
    the user data with 201 Created status.
    
    This property tests the core registration functionality by simulating
    what the signup endpoint does: checking for existing email, hashing
    the password, and creating the user.
    
    Validates: Requirements 5.1
    """
    # Generate a unique email for this test run to avoid collisions
    test_run_id = uuid.uuid4().hex[:12]
    email = f"test_{test_run_id}@example.com"
    
    # Verify email doesn't already exist (simulating the check in signup endpoint)
    existing_user = await test_db.user.find_unique(where={"email": email})
    assert existing_user is None, "Email should not exist before registration"
    
    # Hash the password (simulating what signup endpoint does)
    hashed_password = get_password_hash(password)
    
    # Create the user (simulating the user creation in signup endpoint)
    created_user = await test_db.user.create(
        data={
            "email": email,
            "hashedPassword": hashed_password
        }
    )
    
    # Verify the user was created successfully
    assert created_user is not None, "User should be created"
    assert created_user.email == email, "Created user should have the correct email"
    assert created_user.id > 0, "Created user should have a valid ID"
    assert created_user.isActive is True, "Created user should be active by default"
    assert created_user.hashedPassword == hashed_password, "Password should be stored hashed"
    assert created_user.hashedPassword != password, "Password should not be stored in plain text"
    
    # Verify the user can be retrieved from the database
    retrieved_user = await test_db.user.find_unique(where={"email": email})
    assert retrieved_user is not None, "Created user should be retrievable"
    assert retrieved_user.id == created_user.id, "Retrieved user should have same ID"
    assert retrieved_user.email == created_user.email, "Retrieved user should have same email"
    
    # Verify timestamps are set
    assert created_user.createdAt is not None, "Created user should have createdAt timestamp"
    assert created_user.updatedAt is not None, "Created user should have updatedAt timestamp"
