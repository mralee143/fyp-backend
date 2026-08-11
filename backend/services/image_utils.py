"""
Image utility functions for validation and naming.

This module provides utilities for:
- Generating hashed user IDs for folder organization
- Creating unique filenames with timestamps
- Validating file extensions, MIME types, and sizes
- Converting filenames to snake_case
"""

import hashlib
import time
import re
from typing import Tuple


# Constants
ALLOWED_EXTENSIONS = {
    'jpg', 'jpeg', 'png', 'svg', 'bmp', 'webp', 'gif', 'tiff', 'ico'
}

MIME_TYPE_MAP = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'svg': 'image/svg+xml',
    'bmp': 'image/bmp',
    'webp': 'image/webp',
    'gif': 'image/gif',
    'tiff': 'image/tiff',
    'ico': 'image/x-icon'
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes
MAX_FILENAME_LENGTH = 255  # Filesystem limit

# Compiled regex patterns for performance
_NON_ALPHANUMERIC = re.compile(r'[^a-zA-Z0-9_]')
_MULTIPLE_UNDERSCORES = re.compile(r'_+')


def generate_hashed_user_id(user_id: int) -> str:
    """
    Generate consistent hash-based folder name for user.
    
    Uses MD5 hashing to create a consistent, privacy-preserving folder name
    from the user's database ID. The same user ID will always produce the
    same hashed folder name.
    
    Args:
        user_id: User's database ID (positive integer)
        
    Returns:
        Hashed identifier in format "user_{8_hex_chars}"
        Example: "user_5f4dcc3b"
        
    Raises:
        ValueError: If user_id is not positive
        
    Validates: Requirements 4.1, 4.4
    """
    if user_id <= 0:
        raise ValueError(f"user_id must be positive, got {user_id}")
    
    hash_obj = hashlib.md5(str(user_id).encode())
    return f"user_{hash_obj.hexdigest()[:8]}"


def to_snake_case(filename: str) -> str:
    """
    Convert filename to snake_case.
    
    Converts spaces and hyphens to underscores, removes special characters,
    and converts to lowercase. Preserves the file extension as-is.
    
    Args:
        filename: Original filename (may include extension)
        
    Returns:
        snake_case version of filename
        Example: "My Image File.jpg" -> "my_image_file.jpg"
        
    Validates: Requirements 2.5
    """
    # Split filename and extension
    if '.' in filename:
        name_part, ext_part = filename.rsplit('.', 1)
        ext_part = ext_part.lower()
    else:
        name_part = filename
        ext_part = ''
    
    # Process name part only
    # Replace spaces and hyphens with underscores
    name_part = name_part.translate(str.maketrans(' -', '__'))
    
    # Remove any characters that aren't alphanumeric or underscore
    name_part = _NON_ALPHANUMERIC.sub('', name_part)
    
    # Convert to lowercase
    name_part = name_part.lower()
    
    # Remove consecutive underscores
    name_part = _MULTIPLE_UNDERSCORES.sub('_', name_part)
    
    # Remove leading/trailing underscores
    name_part = name_part.strip('_')
    
    # Reconstruct filename with extension
    return f"{name_part}.{ext_part}" if ext_part else name_part


def generate_unique_filename(original_filename: str) -> Tuple[str, str]:
    """
    Generate unique filename with timestamp.
    
    Converts the original filename to snake_case and appends a millisecond
    timestamp to ensure uniqueness. Returns both the generated filename
    and the extracted extension.
    
    Args:
        original_filename: Original uploaded filename
        
    Returns:
        Tuple of (generated_filename, extension)
        Example: ("my_image_1708531200000.jpg", "jpg")
        
    Validates: Requirements 2.5, 4.2
    """
    # Extract extension
    if '.' in original_filename:
        name_part, extension = original_filename.rsplit('.', 1)
        extension = extension.lower()
    else:
        name_part = original_filename
        extension = ''
    
    # Convert name to snake_case
    snake_case_name = to_snake_case(name_part)
    
    # Handle empty name after sanitization
    if not snake_case_name:
        snake_case_name = 'file'
    
    # Generate timestamp in milliseconds
    timestamp = int(time.time() * 1000)
    
    # Truncate name if total length would exceed filesystem limit
    timestamp_str = str(timestamp)
    max_name_length = MAX_FILENAME_LENGTH - len(timestamp_str) - len(extension) - 2  # for _ and .
    if len(snake_case_name) > max_name_length:
        snake_case_name = snake_case_name[:max_name_length]
    
    # Construct unique filename
    if extension:
        generated_filename = f"{snake_case_name}_{timestamp}.{extension}"
    else:
        generated_filename = f"{snake_case_name}_{timestamp}"
    
    return (generated_filename, extension)


def validate_file_extension(filename: str) -> str:
    """
    Validate and extract file extension.
    
    Checks if the file has an allowed extension. Returns the lowercase
    extension without the dot if valid.
    
    Args:
        filename: Filename to validate
        
    Returns:
        Lowercase extension without dot (e.g., "jpg")
        
    Raises:
        ValueError: If extension is not in ALLOWED_EXTENSIONS or missing
        
    Validates: Requirements 2.1, 2.3
    """
    if '.' not in filename:
        raise ValueError(
            f"File '{filename}' has no extension. "
            f"Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    
    extension = filename.rsplit('.', 1)[1].lower()
    
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File extension '.{extension}' is not allowed. "
            f"Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    
    return extension


def validate_mime_type(content_type: str, extension: str) -> None:
    """
    Validate MIME type matches file extension.
    
    Ensures the provided MIME type (Content-Type header) matches the
    expected MIME type for the given file extension.
    
    Args:
        content_type: MIME type from Content-Type header
        extension: File extension (without dot, lowercase)
        
    Raises:
        ValueError: If MIME type doesn't match expected type for extension
        
    Validates: Requirements 2.4
    """
    expected_mime = MIME_TYPE_MAP.get(extension)
    
    if expected_mime is None:
        raise ValueError(f"Unknown extension: {extension}")
    
    if content_type != expected_mime:
        raise ValueError(
            f"MIME type mismatch: expected '{expected_mime}' "
            f"for extension '{extension}', got '{content_type}'"
        )


def validate_file_size(file_size: int) -> None:
    """
    Validate file size is within limits.
    
    Checks if the file size is within the maximum allowed size (50MB).
    
    Args:
        file_size: File size in bytes
        
    Raises:
        ValueError: If file exceeds MAX_FILE_SIZE
        
    Validates: Requirements 2.2
    """
    if file_size > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE / (1024 * 1024)
        actual_mb = file_size / (1024 * 1024)
        raise ValueError(
            f"File size {actual_mb:.2f}MB exceeds maximum allowed size of {max_mb:.0f}MB"
        )
    
    if file_size <= 0:
        raise ValueError("File size must be greater than 0")
