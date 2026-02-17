"""
Authentication service module.

This module provides password hashing, verification, and JWT token
generation and validation functionality.
"""

import bcrypt
from jose import JWTError, jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from config import settings
from schemas.auth import TokenData


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a hashed password.
    
    Args:
        plain_password: The plain text password to verify
        hashed_password: The bcrypt hashed password to check against
        
    Returns:
        True if the password matches, False otherwise
    """
    # Truncate to bcrypt's 72-byte limit (same as get_password_hash)
    password_bytes = plain_password.encode('utf-8')[:72]
    return bcrypt.checkpw(
        password_bytes,
        hashed_password.encode('utf-8') if isinstance(hashed_password, str) else hashed_password
    )


def get_password_hash(password: str) -> str:
    """
    Hash a plain password using bcrypt (72-byte limit applies).
    
    Args:
        password: The plain text password to hash
        
    Returns:
        The bcrypt hashed password as a string
    """
    password_bytes = password.encode('utf-8')[:72]  # Truncate to bcrypt's 72-byte limit
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode('utf-8')


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token with expiration.
    
    Args:
        data: Dictionary containing the data to encode in the token
        expires_delta: Optional custom expiration time delta
        
    Returns:
        Encoded JWT token string
    """
    to_encode = data.copy()
    
    expire = (
        datetime.now(timezone.utc) + expires_delta
        if expires_delta
        else datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    )
    
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> Optional[TokenData]:
    """
    Decode and validate a JWT token.
    
    Args:
        token: The JWT token string to decode
        
    Returns:
        TokenData object if valid, None if invalid or expired
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        email = payload.get("sub")
        return TokenData(email=email) if email else None
    except JWTError:
        return None
