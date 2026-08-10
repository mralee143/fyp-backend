"""
FastAPI application entry point.

This module initializes the FastAPI application, manages the Prisma client
lifecycle, and registers all route handlers.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from prisma import Prisma
import logging
import os

from routes import auth, users, images, detection
from services.database import set_prisma
from services.minio_client import MinIOClient
from config import settings


# Configure logging
logger = logging.getLogger(__name__)


# Global Prisma client instance
prisma = Prisma()

# Global MinIO client instance
minio_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifespan events.
    
    Handles startup and shutdown events for the FastAPI application,
    including database connection management and MinIO client initialization.
    
    Args:
        app: The FastAPI application instance
        
    Yields:
        None: Control back to the application during its lifetime
    """
    global minio_client
    
    # Startup: Connect to database
    await prisma.connect()
    set_prisma(prisma)
    
    # Startup: Initialize MinIO client
    try:
        minio_client = MinIOClient(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket_name=settings.minio_bucket_name,
            secure=settings.minio_secure
        )
        
        # Ensure bucket exists
        minio_client.ensure_bucket_exists()
        
        # Set global MinIO client for dependency injection
        images.set_minio_client(minio_client)
        
        logger.info("MinIO client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize MinIO client: {e}")
        # Continue startup even if MinIO fails - endpoints will return 503
    
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


# Enable CORS for the Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Serve extracted incident clips at /media (created on demand).
os.makedirs("media/clips", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")


# Register routers
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(images.router)  # Router already has prefix="/images"
app.include_router(detection.router)  # Router already has prefix="/detection"


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="debug"
    )
