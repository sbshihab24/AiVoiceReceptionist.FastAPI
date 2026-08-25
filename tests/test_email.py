"""
Detailed SMTP email delivery diagnostic script.
Usage:
  docker compose exec fastapi python tests/test_email.py <recipient_email>

This script tests connection, login, and message delivery to the SMTP server
using verbose debug logging to diagnose where the email is getting stuck.
"""
import sys
import os
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Add project root to path so we can import services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM

def test_smtp_sync(to_email: str):
    print("=" * 60)
    print("📧 REBA AI EMAIL DELIVERY DIAGNOSTIC")
    print("=" * 60)
    print(f"SMTP Host:      {SMTP_HOST}")
    print(f"SMTP Port:      {SMTP_PORT}")
    print(f"SMTP User:      {SMTP_USER}")
    print(f"SMTP From:      {SMTP_FROM}")
    print(f"Recipient:      {to_email}")
    print("-" * 60)

    # 1. Construct MIME messages
    # Test 1: Plain Text
    msg_plain = MIMEMultipart("alternative")
    msg_plain["Subject"] = "🔍 Test 1: Plain Text (Diagnostic)"
    msg_plain["From"]    = SMTP_FROM
    msg_plain["To"]      = to_email
    msg_plain.attach(MIMEText("This is a simple plain text test email to verify basic SMTP relay functionality.", "plain", "utf-8"))

    # Test 2: HTML with Signup Link
    msg_link = MIMEMultipart("alternative")
    msg_link["Subject"] = "🔗 Test 2: HTML Portal Link (Diagnostic)"
    msg_link["From"]    = SMTP_FROM
    msg_link["To"]      = to_email
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; padding: 20px; background: #f4f4f4;">
        <div style="max-width: 500px; margin: auto; background: white; padding: 24px; border-radius: 8px;">
          <h2>Portal Sign-Up Request</h2>
          <p>Please click the button below to register on our portal:</p>
          <p style="text-align: center; margin: 20px 0;">
            <a href="https://portal.payminimumtax.com/signup" 
               style="background: #6B3FA0; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold;">
               🔗 Sign Up Now
            </a>
          </p>
        </div>
      </body>
    </html>
    """
    msg_link.attach(MIMEText(html_content, "html", "utf-8"))

    # 2. Run STARTTLS test (Default)
    print("\n🚀 Starting STARTTLS Test (Port 587)...")
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        server.set_debuglevel(2)  # Highly verbose
        
        print("  Connecting and sending EHLO...")
        server.ehlo()
        
        print("  Starting TLS handshake...")
        server.starttls()
        server.ehlo()
        
        print(f"  Logging in as {SMTP_USER}...")
        server.login(SMTP_USER, SMTP_PASSWORD)
        
        print("  Sending Plain Text Email...")
        server.sendmail(SMTP_FROM, to_email, msg_plain.as_string())
        print("  ✅ Plain Text Email accepted by SMTP server!")
        
        print("  Sending HTML Link Email...")
        server.sendmail(SMTP_FROM, to_email, msg_link.as_string())
        print("  ✅ HTML Link Email accepted by SMTP server!")
        
        server.quit()
        print("  Connection closed cleanly.")
        print("\n🎉 STARTTLS TEST SUCCESSFUL! Both emails were accepted by the SMTP server.")
    except Exception as e:
        print(f"\n❌ STARTTLS TEST FAILED: {type(e).__name__}: {e}")
        
    # 3. Run SSL Test (Port 465)
    print("\n🚀 Starting SSL Test (Port 465)...")
    try:
        server = smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=20)
        server.set_debuglevel(2)
        
        print("  Connecting and sending EHLO...")
        server.ehlo()
        
        print(f"  Logging in as {SMTP_USER}...")
        server.login(SMTP_USER, SMTP_PASSWORD)
        
        print("  Sending Plain Text Email...")
        server.sendmail(SMTP_FROM, to_email, msg_plain.as_string())
        print("  ✅ Plain Text Email accepted by SMTP server!")
        
        server.quit()
        print("  Connection closed cleanly.")
        print("\n🎉 SSL TEST SUCCESSFUL!")
    except Exception as e:
        print(f"\n❌ SSL TEST FAILED: {type(e).__name__}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Error: Recipient email address required.")
        print("Usage: docker compose exec fastapi python tests/test_email.py <recipient_email>")
        sys.exit(1)
        
    recipient = sys.argv[1]
    test_smtp_sync(recipient)
