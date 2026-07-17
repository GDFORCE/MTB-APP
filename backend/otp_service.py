"""OTP delivery — production providers.

SMS  : MSG91 (India). Requires a DLT-approved OTP template.
Email: SMTP (works with AWS SES, Brevo, Resend, SendGrid, Gmail app-password…).

There is no demo / fallback path: if a channel is requested but not configured,
or the provider rejects the request, we raise so the caller fails loudly instead
of silently telling the user a code was sent. Codes are never logged.
"""
import os
import ssl
import smtplib
import logging
import secrets
from email.message import EmailMessage

import requests

log = logging.getLogger("otp")

OTP_TTL_MIN = int(os.environ.get("OTP_TTL_MIN", "2"))


class OTPConfigError(RuntimeError):
    """A channel was requested but its provider isn't configured."""


class OTPDeliveryError(RuntimeError):
    """The provider accepted the request path but failed to deliver."""


def generate_code(length: int = 6) -> str:
    """Cryptographically-random numeric OTP."""
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise OTPConfigError(f"{name} is not configured")
    return val


# ── SMS via MSG91 ─────────────────────────────────────────────────────────────
def send_sms(phone: str, code: str) -> None:
    """Send `code` to `phone` (E.164, e.g. +9198…) via MSG91. Raises on failure."""
    authkey = _require("MSG91_AUTHKEY")
    template_id = _require("MSG91_TEMPLATE_ID")
    mobile = phone.lstrip("+").replace(" ", "")
    try:
        r = requests.post(
            "https://control.msg91.com/api/v5/otp",
            params={"template_id": template_id, "mobile": mobile, "otp": code},
            headers={"authkey": authkey, "Content-Type": "application/json"},
            timeout=15,
        )
    except requests.RequestException as e:
        log.error("MSG91 request failed for %s: %s", mobile, e)
        raise OTPDeliveryError("Could not reach the SMS provider") from e

    ok = r.status_code == 200
    data = {}
    try:
        data = r.json()
    except ValueError:
        ok = False
    if not ok or str(data.get("type", "")).lower() == "error":
        log.error("MSG91 rejected SMS to %s: %s %s", mobile, r.status_code, data or r.text)
        raise OTPDeliveryError("The SMS provider rejected the request")


# ── Email via SMTP ────────────────────────────────────────────────────────────
def send_email(email: str, code: str) -> None:
    """Send `code` to `email` over SMTP. Raises on failure."""
    host = _require("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("SMTP_FROM") or user
    if not sender:
        raise OTPConfigError("SMTP_FROM (or SMTP_USER) is not configured")

    msg = EmailMessage()
    msg["Subject"] = "Your My Trial Board verification code"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        f"Welcome to My Trial Board.\n\n"
        f"Your verification code is {code}.\n"
        f"It expires in {OTP_TTL_MIN} minutes.\n\n"
        f"If you did not request this, you can safely ignore this email."
    )
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ctx)
            if user:
                s.login(user, password)
            s.send_message(msg)
    except Exception as e:  # smtplib raises a family of exceptions
        log.error("SMTP send to %s failed: %s", email, e)
        raise OTPDeliveryError("Could not send the verification email") from e


def send_password_reset_email(email: str, reset_link: str, expires_minutes: int) -> None:
    """Deliver a single-use password setup/reset link without exposing it to admins."""
    host = _require("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("SMTP_FROM") or user
    if not sender:
        raise OTPConfigError("SMTP_FROM (or SMTP_USER) is not configured")

    msg = EmailMessage()
    msg["Subject"] = "Set your My Trial Board password"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        "A My Trial Board administrator requested a password setup or reset for "
        "your account.\n\n"
        f"Open this single-use link within {expires_minutes} minutes:\n"
        f"{reset_link}\n\n"
        "If you did not expect this message, contact support. Do not share this link."
    )
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(context=ctx)
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as exc:
        log.error("SMTP password-reset delivery to %s failed: %s", email, exc)
        raise OTPDeliveryError("Could not send the password reset email") from exc
