"""
Quick targeted test: test_calendar slot fetch + booking with a REAL available slot.
"""
import asyncio, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dotenv import load_dotenv
load_dotenv()

from services.booking_service import get_slots, book_appointment


async def main():
    print("\n" + "=" * 60)
    print("  TEST CALENDAR — Fetch Slots + Live Booking Check")
    print("=" * 60)

    # ── Step 1: Fetch real slots ──────────────────────────────────
    print("\n📅 STEP 1: get_slots(test_calendar)...")
    slot_result = await get_slots(calendar_type="test_calendar")
    status = slot_result.get("status")
    print(f"   API status   : {status}")

    slots_dict = slot_result.get("available_slots", {})
    raw_slots  = slot_result.get("raw_slots", [])

    if not slots_dict:
        print("   ❌ No slots returned — cannot proceed to booking test.")
        print(f"   Detail: {slot_result.get('message', slot_result)}")
        return

    print(f"   Dates with slots : {list(slots_dict.keys())}")
    for date, times in slots_dict.items():
        print(f"     {date}: {times}")

    # ── Step 2: Pick first real available slot ────────────────────
    # Unwrap: each date value is {"slots": [...]} (ISO strings)
    def unwrap(val):
        if isinstance(val, dict):
            return val.get("slots", [])
        return val if isinstance(val, list) else []

    # Collect ALL slots across all dates, filter to 10 AM–4 PM ET (our validator)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    OFFICE_TZ = ZoneInfo("America/New_York")

    valid_slots = []
    for date_key, val in slots_dict.items():
        for s in unwrap(val):
            try:
                dt = datetime.fromisoformat(s).astimezone(OFFICE_TZ)
                if 10 <= dt.hour < 16:
                    valid_slots.append(s)
            except Exception:
                pass

    if not valid_slots:
        print("   ❌ No slots found within 10 AM–4 PM ET office hours.")
        return

    print(f"\n   Valid (10 AM–4 PM ET) slots available: {len(valid_slots)}")
    print(f"   First few: {valid_slots[:5]}")

    # ── Step 3: Try to book — iterate through valid slots ─────────
    print(f"\n🔖 STEP 3: Attempting booking (trying up to 5 valid slots)...")
    print(f"   is_known_client=False | price=$0 → should bypass Stripe entirely")

    result     = None
    last_slot  = None
    for booking_slot in valid_slots[:5]:
        last_slot = booking_slot
        print(f"\n   → Trying slot: {booking_slot}")
        result = await book_appointment(
            name          = "Reba Test Bot",
            email         = "testbot@payminimumtax.com",
            phone         = "+16175550000",
            booking_slot  = booking_slot,
            call_summary  = "[TEST] Direct booking with real slot — test_calendar",
            calendar_type = "test_calendar",
            is_known_client = False,   # price=0 → should book directly, no Stripe
        )
        bk_status = result.get("status")
        print(f"     status: {bk_status}")
        if bk_status not in ("slot_unavailable", "invalid_slot"):
            break   # stop on first success or real error

    bk_status  = result.get("status")
    appt_id    = result.get("appointment_id", "N/A")
    message    = result.get("message", "")
    email_sent = result.get("email_sent")

    print(f"\n   Final slot tried : {last_slot}")
    print(f"   Result status    : {bk_status}")
    print(f"   Appointment ID   : {appt_id}")
    print(f"   Email sent       : {email_sent}")
    if message:
        print(f"   Message          : {message}")

    print("\n" + "─" * 60)
    if bk_status == "confirmed":
        print("  ✅  TEST CALENDAR BOOKING IS FULLY WORKING!")
        print(f"      Appointment ID: {appt_id}")
    elif bk_status in ("slot_unavailable", "invalid_slot"):
        print("  ⚠️   All 5 tried slots were taken/invalid.")
        print("       The $0 / no-Stripe fix IS correct — no payment error.")
        print("       The calendar just needs more open slots in GHL.")
    elif bk_status == "error" and ("open_hours" in message or "forEach" in message):
        print("  ❌  GHL test_calendar has NO OFFICE HOURS configured.")
        print("      ACTION REQUIRED in GHL:")
        print("      → Calendars → Original Test Calendar → Edit → Availability")
        print("        Add Mon-Fri 10 AM – 4 PM then save.")
    elif bk_status == "payment_required":
        print("  ❌  BUG: System tried to charge for a $0 calendar!")
        print("      Check that is_direct_booking includes `or (price == 0)`.")
    else:
        print(f"  ❌  Unexpected result: status={bk_status}")
        print(f"      Full result: {result}")
    print("─" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
