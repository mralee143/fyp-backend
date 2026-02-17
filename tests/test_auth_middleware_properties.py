"""
Property-based tests for authentication middleware.

Feature: fastapi-prisma-migration
Tests authentication middleware behavior with property-based testing.
"""

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from fastapi import HTTPException
from middleware.auth import get_current_user
from services.database import set_prisma
from services.auth import decode_access_token


# Feature: fastapi-prisma-migration, Property 12: Unauthenticated Request Rejection
@given(
    invalid_token=st.one_of(
        st.text(min_size=1, max_size=100).filter(lambda x: decode_access_token(x) is None),
        st.just(""),
        st.just("invalid.token.here"),
        st.just("Bearer invalid"),
    )
)
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    max_examples=100
)
@pytest.mark.asyncio
async def test_unauthenticated_request_rejection(invalid_token, test_db):
    """
    Property 12: Unauthenticated Request Rejection
    Validates: Requirements 6.2
    
    For any invalid or missing JWT token, requests to protected endpoints
    should be rejected with 401 Unauthorized status.
    """
    # Set up the Prisma client for the middleware
    set_prisma(test_db)
    
    # Attempt to get current user with invalid token should raise HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token=invalid_token, prisma=test_db)
    
    # Verify it's a 401 Unauthorized error
    assert exc_info.value.status_code == 401
    assert "Could not validate credentials" in exc_info.value.detail
