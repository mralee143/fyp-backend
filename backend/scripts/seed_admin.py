"""
Seed / reset the admin user.

Creates (or updates) the admin account from settings.admin_email with a known
password, active and email-verified. Run:  python seed_admin.py
"""

import asyncio
import sys
from pathlib import Path

# Running this file directly puts scripts/ on sys.path, not backend/. Add the
# latter so `config` and `services` resolve. Must precede those imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prisma import Prisma

from config import settings
from services.auth import get_password_hash

ADMIN_PASSWORD = "test1234"


async def main() -> None:
    db = Prisma()
    await db.connect()
    email = settings.admin_email
    hashed = get_password_hash(ADMIN_PASSWORD)

    existing = await db.user.find_unique(where={"email": email})
    if existing:
        await db.user.update(
            where={"email": email},
            data={"hashedPassword": hashed, "isActive": True},
        )
        print(f"Admin updated: {email} (password reset, active)")
    else:
        await db.user.create(
            data={"email": email, "hashedPassword": hashed, "isActive": True}
        )
        print(f"Admin created: {email}")

    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
