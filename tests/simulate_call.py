import asyncio
import json
import base64
import websockets

async def simulate_twilio_call():
    uri = "ws://localhost:8000/api/twilio/stream"
    print(f"🚀 Connecting to {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            # 1. Send 'start' event (mimic Twilio)
            start_event = {
                "event": "start",
                "start": {
                    "streamSid": "test_stream_12345",
                    "callSid": "test_call_12345",
                    "accountSid": "test_account_12345"
                }
            }
            await websocket.send(json.dumps(start_event))
            print("✅ Sent 'start' event to server.")

            # 2. Send some dummy 'media' (silence/noise)
            # This is base64 encoded ulaw-style dummy data
            dummy_payload = base64.b64encode(b"\xff" * 160).decode("utf-8")
            media_event = {
                "event": "media",
                "streamSid": "test_stream_12345",
                "media": {
                    "payload": dummy_payload
                }
            }
            
            print("🔊 Sending dummy audio chunks (5 chunks)...")
            for i in range(5):
                await websocket.send(json.dumps(media_event))
                await asyncio.sleep(0.1)

            # 3. Listen for responses from the server
            print("👂 Listening for AI responses (5 seconds)...")
            try:
                while True:
                    message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    data = json.loads(message)
                    if data.get("event") == "media":
                        print("🤖 Received audio response from Server!")
                    else:
                        print(f"📩 Received other event: {data.get('event')}")
            except asyncio.TimeoutError:
                print("⏱️  Done listening.")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(simulate_twilio_call())
