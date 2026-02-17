"""
Property-based tests for password hashing functionality.

Feature: fastapi-prisma-migration
"""

import pytest
from hypothesis import given, strategies as st, settings
from services.auth import get_password_hash, verify_password


# Feature: fastapi-prisma-migration, Property 1: Password Hashing - No Plain Text Storage
@given(password=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=72))
@settings(max_examples=20, deadline=None)  # Reduced for bcrypt performance, deadline disabled due to bcrypt's intentional slowness
def test_password_never_stored_plain_text(password):
    """
    Property 1: Password Hashing - No Plain Text Storage
    
    For any password provided during user registration, the value stored 
    in the database should be a bcrypt hash that does not equal the 
    original plain-text password.
    
    Validates: Requirements 8.1
    """
    hashed = get_password_hash(password)
    
    # The hash should never equal the plain text password
    assert hashed != password
    
    # Bcrypt hashes are always longer than the input
    assert len(hashed) > len(password)
    
    # Bcrypt hashes start with $2b$ (bcrypt identifier)
    assert hashed.startswith("$2b$")



# Feature: fastapi-prisma-migration, Property 2: Password Verification Correctness
@given(password=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=72))
@settings(max_examples=20, deadline=None)  # Reduced for bcrypt performance, deadline disabled due to bcrypt's intentional slowness
def test_password_verification_correctness(password):
    """
    Property 2: Password Verification Correctness
    
    For any user account, verifying the correct password against the stored 
    hash should return true, and verifying any incorrect password should 
    return false.
    
    Validates: Requirements 8.2
    """
    # Hash the password
    hashed = get_password_hash(password)
    
    # Correct password should verify successfully
    assert verify_password(password, hashed) is True
    
    # Incorrect password should fail verification
    # Use a different password that's guaranteed to be different
    if password != "wrong_password_123":
        assert verify_password("wrong_password_123", hashed) is False
    else:
        assert verify_password("different_wrong_password", hashed) is False
