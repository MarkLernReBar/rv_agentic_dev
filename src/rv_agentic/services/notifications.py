import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional


logger = logging.getLogger(__name__)


def send_run_notification(
    *,
    run_id: str,
    subject: str,
    body: str,
    to_email: Optional[str] = None,
) -> None:
    """Best-effort email notification for a pm_pipeline run.

    Uses SMTP_* and EMAIL_FROM / NOTIFICATION_EMAIL env vars when present.
    Fails open: logs and returns on any error instead of raising.
    """

    host = os.getenv("SMTP_HOST")
    port_raw = os.getenv("SMTP_PORT", "587")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("EMAIL_FROM") or user
    recipient = to_email or os.getenv("NOTIFICATION_EMAIL") or user

    if not host or not user or not password or not from_email or not recipient:
        logger.info(
            "Email not sent for run %s; SMTP/recipient configuration incomplete",
            run_id,
        )
        return

    try:
        port = int(port_raw)
    except ValueError:
        port = 587

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            # Use STARTTLS when available; ignore failures gracefully.
            try:
                smtp.starttls()
            except Exception:
                pass
            smtp.login(user, password)
            smtp.send_message(msg)
        logger.info(
            "Sent notification email for run %s to %s with subject=%r",
            run_id,
            recipient,
            subject,
        )
    except Exception as exc:
        logger.warning(
            "Failed to send notification email for run %s: %s",
            run_id,
            exc,
        )

