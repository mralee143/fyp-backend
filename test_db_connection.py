"""
Simple script to test PostgreSQL database connection.
"""

import asyncio
from config import settings

try:
    from prisma import Prisma
    PRISMA_AVAILABLE = True
except ImportError:
    PRISMA_AVAILABLE = False
    print("⚠️  Prisma not installed yet. Install with: pip install prisma")


async def test_connection():
    """Test database connection using Prisma."""
    if not PRISMA_AVAILABLE:
        print("❌ Cannot test connection - Prisma not installed")
        return False
    
    print(f"🔍 Testing connection to: {settings.database_url}")
    print(f"   Database: vision_db")
    print(f"   User: root")
    print(f"   Host: localhost:5432")
    print()
    
    try:
        prisma = Prisma()
        print("📡 Connecting to database...")
        await prisma.connect()
        print("✅ Successfully connected to PostgreSQL database!")
        
        # Test a simple query
        print("🔍 Testing database query...")
        result = await prisma.query_raw('SELECT version();')
        print(f"✅ PostgreSQL version: {result[0]['version']}")
        
        await prisma.disconnect()
        print("✅ Connection test completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Connection failed: {str(e)}")
        print()
        print("💡 Make sure PostgreSQL is running:")
        print("   docker-compose up -d")
        return False


def test_config():
    """Test configuration loading."""
    print("=" * 60)
    print("Configuration Test")
    print("=" * 60)
    
    try:
        print(f"✅ Configuration loaded successfully!")
        print(f"   DATABASE_URL: {settings.database_url}")
        print(f"   SECRET_KEY: {'*' * len(settings.secret_key)} (hidden)")
        print(f"   ALGORITHM: {settings.algorithm}")
        print(f"   TOKEN_EXPIRE: {settings.access_token_expire_minutes} minutes")
        print(f"   APP_NAME: {settings.app_name}")
        print(f"   DEBUG: {settings.debug}")
        print()
        return True
    except Exception as e:
        print(f"❌ Configuration failed: {str(e)}")
        return False


async def main():
    """Main test function."""
    print()
    print("=" * 60)
    print("FastAPI + Prisma Database Connection Test")
    print("=" * 60)
    print()
    
    # Test configuration
    config_ok = test_config()
    
    if not config_ok:
        return
    
    # Test database connection
    print("=" * 60)
    print("Database Connection Test")
    print("=" * 60)
    print()
    
    await test_connection()


if __name__ == "__main__":
    asyncio.run(main())
