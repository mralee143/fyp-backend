"""
Property-based tests for hash function idempotence.

Feature: image-upload-minio, Property 8: Hash Function Idempotence
Validates: Requirements 4.4
"""

import pytest
from hypothesis import given, strategies as st, settings

from services.image_utils import generate_hashed_user_id


@given(user_id=st.integers(min_value=1, max_value=2**31 - 1))
@settings(max_examples=100)
@pytest.mark.property_test
def test_hash_function_idempotence(user_id):
    """
    Property 8: Hash Function Idempotence
    
    For any user ID, hashing it multiple times should always produce
    the same hashed user folder name.
    
    Validates: Requirements 4.4
    """
    # Hash the same user ID multiple times
    hash1 = generate_hashed_user_id(user_id)
    hash2 = generate_hashed_user_id(user_id)
    hash3 = generate_hashed_user_id(user_id)
    
    # All hashes should be identical
    assert hash1 == hash2 == hash3, \
        f"Hash function not idempotent for user_id={user_id}: {hash1}, {hash2}, {hash3}"
    
    # Verify format: "user_" + 8 hex characters
    assert hash1.startswith("user_"), \
        f"Hash should start with 'user_', got: {hash1}"
    assert len(hash1) == 13, \
        f"Hash should be 13 characters ('user_' + 8 hex), got length {len(hash1)}: {hash1}"
    
    # Verify hex characters
    hex_part = hash1[5:]  # Skip "user_" prefix
    assert all(c in '0123456789abcdef' for c in hex_part), \
        f"Hash should contain only hex characters, got: {hex_part}"


@given(
    user_id1=st.integers(min_value=1, max_value=2**31 - 1),
    user_id2=st.integers(min_value=1, max_value=2**31 - 1)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_different_users_different_hashes(user_id1, user_id2):
    """
    Property: Different user IDs should produce different hashes.
    
    This ensures proper user isolation in folder structure.
    
    Validates: Requirements 4.1, 4.3
    """
    if user_id1 == user_id2:
        # Same user should have same hash (covered by idempotence test)
        return
    
    hash1 = generate_hashed_user_id(user_id1)
    hash2 = generate_hashed_user_id(user_id2)
    
    # Different users should have different hashes
    # Note: Hash collisions are theoretically possible but extremely rare with MD5
    # For practical purposes with reasonable user ID ranges, this should hold
    assert hash1 != hash2, \
        f"Different user IDs should produce different hashes: " \
        f"user_id1={user_id1} -> {hash1}, user_id2={user_id2} -> {hash2}"
