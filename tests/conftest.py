"""
Shared test fixtures for the test suite.
"""

import pytest
import asyncio
from prisma import Prisma
from httpx import AsyncClient, ASGITransport
from main import app
from services.database import set_prisma


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


@pytest.fixture(scope="function")
async def authenticated_client_factory(test_db):
    """
    Factory fixture that creates authenticated HTTP clients.
    
    Returns a coroutine that creates a new user, logs them in,
    and returns an authenticated client with the JWT token.
    
    Usage:
        client, token = await authenticated_client_factory()
    """
    counter = 0
    
    async def _create_authenticated_client():
        nonlocal counter
        counter += 1
        
        set_prisma(test_db)
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Create a unique user
            email = f"testuser{counter}@example.com"
            password = "testpass123"
            
            # Sign up
            await client.post(
                "/auth/signup",
                json={"email": email, "password": password}
            )
            
            # Login to get token
            login_response = await client.post(
                "/auth/login",
                data={"username": email, "password": password}
            )
            token = login_response.json()["access_token"]
            
            # Return a new client instance with the token
            new_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            return new_client, token
    
    return _create_authenticated_client
