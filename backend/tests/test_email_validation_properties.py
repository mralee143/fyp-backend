"""
Property-based tests for email format validation.

Feature: fastapi-prisma-migration
Property 7: Email Format Validation
Validates: Requirements 5.5
"""

import pytest
from hypothesis import given, strategies as st, settings, assume
from pydantic import ValidationError
from schemas.user import UserCreate, UserLogin


# Strategy for generating invalid email strings
# These are strings that should NOT be valid emails
invalid_emails = st.one_of(
    st.text(min_size=1, max_size=50).filter(lambda x: '@' not in x),  # No @ symbol
    st.text(min_size=1, max_size=50).filter(lambda x: x.count('@') > 1),  # Multiple @ symbols
    st.just(""),  # Empty string
    st.just("@"),  # Just @ symbol
    st.just("@example.com"),  # Missing local part
    st.just("user@"),  # Missing domain
    st.just("user"),  # No @ or domain
    st.just("user@.com"),  # Domain starts with dot
    st.just("user@domain"),  # No TLD
    st.just("user name@example.com"),  # Space in local part
)


# Feature: fastapi-prisma-migration, Property 7: Email Format Validation
@given(
    invalid_email=invalid_emails,
    password=st.text(min_size=1, max_size=100)
)
@settings(max_examples=100)
def test_invalid_email_format_rejected_in_user_create(invalid_email, password):
    """
    For any string that does not match valid email format (missing @, invalid domain, etc.),
    registration attempts should fail with validation error including field-level details.
    
    Validates: Requirements 5.5
    """
    # Filter out any strings that might accidentally be valid emails
    assume('@' not in invalid_email or 
           invalid_email.count('@') != 1 or
           '.' not in invalid_email.split('@')[-1] if '@' in invalid_email else True)
    
    # Attempt to create UserCreate schema with invalid email should fail
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(email=invalid_email, password=password)
    
    # Verify the error is related to email validation
    error_dict = exc_info.value.errors()
    assert len(error_dict) > 0, "Should have at least one validation error"
    
    # Check that the error is for the email field
    email_errors = [err for err in error_dict if 'email' in str(err.get('loc', []))]
    assert len(email_errors) > 0, \
        f"Should have email validation error, got errors: {error_dict}"


@given(
    invalid_email=invalid_emails,
    password=st.text(min_size=1, max_size=100)
)
@settings(max_examples=100)
def test_invalid_email_format_rejected_in_user_login(invalid_email, password):
    """
    For any string that does not match valid email format,
    login attempts should fail with validation error including field-level details.
    
    Validates: Requirements 5.5
    """
    # Filter out any strings that might accidentally be valid emails
    assume('@' not in invalid_email or 
           invalid_email.count('@') != 1 or
           '.' not in invalid_email.split('@')[-1] if '@' in invalid_email else True)
    
    # Attempt to create UserLogin schema with invalid email should fail
    with pytest.raises(ValidationError) as exc_info:
        UserLogin(email=invalid_email, password=password)
    
    # Verify the error is related to email validation
    error_dict = exc_info.value.errors()
    assert len(error_dict) > 0, "Should have at least one validation error"
    
    # Check that the error is for the email field
    email_errors = [err for err in error_dict if 'email' in str(err.get('loc', []))]
    assert len(email_errors) > 0, \
        f"Should have email validation error, got errors: {error_dict}"


def test_valid_email_formats_accepted():
    """
    Unit test to verify that valid email formats are accepted.
    This complements the property test by checking the happy path.
    """
    valid_emails = [
        "user@example.com",
        "test.user@example.com",
        "user+tag@example.co.uk",
        "user123@test-domain.com",
        "a@b.co",
    ]
    
    for email in valid_emails:
        # Should not raise any exception
        user_create = UserCreate(email=email, password="password123")
        assert user_create.email == email
        
        user_login = UserLogin(email=email, password="password123")
        assert user_login.email == email


def test_invalid_email_formats_rejected():
    """
    Unit test to verify that specific invalid email formats are rejected.
    """
    invalid_emails = [
        "",  # Empty
        "notanemail",  # No @ symbol
        "@example.com",  # Missing local part
        "user@",  # Missing domain
        "user@@example.com",  # Double @
        "user@domain",  # No TLD
        "user name@example.com",  # Space in email
    ]
    
    for email in invalid_emails:
        with pytest.raises(ValidationError):
            UserCreate(email=email, password="password123")
        
        with pytest.raises(ValidationError):
            UserLogin(email=email, password="password123")
