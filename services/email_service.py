"""
Email notification service using SMTP.
Sends confirmation and Stripe payment emails to callers.
"""
import smtplib
import asyncio
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM

logger = logging.getLogger(__name__)


def _send_email_sync(to_email: str, subject: str, html_body: str) -> bool:
    """Synchronous SMTP email send (run in thread executor).
    Tries STARTTLS on the configured port first, then SSL on 465,
    then plain SMTP — covering all common hosting configurations.
    Returns True on success.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    last_error = None

    # ── Attempt 1: STARTTLS on configured port (587 typical) ──────────────
    try:
        logger.info(f"📧 [Email] Attempting STARTTLS on {SMTP_HOST}:{SMTP_PORT}...")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        logger.info(f"✅ [Email] Sent (STARTTLS) '{subject}' → {to_email}")
        return True
    except Exception as e:
        last_error = e
        logger.warning(f"⚠️ [Email] STARTTLS attempt failed ({type(e).__name__}): {e}")

    # ── Attempt 2: SSL/TLS on port 465 ───────────────────────────────────
    try:
        logger.info(f"📧 [Email] Retrying with SSL on {SMTP_HOST}:465...")
        with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=20) as server:
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        logger.info(f"✅ [Email] Sent (SSL) '{subject}' → {to_email}")
        return True
    except Exception as e:
        last_error = e
        logger.warning(f"⚠️ [Email] SSL attempt failed ({type(e).__name__}): {e}")

    # ── Attempt 3: Plain SMTP on configured port (last resort) ───────────
    try:
        logger.info(f"📧 [Email] Retrying plain SMTP on {SMTP_HOST}:{SMTP_PORT}...")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        logger.info(f"✅ [Email] Sent (plain) '{subject}' → {to_email}")
        return True
    except Exception as e:
        last_error = e
        logger.error(f"❌ [Email] All SMTP attempts failed for {to_email}: {type(e).__name__}: {e}")

    return False


async def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Async wrapper for sending emails. Returns True if delivered, False on failure."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _send_email_sync, to_email, subject, html_body)



async def send_booking_confirmation(
    to_email: str,
    contact_name: str,
    booking_date: str,
    booking_time: str,
    call_summary: str,
):
    """Send appointment confirmation email to an existing contact."""
    subject = "✅ Your Appointment is Confirmed — Pay Minimum Tax"
    html_body = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px;">
  <div style="max-width: 600px; margin: auto; background: white; border-radius: 12px; padding: 32px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
    <h2 style="color: #6B3FA0;">🎉 Appointment Confirmed!</h2>
    <p>Dear <strong>{contact_name}</strong>,</p>
    <p>Your appointment with Pay Minimum Tax has been successfully booked.</p>

    <div style="background: #f0eaff; border-radius: 8px; padding: 16px; margin: 20px 0;">
      <p><strong>📅 Date:</strong> {booking_date}</p>
      <p><strong>🕐 Time:</strong> {booking_time}</p>
    </div>

    <h3 style="color: #6B3FA0;">📞 Call Summary:</h3>
    <div style="background: #f9f9f9; border-left: 4px solid #6B3FA0; padding: 12px; border-radius: 4px;">
      <p style="color: #444;">{call_summary}</p>
    </div>

    <p style="margin-top: 24px; color: #888; font-size: 12px;">
      Thank you for choosing Pay Minimum Tax. If you have any questions, please feel free to contact us.
    </p>
  </div>
</body>
</html>
"""
    return await send_email(to_email, subject, html_body)


async def send_stripe_payment_link(
    to_email: str,
    contact_name: str,
    payment_url: str,
    call_summary: str,
):
    """Send a Stripe payment link email to a new contact."""
    subject = "💳 Complete Your Booking Payment — Pay Minimum Tax"
    html_body = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px;">
  <div style="max-width: 600px; margin: auto; background: white; border-radius: 12px; padding: 32px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
    <h2 style="color: #6B3FA0;">👋 We Received Your Appointment Request!</h2>
    <p>Dear <strong>{contact_name}</strong>,</p>
    <p>You recently spoke with our AI receptionist. To confirm your appointment, please complete the payment using the link below.</p>

    <h3 style="color: #6B3FA0;">📞 Call Summary:</h3>
    <div style="background: #f9f9f9; border-left: 4px solid #6B3FA0; padding: 12px; border-radius: 4px; margin-bottom: 24px;">
      <p style="color: #444;">{call_summary}</p>
    </div>

    <div style="text-align: center; margin: 28px 0;">
      <a href="{payment_url}"
         style="background: linear-gradient(135deg, #6B3FA0, #9B59B6); color: white; padding: 14px 32px;
                border-radius: 50px; text-decoration: none; font-size: 16px; font-weight: bold;">
        💳 Pay Now
      </a>
    </div>

    <p style="color: #888; font-size: 12px;">
      Once payment is complete, your appointment will be automatically added to our system and a confirmation email will be sent to you.
    </p>
  </div>
</body>
</html>
"""
    return await send_email(to_email, subject, html_body)

async def send_otp_email(to_email: str, otp: str):
    """Send a password reset OTP email."""
    subject = f"🔑 Your OTP for Password Reset: {otp}"
    html_body = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px;">
  <div style="max-width: 500px; margin: auto; background: white; border-radius: 12px; padding: 32px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); text-align: center;">
    <h2 style="color: #6B3FA0;">Password Reset OTP</h2>
    <p>You requested an OTP to reset your password. Use the code below to proceed:</p>
    
    <div style="background: #f0eaff; border-radius: 8px; padding: 20px; margin: 24px 0; font-size: 32px; font-weight: bold; color: #6B3FA0; letter-spacing: 5px;">
      {otp}
    </div>

    <p style="color: #888; font-size: 14px;">
      This OTP is valid for 10 minutes. If you did not request this, please ignore this email.
    </p>
  </div>
</body>
</html>
"""
    await send_email(to_email, subject, html_body)
