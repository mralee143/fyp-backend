"""
MinIO client service for object storage operations.

This module provides a client for interacting with MinIO object storage,
including operations for uploading, downloading, and deleting image files.
"""

from minio import Minio
from minio.error import S3Error
from io import BytesIO
import logging
from typing import Optional


# Configure logging
logger = logging.getLogger(__name__)


# Custom exceptions
class MinIOUploadError(Exception):
    """Raised when file upload to MinIO fails."""
    pass


class MinIONotFoundError(Exception):
    """Raised when requested file is not found in MinIO."""
    pass


class MinIOClient:
    """
    MinIO client for object storage operations.
    
    Manages connections to MinIO and provides methods for uploading,
    downloading, and deleting image files.
    
    Validates: Requirements 3.1, 3.2, 3.4, 3.5, 6.1, 8.4, 9.2, 9.5
    """
    
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket_name: str,
        secure: bool = True
    ):
        """
        Initialize MinIO client with connection parameters.
        
        Args:
            endpoint: MinIO server endpoint (e.g., 'localhost:9000')
            access_key: MinIO access key for authentication
            secret_key: MinIO secret key for authentication
            bucket_name: Name of the bucket to use for storage
            secure: Whether to use HTTPS (True) or HTTP (False)
            
        Raises:
            ValueError: If any required parameter is empty
            
        Validates: Requirements 3.1, 8.1
        """
        if not endpoint:
            raise ValueError("MinIO endpoint cannot be empty")
        if not access_key:
            raise ValueError("MinIO access key cannot be empty")
        if not secret_key:
            raise ValueError("MinIO secret key cannot be empty")
        if not bucket_name:
            raise ValueError("MinIO bucket name cannot be empty")
        
        self.bucket_name = bucket_name
        
        try:
            self.client = Minio(
                endpoint=endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=secure
            )
            logger.info(f"MinIO client initialized for endpoint: {endpoint}")
        except Exception as e:
            logger.error(f"Failed to initialize MinIO client: {e}")
            raise ValueError(f"Failed to initialize MinIO client: {e}")
    
    def ensure_bucket_exists(self) -> None:
        """
        Create bucket if it doesn't exist.
        
        Checks if the configured bucket exists and creates it if not.
        This method should be called during application startup.
        
        Raises:
            MinIOUploadError: If bucket creation fails
            
        Validates: Requirements 8.4
        """
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
                logger.info(f"Created MinIO bucket: {self.bucket_name}")
            else:
                logger.info(f"MinIO bucket already exists: {self.bucket_name}")
        except S3Error as e:
            error_msg = f"Failed to ensure bucket exists: {e}"
            logger.error(error_msg)
            raise MinIOUploadError(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error ensuring bucket exists: {e}"
            logger.error(error_msg)
            raise MinIOUploadError(error_msg)
    
    def upload_file(
        self,
        file_data: bytes,
        object_key: str,
        content_type: str
    ) -> str:
        """
        Upload file to MinIO.
        
        Stores the file in MinIO using the provided object key (path).
        The object key should follow the format: user_hash/filename.ext
        
        Args:
            file_data: Binary file content to upload
            object_key: Storage path (e.g., "user_abc123/image_1234567890.jpg")
            content_type: MIME type of the file (e.g., "image/jpeg")
            
        Returns:
            object_key: Confirmation of stored path (same as input)
            
        Raises:
            MinIOUploadError: If upload fails due to connection or storage issues
            ValueError: If file_data is empty or object_key is invalid
            
        Validates: Requirements 3.2, 3.4, 3.5
        """
        if not file_data:
            raise ValueError("File data cannot be empty")
        if not object_key:
            raise ValueError("Object key cannot be empty")
        if not content_type:
            raise ValueError("Content type cannot be empty")
        
        try:
            # Convert bytes to BytesIO stream for MinIO
            file_stream = BytesIO(file_data)
            file_size = len(file_data)
            
            # Upload to MinIO
            self.client.put_object(
                bucket_name=self.bucket_name,
                object_name=object_key,
                data=file_stream,
                length=file_size,
                content_type=content_type
            )
            
            logger.info(f"Successfully uploaded file to MinIO: {object_key}")
            return object_key
            
        except S3Error as e:
            error_msg = f"S3 error uploading file '{object_key}': {e}"
            logger.error(error_msg)
            raise MinIOUploadError(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error uploading file '{object_key}': {e}"
            logger.error(error_msg)
            raise MinIOUploadError(error_msg)
    
    def download_file(self, object_key: str) -> bytes:
        """
        Download file from MinIO.
        
        Retrieves the file content from MinIO using the object key.
        
        Args:
            object_key: Storage path of the file to download
            
        Returns:
            Binary file content
            
        Raises:
            MinIONotFoundError: If file doesn't exist in MinIO
            MinIOUploadError: If download fails due to connection issues
            
        Validates: Requirements 6.1
        """
        if not object_key:
            raise ValueError("Object key cannot be empty")
        
        try:
            # Get object from MinIO
            response = self.client.get_object(
                bucket_name=self.bucket_name,
                object_name=object_key
            )
            
            # Read all data from response
            file_data = response.read()
            response.close()
            response.release_conn()
            
            logger.info(f"Successfully downloaded file from MinIO: {object_key}")
            return file_data
            
        except S3Error as e:
            if e.code == 'NoSuchKey':
                error_msg = f"File not found in MinIO: {object_key}"
                logger.warning(error_msg)
                raise MinIONotFoundError(error_msg)
            else:
                error_msg = f"S3 error downloading file '{object_key}': {e}"
                logger.error(error_msg)
                raise MinIOUploadError(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error downloading file '{object_key}': {e}"
            logger.error(error_msg)
            raise MinIOUploadError(error_msg)
    
    def delete_file(self, object_key: str) -> None:
        """
        Delete file from MinIO.
        
        Removes the file from MinIO storage. Used for cleanup operations
        when database record creation fails or for rollback scenarios.
        
        Args:
            object_key: Storage path of the file to delete
            
        Raises:
            MinIOUploadError: If deletion fails
            
        Note:
            Does not raise an error if the file doesn't exist (idempotent).
            
        Validates: Requirements 9.5
        """
        if not object_key:
            raise ValueError("Object key cannot be empty")
        
        try:
            self.client.remove_object(
                bucket_name=self.bucket_name,
                object_name=object_key
            )
            logger.info(f"Successfully deleted file from MinIO: {object_key}")
            
        except S3Error as e:
            # If file doesn't exist, consider it a success (idempotent)
            if e.code == 'NoSuchKey':
                logger.info(f"File already deleted or doesn't exist: {object_key}")
                return
            
            error_msg = f"S3 error deleting file '{object_key}': {e}"
            logger.error(error_msg)
            raise MinIOUploadError(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error deleting file '{object_key}': {e}"
            logger.error(error_msg)
            raise MinIOUploadError(error_msg)
