"""
Admin routes — restricted to the configured admin user (by email).

Provides a site-wide view: all users, all scans, and overall stats for the
admin dashboard.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma

from middleware.auth import get_current_user
from schemas.user import UserOut
from services.database import get_prisma
from services.scan_store import (
    admin_stats,
    admin_users,
    admin_scans,
    admin_analytics,
    get_scan_any,
)
from config import settings

router = APIRouter(prefix="/admin", tags=["Admin"])


async def require_admin(current_user: UserOut = Depends(get_current_user)) -> UserOut:
    """Allow only the configured admin email."""
    if current_user.email.lower() != settings.admin_email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return current_user


@router.get("/stats")
async def stats(
    _: UserOut = Depends(require_admin),
    prisma: Prisma = Depends(get_prisma),
) -> dict:
    return await admin_stats(prisma)


@router.get("/users")
async def users(
    _: UserOut = Depends(require_admin),
    prisma: Prisma = Depends(get_prisma),
) -> list[dict]:
    return await admin_users(prisma)


@router.get("/scans")
async def scans(
    _: UserOut = Depends(require_admin),
    prisma: Prisma = Depends(get_prisma),
) -> list[dict]:
    return await admin_scans(prisma)


@router.get("/analytics")
async def analytics(
    _: UserOut = Depends(require_admin),
    prisma: Prisma = Depends(get_prisma),
) -> dict:
    return await admin_analytics(prisma)


@router.get("/scan/{scan_id}")
async def scan_detail(
    scan_id: int,
    _: UserOut = Depends(require_admin),
    prisma: Prisma = Depends(get_prisma),
) -> dict:
    row = await get_scan_any(prisma, scan_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found"
        )
    return row


@router.delete("/user/{user_id}")
async def delete_user(
    user_id: int,
    _: UserOut = Depends(require_admin),
    prisma: Prisma = Depends(get_prisma),
) -> dict:
    """Delete a user (and cascade their scans). The admin can't delete itself."""
    user = await prisma.user.find_unique(where={"id": user_id})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.email.lower() == settings.admin_email.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the admin account",
        )
    await prisma.user.delete(where={"id": user_id})  # FK cascade removes their scans
    return {"message": "User deleted"}


@router.delete("/scan/{scan_id}")
async def delete_scan(
    scan_id: int,
    _: UserOut = Depends(require_admin),
    prisma: Prisma = Depends(get_prisma),
) -> dict:
    """Delete a scan and its extracted clip files."""
    import os
    from pathlib import Path

    row = await get_scan_any(prisma, scan_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")

    # Best-effort: remove the clip files this scan produced.
    for seg in (row.get("result") or {}).get("segments", []):
        url = seg.get("clip_url")
        if url and url.startswith("/media/"):
            try:
                p = Path(url.lstrip("/"))
                if p.exists():
                    os.remove(p)
            except OSError:
                pass

    await prisma.execute_raw("DELETE FROM detection_scans WHERE id = $1", scan_id)
    return {"message": "Scan deleted"}
