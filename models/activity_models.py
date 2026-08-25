import datetime
from sqlalchemy import Column, Integer, String, DateTime
from database import Base

class Activity(Base):
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, nullable=False)
    time = Column(String, nullable=False)  # Storing as string e.g. "10:00" or datetime
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    call_sid = Column(String, index=True)
    caller_number = Column(String, nullable=True)
    receiver_number = Column(String, nullable=True)
    start_time = Column(DateTime, default=datetime.datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    duration = Column(Integer, nullable=True) # in seconds
    transcript = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    intent = Column(String, nullable=True)
    outcome = Column(String, nullable=True)
    status = Column(String, default="completed") # e.g., completed, missed
    lead_status = Column(String, nullable=True) # e.g., Qualified Lead
    reason = Column(String, nullable=True) # Concise 2-3 word summary
    tags = Column(String, nullable=True) # JSON or comma-separated string
