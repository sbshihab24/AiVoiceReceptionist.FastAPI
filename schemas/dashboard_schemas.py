from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import datetime

class LeadSummary(BaseModel):
    all: int
    high: int
    mid: int
    low: int
    new: int
    closed: int
    booked: int
    # Backward compatibility
    urgent: int
    qualified: int
    total: int

class LeadResponse(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    priority: str
    intent: str
    status: str
    reason: Optional[str] = None
    last_contact: Optional[str] = None
    tags: List[str]

class LeadsDashboardResponse(BaseModel):
    summary: LeadSummary

class LeadsListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    leads: List[LeadResponse]

class ContactBrief(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class CalendarAppointment(BaseModel):
    id: str
    title: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    selectedSlot: Optional[str] = None
    status: Optional[str] = None
    appointmentStatus: Optional[str] = None
    contact: Optional[ContactBrief] = None
    caller_summary: Optional[str] = None
    reason: Optional[str] = None
    group: Optional[str] = None
    contact_notes: Optional[List[Dict[str, Any]]] = None

class CalendarDashboardResponse(BaseModel):
    total: int
    page: int
    page_size: int
    calendar: List[CalendarAppointment]

class RecentActivity(BaseModel):
    type: str
    title: str
    status: str
    time: Optional[str] = None
    contact_name: str
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None

class CallLogResponse(BaseModel):
    id: int
    name: Optional[str] = "Unknown"
    call_sid: Optional[str] = None
    caller_number: Optional[str] = None
    start_time: datetime.datetime
    duration: Optional[int] = None
    summary: Optional[str] = None
    reason: Optional[str] = None
    status: Optional[str] = None
    outcome: Optional[str] = None
    lead_status: Optional[str] = None
    group: Optional[str] = "None"
    tags: Optional[str] = None

    class Config:
        from_attributes = True

class StatsDashboardResponse(BaseModel):
    todays_call_count: int
    todays_booking_count: int
    calls_growth: str = "0 today"
    booked_growth: str = "0 today"
    ai_insight: str = "No insights available yet."
    recent_activity: List[CallLogResponse]

class CallLogListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    calls: List[CallLogResponse]

class CallLogSummary(BaseModel):
    all: int
    completed: int
    missed: int
    inquiry: int
    booked: int

class CallLogSummaryResponse(BaseModel):
    summary: CallLogSummary
