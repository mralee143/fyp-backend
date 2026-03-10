"""
Authentication routes for user signup and login.

This module provides FastAPI endpoints for user registration and
authentication, including JWT token generation.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from prisma import Prisma
from prisma.errors import PrismaError
from datetime import timedelta
import logging

from schemas.user import UserCreate, UserOut
from schemas.auth import Token
from services.auth import verify_password, get_password_hash, create_access_token
from services.database import get_prisma
from config import settings


# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def signup(
    user_data: UserCreate,
    prisma: Prisma = Depends(get_prisma)
):
    """
    Register a new user account.
    
    Creates a new user with the provided email and password. The password
    is hashed before storage. Returns 409 if email already exists.
    
    Args:
        user_data: User registration data (email and password)
        prisma: Prisma client instance for database operations
        
    Returns:
        UserOut: The created user's data (excluding password)
        
    Raises:
        HTTPException: 409 Conflict if email already registered
        HTTPException: 500 Internal Server Error if database operation fails
    """
    try:
        
        print("user_data", user_data)
        # Check if user already exists
        existing_user = await prisma.user.find_unique(where={"email": user_data.email})
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered"
            )
        
        # Hash password and create user
        hashed_password = get_password_hash(user_data.password)
        user = await prisma.user.create(
            data={
                "email": user_data.email,
                "hashedPassword": hashed_password
            }
        )
        
        return UserOut.model_validate(user)
    
    except HTTPException:
        # Re-raise HTTP exceptions (like 409 Conflict)
        raise
    except PrismaError as e:
        # Log database errors and return 500
        logger.error(f"Database error during signup: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating the user account"
        )
    except Exception as e:
        # Log unexpected errors and return 500
        logger.error(f"Unexpected error during signup: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    prisma: Prisma = Depends(get_prisma)
):
    """
    Authenticate user and return JWT token.
    
    Accepts OAuth2 password flow credentials (username field contains email).
    Verifies credentials and returns a JWT access token if valid.
    
    Args:
        form_data: OAuth2 form with username (email) and password
        prisma: Prisma client instance for database operations
        
    Returns:
        Token: JWT access token and token type
        
    Raises:
        HTTPException: 401 Unauthorized if credentials invalid
        HTTPException: 400 Bad Request if user account is inactive
        HTTPException: 500 Internal Server Error if database operation fails
    """
    try:
        # Find user by email (username field in OAuth2 form)
        user = await prisma.user.find_unique(where={"email": form_data.username})
        
        # Verify user exists and password is correct
        if not user or not verify_password(form_data.password, user.hashedPassword):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Check if user is active
        if not user.isActive:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Inactive user"
            )
        
        # Create access token
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(
            data={"sub": user.email},
            expires_delta=access_token_expires
        )
        
        return Token(access_token=access_token)
    
    except HTTPException:
        # Re-raise HTTP exceptions (like 401, 400)
        raise
    except PrismaError as e:
        # Log database errors and return 500
        logger.error(f"Database error during login: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing login"
        )
    except Exception as e:
        # Log unexpected errors and return 500
        logger.error(f"Unexpected error during login: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
