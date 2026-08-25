from fastapi import APIRouter, Depends
from sqlalchemy import or_, and_, desc
from sqlalchemy.orm import Session
import datetime
import httpx
import base64
from typing import Optional

from database import get_db, SessionLocal
from models.activity_models import Activity, CallLog
from services.ghl import get_all_appointments, get_contacts
from routers.twilio import TWILIO_SID, TWILIO_AUTH_TOKEN
from schemas.dashboard_schemas import LeadsDashboardResponse, CalendarDashboardResponse, StatsDashboardResponse, LeadsListResponse, CallLogListResponse, CallLogSummaryResponse
from services.ai_call import generate_ai_response
import time as _time
import re

_insight_cache = {"timestamp": 0, "text": "Tax season is peaking — 67% of today's calls are tax-related. Consider promoting your express filing package."}

def normalize_phone(phone: str) -> str:
    """Normalize phone number to digits, prepending 1 for 10-digit numbers."""
    if not phone: return ""
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) == 10:
        return f"1{digits}"
    return digits

router = APIRouter(
    prefix="/api/dashboard",
    tags=["Dashboard"]
)

async def get_all_calls(calendar_id: Optional[str] = None):
    db = SessionLocal()
    try:
        calls = db.query(CallLog).order_by(CallLog.start_time.desc()).all()
        return calls
    finally:
        db.close()

async def get_twilio_calls_today():
    if not TWILIO_SID or not TWILIO_AUTH_TOKEN:
        return 0
        
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls.json"
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    params = {"StartTime>": today}
    
    auth_header = base64.b64encode(f"{TWILIO_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                return len(data.get("calls", []))
        except Exception as e:
            print(f"Twilio API Error: {e}")
            pass
    return 0

@router.get("/stats", response_model=StatsDashboardResponse)
async def get_dashboard_stats(calendar_id: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Returns dashboard statistics including today's call count, 
    booking count, recent GHL activity, and calendar details.
    """
    # 1. Today's Call Count (from Twilio API and Local DB)
    twilio_calls = await get_twilio_calls_today()
    
    today_date = datetime.datetime.utcnow().date()
    # Count local activity calls if they are logging there
    db_calls = db.query(Activity).filter(Activity.type == 'call').count()
    
    calls_today = twilio_calls if twilio_calls > 0 else db_calls

    # 2. Today's Booking Count & Calendar Details
    try:
        # Fetch today's bookings
        today_bookings = await get_all_appointments(specific_day="today", calendar_id=calendar_id)
        todays_booking_count = len(today_bookings)
    except Exception as e:
        print(f"Error fetching today bookings: {e}")
        todays_booking_count = 0
        today_bookings = []

    try:
        # Fetch all upcoming or recent bookings
        appointments = await get_all_appointments(calendar_id=calendar_id)
        
        # Format recent activity (top 5 most recent or upcoming)
        recent_activity = []
        # Sort appointments by time if possible (assuming ISO strings)
        sorted_appointments = sorted(
            appointments, 
            key=lambda x: x.get("selectedSlot") or x.get("startTime") or "", 
            reverse=True
        )
        
        for appt in sorted_appointments[:5]:
            contact = appt.get("contact", {})
            name = contact.get("name") or f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip() or "Unknown"
            status = appt.get("appointmentStatus", "booked")
            
            recent_activity.append({
                "type": "appointment",
                "title": appt.get("title", f"Appointment with {name}"),
                "status": status,
                "time": appt.get("selectedSlot") or appt.get("startTime"),
                "contact_name": name,
                "contact_email": contact.get("email", ""),
                "contact_phone": contact.get("phone", "")
            })
            
    except Exception as e:
        print(f"Error fetching all bookings: {e}")
        appointments = []
        recent_activity = []

    # Fetch all recent call reasons to map to activity
    recent_calls_raw = db.query(CallLog).order_by(desc(CallLog.start_time)).limit(10).all()
    
    raw_contacts = await get_contacts()
    phone_map = {normalize_phone(c.get("phone")): c for c in raw_contacts if c.get("phone")}
    
    recent_activity = []
    for call in recent_calls_raw:
        norm_caller = normalize_phone(call.caller_number)
        c_info = phone_map.get(norm_caller, {})
        name = c_info.get("name") or f"{c_info.get('firstName', '')} {c_info.get('lastName', '')}".strip() or "Unknown"
        
        tags = c_info.get("tags", [])
        if isinstance(tags, str): tags = [t.strip().upper() for t in tags.split(",")]
        else: tags = [str(t).strip().upper() for t in tags]
        
        grp = "None"
        for g in ["A", "B", "C", "D"]:
            if any(g == tag or f"GROUP {g}" == tag for tag in tags):
                grp = g
                break
        
        recent_activity.append({
            "id": call.id,
            "name": name,
            "group": grp,
            "call_sid": call.call_sid,
            "caller_number": call.caller_number,
            "start_time": call.start_time,
            "duration": call.duration,
            "summary": call.summary,
            "reason": call.reason,
            "intent": "N/A",
            "outcome": "N/A",
            "status": call.status,
            "lead_status": call.lead_status,
            "tags": call.tags
        })

    # 4. Generate simulated insights and growth (for now)
    calls_growth = f"+{calls_today} today"
    booked_growth = f"+{todays_booking_count} today"
    
    # Generate dynamic AI insight based on recent calls, cache for 5 mins
    now_sec = _time.time()
    if now_sec - _insight_cache["timestamp"] > 300:
        summary_texts = []
        for c in recent_calls_raw[:5]:
            summary_texts.append(f"Call reason: {c.reason}, Duration: {c.duration}s")
        prompt = "Based on these recent calls: " + "; ".join(summary_texts) + ". Provide a one-sentence business insight or recommendation (under 15 words) for the dashboard."
        try:
            new_insight = await generate_ai_response(prompt, system_context="You are an expert data analyst AI.")
            _insight_cache["text"] = new_insight
            _insight_cache["timestamp"] = now_sec
        except Exception:
            pass
            
    ai_insight = _insight_cache["text"]

    return {
        "todays_call_count": calls_today,
        "todays_booking_count": todays_booking_count,
        "calls_growth": calls_growth,
        "booked_growth": booked_growth,
        "ai_insight": ai_insight,
        "recent_activity": recent_activity
    }


@router.get("/leads", response_model=LeadsDashboardResponse)
async def ViewLeads(query: Optional[str] = None):
    raw_leads = await get_contacts(query=query)
    
    high_count = 0
    mid_count = 0
    low_count = 0
    new_count = 0
    booked_count = 0
    closed_count = 0
    total_count = len(raw_leads)

    for lead in raw_leads:
        tags = lead.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        
        # 1. Map Priority
        if any(t in ["Group A", "A", "Group B", "B"] for t in tags):
            high_count += 1
        elif any(t in ["Group C", "C"] for t in tags):
            mid_count += 1
        else:
            low_count += 1
            
        # 2. Determine Status
        if lead.get("lastAppointment"):
            booked_count += 1
        elif lead.get("lastContact"):
            closed_count += 1
        else:
            new_count += 1

    return {
        "summary": {
            "all": total_count,
            "high": high_count,
            "mid": mid_count,
            "low": low_count,
            "new": new_count,
            "closed": closed_count,
            "booked": booked_count,
            "urgent": high_count,
            "qualified": booked_count,
            "total": total_count
        }
    }


@router.get("/leads/list", response_model=LeadsListResponse)
async def ViewLeadsList(
    query: Optional[str] = None,
    filter: Optional[str] = "all", # Unified filter key
    page: int = 1,
    page_size: int = 10,
    db: Session = Depends(get_db)
):
    raw_leads = await get_contacts(query=query)
    
    # 1. Fetch all recent call reasons to map to leads
    call_logs = db.query(CallLog).all()
    phone_to_reason = {normalize_phone(log.caller_number): log.reason for log in call_logs if log.caller_number}

    enriched_leads = []
    for lead in raw_leads:
        tags = lead.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        
        # a) Map Priority
        lead_priority = "Low"
        if any(t in ["Group A", "A", "Group B", "B"] for t in tags):
            lead_priority = "High"
        elif any(t in ["Group C", "C"] for t in tags):
            lead_priority = "Medium"
            
        # b) Determine Status
        lead_status = "New"
        if lead.get("lastAppointment"):
            lead_status = "Booked"
        elif lead.get("lastContact"):
            lead_status = "Contacted"

        # c) Get Reason from CallLog
        lead_phone = normalize_phone(lead.get("phone"))
        lead_reason = phone_to_reason.get(lead_phone, None)

        # d) Get Intent
        intent = "General"
        for t in tags:
            if not any(g in t for g in ["Group", "A", "B", "C", "D"]) or len(t) > 2:
                intent = t
                break

        # e) Unified Filter Logic
        filter_val = filter.lower() if filter else "all"
        if filter_val != "all":
            if filter_val == "high" and lead_priority != "High":
                continue
            if filter_val == "mid" and lead_priority != "Medium":
                continue
            if filter_val == "low" and lead_priority != "Low":
                continue
            if filter_val == "new" and lead_status != "New":
                continue
            if filter_val == "booked" and lead_status != "Booked":
                continue
            if filter_val == "closed" and lead_status != "Contacted":
                continue

        enriched_leads.append({
            "id": lead.get("id"),
            "name": lead.get("name") or f"{lead.get('firstName', '')} {lead.get('lastName', '')}".strip() or "Unknown",
            "email": lead.get("email"),
            "phone": lead.get("phone"),
            "priority": lead_priority,
            "intent": intent,
            "status": lead_status,
            "reason": lead_reason,
            "last_contact": lead.get("dateUpdated") or lead.get("dateAdded"),
            "tags": tags
        })

    # Pagination logic
    total = len(enriched_leads)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated_leads = enriched_leads[start_idx:end_idx]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "leads": paginated_leads
    }


@router.get("/calendar", response_model=CalendarDashboardResponse)
async def ViewCalendar(
    calendar_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    title: Optional[str] = None,
    group: Optional[str] = None,
    reason: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    db: Session = Depends(get_db)
):
    # 1. Fetch appointments from GHL
    appointments = await get_all_appointments(calendar_id=calendar_id)
    
    # 2. Fetch all call reasons for mapping
    call_logs = db.query(CallLog).all()
    phone_to_reason = {normalize_phone(log.caller_number): log.reason for log in call_logs if log.caller_number}

    filtered_calendar = []
    for appt in appointments:
        # a) Extract Data
        appt_title = appt.get("title", "")
        appt_start = appt.get("startTime") or appt.get("selectedSlot") or ""
        contact = appt.get("contact", {})
        contact_tags = contact.get("tags", [])
        if isinstance(contact_tags, str):
            contact_tags = [t.strip().upper() for t in contact_tags.split(",")]
        else:
            contact_tags = [str(t).strip().upper() for t in contact_tags]

        # b) Map Group
        appt_group = "None"
        for g in ["A", "B", "C", "D"]:
            if any(g == tag or f"GROUP {g}" == tag for tag in contact_tags):
                appt_group = g
                break
        
        # c) Map Reason
        phone = normalize_phone(contact.get("phone"))
        appt_reason = phone_to_reason.get(phone, None)

        # d) Filtering Logic
        if start_date and appt_start < start_date:
            continue
        if end_date and appt_start > end_date:
            continue
        if title and title.lower() not in appt_title.lower():
            continue
        if group and group.upper() != appt_group:
            continue
        if reason and (not appt_reason or reason.lower() not in appt_reason.lower()):
            continue

        # e) Enrich
        appt["group"] = appt_group
        appt["reason"] = appt_reason
        filtered_calendar.append(appt)

    # 3. Pagination
    total = len(filtered_calendar)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated_calendar = filtered_calendar[start_idx:end_idx]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "calendar": paginated_calendar
    }


@router.get("/call-log", response_model=CallLogListResponse)
async def ViewCallLog(
    page: int = 1,
    page_size: int = 10,
    query: Optional[str] = None,
    filter: Optional[str] = "all", # Unified filter key
    priority: Optional[str] = None,
    status: Optional[str] = None,
    reason: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Returns paginated and filtered call logs from the local database.
    """
    stmt = db.query(CallLog)

    # 1. Search Query (phone or summary)
    if query:
        stmt = stmt.filter(or_(
            CallLog.caller_number.contains(query),
            CallLog.summary.contains(query)
        ))

    # 2. Priority/Class (filtering by tags)
    if priority:
        # Assuming priority is stored in tags like "Group A"
        stmt = stmt.filter(CallLog.tags.contains(f"Group {priority.upper()}"))

    # 3. Status
    if status:
        stmt = stmt.filter(CallLog.status == status)

    # 4. Reason
    if reason:
        stmt = stmt.filter(CallLog.reason.contains(reason))

    # Unified Filter
    filter_val = filter.lower() if filter else "all"
    if filter_val != "all":
        if filter_val == "completed":
            stmt = stmt.filter(CallLog.status == "completed")
        elif filter_val == "missed":
            stmt = stmt.filter(CallLog.status != "completed")
        elif filter_val == "inquiry":
            stmt = stmt.filter(or_(CallLog.outcome == "Inquiry", CallLog.reason.ilike("%inquiry%")))
        elif filter_val == "booked":
            stmt = stmt.filter(and_(CallLog.outcome != "Completed", CallLog.outcome != "Inquiry", CallLog.outcome != "Other", CallLog.outcome != "N/A"))

    # 5. Date Range
    if start_date:
        stmt = stmt.filter(CallLog.start_time >= start_date)
    if end_date:
        stmt = stmt.filter(CallLog.start_time <= end_date)

    # Order by most recent
    stmt = stmt.order_by(desc(CallLog.start_time))

    # 6. Fetch Contact Info from GHL to enrich name and group
    # To keep it efficient, we'll fetch all contacts and map them
    raw_contacts = await get_contacts()
    phone_map = {}
    for c in raw_contacts:
        phone = c.get("phone")
        if phone:
            # Map name
            full_name = c.get("name") or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() or "Unknown"
            
            # Map group
            tags = c.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip().upper() for t in tags.split(",")]
            else:
                tags = [str(t).strip().upper() for t in tags]
            
            grp = "None"
            for g in ["A", "B", "C", "D"]:
                if any(g == tag or f"GROUP {g}" == tag for tag in tags):
                    grp = g
                    break
            
            phone_map[phone] = {"name": full_name, "group": grp}

    # 7. Pagination
    total = stmt.count()
    raw_calls = stmt.offset((page - 1) * page_size).limit(page_size).all()
    
    enriched_calls = []
    for call in raw_calls:
        contact_info = phone_map.get(call.caller_number, {"name": "Unknown", "group": "None"})
        
        # Create a dictionary version of the call object
        call_dict = {
            "id": call.id,
            "name": contact_info["name"],
            "group": contact_info["group"],
            "call_sid": call.call_sid,
            "caller_number": call.caller_number,
            "start_time": call.start_time,
            "duration": call.duration,
            "summary": call.summary,
            "reason": call.reason,
            "status": call.status,
            "outcome": call.outcome or "Completed", # Default outcome if null
            "lead_status": call.lead_status,
            "tags": call.tags
        }
        enriched_calls.append(call_dict)

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "calls": enriched_calls
    }

@router.get("/call-details/{call_id}")
async def ViewCallDetail(call_id: int):
    db = SessionLocal()
    try:
        call = db.query(CallLog).filter(CallLog.id == call_id).first()
        if not call:
            return {"error": "Call log not found"}
        
        # Enrich with GHL name and group
        # This is a single call, so we can fetch specific contact if phone exists
        name = "Unknown"
        group = "None"
        if call.caller_number:
            contacts = await get_contacts(query=call.caller_number)
            if contacts:
                c = contacts[0]
                name = c.get("name") or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() or "Unknown"
                
                tags = c.get("tags", [])
                if isinstance(tags, str): tags = [t.strip().upper() for t in tags.split(",")]
                else: tags = [str(t).strip().upper() for t in tags]
                
                for g in ["A", "B", "C", "D"]:
                    if any(g == tag or f"GROUP {g}" == tag for tag in tags):
                        group = g
                        break
        
        return {
            "id": call.id,
            "name": name,
            "group": group,
            "call_sid": call.call_sid,
            "caller_number": call.caller_number,
            "start_time": call.start_time,
            "duration": call.duration,
            "transcript": call.transcript,
            "summary": call.summary,
            "reason": call.reason,
            "status": call.status,
            "outcome": call.outcome or "Completed",
            "lead_status": call.lead_status,
            "tags": call.tags
        }
    finally:
        db.close()


@router.get("/call-log/summary", response_model=CallLogSummaryResponse)
async def ViewCallLogSummary(db: Session = Depends(get_db)):
    """
    Returns counts for different call log filter tabs.
    """
    all_count = db.query(CallLog).count()
    completed_count = db.query(CallLog).filter(CallLog.status == "completed").count()
    missed_count = db.query(CallLog).filter(CallLog.status != "completed").count()
    inquiry_count = db.query(CallLog).filter(or_(CallLog.outcome == "Inquiry", CallLog.reason.ilike("%inquiry%"))).count()
    booked_count = db.query(CallLog).filter(and_(CallLog.outcome != "Completed", CallLog.outcome != "Inquiry", CallLog.outcome != "Other", CallLog.outcome != "N/A")).count()

    return {
        "summary": {
            "all": all_count,
            "completed": completed_count,
            "missed": missed_count,
            "inquiry": inquiry_count,
            "booked": booked_count
        }
    }