"""
Email OTP generation and verification.

OTP codes are stored in an in-memory dict keyed by email — suitable for
development / single-worker deployments. For production, back this with Redis
or a database table so codes survive restarts and work across workers.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from config import settings


@dataclass
class OtpRecord:
    code_hash: str
    expires_at: datetime
    attempts: int = 0


# email (lowercased) -> OtpRecord
_store: dict[str, OtpRecord] = {}

MAX_ATTEMPTS = 5


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def generate_otp(email: str) -> str:
    """Create and store a fresh 6-digit OTP for the email; returns the code."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    _store[email.lower()] = OtpRecord(
        code_hash=_hash(code),
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=settings.otp_expiry_minutes),
    )
    return code


def verify_otp(email: str, code: str) -> tuple[bool, str]:
    """
    Validate a code for an email.

    Returns (ok, message). On success the code is consumed (single-use).
    """
    record = _store.get(email.lower())
    if record is None:
        return False, "No verification code found. Please request a new one."
    if datetime.now(timezone.utc) > record.expires_at:
        _store.pop(email.lower(), None)
        return False, "Code expired. Please request a new one."
    if record.attempts >= MAX_ATTEMPTS:
        _store.pop(email.lower(), None)
        return False, "Too many attempts. Please request a new code."

    record.attempts += 1
    if _hash(code) != record.code_hash:
        return False, "Incorrect code."

    _store.pop(email.lower(), None)  # consume on success
    return True, "Verified."
