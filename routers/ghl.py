from fastapi import APIRouter, HTTPException, Query
from schemas import *
from services.ghl import *
from typing import Optional

router = APIRouter(
    prefix="/api/ghl",
    tags=["GoHighLevel"]
)


@router.get("/appointments/")
async def list_appointments(
    email: Optional[str] = Query(None, description="Filter appointments by contact email"),
    phone: Optional[str] = Query(None, description="Filter appointments by contact phone number"),
    start_time: Optional[str] = Query(None, description="Filter appointments starting on or after this timestamp"),
    end_time: Optional[str] = Query(None, description="Filter appointments ending on or before this timestamp"),
    specific_day: Optional[str] = Query(None, description="Filter appointments for a specific day (e.g. YYYY-MM-DD or 'today')"),
    this_week: Optional[bool] = Query(None, description="Filter appointments scheduled for this current week")
):
    """
    Get all appointments from GoHighLevel with optional filtering.
    """
    try:
        response = await get_all_appointments(
            email=email,
            phone=phone,
            start_time=start_time,
            end_time=end_time,
            specific_day=specific_day,
            this_week=this_week
        )
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/contacts/")
async def create_new_contact(contact: ContactCreate):
    """
    Create a new contact in GoHighLevel
    """
    try:
        response = await add_contact(contact)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/contacts/{contact_id}")
async def update_existing_contact(contact_id: str, contact: ContactUpdate):
    """
    Update an existing contact in GoHighLevel
    """
    try:
        response = await update_contact(contact_id, contact)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/contacts/{contact_id}")
async def fetch_contact(contact_id: str):
    """
    Get a contact from GoHighLevel by ID
    """
    try:
        response = await get_contact(contact_id)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/appointments/")
async def schedule_appointment(appointment: AppointmentCreate):
    """
    Schedule a new appointment in GoHighLevel
    """
    try:
        response = await create_appointment(appointment)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/appointments/{appointment_id}")
async def update_existing_appointment(appointment_id: str, appointment: AppointmentUpdate):
    """
    Update an existing appointment in GoHighLevel
    """
    try:
        response = await update_appointment(appointment_id, appointment)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
