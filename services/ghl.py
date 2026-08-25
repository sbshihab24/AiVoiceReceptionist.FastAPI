import logging
logger = logging.getLogger(__name__)

import httpx
import datetime
from config import (
    GHL_BASE_URL, GHL_API_KEY, GHL_LOCATION_ID,
    CALENDAR_FOLLOW_UP_C, CALENDAR_FOLLOW_UP_B, 
    CALENDAR_VIRTUAL_CONSULT_15, CALENDAR_VIRTUAL_CPA_45, 
    CALENDAR_OFFICE_CPA_45, CALENDAR_BEAUTY_SALON_45, CALENDAR_TEST
)
from schemas import *
from fastapi import HTTPException
from typing import Optional
import time as _time

# In-memory cache stores
CACHE_TTL = 300  # 5 minutes in seconds
_appointments_cache = {}
_contacts_cache = {}

# ── General GHL API headers (V1 / existing code — unchanged) ──────────────
# Used by: booking, contacts, calendar, appointments
# Token: GHL_API_KEY (original — safe for all existing functionality)
def get_ghl_headers():
    return {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json",
    }


# ── SMS-only headers (V2 LeadConnector) ───────────────────────────────────
# Used ONLY by: send_sms_via_ghl()
# Token: GHL_PIT_TOKEN → falls back to GHL_API_KEY
def get_sms_headers():
    import os
    pit = os.getenv("GHL_PIT_TOKEN", "")
    token = pit if pit else GHL_API_KEY
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }



async def add_contact(contact: ContactCreate):
    url = f"{GHL_BASE_URL}/contacts/"
    
    # We must ensure locationId is included if necessary in v1
    # Check GHL documentation: The typical payload includes locationId.
    payload = contact.model_dump(exclude_none=True)
    payload["locationId"] = GHL_LOCATION_ID
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=get_ghl_headers())
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)



async def update_contact(contact_id: str, contact: ContactUpdate):
    url = f"{GHL_BASE_URL}/contacts/{contact_id}"
    
    payload = contact.model_dump(exclude_none=True)
    
    async with httpx.AsyncClient() as client:
        response = await client.put(url, json=payload, headers=get_ghl_headers())
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)



async def get_contact(contact_id: str):
    url = f"{GHL_BASE_URL}/contacts/{contact_id}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=get_ghl_headers())
        
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)

async def get_contact_by_phone(phone: str):
    """Search for a contact by phone number in GHL. Returns raw contact dict."""
    url = f"{GHL_BASE_URL}/contacts/lookup"
    params = {"phone": phone}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=get_ghl_headers(), params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("contact"):
                    return data.get("contact")
        except Exception as e:
            logger.error(f"Error looking up contact by phone: {e}")
    return None


async def get_contact_profile_by_phone(phone: str) -> dict:
    """
    Returns a structured profile for the AI: name, group (A/B/C/D), 
    client type, and invoice status from GHL tags.
    """
    from services.known_clients import normalize_phone
    normalized = normalize_phone(phone)
    search_phone = phone
    if normalized:
        if len(normalized) == 11 and normalized.startswith("1"):
            search_phone = f"+{normalized}"
        elif len(normalized) == 10:
            search_phone = f"+1{normalized}"

    contact = await get_contact_by_phone(search_phone)
    if not contact and normalized:
        # Fallback 1: Try searching with 11-digit format without +
        contact = await get_contact_by_phone(normalized)
        if not contact and len(normalized) == 11 and normalized.startswith("1"):
            # Fallback 2: Try searching with 10-digit format
            contact = await get_contact_by_phone(normalized[1:])

    if not contact:
        return {"found": False, "client_type": "Prospect", "group": None, "name": None}

    first = contact.get("firstName", "") or ""
    last = contact.get("lastName", "") or ""
    name = f"{first} {last}".strip() or "Client"

    # Parse tags to determine group and client type
    raw_tags = contact.get("tags", [])
    if isinstance(raw_tags, str):
        tags = [t.strip().upper() for t in raw_tags.split(",")]
    else:
        tags = [str(t).strip().upper() for t in raw_tags]

    group = None
    for g in ["A", "B", "C", "D"]:
        if any(g == tag or f"GROUP {g}" == tag for tag in tags):
            group = g
            break

    client_type = "Prospect"
    if any("ADHOC" in tag for tag in tags):
        client_type = "Adhoc"
    elif group:
        client_type = f"Class {group} Client"

    invoice_due = any("INVOICE" in tag or "DUE" in tag for tag in tags)

    return {
        "found": True,
        "contact_id": contact.get("id"),
        "name": name,
        "group": group,
        "client_type": client_type,
        "invoice_due": invoice_due,
        "tags": tags,
    }


async def add_crm_note(contact_id: str, note_body: str) -> bool:
    """Add a note to a GHL contact — used to record missed call messages."""
    if not contact_id:
        return False
    url = f"{GHL_BASE_URL}/contacts/{contact_id}/notes"
    payload = {"body": note_body}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=get_ghl_headers())
            if response.status_code in [200, 201]:
                logger.info(f"✅ [GHL] Note saved for contact {contact_id}")
                return True
        except Exception as e:
            logger.error(f"Error saving CRM note: {e}")
    return False




async def create_appointment(appointment: AppointmentCreate):
    url = f"{GHL_BASE_URL}/appointments/"
    
    payload = appointment.model_dump(exclude_none=True)
    payload["locationId"] = GHL_LOCATION_ID
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=get_ghl_headers())
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)



async def update_appointment(appointment_id: str, appointment: AppointmentUpdate):
    url = f"{GHL_BASE_URL}/appointments/{appointment_id}"
    
    payload = appointment.model_dump(exclude_none=True)
    
    async with httpx.AsyncClient() as client:
        response = await client.put(url, json=payload, headers=get_ghl_headers())
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)


async def get_all_appointments(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    specific_day: Optional[str] = None,
    this_week: Optional[bool] = None,
    calendar_id: Optional[str] = None
):
    """
    Fetch all appointments from GoHighLevel and optionally filter by email, phone, 
    time range, specific day (or today), or this week.
    """
    cache_key = f"{email}_{phone}_{start_time}_{end_time}_{specific_day}_{this_week}_{calendar_id}"
    now_sec = _time.time()
    
    if cache_key in _appointments_cache:
        cached_entry = _appointments_cache[cache_key]
        if now_sec - cached_entry["timestamp"] < CACHE_TTL:
            return cached_entry["data"]

    url = f"{GHL_BASE_URL}/appointments/"
    # GHL requires startDate and endDate (epoch ms). Default to 90-day window.
    now_ms = int(now_sec * 1000)
    
    # If no specific calendar_id is provided, fetch from all known calendars
    target_calendars = [calendar_id] if calendar_id else [
        CALENDAR_FOLLOW_UP_C, CALENDAR_FOLLOW_UP_B,
        CALENDAR_VIRTUAL_CONSULT_15, CALENDAR_VIRTUAL_CPA_45,
        CALENDAR_OFFICE_CPA_45, CALENDAR_BEAUTY_SALON_45, CALENDAR_TEST
    ]
    # Filter out None values in case some env vars are missing
    target_calendars = [cid for cid in target_calendars if cid]

    all_raw_appointments = []
    
    async with httpx.AsyncClient(timeout=20) as client:
        for cid in target_calendars:
            params = {
                "locationId": GHL_LOCATION_ID,
                "calendarId": cid,
                "startDate": now_ms - (30 * 24 * 60 * 60 * 1000),  # 30 days ago
                "endDate": now_ms + (60 * 24 * 60 * 60 * 1000),    # 60 days ahead
            }
            try:
                response = await client.get(url, params=params, headers=get_ghl_headers())
                if response.status_code == 200:
                    data = response.json()
                    appts = data if isinstance(data, list) else data.get("appointments", [])
                    all_raw_appointments.extend(appts)
            except Exception as e:
                logger.info(f"Error fetching from calendar {cid}: {e}")
            
        # Perform local filtering on the consolidated list
        filtered_appointments = []
        # Use a set to avoid duplicates if an appointment somehow appears in multiple calendars (rare but safe)
        seen_ids = set()
        
        for appt in all_raw_appointments:
            appt_id = appt.get("id")
            if appt_id in seen_ids:
                continue
            seen_ids.add(appt_id)
            
            contact = appt.get("contact", {})
            
            if email and email.lower() not in contact.get("email", "").lower():
                continue
                
            if phone and phone not in contact.get("phone", ""):
                continue
                
            appt_time = appt.get("selectedSlot") or appt.get("startTime")
            
            if appt_time:
                if start_time and appt_time < start_time:
                    continue
                if end_time and appt_time > end_time:
                    continue
                    
                appt_day = appt_time[:10]
                
                if specific_day:
                    target_day = specific_day
                    if specific_day.lower() == "today":
                        target_day = datetime.date.today().isoformat()
                    if appt_day != target_day:
                        continue
                        
                if this_week:
                    today = datetime.date.today()
                    week_start = today - datetime.timedelta(days=today.weekday())
                    week_end = week_start + datetime.timedelta(days=6)
                    if not (week_start.isoformat() <= appt_day <= week_end.isoformat()):
                        continue
                    
            filtered_appointments.append(appt)
        
        # Enrich each appointment with full details (notes, title, contact + contact notes)
        import asyncio
        async def enrich(appt_summary):
            appt_id = appt_summary.get("id")
            merged = dict(appt_summary)
            try:
                # 1) Fetch full appointment detail
                detail_resp = await client.get(
                    f"{GHL_BASE_URL}/appointments/{appt_id}",
                    headers=get_ghl_headers()
                )
                if detail_resp.status_code == 200:
                    merged.update(detail_resp.json())

                # 2) Fetch contact notes to surface AI call summary
                contact_id = merged.get("contactId")
                if contact_id:
                    notes_resp = await client.get(
                        f"{GHL_BASE_URL}/contacts/{contact_id}/notes",
                        headers=get_ghl_headers()
                    )
                    if notes_resp.status_code == 200:
                        all_notes = notes_resp.json().get("notes", [])
                        # Find the AI booking note that matches this appointment
                        ai_notes = [
                            n for n in all_notes
                            if appt_id in n.get("body", "")
                            or "AI Receptionist Booking" in n.get("body", "")
                        ]
                        if ai_notes:
                            # Use the most recent matching note
                            latest_note = sorted(ai_notes, key=lambda x: x.get("dateAdded", ""), reverse=True)[0]
                            merged["caller_summary"] = latest_note.get("body", "")
                        # Also include all notes for full visibility
                        merged["contact_notes"] = all_notes
            except Exception:
                pass
            return merged
        
        enriched = await asyncio.gather(*[enrich(a) for a in filtered_appointments])
        
        final_data = list(enriched)
        _appointments_cache[cache_key] = {
            "timestamp": now_sec,
            "data": final_data
        }
        return final_data


async def get_contacts(query: Optional[str] = None, limit: int = 20):
    """
    Fetch contacts from GoHighLevel.
    """
    cache_key = f"{query}_{limit}"
    now_sec = _time.time()
    
    if cache_key in _contacts_cache:
        cached_entry = _contacts_cache[cache_key]
        if now_sec - cached_entry["timestamp"] < CACHE_TTL:
            return cached_entry["data"]

    url = f"{GHL_BASE_URL}/contacts/"
    params = {
        "locationId": GHL_LOCATION_ID,
        "limit": limit
    }
    if query:
        params["query"] = query
        
    async with httpx.AsyncClient() as client:
        # Avoid trailing slash issues
        clean_url = url.rstrip('/')
        response = await client.get(clean_url, params=params, headers=get_ghl_headers())
        
        if response.status_code == 200:
            data = response.json().get("contacts", [])
            _contacts_cache[cache_key] = {
                "timestamp": now_sec,
                "data": data
            }
            return data
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)


async def send_sms_via_ghl(to_phone: str, message: str) -> bool:
    """
    Sends an SMS via GoHighLevel V2 (LeadConnector) API.
    All calls use PIT token (GHL_PIT_TOKEN) via get_ghl_headers().
    """
    import os
    from services.known_clients import normalize_phone
    from services.ghl_search import search_contact_by_phone_or_email

    # Normalize to E.164
    normalized = normalize_phone(to_phone)
    if not normalized.startswith("+"):
        if len(normalized) == 10:
            normalized = f"+1{normalized}"
        elif len(normalized) == 11 and normalized.startswith("1"):
            normalized = f"+{normalized}"
        else:
            normalized = f"+{normalized}"

    logger.info(f"📤 [GHL SMS] Sending SMS to {normalized}...")
    headers = get_sms_headers()  # PIT token — SMS only, does not affect booking/calendar

    async with httpx.AsyncClient(timeout=15.0) as client:

        # ── Step 1: Find or create contact via V2 ──
        contact_id = None

        try:
            contact = await search_contact_by_phone_or_email(phone=normalized)
            if contact:
                contact_id = contact.get("id") or contact.get("contactId")
                logger.info(f"✅ [GHL SMS] Found contact: {contact_id}")
        except Exception as e:
            logger.error(f"❌ [GHL SMS] Search error: {e}")

        if not contact_id:
            try:
                logger.info("🆕 [GHL SMS] Creating contact via V2...")
                r = await client.post(
                    "https://services.leadconnectorhq.com/contacts/",
                    json={"phone": normalized, "locationId": GHL_LOCATION_ID, "name": "Prospect"},
                    headers=headers,
                )
                if r.status_code in (200, 201):
                    obj = r.json().get("contact") or r.json()
                    contact_id = obj.get("id") or obj.get("contactId")
                    logger.info(f"✅ [GHL SMS] Created contact: {contact_id}")
                elif r.status_code == 400:
                    # GHL returns existing contactId in meta on duplicate — use it directly
                    meta = r.json().get("meta", {})
                    contact_id = meta.get("contactId")
                    if contact_id:
                        logger.info(f"✅ [GHL SMS] Duplicate contact — reusing existing ID: {contact_id}")
                    else:
                        logger.error(f"❌ [GHL SMS] Contact create failed (400): {r.text[:200]}")
                else:
                    logger.error(f"❌ [GHL SMS] Contact create failed ({r.status_code}): {r.text[:200]}")
            except Exception as e:
                logger.error(f"❌ [GHL SMS] Contact create error: {e}")

        if not contact_id:
            logger.error("❌ [GHL SMS] No contact_id — aborting.")
            return False

        # ── Step 2: Send SMS via V2 Conversations ──
        from_num = os.getenv("GHL_FROM_NUMBER", "")
        payload = {"type": "SMS", "contactId": contact_id, "message": message}
        if from_num:
            payload["fromNumber"] = from_num

        try:
            resp = await client.post(
                "https://services.leadconnectorhq.com/conversations/messages",
                json=payload,
                headers=headers,
            )
            if resp.status_code in (200, 201):
                logger.info(f"✅ [GHL SMS] SMS sent to {normalized} from {from_num or 'default'}")
                return True
            else:
                logger.error(f"❌ [GHL SMS] Send failed ({resp.status_code}): {resp.text[:300]}")
        except Exception as e:
            logger.error(f"❌ [GHL SMS] Send exception: {e}")

    return False
