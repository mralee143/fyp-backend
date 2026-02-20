"""
Property-based tests for file validation.

Feature: image-upload-minio, Property 3: File Extension Validation
Feature: image-upload-minio, Property 4: MIME Type Consistency
Validates: Requirements 2.1, 2.3, 2.4
"""

import pytest
from hypothesis import given, strategies as st, settings, assume

from services.image_utils import (
    validate_file_extension,
    validate_mime_type,
    validate_file_size,
    ALLOWED_EXTENSIONS,
    MIME_TYPE_MAP,
    MAX_FILE_SIZE
)


# Strategies
allowed_extensions = st.sampled_from(list(ALLOWED_EXTENSIONS))
invalid_extensions = st.text(min_size=1, max_size=10).filter(
    lambda x: x.lower() not in ALLOWED_EXTENSIONS and '.' not in x
)
filenames_base = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_-')
)


@given(
    filename_base=filenames_base,
    extension=allowed_extensions
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_file_extension_validation_accepts_allowed(filename_base, extension):
    """
    Property 3: File Extension Validation (Positive Case)
    
    For any uploaded file with an allowed extension, the system should
    accept it and return the normalized extension.
    
    Validates: Requirements 2.1
    """
    filename = f"{filename_base}.{extension}"
    
    # Should not raise an exception
    result = validate_file_extension(filename)
    
    # Should return lowercase extension without dot
    assert result == extension.lower(), \
        f"Expected {extension.lower()}, got {result}"
    assert result in ALLOWED_EXTENSIONS, \
        f"Result {result} should be in allowed extensions"


@given(
    filename_base=filenames_base,
    extension=invalid_extensions
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_file_extension_validation_rejects_disallowed(filename_base, extension):
    """
    Property 3: File Extension Validation (Negative Case)
    
    For any uploaded file with a disallowed extension, the system should
    reject it with a ValueError.
    
    Validates: Requirements 2.3
    """
    filename = f"{filename_base}.{extension}"
    
    # Should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        validate_file_extension(filename)
    
    # Error message should mention the extension
    error_msg = str(exc_info.value)
    assert "not allowed" in error_msg.lower() or "extension" in error_msg.lower(), \
        f"Error message should mention extension: {error_msg}"


@given(filename_base=filenames_base)
@settings(max_examples=100)
@pytest.mark.property_test
def test_file_extension_validation_rejects_no_extension(filename_base):
    """
    Property 3: File Extension Validation (No Extension Case)
    
    For any uploaded file without an extension, the system should
    reject it with a ValueError.
    
    Validates: Requirements 2.1, 2.3
    """
    # Ensure filename has no dot
    filename = filename_base.replace('.', '')
    assume(len(filename) > 0)
    
    # Should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        validate_file_extension(filename)
    
    # Error message should mention missing extension
    error_msg = str(exc_info.value)
    assert "no extension" in error_msg.lower() or "extension" in error_msg.lower(), \
        f"Error message should mention missing extension: {error_msg}"


@given(extension=allowed_extensions)
@settings(max_examples=100)
@pytest.mark.property_test
def test_mime_type_consistency_valid(extension):
    """
    Property 4: MIME Type Consistency (Positive Case)
    
    For any uploaded file with a valid extension, the system should
    verify that the MIME type matches the expected type for that extension.
    
    Validates: Requirements 2.4
    """
    expected_mime = MIME_TYPE_MAP[extension]
    
    # Should not raise an exception when MIME type matches
    validate_mime_type(expected_mime, extension)


@given(
    extension=allowed_extensions,
    wrong_mime=st.text(min_size=1, max_size=50)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_mime_type_consistency_invalid(extension, wrong_mime):
    """
    Property 4: MIME Type Consistency (Negative Case)
    
    For any uploaded file where the MIME type doesn't match the extension,
    the system should reject it with a ValueError.
    
    Validates: Requirements 2.4
    """
    expected_mime = MIME_TYPE_MAP[extension]
    
    # Skip if wrong_mime happens to match the expected one
    assume(wrong_mime != expected_mime)
    
    # Should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        validate_mime_type(wrong_mime, extension)
    
    # Error message should mention MIME type mismatch
    error_msg = str(exc_info.value)
    assert "mime" in error_msg.lower() or "mismatch" in error_msg.lower(), \
        f"Error message should mention MIME type mismatch: {error_msg}"


@given(file_size=st.integers(min_value=1, max_value=MAX_FILE_SIZE))
@settings(max_examples=100)
@pytest.mark.property_test
def test_file_size_validation_accepts_valid(file_size):
    """
    Property: File size validation should accept files within limit.
    
    For any file size from 1 byte to MAX_FILE_SIZE, the system should
    accept it without raising an exception.
    
    Validates: Requirements 2.2
    """
    # Should not raise an exception
    validate_file_size(file_size)


@given(file_size=st.integers(min_value=MAX_FILE_SIZE + 1, max_value=MAX_FILE_SIZE * 2))
@settings(max_examples=100)
@pytest.mark.property_test
def test_file_size_validation_rejects_oversized(file_size):
    """
    Property: File size validation should reject files exceeding limit.
    
    For any file size exceeding MAX_FILE_SIZE (50MB), the system should
    reject it with a ValueError.
    
    Validates: Requirements 2.2
    """
    # Should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        validate_file_size(file_size)
    
    # Error message should mention size limit
    error_msg = str(exc_info.value)
    assert "size" in error_msg.lower() or "exceeds" in error_msg.lower() or "mb" in error_msg.lower(), \
        f"Error message should mention size limit: {error_msg}"


@given(file_size=st.integers(max_value=0))
@settings(max_examples=100)
@pytest.mark.property_test
def test_file_size_validation_rejects_zero_or_negative(file_size):
    """
    Property: File size validation should reject zero or negative sizes.
    
    For any file size that is zero or negative, the system should
    reject it with a ValueError.
    
    Validates: Requirements 2.2
    """
    # Should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        validate_file_size(file_size)
    
    # Error message should mention invalid size
    error_msg = str(exc_info.value)
    assert "size" in error_msg.lower() or "greater" in error_msg.lower(), \
        f"Error message should mention invalid size: {error_msg}"
