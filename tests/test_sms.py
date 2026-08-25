"""
Quick SMS send test via GHL PIT token.
Usage:  docker compose exec fastapi python tests/test_sms.py +1xxxxxxxxxx
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ghl import send_sms_via_ghl

async def main():
    to  = sys.argv[1] if len(sys.argv) > 1 else "+17759802006"
    msg = "Hello from Reba! GHL SMS test."
    print(f"📤 Sending to {to}...")
    ok = await send_sms_via_ghl(to, msg)
    print("✅ SMS sent!" if ok else "❌ Failed — check logs above.")

asyncio.run(main())
