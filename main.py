"""
FastAPI application entry point.

This module initializes the FastAPI application, manages the Prisma client
lifecycle, and registers all route handlers.
"""

from fastapi import FastAPI
from contextlib import asynccontextmanager
from prisma import Prisma

from routes import auth, users
from services.database import set_prisma
from config import settings


# Global Prisma client instance
prisma = Prisma()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifespan events.
    
    Handles startup and shutdown events for the FastAPI application,
    including database connection management.
    
    Args:
        app: The FastAPI application instance
        
    Yields:
        None: Control back to the application during its lifetime
    """
    # Startup: Connect to database
    await prisma.connect()
    set_prisma(prisma)
    yield
    # Shutdown: Disconnect from database
    await prisma.disconnect()


# Initialize FastAPI application
app = FastAPI(
    title="Authentication API",
    description="User authentication system with JWT tokens and secure password management",
    version="2.0.0",
    lifespan=lifespan
)


# Register routers
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/users", tags=["Users"])


@app.get("/", tags=["Health"])
async def root():
    """
    Root endpoint for health check.
    
    Returns basic API information to verify the service is running.
    
    Returns:
        dict: API name and version information
    """
    return {
        "message": "Authentication API v2.0",
        "status": "running",
        "docs": "/docs"
    }
