"""OTP delivery — production providers.

SMS  : MSG91 (India). Requires a DLT-approved OTP template.
Email: Brevo Transactional Email API.

There is no demo / fallback path: if a channel is requested but not configured,
or the provider rejects the request, we raise so the caller fails loudly instead
of silently telling the user a code was sent. Codes are never logged.
"""
import os
import logging
import secrets
from html import escape

import requests

log = logging.getLogger("otp")

OTP_TTL_MIN = int(os.environ.get("OTP_TTL_MIN", "10"))


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


# ── Email via Brevo Transactional Email API ───────────────────────────────────
BREVO_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"


def _send_brevo_email(*, to_email: str, subject: str, text_content: str, html_content: str) -> str:
    """Send one transactional email through Brevo and return its message ID."""
    api_key = _require("BREVO_API_KEY")
    sender_email = _require("BREVO_FROM_EMAIL")
    sender_name = os.environ.get("BREVO_FROM_NAME", "My Trial Board")
    payload = {
        "sender": {"email": sender_email, "name": sender_name},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": text_content,
        "htmlContent": html_content,
        "tags": ["mtb-transactional"],
    }
    try:
        response = requests.post(
            BREVO_EMAIL_URL,
            headers={
                "api-key": api_key,
                "accept": "application/json",
                "content-type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["messageId"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        log.error("Brevo email send to %s failed: %s", to_email, exc)
        raise OTPDeliveryError("Could not send the email") from exc


def send_email(email: str, code: str) -> None:
    """Send `code` to `email` through the Brevo API. Raises on failure."""
    _send_brevo_email(
        to_email=email,
        subject="Your My Trial Board verification code",
        text_content=(
            f"Welcome to My Trial Board.\n\nYour verification code is {code}.\n"
            f"It expires in {OTP_TTL_MIN} minutes.\n\n"
            "If you did not request this, you can safely ignore this email."
        ),
        html_content=(
            "<p>Welcome to My Trial Board.</p>"
            f"<p>Your verification code is <strong>{code}</strong>.</p>"
            f"<p>It expires in {OTP_TTL_MIN} minutes.</p>"
            "<p>If you did not request this, you can safely ignore this email.</p>"
        ),
    )


def send_invitation_email(email: str, invite_link: str, recipient_name: str = "") -> None:
    """Deliver a manual, time-limited My Trial Board invitation code."""
    name = escape(recipient_name.strip()) if recipient_name else "there"
    invite_code = invite_link.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    safe_code = escape(invite_code)
    _send_brevo_email(
        to_email=email,
        subject="You're invited to My Trial Board",
        text_content=(
            f"Hi {recipient_name.strip() or 'there'},\n\n"
            "You've been invited to join My Trial Board.\n\n"
            f"Your invitation code: {invite_code}\n\n"
            "Open My Trial Board, choose “Join with an invite”, and enter this code to start registration.\n\n"
            "This invitation expires in 3 days. If you did not expect it, you can safely ignore this email."
        ),
        html_content=(
            "<div style=\"margin:0;padding:28px 16px;background:#F4E5D3;"
            "font-family:Arial,sans-serif;color:#2E1B33\">"
            "<div style=\"max-width:560px;margin:auto;background:#FEFAF1;border-radius:18px;"
            "border:1px solid #E6D6C5;padding:28px\">"
            "<div style=\"color:#A6213F;font-size:13px;font-weight:700;letter-spacing:1px\">"
            "MY TRIAL BOARD</div>"
            f"<h1 style=\"margin:12px 0 8px;font-size:24px;color:#2E1B33\">Hi {name},</h1>"
            "<p style=\"font-size:16px;line-height:24px\">You've been invited to join My Trial Board.</p>"
            "<p style=\"font-size:14px;line-height:21px\">Open the app, select "
            "<strong>Join with an invite</strong>, then enter this code:</p>"
            f"<div style=\"margin:20px 0;padding:16px;background:#FDE8E1;border:1px dashed #A6213F;"
            f"border-radius:12px;color:#A6213F;font-size:22px;font-weight:700;"
            f"letter-spacing:2px;text-align:center\">{safe_code}</div>"
            "<p style=\"font-size:14px;line-height:21px;color:#7B5F73\">This invitation expires in 3 days. "
            "If you did not expect it, you can safely ignore this email.</p>"
            "</div></div>"
        ),
    )


def send_password_reset_email(email: str, reset_link: str, expires_minutes: int) -> None:
    """Deliver a single-use password setup/reset link without exposing it to admins."""
    _send_brevo_email(
        to_email=email,
        subject="Set your My Trial Board password",
        text_content=(
            "A My Trial Board administrator requested a password setup or reset for your account.\n\n"
            f"Open this single-use link within {expires_minutes} minutes:\n{reset_link}\n\n"
            "If you did not expect this message, contact support. Do not share this link."
        ),
        html_content=(
            "<p>A My Trial Board administrator requested a password setup or reset for your account.</p>"
            f"<p><a href=\"{reset_link}\">Set your password</a></p>"
            f"<p>This single-use link expires in {expires_minutes} minutes.</p>"
            "<p>If you did not expect this message, contact support. Do not share this link.</p>"
        ),
    )
