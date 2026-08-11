"""
Property-based tests for unique filename generation.

Feature: image-upload-minio, Property 5: Unique Filename Generation
Validates: Requirements 2.5
"""

import pytest
import time
from hypothesis import given, strategies as st, settings

from services.image_utils import generate_unique_filename, to_snake_case


# Strategy for generating valid filenames
filenames = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(
        whitelist_categories=('Lu', 'Ll', 'Nd'),
        whitelist_characters=' -_.'
    )
).filter(lambda x: x.strip() and not x.startswith('.') and not x.endswith('.'))

extensions = st.sampled_from(['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'tiff', 'ico'])


@given(
    filename=filenames,
    extension=extensions
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_unique_filename_generation(filename, extension):
    """
    Property 5: Unique Filename Generation
    
    For any uploaded image, the system should generate a filename in
    snake_case format with a timestamp suffix that is unique within
    the user's folder.
    
    Validates: Requirements 2.5
    """
    original_filename = f"{filename}.{extension}"
    
    # Generate filename twice with small delay
    generated1, ext1 = generate_unique_filename(original_filename)
    time.sleep(0.001)  # 1ms delay to ensure different timestamp
    generated2, ext2 = generate_unique_filename(original_filename)
    
    # Filenames should be different due to timestamp
    assert generated1 != generated2, \
        f"Generated filenames should be unique: {generated1} vs {generated2}"
    
    # Both should have correct extension
    assert ext1 == extension.lower(), \
        f"Extension should be {extension.lower()}, got {ext1}"
    assert ext2 == extension.lower(), \
        f"Extension should be {extension.lower()}, got {ext2}"
    
    # Both should end with the extension
    assert generated1.endswith(f".{extension.lower()}"), \
        f"Generated filename should end with .{extension.lower()}: {generated1}"
    assert generated2.endswith(f".{extension.lower()}"), \
        f"Generated filename should end with .{extension.lower()}: {generated2}"
    
    # Both should contain a timestamp (numeric suffix before extension)
    name_part1 = generated1.rsplit('.', 1)[0]
    name_part2 = generated2.rsplit('.', 1)[0]
    
    # Should end with underscore followed by digits (timestamp)
    assert '_' in name_part1, \
        f"Generated filename should contain underscore before timestamp: {name_part1}"
    assert '_' in name_part2, \
        f"Generated filename should contain underscore before timestamp: {name_part2}"
    
    timestamp1 = name_part1.rsplit('_', 1)[1]
    timestamp2 = name_part2.rsplit('_', 1)[1]
    
    assert timestamp1.isdigit(), \
        f"Timestamp should be numeric: {timestamp1}"
    assert timestamp2.isdigit(), \
        f"Timestamp should be numeric: {timestamp2}"
    
    # Timestamps should be different
    assert timestamp1 != timestamp2, \
        f"Timestamps should be different: {timestamp1} vs {timestamp2}"


@given(filename=filenames)
@settings(max_examples=100)
@pytest.mark.property_test
def test_snake_case_conversion(filename):
    """
    Property: Filenames should be converted to snake_case.
    
    For any filename, the conversion should produce lowercase with
    underscores replacing spaces and hyphens.
    
    Validates: Requirements 2.5
    """
    result = to_snake_case(filename)
    
    # Should be lowercase
    assert result == result.lower(), \
        f"Result should be lowercase: {result}"
    
    # Should not contain spaces or hyphens
    assert ' ' not in result, \
        f"Result should not contain spaces: {result}"
    assert '-' not in result, \
        f"Result should not contain hyphens: {result}"
    
    # Should only contain alphanumeric, underscores, and dots
    for char in result:
        assert char.isalnum() or char in ('_', '.'), \
            f"Result should only contain alphanumeric, underscores, and dots: {result}"
    
    # Should not have consecutive underscores
    assert '__' not in result, \
        f"Result should not have consecutive underscores: {result}"
    
    # Should not start or end with underscore (unless empty)
    if result and result != '.':
        name_part = result.split('.')[0] if '.' in result else result
        if name_part:
            assert not name_part.startswith('_'), \
                f"Result should not start with underscore: {result}"
            assert not name_part.endswith('_'), \
                f"Result should not end with underscore: {result}"


@given(
    filename=filenames,
    extension=extensions
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_filename_format_compliance(filename, extension):
    """
    Property: Generated filenames should follow the format:
    {snake_case_name}_{timestamp}.{extension}
    
    Validates: Requirements 2.5, 4.2
    """
    original_filename = f"{filename}.{extension}"
    generated, ext = generate_unique_filename(original_filename)
    
    # Should have extension
    assert '.' in generated, \
        f"Generated filename should have extension: {generated}"
    
    # Extension should match
    assert generated.endswith(f".{extension.lower()}"), \
        f"Generated filename should end with .{extension.lower()}: {generated}"
    
    # Name part should be in snake_case
    name_part = generated.rsplit('.', 1)[0]
    
    # Should contain at least one underscore (before timestamp)
    assert '_' in name_part, \
        f"Name part should contain underscore: {name_part}"
    
    # Last part after underscore should be timestamp (all digits)
    parts = name_part.rsplit('_', 1)
    if len(parts) == 2:
        timestamp_part = parts[1]
        assert timestamp_part.isdigit(), \
            f"Timestamp part should be all digits: {timestamp_part}"
        assert len(timestamp_part) >= 10, \
            f"Timestamp should be at least 10 digits (Unix timestamp in ms): {timestamp_part}"
