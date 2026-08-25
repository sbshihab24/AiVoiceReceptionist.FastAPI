import os
from dotenv import load_dotenv

load_dotenv()

GHL_BASE_URL = os.getenv("GHL_BASE_URL", "https://rest.gohighlevel.com/v1")
GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")

STRIPE_PUBLIC_KEY = os.getenv("STRIPE_PUBLIC_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "noreply@payminimumtax.com")

# Calendars
CALENDAR_FOLLOW_UP_C = os.getenv("CALENDAR_FOLLOW_UP_C")
CALENDAR_FOLLOW_UP_B = os.getenv("CALENDAR_FOLLOW_UP_B")
CALENDAR_VIRTUAL_CONSULT_15 = os.getenv("CALENDAR_VIRTUAL_CONSULT_15")
CALENDAR_VIRTUAL_CPA_45 = os.getenv("CALENDAR_VIRTUAL_CPA_45")
CALENDAR_OFFICE_CPA_45 = os.getenv("CALENDAR_OFFICE_CPA_45")
CALENDAR_BEAUTY_SALON_45 = os.getenv("CALENDAR_BEAUTY_SALON_45")
CALENDAR_TEST = os.getenv("CALENDAR_TEST")

# Call Forwarding Numbers
FORWARD_SIMON   = os.getenv("FORWARD_SIMON", "")
FORWARD_TANZINA = os.getenv("FORWARD_TANZINA", "")
FORWARD_ALEX    = os.getenv("FORWARD_ALEX", "")
FORWARD_NAFI    = os.getenv("FORWARD_NAFI", "")
