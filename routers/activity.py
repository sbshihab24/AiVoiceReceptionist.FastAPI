from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models.activity_models import Activity
from schemas.activity_schemas import ActivityCreate, ActivityResponse

router = APIRouter(
    prefix="/api/activity",
    tags=["Activity Logging"]
)


@router.post("/", response_model=ActivityResponse, status_code=status.HTTP_201_CREATED)
def log_activity_webhook(activity_in: ActivityCreate, db: Session = Depends(get_db)):
    """
    Webhook endpoint to log a single activity and store it on the database.
    """
    db_activity = Activity(
        type=activity_in.type,
        time=activity_in.time
    )
    db.add(db_activity)
    db.commit()
    db.refresh(db_activity)
    return db_activity


@router.post("/batch/", response_model=List[ActivityResponse], status_code=status.HTTP_201_CREATED)
def log_activities_batch(activities_in: List[ActivityCreate], db: Session = Depends(get_db)):
    """
    Webhook endpoint to log a batch/list of activities and store them on the database.
    """
    created_activities = []
    for activity in activities_in:
        db_activity = Activity(
            type=activity.type,
            time=activity.time
        )
        db.add(db_activity)
        created_activities.append(db_activity)
        
    db.commit()
    for item in created_activities:
        db.refresh(item)
    return created_activities


@router.get("/", response_model=List[ActivityResponse])
def get_all_activities(db: Session = Depends(get_db)):
    """
    API endpoint to retrieve all logged activities from the database.
    """
    return db.query(Activity).order_by(Activity.created_at.desc()).all()
