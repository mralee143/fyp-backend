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
from schemas.auth import (
    Token,
    SignupResponse,
    OtpVerify,
    OtpResend,
    MessageResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from services.auth import verify_password, get_password_hash, create_access_token
from services.database import get_prisma
from services.otp import generate_otp, verify_otp
from services.email import send_otp_email, send_reset_email
from middleware.auth import get_current_user
from config import settings


# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter()


def _send_verification(email: str) -> None:
    """
    Generate an OTP for the email and send it (or log it if SMTP is off).

    Raises a clean 502 if email delivery fails (e.g. SMTP/DNS/network error)
    instead of leaking a 500 stack trace to the client.
    """
    code = generate_otp(email)
    try:
        send_otp_email(email, code)
    except Exception as e:
        logger.error(f"Failed to send verification email to {email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not send the verification email right now. Please try again in a moment.",
        )


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    user_data: UserCreate,
    prisma: Prisma = Depends(get_prisma)
):
    """
    Register a new user account (email verification required).

    Creates the user as INACTIVE and emails a one-time verification code.
    The account is activated once the code is confirmed via /auth/verify-otp;
    login rejects inactive (unverified) users.

    If the email already exists but is unverified, a new code is sent instead
    of returning a conflict. A verified account returns 409.

    Args:
        user_data: User registration data (email and password)
        prisma: Prisma client instance for database operations

    Returns:
        SignupResponse: confirmation that a verification code was sent

    Raises:
        HTTPException: 409 Conflict if email already verified
        HTTPException: 500 Internal Server Error if database operation fails
    """
    try:
        # Check if user already exists
        existing_user = await prisma.user.find_unique(where={"email": user_data.email})
        if existing_user:
            if existing_user.isActive:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already registered"
                )
            # Exists but unverified: refresh password + resend code
            await prisma.user.update(
                where={"email": user_data.email},
                data={"hashedPassword": get_password_hash(user_data.password)},
            )
            _send_verification(user_data.email)
            return SignupResponse(email=user_data.email)

        # Hash password and create the user as inactive (pending verification)
        hashed_password = get_password_hash(user_data.password)
        await prisma.user.create(
            data={
                "email": user_data.email,
                "hashedPassword": hashed_password,
                "isActive": False,
            }
        )

        _send_verification(user_data.email)
        return SignupResponse(email=user_data.email)

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


@router.post("/verify-otp", response_model=Token)
async def verify_otp_route(
    payload: OtpVerify,
    prisma: Prisma = Depends(get_prisma)
):
    """
    Verify an email OTP, activate the account, and return a JWT (auto-login).

    A valid single-use OTP proves email ownership, so a token is issued so the
    frontend can go straight to the dashboard. An already-verified account can
    NOT obtain a token here (must log in normally) — this prevents bypassing
    the password.

    Raises 404 if no such user, 400 if already verified or the code is invalid.
    """
    user = await prisma.user.find_unique(where={"email": payload.email})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found for this email",
        )
    if user.isActive:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already verified. Please log in.",
        )

    ok, message = verify_otp(payload.email, payload.code)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    await prisma.user.update(
        where={"email": payload.email}, data={"isActive": True}
    )

    # Issue a token so the user lands straight on the dashboard.
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    return Token(access_token=access_token)


@router.post("/resend-otp", response_model=MessageResponse)
async def resend_otp_route(
    payload: OtpResend,
    prisma: Prisma = Depends(get_prisma)
):
    """Resend a verification code to an unverified account."""
    user = await prisma.user.find_unique(where={"email": payload.email})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found for this email",
        )
    if user.isActive:
        return MessageResponse(message="Email already verified. You can log in.")

    _send_verification(payload.email)
    return MessageResponse(message="A new verification code has been sent.")


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
        
        # Check if user is active (email must be verified via OTP)
        if not user.isActive:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email not verified. Please check your inbox for the verification code."
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


@router.get("/me")
async def me(current_user: UserOut = Depends(get_current_user)) -> dict:
    """Current user's identity + whether they're the admin (for the UI)."""
    return {
        "email": current_user.email,
        "is_admin": current_user.email.lower() == settings.admin_email.lower(),
    }


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    payload: ForgotPasswordRequest,
    prisma: Prisma = Depends(get_prisma),
):
    """Email a password-reset code (if the account exists)."""
    user = await prisma.user.find_unique(where={"email": payload.email})
    if user:
        code = generate_otp(payload.email)
        try:
            send_reset_email(payload.email, code)
        except Exception as e:
            logger.error(f"Failed to send reset email to {payload.email}: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not send the reset email. Please try again.",
            )
    # Same message whether or not the email exists (don't leak accounts).
    return MessageResponse(message="If that email exists, a reset code has been sent.")


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    payload: ResetPasswordRequest,
    prisma: Prisma = Depends(get_prisma),
):
    """Set a new password using the emailed reset code."""
    user = await prisma.user.find_unique(where={"email": payload.email})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found for this email",
        )
    ok, message = verify_otp(payload.email, payload.code)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    await prisma.user.update(
        where={"email": payload.email},
        data={
            "hashedPassword": get_password_hash(payload.new_password),
            "isActive": True,
        },
    )
    return MessageResponse(message="Password reset. You can now log in.")
