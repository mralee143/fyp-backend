"""
User-related Pydantic schemas for request/response validation.

This module defines all schemas related to user data, including
registration, login, and user profile responses.
"""

from pydantic import BaseModel, EmailStr, ConfigDict, Field
from datetime import datetime


class UserBase(BaseModel):
    """
    Base user schema with common fields.
    
    Contains the email field that is shared across multiple user schemas.
    """
    email: EmailStr


class UserCreate(UserBase):
    """
    Schema for user registration requests.
    
    Extends UserBase with password field for account creation.
    """
    password: str


class UserLogin(BaseModel):
    """
    Schema for user login requests.
    
    Contains email and password for authentication.
    """
    email: EmailStr
    password: str


class UserOut(UserBase):
    """
    Schema for user responses (excludes sensitive data).
    
    Used for API responses that return user information.
    Excludes password-related fields for security.
    """
    id: int
    is_active: bool = Field(alias="isActive")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class UserInDB(UserOut):
    """
    Schema for user data as stored in database.
    
    Extends UserOut with hashed_password field.
    Used internally, never returned in API responses.
    """
    hashed_password: str
