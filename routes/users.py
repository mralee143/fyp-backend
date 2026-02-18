"""
User management routes.

This module provides FastAPI endpoints for user profile management
and other user-related operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from prisma.errors import PrismaError
import logging

from schemas.user import UserOut
from middleware.auth import get_current_active_user


# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/me", response_model=UserOut)
async def get_current_user_profile(
    current_user: UserOut = Depends(get_current_active_user)
):
    """
    Get the current authenticated user's profile.
    
    Returns the profile information for the currently authenticated user.
    Requires a valid JWT token in the Authorization header.
    
    Args:
        current_user: The authenticated user from JWT token validation
        
    Returns:
        UserOut: The user's profile data (id, email, isActive, timestamps)
        
    Raises:
        HTTPException: 401 Unauthorized if token is invalid or missing
        HTTPException: 400 Bad Request if user account is inactive
        HTTPException: 500 Internal Server Error if an unexpected error occurs
        
    Usage:
        GET /users/me
        Headers: Authorization: Bearer <jwt_token>
    """
    try:
        return current_user
    except Exception as e:
        # Log unexpected errors and return 500
        logger.error(f"Unexpected error in get_current_user_profile: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
