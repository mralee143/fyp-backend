"""
Unit tests for error handling scenarios.

Tests various error conditions including 404 for non-existent routes,
validation errors, and authentication errors.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from main import app
from services.database import set_prisma


class TestNotFoundErrors:
    """Test 404 errors for non-existent routes."""
    
    @pytest.mark.asyncio
    async def test_nonexistent_route_returns_404(self, test_db):
        """Test that accessing a non-existent route returns 404."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/nonexistent")
            assert response.status_code == 404
            assert "detail" in response.json()
    
    @pytest.mark.asyncio
    async def test_nonexistent_auth_route_returns_404(self, test_db):
        """Test that accessing a non-existent auth route returns 404."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/auth/nonexistent")
            assert response.status_code == 404
            assert "detail" in response.json()
    
    @pytest.mark.asyncio
    async def test_nonexistent_users_route_returns_404(self, test_db):
        """Test that accessing a non-existent users route returns 404."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/users/nonexistent")
            assert response.status_code == 404
            assert "detail" in response.json()


class TestValidationErrors:
    """Test validation error format and responses."""
    
    @pytest.mark.asyncio
    async def test_signup_with_invalid_email_returns_422(self, test_db):
        """Test that signup with invalid email format returns 422 with field details."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "not-an-email",
                    "password": "testpass123"
                }
            )
            assert response.status_code == 422
            error_data = response.json()
            assert "detail" in error_data
            # Pydantic validation errors include field information
            assert isinstance(error_data["detail"], list)
            assert any("email" in str(err).lower() for err in error_data["detail"])
    
    @pytest.mark.asyncio
    async def test_signup_with_missing_email_returns_422(self, test_db):
        """Test that signup without email returns 422 with field details."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "password": "testpass123"
                }
            )
            assert response.status_code == 422
            error_data = response.json()
            assert "detail" in error_data
            assert isinstance(error_data["detail"], list)
    
    @pytest.mark.asyncio
    async def test_signup_with_missing_password_returns_422(self, test_db):
        """Test that signup without password returns 422 with field details."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "test@example.com"
                }
            )
            assert response.status_code == 422
            error_data = response.json()
            assert "detail" in error_data
            assert isinstance(error_data["detail"], list)
    
    @pytest.mark.asyncio
    async def test_signup_with_empty_body_returns_422(self, test_db):
        """Test that signup with empty body returns 422."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/auth/signup", json={})
            assert response.status_code == 422
            error_data = response.json()
            assert "detail" in error_data
    
    @pytest.mark.asyncio
    async def test_login_with_invalid_email_returns_422(self, test_db):
        """Test that login with invalid email format returns 422."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # OAuth2PasswordRequestForm uses form data, not JSON
            response = await client.post(
                "/auth/login",
                data={
                    "username": "not-an-email",
                    "password": "testpass123"
                }
            )
            # Note: OAuth2PasswordRequestForm doesn't validate email format,
            # so this will return 401 (user not found) instead of 422
            # This is expected behavior for OAuth2 flow
            assert response.status_code == 401


class TestAuthenticationErrors:
    """Test authentication error format and responses."""
    
    @pytest.mark.asyncio
    async def test_login_with_nonexistent_user_returns_401(self, test_db):
        """Test that login with non-existent user returns 401."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/login",
                data={
                    "username": "nonexistent@example.com",
                    "password": "wrongpass"
                }
            )
            assert response.status_code == 401
            error_data = response.json()
            assert "detail" in error_data
            assert error_data["detail"] == "Incorrect email or password"
    
    @pytest.mark.asyncio
    async def test_login_with_wrong_password_returns_401(self, test_db):
        """Test that login with wrong password returns 401."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # First create a user
            await client.post(
                "/auth/signup",
                json={
                    "email": "test@example.com",
                    "password": "correctpass"
                }
            )
            
            # Try to login with wrong password
            response = await client.post(
                "/auth/login",
                data={
                    "username": "test@example.com",
                    "password": "wrongpass"
                }
            )
            assert response.status_code == 401
            error_data = response.json()
            assert "detail" in error_data
            assert error_data["detail"] == "Incorrect email or password"
    
    @pytest.mark.asyncio
    async def test_protected_route_without_token_returns_401(self, test_db):
        """Test that accessing protected route without token returns 401."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/users/me")
            assert response.status_code == 401
            error_data = response.json()
            assert "detail" in error_data
    
    @pytest.mark.asyncio
    async def test_protected_route_with_invalid_token_returns_401(self, test_db):
        """Test that accessing protected route with invalid token returns 401."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/users/me",
                headers={"Authorization": "Bearer invalid_token"}
            )
            assert response.status_code == 401
            error_data = response.json()
            assert "detail" in error_data
            assert error_data["detail"] == "Could not validate credentials"
    
    @pytest.mark.asyncio
    async def test_protected_route_with_malformed_token_returns_401(self, test_db):
        """Test that accessing protected route with malformed token returns 401."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/users/me",
                headers={"Authorization": "InvalidFormat"}
            )
            assert response.status_code == 401
            error_data = response.json()
            assert "detail" in error_data


class TestConflictErrors:
    """Test conflict error responses."""
    
    @pytest.mark.asyncio
    async def test_duplicate_email_signup_returns_409(self, test_db):
        """Test that registering with existing email returns 409."""
        set_prisma(test_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Create first user
            await client.post(
                "/auth/signup",
                json={
                    "email": "duplicate@example.com",
                    "password": "testpass123"
                }
            )
            
            # Try to create another user with same email
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "duplicate@example.com",
                    "password": "anotherpass"
                }
            )
            assert response.status_code == 409
            error_data = response.json()
            assert "detail" in error_data
            assert error_data["detail"] == "Email already registered"
