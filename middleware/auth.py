"""
Authentication middleware for FastAPI.

This module provides JWT authentication dependencies for protecting
routes and retrieving the current authenticated user.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from prisma import Prisma
from services.auth import decode_access_token
from services.database import get_prisma
from schemas.user import UserOut


# OAuth2 scheme for token extraction from Authorization header
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    prisma: Prisma = Depends(get_prisma)
) -> UserOut:
    """
    Dependency to get the current authenticated user from JWT token.
    
    Extracts the JWT token from the Authorization header, validates it,
    and retrieves the corresponding user from the database.
    
    Args:
        token: JWT token extracted from Authorization header
        prisma: Prisma client instance for database queries
        
    Returns:
        UserOut: The authenticated user's data
        
    Raises:
        HTTPException: 401 Unauthorized if token is invalid or user not found
        
    Usage:
        @app.get("/protected")
        async def protected_route(user: UserOut = Depends(get_current_user)):
            return {"user": user}
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Decode and validate the token
    token_data = decode_access_token(token)
    if token_data is None or token_data.email is None:
        raise credentials_exception
    
    # Query user from database
    user = await prisma.user.find_unique(where={"email": token_data.email})
    if user is None:
        raise credentials_exception
    
    # Convert Prisma model to Pydantic schema
    return UserOut.model_validate(user)


async def get_current_active_user(
    current_user: UserOut = Depends(get_current_user)
) -> UserOut:
    """
    Dependency to ensure the current user is active.
    
    Builds on get_current_user to add an additional check that the
    user account is active (not disabled).
    
    Args:
        current_user: The authenticated user from get_current_user
        
    Returns:
        UserOut: The authenticated and active user's data
        
    Raises:
        HTTPException: 400 Bad Request if user account is inactive
        
    Usage:
        @app.get("/protected")
        async def protected_route(user: UserOut = Depends(get_current_active_user)):
            return {"user": user}
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user
