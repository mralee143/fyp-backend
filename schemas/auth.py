"""
Authentication-related Pydantic schemas.

This module defines schemas for JWT token responses and token data
used in the authentication flow.
"""

from pydantic import BaseModel, EmailStr, Field


class SignupResponse(BaseModel):
    """Response after signup: account created, awaiting email verification."""
    email: EmailStr
    message: str = "Account created. Check your email for a verification code."
    verification_required: bool = True


class OtpVerify(BaseModel):
    """Payload to verify an email OTP."""
    email: EmailStr
    code: str = Field(min_length=4, max_length=8)


class OtpResend(BaseModel):
    """Payload to resend an email OTP."""
    email: EmailStr


class MessageResponse(BaseModel):
    """Generic message response."""
    message: str


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
