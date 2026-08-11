"""
Script to verify API documentation endpoints are accessible.
"""
import asyncio
import sys
from httpx import AsyncClient, ASGITransport
from main import app


async def verify_documentation():
    """Verify that all API documentation endpoints are accessible."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        print("Verifying API Documentation Endpoints...")
        print("-" * 50)
        
        try:
            # Test root endpoint
            response = await client.get("/")
            assert response.status_code == 200, f"Root endpoint failed: {response.status_code}"
            print(f"✓ Root endpoint (/): {response.status_code}")
            print(f"  Response: {response.json()}")
            
            # Test OpenAPI schema
            response = await client.get("/openapi.json")
            assert response.status_code == 200, f"OpenAPI schema failed: {response.status_code}"
            print(f"\n✓ OpenAPI Schema (/openapi.json): {response.status_code}")
            schema = response.json()
            print(f"  API Title: {schema.get('info', {}).get('title')}")
            print(f"  API Version: {schema.get('info', {}).get('version')}")
            print(f"  Endpoints: {len(schema.get('paths', {}))}")
            
            # Test Swagger UI
            response = await client.get("/docs")
            assert response.status_code == 200, f"Swagger UI failed: {response.status_code}"
            print(f"\n✓ Swagger UI (/docs): {response.status_code}")
            
            # Test ReDoc
            response = await client.get("/redoc")
            assert response.status_code == 200, f"ReDoc failed: {response.status_code}"
            print(f"✓ ReDoc (/redoc): {response.status_code}")
            
            print("\n" + "=" * 50)
            print("All API documentation endpoints are accessible!")
            print("=" * 50)
            return True
            
        except AssertionError as e:
            print(f"\n✗ Verification failed: {e}")
            return False
        except Exception as e:
            print(f"\n✗ Unexpected error: {e}")
            return False


if __name__ == "__main__":
    success = asyncio.run(verify_documentation())
    sys.exit(0 if success else 1)
