"""
Database service for Prisma client management.

This module provides global Prisma client instance management and
dependency injection for FastAPI routes.
"""

from prisma import Prisma
from typing import Optional


# Global Prisma client instance
prisma_client: Optional[Prisma] = None


def get_prisma() -> Prisma:
    """
    Dependency injection for Prisma client.
    
    Returns:
        Prisma: The global Prisma client instance
        
    Raises:
        RuntimeError: If Prisma client is not initialized
        
    Usage:
        @app.get("/users")
        async def get_users(prisma: Prisma = Depends(get_prisma)):
            users = await prisma.user.find_many()
            return users
    """
    global prisma_client
    if prisma_client is None:
        raise RuntimeError("Prisma client not initialized")
    return prisma_client


def set_prisma(client: Prisma) -> None:
    """
    Set the global Prisma client instance.
    
    This function should be called during application startup to initialize
    the global Prisma client that will be used throughout the application.
    
    Args:
        client: The Prisma client instance to set as global
        
    Usage:
        prisma = Prisma()
        await prisma.connect()
        set_prisma(prisma)
    """
    global prisma_client
    prisma_client = client
