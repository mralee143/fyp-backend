"""
Image-related Pydantic schemas for request/response validation.

This module defines all schemas related to image upload, retrieval,
and listing operations.
"""

from pydantic import BaseModel, ConfigDict
from datetime import datetime


class ImageUploadResponse(BaseModel):
    """
    Schema for image upload response.
    
    Returned after successful image upload with complete metadata.
    Contains all information about the uploaded image including storage details.
    """
    id: int
    object_key: str
    original_filename: str
    generated_filename: str
    mime_type: str
    file_size: int
    uploaded_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ImageMetadata(BaseModel):
    """
    Schema for individual image metadata in listings.
    
    Contains all metadata fields for a single image.
    Used in image listing responses.
    """
    id: int
    object_key: str
    original_filename: str
    generated_filename: str
    mime_type: str
    file_size: int
    uploaded_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ImageListResponse(BaseModel):
    """
    Schema for image listing response.
    
    Returns a list of images with metadata and total count.
    Used for GET /images endpoint to list all user images.
    """
    images: list[ImageMetadata]
    total: int
