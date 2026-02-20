"""
Image upload and management routes.

This module provides FastAPI endpoints for image upload, retrieval,
and listing operations with MinIO object storage integration.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import StreamingResponse
from prisma import Prisma
from prisma.errors import PrismaError
import logging
from typing import Optional
from io import BytesIO

from schemas.image import ImageUploadResponse, ImageListResponse, ImageMetadata
from schemas.user import UserOut
from middleware.auth import get_current_user
from services.database import get_prisma
from services.minio_client import MinIOClient, MinIOUploadError, MinIONotFoundError
from services.image_utils import (
    generate_hashed_user_id,
    generate_unique_filename,
    validate_file_extension,
    validate_mime_type,
    validate_file_size,
    MIME_TYPE_MAP
)
from config import settings


# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])


# Global MinIO client instance
_minio_client: Optional[MinIOClient] = None


def set_minio_client(client: MinIOClient) -> None:
    """
    Set the global MinIO client instance.
    
    This function should be called during application startup to initialize
    the global MinIO client that will be used throughout the application.
    
    Args:
        client: The MinIO client instance to set as global
    """
    global _minio_client
    _minio_client = client


def get_minio_client() -> MinIOClient:
    """
    Dependency injection for MinIO client.
    
    Returns:
        MinIOClient: The global MinIO client instance
        
    Raises:
        HTTPException: 503 Service Unavailable if MinIO client is not initialized
        
    Usage:
        @app.post("/upload")
        async def upload(minio: MinIOClient = Depends(get_minio_client)):
            ...
    """
    global _minio_client
    if _minio_client is None:
        logger.error("MinIO client not initialized")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image storage service is not available"
        )
    return _minio_client


@router.post("/upload", response_model=ImageUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
    minio_client: MinIOClient = Depends(get_minio_client)
) -> ImageUploadResponse:
    """
    Upload an image file.
    
    Accepts multipart/form-data with an image file. Validates file type, size,
    and MIME type, then stores the file in MinIO and creates a metadata record
    in the database. Images are organized in user-specific folders using hashed
    user identifiers.
    
    Args:
        file: Uploaded image file (multipart/form-data)
        current_user: Authenticated user from JWT token
        prisma: Prisma client instance for database operations
        minio_client: MinIO client instance for object storage
        
    Returns:
        ImageUploadResponse: Uploaded image metadata including storage details
        
    Raises:
        HTTPException: 400 Bad Request if file validation fails
        HTTPException: 401 Unauthorized if authentication fails
        HTTPException: 503 Service Unavailable if MinIO is unavailable
        HTTPException: 500 Internal Server Error if unexpected error occurs
        
    Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 2.5, 3.2, 3.3,
               3.4, 3.5, 4.2, 4.3, 5.1, 5.2, 5.3, 5.4, 5.5, 5.7, 5.8, 9.1,
               9.3, 9.4, 9.5
    """
    object_key = None
    
    try:
        # Validate filename exists
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Filename is required"
            )
        
        # Step 1: Validate file extension
        try:
            extension = validate_file_extension(file.filename)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        
        # Step 2: Read file content
        file_content = await file.read()
        
        # Step 3: Validate file size
        try:
            validate_file_size(len(file_content))
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        
        # Step 4: Validate MIME type
        content_type = file.content_type or MIME_TYPE_MAP.get(extension, "application/octet-stream")
        try:
            validate_mime_type(content_type, extension)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        
        # Step 5: Generate unique filename and user folder
        generated_filename, _ = generate_unique_filename(file.filename)
        hashed_user_id = generate_hashed_user_id(current_user.id)
        object_key = f"{hashed_user_id}/{generated_filename}"
        
        logger.info(f"Uploading image for user {current_user.id}: {object_key}")
        
        # Step 6: Upload to MinIO
        try:
            minio_client.upload_file(
                file_data=file_content,
                object_key=object_key,
                content_type=content_type
            )
        except MinIOUploadError as e:
            logger.error(f"MinIO upload failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to upload image to storage service"
            )
        except Exception as e:
            logger.error(f"Unexpected error during MinIO upload: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Image storage service is unavailable"
            )
        
        # Step 7: Create database metadata record
        try:
            image = await prisma.image.create(
                data={
                    "userId": current_user.id,
                    "objectKey": object_key,
                    "originalFilename": file.filename,
                    "generatedFilename": generated_filename,
                    "mimeType": content_type,
                    "fileSize": len(file_content)
                }
            )
            
            logger.info(f"Successfully created image record: {image.id}")
            return ImageUploadResponse.model_validate(image)
            
        except PrismaError as e:
            # Rollback: Delete from MinIO if database record creation fails
            logger.error(f"Database error during image upload: {e}")
            logger.info(f"Attempting to delete uploaded file from MinIO: {object_key}")
            
            try:
                minio_client.delete_file(object_key)
                logger.info(f"Successfully cleaned up MinIO file: {object_key}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup MinIO file after DB error: {cleanup_error}")
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save image metadata"
            )
        except Exception as e:
            # Rollback: Delete from MinIO for any unexpected error
            logger.error(f"Unexpected error creating image record: {e}")
            
            if object_key:
                try:
                    minio_client.delete_file(object_key)
                    logger.info(f"Successfully cleaned up MinIO file: {object_key}")
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup MinIO file: {cleanup_error}")
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred during image upload"
            )
    
    except HTTPException:
        # Re-raise HTTP exceptions (validation errors, auth errors, etc.)
        raise
    except Exception as e:
        # Catch any other unexpected errors
        logger.error(f"Unexpected error in upload_image: {e}", exc_info=True)
        
        # Attempt cleanup if we have an object_key
        if object_key:
            try:
                minio_client.delete_file(object_key)
                logger.info(f"Successfully cleaned up MinIO file: {object_key}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup MinIO file: {cleanup_error}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/", response_model=ImageListResponse)
async def list_images(
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma)
) -> ImageListResponse:
    """
    List all images for the authenticated user.
    
    Returns all images owned by the authenticated user with complete metadata.
    Images are ordered by upload timestamp in descending order (newest first).
    Returns an empty list if the user has no uploaded images.
    
    Args:
        current_user: Authenticated user from JWT token
        prisma: Prisma client instance for database operations
        
    Returns:
        ImageListResponse: List of images with metadata and total count
        
    Raises:
        HTTPException: 401 Unauthorized if authentication fails
        HTTPException: 500 Internal Server Error if unexpected error occurs
        
    Validates: Requirements 7.1, 7.2, 7.3, 7.4
    """
    try:
        # Query all images for current user, ordered by uploadedAt descending
        images = await prisma.image.find_many(
            where={"userId": current_user.id},
            order={"uploadedAt": "desc"}
        )
        
        # Convert to ImageMetadata schemas
        image_metadata_list = [ImageMetadata.model_validate(img) for img in images]
        
        logger.info(f"Retrieved {len(images)} images for user {current_user.id}")
        
        # Return response with images and total count
        return ImageListResponse(
            images=image_metadata_list,
            total=len(images)
        )
        
    except Exception as e:
        # Catch any unexpected errors
        logger.error(f"Unexpected error listing images for user {current_user.id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving images"
        )


@router.get("/{image_id}")
async def get_image(
    image_id: int,
    current_user: UserOut = Depends(get_current_user),
    prisma: Prisma = Depends(get_prisma),
    minio_client: MinIOClient = Depends(get_minio_client)
) -> StreamingResponse:
    """
    Retrieve an image file.
    
    Downloads and returns an image file for the authenticated user. Verifies
    that the user owns the image before allowing access. Returns the image
    with appropriate Content-Type header based on the stored MIME type.
    
    Args:
        image_id: ID of the image to retrieve
        current_user: Authenticated user from JWT token
        prisma: Prisma client instance for database operations
        minio_client: MinIO client instance for object storage
        
    Returns:
        StreamingResponse: Image file with correct Content-Type header
        
    Raises:
        HTTPException: 401 Unauthorized if authentication fails
        HTTPException: 403 Forbidden if user doesn't own the image
        HTTPException: 404 Not Found if image doesn't exist in database or MinIO
        HTTPException: 503 Service Unavailable if MinIO is unavailable
        
    Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
    """
    try:
        # Step 1: Retrieve image metadata from database
        image = await prisma.image.find_unique(
            where={"id": image_id}
        )
        
        # Step 2: Handle non-existent image (404)
        if image is None:
            logger.warning(f"Image not found in database: {image_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Image with ID {image_id} not found"
            )
        
        # Step 3: Verify user owns the image (403)
        if image.userId != current_user.id:
            logger.warning(
                f"User {current_user.id} attempted to access image {image_id} "
                f"owned by user {image.userId}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this image"
            )
        
        # Step 4: Download image from MinIO
        try:
            file_data = minio_client.download_file(image.objectKey)
            logger.info(f"Successfully retrieved image {image_id} for user {current_user.id}")
            
        except MinIONotFoundError:
            # Handle orphaned metadata (file exists in DB but not in MinIO)
            logger.error(
                f"Orphaned metadata detected: Image {image_id} exists in database "
                f"but file not found in MinIO: {image.objectKey}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image file not found in storage"
            )
        except Exception as e:
            # Handle MinIO connection errors
            logger.error(f"MinIO error retrieving image {image_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Image storage service is unavailable"
            )
        
        # Step 5: Return StreamingResponse with correct Content-Type
        return StreamingResponse(
            BytesIO(file_data),
            media_type=image.mimeType,
            headers={
                "Content-Disposition": f'inline; filename="{image.originalFilename}"'
            }
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Catch any other unexpected errors
        logger.error(f"Unexpected error retrieving image {image_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving the image"
        )
