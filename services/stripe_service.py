"""
Stripe service for creating payment links for new contacts.
"""
import httpx, os
from dotenv import load_dotenv
load_dotenv()
from config import STRIPE_SECRET_KEY


def _get_base_url() -> str:
    """
    Resolve the public base URL for Stripe success/cancel redirects.
    Priority:
      1. BASE_URL env var  (e.g. https://payminimumtax.com)
      2. PUBLIC_HOST env var (e.g. pmtax.duckdns.org) → prefixed with https://
      3. Hardcoded fallback
    """
    base = os.getenv("BASE_URL", "").strip().rstrip("/")
    if base:
        return base

    host = os.getenv("PUBLIC_HOST", "").strip().rstrip("/")
    if host:
        if host.startswith(("http://", "https://")):
            return host
        return f"https://{host}"

    return "https://payminimumtax.com"


async def create_stripe_payment_link(
    customer_email: str,
    customer_name: str,
    booking_slot: str,
    call_summary: str,
    amount_cents: int,
    calendar_id: str = "",
    customer_phone: str = "",
) -> str:
    """
    Creates a Stripe Checkout payment link for a new contact.
    Embeds all booking metadata so it's fully accessible in the webhook after payment.
    Returns the Stripe Checkout URL.
    """
    if not STRIPE_SECRET_KEY:
        raise ValueError("STRIPE_SECRET_KEY not configured")
    if amount_cents <= 0:
        raise ValueError("Payment amount must be greater than zero")

    url = "https://api.stripe.com/v1/checkout/sessions"
    headers = {
        "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    # Truncate summary to stay within Stripe metadata 500-char limit per value
    summary_short = call_summary[:490] if call_summary else ""

    data = {
        "mode": "payment",
        "customer_email": customer_email,
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][product_data][name]": "AI Receptionist Booking Fee",
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][quantity]": "1",
        "success_url": f"{_get_base_url()}/booking-success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url":  f"{_get_base_url()}/booking-cancelled?session_id={{CHECKOUT_SESSION_ID}}",
        # All metadata fields for the webhook to consume
        "metadata[customer_name]": customer_name,
        "metadata[customer_email]": customer_email,
        "metadata[customer_phone]": customer_phone,
        "metadata[booking_slot]": booking_slot,
        "metadata[calendar_id]": calendar_id,
        "metadata[call_summary]": summary_short,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, headers=headers, data=data)
        if response.status_code != 200:
            raise Exception(f"Stripe error {response.status_code}: {response.text}")
        session = response.json()
        print(f"💳 [Stripe] Created payment link: {session['url']}")
        return session["url"]
