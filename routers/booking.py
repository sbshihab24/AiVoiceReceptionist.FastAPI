import logging
logger = logging.getLogger(__name__)

"""
AI Post-Call Booking Router.

Handles the full booking flow after an AI call:
 - Step 1: Search existing contact in GHL by phone/email
 - Step 2a: Existing contact -> book appointment + send confirmation email
 - Step 2b: New contact -> send Stripe payment link -> webhook creates contact + booking
"""
import json
import hmac
import hashlib
import time
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel, EmailStr
from typing import Optional
from config import STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, GHL_LOCATION_ID
from services.ghl_search import search_contact_by_phone_or_email
from services.ghl import add_contact, create_appointment, get_all_appointments
from services.email_service import send_booking_confirmation, send_stripe_payment_link
from services.stripe_service import create_stripe_payment_link
from schemas import ContactCreate, AppointmentCreate
from services.booking_service import (
    OFFICE_TIMEZONE,
    get_calendar_price_by_id,
    validate_office_slot,
    _timezone_from_offset,
)

router = APIRouter(
    prefix="/api/booking",
    tags=["AI Booking Flow"]
)


# ─────────────────────────────────────────────
# Request schema
# ─────────────────────────────────────────────
class BookingRequest(BaseModel):
    phone: str
    email: EmailStr
    name: Optional[str] = "Caller"           # Default name if unknown
    calendar_id: str                          # GHL Calendar ID to book into
    booking_slot: str                         # ISO datetime: "2026-05-10T10:00:00Z"
    timezone: Optional[str] = OFFICE_TIMEZONE
    call_summary: str                         # AI-generated summary of the call
    title: Optional[str] = "Appointment After AI Call"


# ─────────────────────────────────────────────
# Get Available Slots endpoint
# ─────────────────────────────────────────────
@router.get("/slots")
async def get_slots(calendar_id: str, timezone: str = OFFICE_TIMEZONE):
    import time
    import httpx
    from config import GHL_BASE_URL
    from services.ghl import get_ghl_headers
    
    url = f"{GHL_BASE_URL}/appointments/slots"
    now_ms = int(time.time() * 1000)
    end_ms = now_ms + (7 * 24 * 60 * 60 * 1000) # Next 7 days
    
    params = {
        "calendarId": calendar_id,
        "startDate": now_ms,
        "endDate": end_ms,
        "timezone": timezone
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, headers=get_ghl_headers())
        if resp.status_code == 200:
            return resp.json()
        else:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

# ─────────────────────────────────────────────
# Get Appointments endpoint
# ─────────────────────────────────────────────
@router.get("/appointments")
async def list_appointments(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    specific_day: Optional[str] = None,   # e.g. "today" or "2026-05-11"
    this_week: Optional[bool] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    """
    List GHL appointments with optional filters.
    - ?specific_day=today        → today's appointments
    - ?specific_day=2026-05-11  → specific date
    - ?this_week=true            → this week's appointments
    - ?email=...&phone=...       → filter by contact
    """
    appointments = await get_all_appointments(
        email=email,
        phone=phone,
        start_time=start_time,
        end_time=end_time,
        specific_day=specific_day,
        this_week=this_week,
    )
    return {
        "count": len(appointments),
        "appointments": appointments,
    }


# ─────────────────────────────────────────────
# Main booking endpoint
# ─────────────────────────────────────────────
@router.post("/process")
async def process_booking(req: BookingRequest):
    """
    Main booking processor after an AI call.

    Existing contact  -> Book appointment in GHL + send confirmation email
    New contact       -> Send Stripe payment link -> Booking created after payment
    """

    logger.info(f"\n📋 [Booking] Processing booking for phone={req.phone}, email={req.email}")
    
    final_booking_slot = req.booking_slot
    is_valid_slot, slot_error = validate_office_slot(final_booking_slot)
    if not is_valid_slot:
        raise HTTPException(status_code=400, detail=slot_error)

    # ── Step 1: Search contact in GHL ──────────────────────────────────────
    existing_contact = await search_contact_by_phone_or_email(
        phone=req.phone, email=str(req.email)
    )

    if existing_contact:
        contact_id = existing_contact.get("id")
        contact_name = (
            existing_contact.get("name")
            or f"{existing_contact.get('firstName', '')} {existing_contact.get('lastName', '')}".strip()
            or req.name
        )

        logger.info(f"✅ [Booking] Existing contact found: {contact_id} ({contact_name})")

        # ── Step 2a: Book appointment in GHL ──────────────────────────────
        # Ensure timezone matches the slot format (GHL requirement)
        ghl_timezone = req.timezone
        if final_booking_slot.endswith("Z"):
            ghl_timezone = "UTC"
        elif "-04:00" in final_booking_slot:
            ghl_timezone = "America/New_York"
        elif "-05:00" in final_booking_slot:
            ghl_timezone = "America/New_York"
        elif "+06:00" in final_booking_slot:
            ghl_timezone = "Asia/Dhaka"

        appointment_data = AppointmentCreate(
            contactId=contact_id,
            calendarId=req.calendar_id,
            selectedTimezone=ghl_timezone,
            selectedSlot=final_booking_slot,
            title=req.title,
            notes=req.call_summary,
            status="booked",
        )
        appointment = await create_appointment(appointment_data)
        appointment_id = appointment.get("id", "N/A")
        logger.info(f"📅 [Booking] Appointment created in GHL: {appointment_id}")

        # Parse slot for readable display
        try:
            dt = datetime.fromisoformat(final_booking_slot.replace("Z", "+00:00"))
            booking_date = dt.strftime("%d %B %Y")
            booking_time = dt.strftime("%I:%M %p")
        except Exception:
            booking_date = final_booking_slot[:10]
            booking_time = final_booking_slot[11:16]

        # ── Step 2b: Send confirmation email ──────────────────────────────
        await send_booking_confirmation(
            to_email=str(req.email),
            contact_name=contact_name,
            booking_date=booking_date,
            booking_time=booking_time,
            call_summary=req.call_summary,
        )
        logger.info(f"📧 [Booking] Confirmation email sent to {req.email}")

        return {
            "status": "confirmed",
            "type": "existing_contact",
            "contact_id": contact_id,
            "appointment_id": appointment_id,
            "summary": _build_summary(
                is_new=False,
                name=contact_name,
                email=str(req.email),
                slot=final_booking_slot,
                appointment_id=appointment_id,
                call_summary=req.call_summary,
            ),
        }

    else:
        # ── Step 2b: New contact — create Stripe payment link ──────────────
        logger.info(f"🆕 [Booking] New contact. Creating Stripe payment link...")

        payment_url = await create_stripe_payment_link(
            customer_email=str(req.email),
            customer_name=req.name,
            booking_slot=final_booking_slot,
            call_summary=req.call_summary,
            calendar_id=req.calendar_id,
            customer_phone=req.phone,
            amount_cents=get_calendar_price_by_id(req.calendar_id) * 100,
        )

        await send_stripe_payment_link(
            to_email=str(req.email),
            contact_name=req.name,
            payment_url=payment_url,
            call_summary=req.call_summary,
        )
        logger.info(f"💳 [Booking] Stripe payment link sent to {req.email}")

        return {
            "status": "payment_required",
            "type": "new_contact",
            "payment_url": payment_url,
            "summary": _build_summary(
                is_new=True,
                name=req.name,
                email=str(req.email),
                slot=req.booking_slot,
                payment_url=payment_url,
                call_summary=req.call_summary,
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Stripe Webhook — fires after payment is done
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/stripe-webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
):
    """
    Listens for Stripe 'checkout.session.completed' events.

    On successful payment:
      Step 1 -> Create GHL contact
      Step 2 -> Book appointment with AI call summary as notes
      Step 3 -> Send confirmation email
    """

    # ── Read raw body bytes (required for HMAC signature verification) ────
    body_bytes = await request.body()

    # ── Verify Stripe webhook signature ───────────────────────────────────
    if STRIPE_WEBHOOK_SECRET:
        if not stripe_signature:
            raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")
        try:
            # Parse header: "t=1234567,v1=abc123def..."
            sig_parts = {}
            for part in stripe_signature.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    sig_parts[k.strip()] = v.strip()

            ts = sig_parts.get("t", "")
            v1 = sig_parts.get("v1", "")

            if not ts or not v1:
                raise HTTPException(status_code=400, detail="Malformed Stripe-Signature header")

            # Reject stale events (>5 minutes old)
            if abs(int(ts) - int(time.time())) > 300:
                raise HTTPException(status_code=400, detail="Stripe webhook event is too old — possible replay attack")

            # Compute HMAC-SHA256
            signed_payload = f"{ts}.{body_bytes.decode('utf-8')}"
            expected = hmac.new(
                STRIPE_WEBHOOK_SECRET.encode("utf-8"),
                signed_payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(expected, v1):
                raise HTTPException(status_code=400, detail="Stripe signature mismatch — unauthorized request")

            logger.info("🔐 [Stripe Webhook] Signature verified.")

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Signature error: {exc}")
    else:
        logger.info("⚠️  [Stripe Webhook] STRIPE_WEBHOOK_SECRET not set — skipping signature check!")

    # ── Parse event JSON ──────────────────────────────────────────────────
    try:
        event = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse Stripe event JSON")

    event_type = event.get("type", "unknown")
    logger.info(f"\n📦 [Stripe Webhook] Event received: {event_type}")

    # ── Only handle completed checkout sessions ────────────────────────────
    if event_type != "checkout.session.completed":
        logger.info(f"   Ignored (not a completed checkout)")
        return {"received": True, "processed": False}

    session = event["data"]["object"]
    meta = session.get("metadata", {})
    stripe_session_id = session.get("id", "")

    # ── Extract metadata embedded when payment link was created ───────────
    customer_name  = meta.get("customer_name") or "New Customer"
    customer_email = meta.get("customer_email") or session.get("customer_email", "")
    customer_phone = meta.get("customer_phone", "")
    booking_slot   = meta.get("booking_slot", "")
    calendar_id    = meta.get("calendar_id", "")
    call_summary   = meta.get("call_summary", "")

    logger.info(f"💰 [Stripe Webhook] Payment confirmed!")
    logger.info(f"   👤 Name:    {customer_name}")
    logger.info(f"   📧 Email:   {customer_email}")
    logger.info(f"   📅 Slot:    {booking_slot}")
    logger.info(f"   💳 Session: {stripe_session_id}")

    if not customer_email:
        raise HTTPException(status_code=400, detail="No customer_email in Stripe session/metadata")

    if not booking_slot:
        raise HTTPException(status_code=400, detail="No booking_slot in Stripe metadata — cannot book appointment")

    # ────────────────────────────────────────────────────────────────────
    # Step 1: Create contact in GHL
    # ────────────────────────────────────────────────────────────────────
    logger.info(f"\n📋 [Step 1/3] Finding or creating GHL contact for '{customer_name}' <{customer_email}>...")
    try:
        existing_contact = await search_contact_by_phone_or_email(
            phone=customer_phone,
            email=customer_email,
        )
        if existing_contact:
            contact_id = existing_contact.get("id") or existing_contact.get("contactId", "unknown")
            logger.info(f"✅ [Step 1/3] Existing GHL contact found — ID: {contact_id}")
        else:
            contact_resp = await add_contact(ContactCreate(
                email=customer_email,
                name=customer_name,
                phone=customer_phone or None,
                source="AI Call + Stripe Payment",
                tags=["ai-call", "new-client", "stripe-paid"],
            ))
            # GHL v1 API returns {"contact": {id, ...}} or the object directly
            contact_obj = contact_resp.get("contact") or contact_resp
            contact_id = contact_obj.get("id") or contact_obj.get("contactId", "unknown")
            logger.info(f"✅ [Step 1/3] GHL contact created — ID: {contact_id}")
    except Exception as exc:
        logger.info(f"🔴 [Step 1/3] Failed to find/create GHL contact: {exc}")
        raise HTTPException(status_code=500, detail=f"GHL contact find/create failed: {exc}")

    # ────────────────────────────────────────────────────────────────────
    # Step 2: Book appointment in GHL
    # ────────────────────────────────────────────────────────────────────
    appointment_id = "N/A"
    if calendar_id:
        logger.info(f"\n📅 [Step 2/3] Booking appointment in GHL calendar '{calendar_id}'...")
        try:
            appt_resp = await create_appointment(AppointmentCreate(
                contactId=contact_id,
                calendarId=calendar_id,
                selectedTimezone=_timezone_from_offset(booking_slot),
                selectedSlot=booking_slot,
                title="Appointment After AI Call (Payment Completed)",
                notes=(
                    f"Stripe Session ID: {stripe_session_id}\n\n"
                    f"AI Call Summary:\n{call_summary}"
                ),
                status="booked",
            ))
            appointment_id = appt_resp.get("id", "N/A")
            logger.info(f"✅ [Step 2/3] GHL appointment booked — ID: {appointment_id}")
        except Exception as exc:
            # Non-fatal — still send email
            logger.info(f"🔴 [Step 2/3] Appointment booking failed (non-fatal): {exc}")
    else:
        logger.info("⚠️  [Step 2/3] calendar_id missing from metadata — skipping appointment creation")

    # ────────────────────────────────────────────────────────────────────
    # Step 3: Send confirmation email
    # ────────────────────────────────────────────────────────────────────
    logger.info(f"\n📧 [Step 3/3] Sending confirmation email to {customer_email}...")
    try:
        dt = datetime.fromisoformat(booking_slot.replace("Z", "+00:00"))
        booking_date = dt.strftime("%d %B %Y")
        booking_time = dt.strftime("%I:%M %p")
    except Exception:
        booking_date = booking_slot[:10] if len(booking_slot) >= 10 else booking_slot
        booking_time = booking_slot[11:16] if len(booking_slot) >= 16 else ""

    try:
        await send_booking_confirmation(
            to_email=customer_email,
            contact_name=customer_name,
            booking_date=booking_date,
            booking_time=booking_time,
            call_summary=call_summary,
        )
        logger.info(f"✅ [Step 3/3] Confirmation email sent to {customer_email}")
    except Exception as exc:
        logger.info(f"🔴 [Step 3/3] Email sending failed (non-fatal): {exc}")

    # ── Print summary to terminal ─────────────────────────────────────────
    summary = (
        f"\n{'='*60}\n"
        f"Payment Confirmed — Booking Complete!\n"
        f"{'='*60}\n"
        f"Name:             {customer_name}\n"
        f"Email:            {customer_email}\n"
        f"Phone:            {customer_phone or 'N/A'}\n"
        f"Date:             {booking_date}\n"
        f"Time:             {booking_time}\n"
        f"GHL Contact ID:   {contact_id}\n"
        f"Appointment ID:   {appointment_id}\n"
        f"Stripe Session:   {stripe_session_id}\n"
        f"{'='*60}\n"
        f"Call Summary:\n{call_summary}\n"
        f"{'='*60}\n"
    )
    logger.info(summary)

    return {
        "received": True,
        "processed": True,
        "contact_id": contact_id,
        "appointment_id": appointment_id,
        "summary": summary.strip(),
    }


# ─────────────────────────────────────────────
# Summary builder
# ─────────────────────────────────────────────
def _build_summary(
    is_new: bool,
    name: str,
    email: str,
    slot: str,
    call_summary: str,
    appointment_id: str = "",
    payment_url: str = "",
) -> str:
    try:
        dt = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        readable_slot = dt.strftime("%d %B %Y, %I:%M %p")
    except Exception:
        readable_slot = slot

    if not is_new:
        return (
            f"Booking Confirmed!\n\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Appointment: {readable_slot}\n"
            f"Appointment ID: {appointment_id}\n\n"
            f"Call Summary:\n{call_summary}\n\n"
            f"A confirmation email has been sent."
        )
    else:
        return (
            f"New Contact — Payment Link Sent\n\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Requested Slot: {readable_slot}\n\n"
            f"Call Summary:\n{call_summary}\n\n"
            f"Payment Link: {payment_url}\n\n"
            f"Once payment is complete, the contact and appointment will be automatically created in GHL."
        )
