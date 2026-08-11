"""
Email delivery via SMTP (configured for Gmail by default).

When SMTP credentials are not configured (settings.email_enabled is False),
messages are logged to the console instead of sent — this lets the OTP flow be
developed and tested without real credentials.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from config import settings

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Falls back to logging if SMTP is not configured."""
    if not settings.email_enabled:
        logger.warning(
            "SMTP not configured — email NOT sent. To=%s | Subject=%s\n%s",
            to,
            subject,
            body,
        )
        return

    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.starttls(context=context)
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    logger.info("Sent email to %s (subject: %s)", to, subject)


def send_otp_email(to: str, code: str) -> None:
    """Send the signup verification OTP."""
    subject = "Your SentinelAI verification code"
    body = (
        f"Welcome to SentinelAI!\n\n"
        f"Your email verification code is: {code}\n\n"
        f"It expires in {settings.otp_expiry_minutes} minutes.\n"
        f"If you didn't sign up, you can ignore this email."
    )
    send_email(to, subject, body)


def send_reset_email(to: str, code: str) -> None:
    """Send a password-reset code."""
    subject = "Your SentinelAI password reset code"
    body = (
        f"You requested a password reset.\n\n"
        f"Your reset code is: {code}\n\n"
        f"It expires in {settings.otp_expiry_minutes} minutes.\n"
        f"If you didn't request this, you can ignore this email."
    )
    send_email(to, subject, body)
