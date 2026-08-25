from pydantic import BaseModel
from typing import Optional
import datetime

class ActivityCreate(BaseModel):
    type: str
    time: str

class ActivityResponse(ActivityCreate):
    id: int
    created_at: datetime.datetime

    class Config:
        from_attributes = True
