"""
Authentication-related Pydantic schemas.

This module defines schemas for JWT token responses and token data
used in the authentication flow.
"""

from pydantic import BaseModel


class Token(BaseModel):
    """
    Schema for JWT token response.
    
    Returned after successful login with access token and token type.
    """
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """
    Schema for decoded JWT token data.
    
    Contains the user identifier (email) extracted from the token payload.
    Used internally for token validation.
    """
    email: str | None = None
