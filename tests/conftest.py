"""
Shared test fixtures for the test suite.
"""

import pytest
import asyncio
from prisma import Prisma


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def test_db():
    """
    Provide a clean Prisma client for each test.
    Connects before the test and disconnects after.
    """
    prisma = Prisma()
    await prisma.connect()
    
    yield prisma
    
    # Cleanup: Delete all users created during the test
    await prisma.user.delete_many()
    await prisma.disconnect()
