"""
GHL SMS Pipeline Diagnostic Test
Run: python tests/test_ghl_sms.py +1XXXXXXXXXX
Tests each step of the pipeline individually and shows exactly where it fails.
"""

import asyncio
import sys
import os
import httpx

# Load .env manually (no dotenv dependency needed)
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

GHL_BASE_URL  = os.getenv("GHL_BASE_URL", "https://rest.gohighlevel.com/v1")
GHL_API_KEY   = os.getenv("GHL_API_KEY", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_FROM_NUMBER = os.getenv("GHL_FROM_NUMBER", "")

HEADERS = {
    "Authorization": f"Bearer {GHL_API_KEY}",
    "Content-Type": "application/json",
}

TEST_MESSAGE = "Test SMS from রেবা AI — GHL pipeline diagnostic. Please ignore."


def hr(label: str):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print('='*60)


async def step1_check_config(to_phone: str):
    hr("STEP 1 — Environment Config Check")
    print(f"  GHL_BASE_URL    : {GHL_BASE_URL}")
    print(f"  GHL_API_KEY     : {'✅ set' if GHL_API_KEY else '❌ MISSING'}")
    print(f"  GHL_LOCATION_ID : {'✅ set' if GHL_LOCATION_ID else '❌ MISSING'}")
    print(f"  GHL_FROM_NUMBER : {GHL_FROM_NUMBER or '⚠️  NOT SET (may be optional)'}")
    print(f"  To Phone        : {to_phone}")
    if not GHL_API_KEY:
        print("\n❌ FATAL: GHL_API_KEY is not set in .env")
        return False
    return True


async def step2_lookup_contact(client: httpx.AsyncClient, to_phone: str):
    hr("STEP 2 — Contact Lookup by Phone")
    url = f"{GHL_BASE_URL}/contacts/lookup"
    params = {"phone": to_phone}
    try:
        resp = await client.get(url, headers=HEADERS, params=params)
        print(f"  Status : {resp.status_code}")
        print(f"  Body   : {resp.text[:500]}")
        if resp.status_code == 200:
            data = resp.json()
            contact = data.get("contact") or (data.get("contacts") or [None])[0]
            if contact:
                cid = contact.get("id") or contact.get("contactId")
                print(f"  ✅ Contact found: {cid} — {contact.get('firstName','')} {contact.get('lastName','')}")
                return cid
            else:
                print("  ⚠️  200 OK but no contact returned. Will create one.")
        else:
            print(f"  ⚠️  Lookup failed with {resp.status_code}. Will attempt creation.")
    except Exception as e:
        print(f"  ❌ Exception: {e}")
    return None


async def step3_create_contact(client: httpx.AsyncClient, to_phone: str):
    hr("STEP 3 — Create Contact (if not found)")
    url = f"{GHL_BASE_URL}/contacts/"
    payload = {
        "phone": to_phone,
        "firstName": "SMSTest",
        "lastName": "Prospect",
        "locationId": GHL_LOCATION_ID,
        "source": "AI Test",
        "tags": ["ai-sms-test"],
    }
    try:
        resp = await client.post(url, headers=HEADERS, json=payload)
        print(f"  Status : {resp.status_code}")
        print(f"  Body   : {resp.text[:500]}")
        if resp.status_code in (200, 201):
            data = resp.json()
            contact_obj = data.get("contact") or data
            cid = contact_obj.get("id") or contact_obj.get("contactId")
            print(f"  ✅ Contact created: {cid}")
            return cid
        else:
            print(f"  ❌ Contact creation failed: {resp.status_code}")
    except Exception as e:
        print(f"  ❌ Exception: {e}")
    return None


async def step4_send_sms_v1_conversation(client: httpx.AsyncClient, contact_id: str, to_phone: str):
    hr("STEP 4a — GHL V1: Find/Create Conversation then Send SMS")

    # 4a-i: Search for existing conversation
    convo_id = None
    search_resp = await client.get(
        f"{GHL_BASE_URL}/conversations/search",
        params={"contactId": contact_id, "locationId": GHL_LOCATION_ID},
        headers=HEADERS,
    )
    print(f"  Conversation search status: {search_resp.status_code}")
    print(f"  Conversation search body  : {search_resp.text[:400]}")
    if search_resp.status_code == 200:
        convos = search_resp.json().get("conversations", [])
        if convos:
            convo_id = convos[0].get("id")
            print(f"  ✅ Found conversation: {convo_id}")

    if not convo_id:
        create_resp = await client.post(
            f"{GHL_BASE_URL}/conversations",
            json={"contactId": contact_id, "locationId": GHL_LOCATION_ID},
            headers=HEADERS,
        )
        print(f"  Conversation create status: {create_resp.status_code}")
        print(f"  Conversation create body  : {create_resp.text[:400]}")
        if create_resp.status_code in (200, 201):
            data = create_resp.json()
            convo_id = (data.get("conversation") or data).get("id")
            print(f"  ✅ Created conversation: {convo_id}")

    if not convo_id:
        print("  ❌ Could not get or create a conversation.")
        return False

    # 4a-ii: Send SMS to the conversation
    payload = {"type": "SMS", "message": TEST_MESSAGE}
    if GHL_FROM_NUMBER:
        payload["fromNumber"] = GHL_FROM_NUMBER
    msg_resp = await client.post(
        f"{GHL_BASE_URL}/conversations/{convo_id}/messages",
        json=payload,
        headers=HEADERS,
    )
    print(f"  SMS send status: {msg_resp.status_code}")
    print(f"  SMS send body  : {msg_resp.text[:500]}")
    if msg_resp.status_code in (200, 201):
        print("  ✅ GHL V1 Conversation SMS SENT SUCCESSFULLY")
        return True
    print(f"  ❌ GHL V1 message send failed")
    return False


async def step4b_send_sms_v2(client: httpx.AsyncClient, contact_id: str, to_phone: str):
    hr("STEP 4b — Send SMS via GHL V2 Conversations Endpoint")
    v2_url = "https://services.leadconnectorhq.com/conversations/messages"
    v2_headers = {**HEADERS, "Version": "2021-07-28"}
    payload = {
        "type": "SMS",
        "contactId": contact_id,
        "message": TEST_MESSAGE,
    }
    if GHL_FROM_NUMBER:
        payload["fromNumber"] = GHL_FROM_NUMBER
    try:
        resp = await client.post(v2_url, headers=v2_headers, json=payload)
        print(f"  Status : {resp.status_code}")
        print(f"  Body   : {resp.text[:1000]}")
        if resp.status_code in (200, 201):
            print("  ✅ GHL V2 SMS SENT SUCCESSFULLY")
            return True
        else:
            print(f"  ❌ GHL V2 failed: {resp.status_code}")
    except Exception as e:
        print(f"  ❌ Exception: {e}")
    return False


async def main():
    if len(sys.argv) < 2:
        print("Usage: python tests/test_ghl_sms.py +1XXXXXXXXXX")
        print("Example: python tests/test_ghl_sms.py +17814887674")
        sys.exit(1)

    to_phone = sys.argv[1]
    if not to_phone.startswith("+"):
        to_phone = f"+1{to_phone}" if len(to_phone) == 10 else f"+{to_phone}"

    print(f"\n🧪 GHL SMS PIPELINE DIAGNOSTIC")
    print(f"   Target: {to_phone}")

    config_ok = await step1_check_config(to_phone)
    if not config_ok:
        sys.exit(1)

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 2: find or create contact
        contact_id = await step2_lookup_contact(client, to_phone)
        if not contact_id:
            contact_id = await step3_create_contact(client, to_phone)

        if not contact_id:
            print("\n❌ FATAL: Could not find or create a GHL contact. SMS cannot be sent.")
            sys.exit(1)

        # Step 4a: try V1 conversation-based
        v1_ok = await step4_send_sms_v1_conversation(client, contact_id, to_phone)

        # Step 4b: also try V2 so we can compare
        v2_ok = await step4b_send_sms_v2(client, contact_id, to_phone)

    hr("FINAL RESULT")
    if v1_ok:
        print("  ✅ GHL V1 worked — SMS delivered via V1 endpoint.")
    elif v2_ok:
        print("  ✅ GHL V2 worked — SMS delivered via V2 endpoint.")
    else:
        print("  ❌ BOTH V1 and V2 failed. Check the error bodies above.")
        print("  Possible causes:")
        print("    1. GHL_FROM_NUMBER not set or not a registered SMS number in GHL")
        print("    2. GHL API key doesn't have Conversations/SMS permission")
        print("    3. Contact phone number format mismatch")
        print("    4. GHL sub-account has SMS disabled")


if __name__ == "__main__":
    asyncio.run(main())
