"""
GHL contact search across all groups/pipelines.
"""
import httpx
import logging
from typing import Optional
from config import GHL_BASE_URL, GHL_API_KEY, GHL_LOCATION_ID

logger = logging.getLogger(__name__)


def get_ghl_headers():
    # For SMS contact search — use PIT token via shared get_sms_headers()
    try:
        from services.ghl import get_sms_headers
        return get_sms_headers()
    except Exception:
        return {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Content-Type": "application/json",
            "Version": "2021-07-28",
        }


async def search_contact_by_phone_or_email(phone: Optional[str] = None, email: Optional[str] = None) -> Optional[dict]:
    """
    Search for a contact in GHL by phone or email.
    GHL V2 contacts API requires the 'query' parameter (not 'phone' or 'email' directly).
    """
    url = "https://services.leadconnectorhq.com/contacts/"
    # GHL V2 uses 'query' for text search — phone or email both go through 'query'
    query_value = email if email else phone
    if not query_value:
        return None

    params = {
        "locationId": GHL_LOCATION_ID,
        "query": query_value,   # ← correct param for GHL V2 contacts search
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        logger.info(f"🔍 [GHL Search] Searching contact: query={query_value}")
        response = await client.get(url, params=params, headers=get_ghl_headers())

        if response.status_code != 200:
            logger.error(f"🔴 [GHL Search] API Error: {response.status_code} — {response.text[:200]}")
            return None

        data = response.json()
        contacts = data.get("contacts", [])

        if contacts:
            contact = contacts[0]
            logger.info(f"✅ [GHL Search] Found contact: {contact.get('id')}")
            return contact

        logger.info(f"🆕 [GHL Search] No contact found for: {query_value}")
        return None
