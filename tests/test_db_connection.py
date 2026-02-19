"""
Test script to verify PostgreSQL database connection.
Run this after setting up the database to ensure everything is configured correctly.
"""

import asyncio
import sys
from prisma import Prisma
from config import settings


async def test_connection():
    """Test the database connection"""
    print("=" * 60)
    print("PostgreSQL Database Connection Test")
    print("=" * 60)
    print()
    
    print(f"Database URL: {settings.database_url.split('@')[1] if '@' in settings.database_url else 'Not configured'}")
    print()
    
    prisma = Prisma()
    
    try:
        print("Attempting to connect to the database...")
        await prisma.connect()
        print("✓ Successfully connected to PostgreSQL!")
        print()
        
        # Test a simple query
        print("Testing database query...")
        result = await prisma.query_raw("SELECT version();")
        print(f"✓ Database query successful!")
        print(f"  PostgreSQL version: {result[0]['version'].split(',')[0]}")
        print()
        
        # Check if User table exists
        print("Checking if User table exists...")
        try:
            count = await prisma.user.count()
            print(f"✓ User table exists with {count} records")
        except Exception as e:
            print(f"⚠ User table not found. Run 'prisma db push' to create tables.")
            print(f"  Error: {str(e)}")
        
        print()
        print("=" * 60)
        print("Database connection test completed successfully!")
        print("=" * 60)
        
        await prisma.disconnect()
        return True
        
    except Exception as e:
        print(f"✗ Connection failed!")
        print(f"  Error: {str(e)}")
        print()
        print("Troubleshooting steps:")
        print("1. Ensure PostgreSQL is running")
        print("2. Verify DATABASE_URL in .env file")
        print("3. Check if the database 'vision_db' exists")
        print("4. Verify username and password are correct")
        print()
        print("If using Docker:")
        print("  Run: docker-compose up -d")
        print("  Check status: docker ps")
        print()
        print("=" * 60)
        
        try:
            await prisma.disconnect()
        except:
            pass
        
        return False


if __name__ == "__main__":
    success = asyncio.run(test_connection())
    sys.exit(0 if success else 1)
