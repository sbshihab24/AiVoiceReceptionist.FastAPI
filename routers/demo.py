"""
Browser Demo Router.

Provides a browser-compatible WebSocket endpoint for testing the AI voice
receptionist without needing Twilio. Sends real-time debug events to the
frontend so every step of the flow is visible.
"""
import os
import json
import base64
import random
import asyncio
import time
import websockets
from datetime import datetime
from typing import List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from services.openai_realtime import get_openai_realtime_model, get_openai_realtime_ws_url
from services.prompts import system_prompt
from routers.twilio import ADS, _asks_anything_else, _is_end_call_consent, _is_negative_response, _is_meaningful_transcript
from routers.common_tools import (
    handle_book_appointment,
    handle_get_slots,
    handle_transfer_call,
    handle_end_call,
    handle_send_link_sms,
    handle_send_link_email,
    handle_record_message,
)

router = APIRouter(
    prefix="/api/demo",
    tags=["Demo & Debug"]
)

# Global list of connected debug WebSockets
debug_clients: List[WebSocket] = []

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = get_openai_realtime_model()
OPENAI_WS_URL = get_openai_realtime_ws_url()


async def broadcast_debug(event: str, message: str, data: dict = None):
    """Send a debug event to all connected debug WebSocket clients."""
    payload = {
        "type": "debug",
        "event": event,
        "message": message,
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "data": data or {}
    }
    disconnected = []
    for client in debug_clients:
        try:
            await client.send_json(payload)
        except Exception:
            disconnected.append(client)
    for c in disconnected:
        debug_clients.remove(c)


@router.websocket("/debug-ws")
async def debug_websocket(websocket: WebSocket):
    """WebSocket endpoint for receiving real-time debug events."""
    await websocket.accept()
    debug_clients.append(websocket)
    try:
        while True:
            # Keep alive - client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in debug_clients:
            debug_clients.remove(websocket)


@router.websocket("/voice-stream")
async def demo_voice_stream(websocket: WebSocket):
    """
    Browser-compatible WebSocket for voice streaming.
    """
    await websocket.accept()
    phone = websocket.query_params.get("phone", "+1234567890")
    print(f"\n🔌 [Demo] Browser WebSocket connected. Phone: {phone}")

    # Use an event to signal when the call ends (keeps both tasks alive)
    call_done = asyncio.Event()
    openai_ws = None
    call_start_time = None
    user_transcripts = []
    ai_transcripts = []

    # Silence watchdog state
    last_ai_response_done_at: list = [None]   # use list so inner funcs can mutate
    caller_spoke_after_ai: list = [False]       # reset when AI responds, set when caller speaks
    watchdog_active: list = [False]             # becomes True after first greeting is done
    # 3-state call-close tracker: 0=idle, 1=end_call_permission_asked
    call_close_state: list = [0]
    listening_active: list = [False]

    async def _send(msg: dict):
        """Helper to safely send JSON to browser."""
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    async def _debug(event, message):
        """Send debug to browser + print to Docker logs."""
        print(f"  [{event}] {message}")
        await _send({"type": "debug", "event": event, "message": message,
                      "timestamp": datetime.now().strftime("%H:%M:%S")})

    try:
        await _debug("connected", "🔌 Connected to AI Voice Server")

        # ── Try connecting to OpenAI ──
        if OPENAI_API_KEY:
            try:
                await _debug("openai_connecting", "🤖 Connecting to OpenAI Realtime API...")

                headers = {
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                }
                openai_ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers)
                await _debug("openai_connected", "🟢 OpenAI Realtime API connected!")

                # GHL profile lookup for Demo
                from services.ghl import get_contact_profile_by_phone
                from services.known_clients import find_known_client_by_phone, profile_from_known_client
                
                contact_name = "Prospect"
                client_type = "Prospect"
                group = ""
                contact_id = ""
                invoice_due = "false"
                email = ""
                business_name = ""
                client_notes = ""
                try:
                    known_client = find_known_client_by_phone(phone)
                    profile = profile_from_known_client(known_client) if known_client else await get_contact_profile_by_phone(phone)
                    if profile.get("found"):
                        contact_name = profile.get("name", "Client")
                        client_type = profile.get("client_type", "Prospect")
                        group = profile.get("group") or ""
                        contact_id = profile.get("contact_id") or ""
                        invoice_due = "true" if profile.get("invoice_due") else "false"
                        email = profile.get("email") or ""
                        business_name = profile.get("business_name") or ""
                        client_notes = profile.get("notes") or ""
                        source = profile.get("source") or "ghl"
                        print(f"📌 [Demo Client:{source}] {phone} -> {contact_name} | {client_type}")
                except Exception as e:
                    print(f"Error fetching contact profile in demo: {e}")

                instructions, selected_greeting = system_prompt()
                
                # Greet known client by notes if available, else by name
                greeting_name = client_notes.strip() if (client_notes and client_notes.strip()) else contact_name
                if greeting_name and greeting_name != "Prospect":
                    if "Dhonnobad, Thank you for calling Pay Minimum Tax" in selected_greeting:
                        selected_greeting = selected_greeting.replace("How can I help you?", f"Hello, {greeting_name}! How can I help you today?")
                        selected_greeting = selected_greeting.replace("What could I do for you?", f"Hello, {greeting_name}! What can I do for you today?")
                        selected_greeting = selected_greeting.replace("Who do I have the pleasure to speak with today?", f"Hello, {greeting_name}! How can I help you today?")
                    else:
                        selected_greeting = f"Dhonnobad, Thank you for calling Pay Minimum Tax, I am রেবা. Hello, {greeting_name}! How can I help you today?"

                session_update = {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "model": OPENAI_REALTIME_MODEL,
                        "output_modalities": ["audio"],
                        "instructions": instructions + f"""

                        # LIVE SESSION RULES (OVERRIDE NOTHING — ADD TO ABOVE)

                        ## LANGUAGE LOCK — CRITICAL
                        - You support EXACTLY TWO languages: English and Banglish (romanized Bangla).
                        - NEVER output Bengali Unicode characters (e.g. ক খ গ). Always write Bangla in Latin script (Banglish).
                        - DEFAULT language is ENGLISH. Start in English.
                        - SWITCH to Banglish ONLY when the caller speaks a FULL Bangla/Banglish sentence. A single word, name, or greeting is NOT enough.
                        - Once you detect the caller's language (English or Banglish), LOCK to it for the entire call. Never switch back.
                        - If the caller speaks a third language (Hindi, Gujarati, Spanish, Chinese, etc.) say ONCE: "I am sorry, I only speak English and Bangla. How can I help you?" then continue in English.
                        - IGNORE transcription noise, foreign hallucinations, or background sounds. Respond only to clear human speech.

                        ## CALL ENDING — 2-STEP ONLY
                        STEP 1: After completing a task, ask ONCE if they need more help:
                          English: "Is there anything else I can help you with today?"
                          Banglish: "Ar kono help lagbe apnar?"
                        STEP 2: If they say NO (no, nah, na, na dhonnobad, that's all, ar lagbe na, nothing else, thanks):
                          Say a warm SHORT goodbye in the SAME language, then call end_call immediately.
                          English goodbye: "Thank you for calling Pay Minimum Tax! Have a great day. Goodbye!"
                          Banglish goodbye: "Dhonnobad, Pay Minimum Tax-e call korar jonno. Bhalo thakben. Khoda Hafez!"
                        If they say YES or have more (yes, haan, acha, bolun, ektu, wait, one more):
                          Say "Of course, go ahead!" or "Ji bolun!" and continue. Do NOT call end_call.
                        CRITICAL: Do NOT ask "Can I end the call?" or "Ami ki call shesh kore dii?" — this extra permission step is REMOVED.

                        ## NO REPETITIVE QUESTIONS
                        - The caller's phone number is ALREADY KNOWN: {phone}. NEVER ask for it again.
                        - If the caller's email is in the CRM profile, or if they have already provided it in this conversation, NEVER ask for it again.
                        - Only collect email once if it is missing from both the CRM profile and the conversation history. Once provided, remember it.
                        - NEVER ask for information already visible in the CALLER CRM PROFILE or already collected during this call.

                        ## PAYMENT MODEL
                        - For paid appointments (virtual_cpa_45, office_cpa_45): payment is a PRE-PAYMENT credited to their invoice. It is NOT an extra charge.
                        - English: "This payment will be credited towards your invoice — it's not an extra charge."
                        - Banglish: "Ei payment ta apnar invoice-e credit hoye jabe — eta alada kono charge na."

                        ## CALLER CRM PROFILE
                        - Name: {contact_name}
                        - Phone: {phone} (confirmed — do NOT ask)
                        - Client Type: {client_type}
                        - Group: {group}
                        - Contact ID: {contact_id}
                        - Email: {email if email else 'Not on file — ask once if needed'}
                        - Business Name: {business_name if business_name else 'Not Provided'}
                        - Client Notes: {client_notes if client_notes else 'None'}
                        - Has Invoice Due: {invoice_due}
                        """,
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {
                                    "model": "whisper-1"
                                    # NOTE: Do NOT set "language" here. Callers speak Banglish
                                    # (romanized Bengali in Latin script). Forcing "bn" causes
                                    # Whisper to drop transcripts entirely for mixed speech.
                                },
                                "turn_detection": {
                                    "type": "server_vad",
                                    "threshold": 0.65,        # lower = less likely to fire on mid-sentence pauses
                                    "prefix_padding_ms": 400, # capture start of speech more reliably
                                    "silence_duration_ms": 800 # longer wait — Bangla speech has natural mid-sentence pauses
                                },
                            },
                            "output": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "voice": "shimmer",
                            },
                        },
                        "tools": [
                            {
                                "type": "function",
                                "name": "get_available_slots",
                                "description": "Fetch available booking slots for the next 7 days.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "calendar_type": {
                                            "type": "string", 
                                            "enum": ["follow_up_c", "follow_up_b", "virtual_consult_15", "virtual_cpa_45", "office_cpa_45", "test_calendar"],
                                            "description": "The type of meeting to check slots for"
                                        }
                                    },
                                    "required": ["calendar_type"]
                                }
                            },
                            {
                                "type": "function",
                                "name": "book_appointment",
                                "description": "For prospects/demo callers, send the payment link email for the selected appointment. Call this ONLY after getting name, email, phone, requested slot, and explicit caller confirmation to receive the payment link. The appointment is booked after Stripe payment.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "email": {"type": "string"},
                                        "phone": {"type": "string"},
                                        "booking_slot": {"type": "string", "description": "ISO date format like 2026-06-10T10:00:00Z"},
                                        "calendar_type": {
                                            "type": "string", 
                                            "enum": ["follow_up_c", "follow_up_b", "virtual_consult_15", "virtual_cpa_45", "office_cpa_45", "test_calendar"],
                                            "description": "The type of meeting the user selected"
                                        },
                                        "call_summary": {"type": "string"}
                                    },
                                    "required": ["name", "email", "phone", "booking_slot", "calendar_type", "call_summary"]
                                }
                            },
                            {
                                "type": "function",
                                "name": "transfer_call",
                                "description": "Transfer the caller to a team member like Simon or Tanzina. Call this when the user is a VIP or requests a human.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "target": {"type": "string", "enum": ["simon", "tanzina", "alex"]},
                                        "reason": {"type": "string"}
                                    },
                                    "required": ["target", "reason"]
                                }
                            },
                            {
                                "type": "function",
                                "name": "end_call",
                                "description": "End the demo session. ONLY call this AFTER you have explicitly asked the user for permission to end the call (e.g. 'Can I end the call now?') AND they have said YES. Never use this just because they say goodbye.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "reason": {
                                            "type": "string",
                                            "description": "Reason: 'caller_goodbye', 'task_complete', 'no_response', 'caller_request'"
                                        }
                                    },
                                    "required": ["reason"]
                                }
                            },
                            {
                                "type": "function",
                                "name": "record_message",
                                "description": "Record a callback request or message for a team member in the CRM. Call this when the client wants Simon or another team member to call them back, or wants to leave a message.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "caller_name": {"type": "string", "description": "The name of the caller"},
                                        "caller_phone": {"type": "string", "description": "The callback phone number"},
                                        "message": {"type": "string", "description": "The message details or why they want a callback"},
                                        "call_reason": {"type": "string", "description": "The reason for the call (e.g. tax, notice, callback)"}
                                    },
                                    "required": ["caller_name", "caller_phone", "message"]
                                }
                            },
                            {
                                "type": "function",
                                "name": "send_link_sms",
                                "description": "Send a portal sign up, login, or direct document notice upload link via SMS text message to the caller. Call this when the user agrees to receive a link via text/SMS.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "link_type": {
                                            "type": "string",
                                            "enum": ["signup", "login", "upload"],
                                            "description": "The type of link to send: 'signup' for portal.payminimumtax.com/signup, 'login' for portal.payminimumtax.com/login, 'upload' for www.PayMinimumTax.com/upload"
                                         },
                                         "phone_number": {
                                             "type": "string",
                                             "description": "Optional destination phone number. Defaults to the caller's phone."
                                         }
                                    },
                                    "required": ["link_type"]
                                }
                            },
                            {
                                "type": "function",
                                "name": "send_link_email",
                                "description": "Send a portal, payment, or any link to the caller via EMAIL. Use this when the caller says they don't have their phone, or explicitly asks to receive a link by email instead of text. Ask for their email address first if you don't already have it.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "email": {
                                            "type": "string",
                                            "description": "The caller's email address to send the link to."
                                        },
                                        "link_type": {
                                            "type": "string",
                                            "enum": ["signup", "login", "upload", "payment", "custom"],
                                            "description": "Type of link: 'signup'=portal signup, 'login'=portal login, 'upload'=document upload, 'payment'=Stripe payment link, 'custom'=any other URL."
                                        },
                                        "custom_url": {
                                            "type": "string",
                                            "description": "Required when link_type is 'payment' or 'custom'. The full URL to send."
                                        },
                                        "name": {
                                            "type": "string",
                                            "description": "The caller's name for the email greeting."
                                        }
                                    },
                                    "required": ["email", "link_type"]
                                }
                            }

                        ],
                        "tool_choice": "auto"
                    }
                }
                await openai_ws.send(json.dumps(session_update))
                await _debug("session_configured", "📝 Session configured (PCM16, 24kHz, VAD)")

                initial_greeting = {
                    "type": "response.create",
                    "response": {
                        "output_modalities": ["audio"],
                        "instructions": f"Greet the caller by saying: \"{selected_greeting}\". Speak it naturally and warmly. IMPORTANT: Use ONLY English or Bangla. NEVER use any other language."
                    }
                }
                await openai_ws.send(json.dumps(initial_greeting))
                await _debug("greeting_sent", "🗣️ AI is preparing a greeting...")
                # Watchdog starts counting after greeting is sent
                watchdog_active[0] = True

            except Exception as e:
                print(f"🔴 [Demo] OpenAI error: {e}")
                await _debug("openai_error", f"🔴 OpenAI error: {str(e)[:120]}. Using mock mode.")
                openai_ws = None
        else:
            await _debug("mock_mode", "⚠️ No OPENAI_API_KEY set. Running in Mock Mode — voice won't respond but debug works.")

        # ── Task 1: Receive audio from browser ──
        async def receive_from_browser():
            nonlocal call_start_time
            chunk_count = 0
            try:
                while not call_done.is_set():
                    raw = await websocket.receive_text()
                    msg = json.loads(raw)

                    if msg.get("type") == "start":
                        call_start_time = time.time()
                        await _debug("call_started", "📞 Call started! Listening...")

                    elif msg.get("type") == "audio":
                        chunk_count += 1
                        audio_data = msg.get("data", "")
                        if chunk_count % 50 == 0:
                            await _debug("audio_chunks", f"🔊 Received {chunk_count} audio chunks from mic")
                        if openai_ws and listening_active[0]:
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": audio_data
                            }))

                    elif msg.get("type") == "stop":
                        duration = round(time.time() - call_start_time, 1) if call_start_time else 0
                        await _debug("call_ended", f"🛑 Call ended. Duration: {duration}s, Chunks: {chunk_count}")
                        summary = _build_call_summary(user_transcripts, ai_transcripts, duration)
                        await _send({"type": "call_summary", "data": summary})
                        call_done.set()
                        break

                    elif msg.get("type") == "ping":
                        await _send({"type": "pong"})

            except WebSocketDisconnect:
                print("⚠️ [Demo] Browser disconnected.")
                call_done.set()
            except Exception as e:
                print(f"🔴 [Demo] receive error: {e}")
                call_done.set()

        # ── Task 2: Forward OpenAI responses to browser ──
        async def send_to_browser():
            if not openai_ws:
                # In mock mode, just wait until the call is done
                await call_done.wait()
                return

            # ── Helper: cleanly end the demo session ──
            async def _end_demo_call():
                if call_done.is_set():
                    return
                call_done.set()
                try:
                    await _send({"type": "call_ended", "reason": "goodbye"})
                    await _debug("call_end", "📞 Call ended by AI goodbye detection.")
                except Exception:
                    pass

            ai_chunk_count = 0
            # asyncio.Event for atomic cross-coroutine interrupt signaling
            interrupt_event = asyncio.Event()
            last_assistant_item_id = None
            response_audio_sent_ms = 0
            end_call_in_progress = [False]
            # After a decline, block the AI's re-ask for 30s to prevent permission loop
            end_call_blocked_until = [0.0]

            async def _end_demo_after_consent():
                if end_call_in_progress[0] or call_done.is_set():
                    return
                end_call_in_progress[0] = True
                call_close_state[0] = 0
                # Block orphaned audio from the OLD response that was playing
                interrupt_event.set()
                try:
                    await openai_ws.send(json.dumps({"type": "response.cancel"}))
                except Exception:
                    pass
                # Brief pause to let the cancel take effect before sending goodbye
                await asyncio.sleep(0.3)
                # CRITICAL: Clear the interrupt so the NEW goodbye audio can play through
                interrupt_event.clear()

                # Detect conversation language from all transcripts.
                # Score-based: >= 2 Banglish indicator words = Bangla conversation.
                is_bangla_convo = False
                bangla_score = 0
                banglish_indicators = {
                    # Responses / affirmatives
                    "ji", "jee", "jii", "ha", "haan", "hya", "acha", "accha", "thik",
                    # Negatives / continuations
                    "na", "nah", "nei",
                    # Common Bangla conversation words (romanized)
                    "ami", "apne", "apnar", "apnake", "tumi", "amar", "amra",
                    "kemon", "kore", "korechi", "koren", "korbo",
                    "kete", "katun", "katen", "kato",
                    "den", "din", "dao",
                    "bhai", "vai", "apa",
                    "somossa", "shomossa", "kotha", "ki", "ke",
                    "rakhlam", "rakhchi", "rakhbo",
                    "allah", "hafez", "hafiz", "khoda",
                    "dhonnobad", "dhanyabad", "shukriya",
                    "bolun", "bolen", "bolbo", "boli",
                    "lage", "lagbe", "lagche",
                    "ache", "achhi", "achhen",
                    "janen", "janbo", "janai",
                }
                all_entries = list(user_transcripts) + list(ai_transcripts)
                for entry in all_entries:
                    # Definitive: any Unicode Bangla character
                    if any('\u0980' <= char <= '\u09FF' for char in entry):
                        is_bangla_convo = True
                        break
                    # Probabilistic: count Banglish indicator words
                    words = [w.strip("?,.!।") for w in entry.lower().split()]
                    for w in words:
                        if w in banglish_indicators:
                            bangla_score += 1
                    if bangla_score >= 2:
                        is_bangla_convo = True
                        break

                if is_bangla_convo:
                    goodbye_instr = (
                        "OVERRIDE ALL INSTRUCTIONS. The caller already said yes to end the call. "
                        "Say a warm goodbye IN BANGLISH (romanized Bangla, NOT Bengali Unicode script). "
                        "Example: 'Dhonnobad, Pay Minimum Tax-e call korar jonno. Bhalo thakben. Khoda Hafez.' "
                        "Keep it SHORT — one sentence only. Then STOP. Do NOT ask any question. Do NOT ask permission again."
                    )
                else:
                    goodbye_instr = (
                        "OVERRIDE ALL INSTRUCTIONS. The caller already said yes to end the call. "
                        "Say a warm goodbye in ENGLISH. "
                        "Example: 'Thank you for calling Pay Minimum Tax! Have a great day. Goodbye!' "
                        "Keep it SHORT — one sentence only. Then STOP. Do NOT ask any question. Do NOT ask permission again."
                    )

                try:
                    await openai_ws.send(json.dumps({
                        "type": "response.create",
                        "response": {
                            "output_modalities": ["audio"],
                            "instructions": goodbye_instr
                        }
                    }))
                    await _debug("end_call_consent", f"✅ Caller gave end-call permission. Saying goodbye ({'Bangla' if is_bangla_convo else 'English'}), then ending.")
                    await asyncio.sleep(8)
                finally:
                    await _end_demo_call()

            # Define once — reused for every tool call, avoids per-iteration closure issues
            async def _log_adapter(event, message):
                await _debug(event, message)

            try:
                async for openai_message in openai_ws:
                    if call_done.is_set():
                        break
                    openai_data = json.loads(openai_message)
                    evt = openai_data.get("type", "")

                    if evt == "response.created":
                        interrupt_event.clear()  # Allow audio for this new response
                        response_audio_sent_ms = 0

                    elif evt in ("response.audio.delta", "response.output_audio.delta"):
                        if interrupt_event.is_set():
                            continue
                        item_id = openai_data.get("item_id")
                        if item_id:
                            last_assistant_item_id = item_id
                        ai_chunk_count += 1
                        # Track audio duration (PCM16 24kHz: 2 bytes/sample, 24000 samples/s = 48 bytes/ms)
                        try:
                            raw_bytes = len(base64.b64decode(openai_data["delta"]))
                            response_audio_sent_ms += raw_bytes / 48
                        except Exception:
                            response_audio_sent_ms += 20
                        await _send({"type": "audio", "data": openai_data["delta"]})
                        if ai_chunk_count % 50 == 0:
                            await _debug("ai_audio", f"🎙️ Streamed {ai_chunk_count} AI audio chunks")

                    elif evt in (
                        "response.text.done",
                        "response.output_text.done",
                        "response.audio_transcript.done",
                        "response.output_audio_transcript.done",
                    ):
                        text = openai_data.get("text") or openai_data.get("transcript")
                        if text:
                            ai_transcripts.append(text)
                            if _asks_anything_else(text):
                                import time as _time
                                if _time.time() < end_call_blocked_until[0]:
                                    # Still in cooldown — suppress duplicate
                                    await _debug("end_call_blocked", "🚫 AI tried to re-ask 'anything else' during cooldown — suppressed.")
                                elif end_call_in_progress[0]:
                                    await _debug("end_call_blocked", "🚫 End-call in progress — ignoring wrap-up cue.")
                                else:
                                    call_close_state[0] = 1
                                    await _debug("end_call_state", "📋 Wrap-up question asked — next negative reply will trigger goodbye.")
                            await _debug("ai_transcript", f"🤖 AI: {text}")
                            await _send({"type": "transcript", "role": "assistant", "text": text})

                    elif evt == "conversation.item.input_audio_transcription.completed":
                        user_text = openai_data.get("transcript")
                        if user_text:
                            user_transcripts.append(user_text)
                            await _debug("user_transcript", f"👤 User: {user_text}")
                            await _send({"type": "transcript", "role": "user", "text": user_text})

                            # Explicit hangup cues always trigger hangup regardless of state
                            is_explicit = any(cue in user_text.lower() for cue in ["kete dao", "kete den", "kete din", "cut kore den", "kat kore den", "কেটে দাও", "কেটে দেন", "কেটে দিন", "কল কেটে", "কলটা কেটে", "cut the call", "hang up", "allah hafez", "khoda hafez", "রাখলাম", "রাখছি", "rakhlam", "rakhchi", "bye bye", "allah hafiz"])

                            if is_explicit:
                                asyncio.create_task(_end_demo_after_consent())
                            elif call_close_state[0] == 1:
                                # Wrap-up mode: AI asked "anything else?" — evaluate reply
                                is_negative = _is_negative_response(user_text)
                                is_positive  = _is_end_call_consent(user_text)
                                if is_negative:
                                    # Caller doesn't need anything else → goodbye + hang up directly
                                    await _debug("end_call_consent", "✅ Caller said no to 'anything else' — triggering goodbye.")
                                    call_close_state[0] = 0
                                    asyncio.create_task(_end_demo_after_consent())
                                elif is_positive:
                                    # Caller has more to say — keep talking
                                    import time as _time
                                    call_close_state[0] = 0
                                    end_call_blocked_until[0] = _time.time() + 30
                                    await _debug("end_call_declined", "↩️ Caller has more — continuing (blocked for 30s).")
                                else:
                                    # Check if the transcript is actually meaningful (e.g. a new request or question)
                                    if _is_meaningful_transcript(user_text):
                                        await _debug("end_call_meaningful", "📝 Caller spoke a meaningful new sentence. Letting AI handle naturally.")
                                        call_close_state[0] = 0
                                    else:
                                        # Truly unclear / noise — force AI to ask caller to REPEAT
                                        await _debug("end_call_unclear", "❓ Unclear response — forcing AI to ask caller to repeat.")
                                        try:
                                            await openai_ws.send(json.dumps({
                                                "type": "response.create",
                                                "response": {
                                                    "output_modalities": ["audio"],
                                                    "instructions": (
                                                        "The caller's reply was unclear. "
                                                        "Do NOT repeat 'Ar kono help lagbe' or 'Is there anything else I can help you with'. "
                                                        "Ask them ONCE to repeat what they said. "
                                                        "English: 'Sorry, I didn\\'t quite catch that — could you say that again?' "
                                                        "Banglish: 'Sorry, ektu abar bolben?' "
                                                        "Use the SAME language as the rest of the conversation. One short sentence only."
                                                    )
                                                }
                                            }))
                                        except Exception:
                                            pass
                            

                    elif evt == "input_audio_buffer.speech_started":
                        # When end-call is in progress or greeting is still playing, let the audio play uninterrupted.
                        # Cancelling it here causes the call to hang instead of ending cleanly.
                        if end_call_in_progress[0] or not listening_active[0]:
                            await _debug("vad_speech_started", "🌐 Speech detected (ignored — goodbye/greeting in progress)")
                        else:
                            await _debug("vad_speech_started", "🎤 Speech detected — interrupting AI...")
                            interrupt_event.set()

                            async def _notify_interrupt():
                                try:
                                    await _send({"type": "interrupt"})
                                except Exception:
                                    pass
                            asyncio.create_task(_notify_interrupt())

                            try:
                                await openai_ws.send(json.dumps({"type": "response.cancel"}))
                            except Exception:
                                pass
                            if last_assistant_item_id:
                                try:
                                    await openai_ws.send(json.dumps({
                                        "type": "conversation.item.truncate",
                                        "item_id": last_assistant_item_id,
                                        "content_index": 0,
                                        "audio_end_ms": int(response_audio_sent_ms)
                                    }))
                                except Exception:
                                    pass
                        
                        # Caller spoke — reset silence tracking (if listening is active)
                        if listening_active[0]:
                            caller_spoke_after_ai[0] = True

                    elif evt == "input_audio_buffer.speech_stopped":
                        await _debug("vad_speech_stopped", "🔇 Speech ended, processing...")

                    # Response finished or cancelled — clear interrupt so next response plays normally
                    elif evt in ("response.done", "response.cancelled"):
                        interrupt_event.clear()
                        # Start silence timer: AI finished speaking, now waiting for caller
                        # We add the audio duration to the current time so the watchdog only starts counting
                        # AFTER the caller actually finishes hearing the audio.
                        audio_duration_sec = response_audio_sent_ms / 1000.0
                        last_ai_response_done_at[0] = asyncio.get_event_loop().time() + audio_duration_sec
                        caller_spoke_after_ai[0] = False
                        await _debug("response_done", f"✅ Response done. Audio duration: {audio_duration_sec:.2f}s")

                        if not listening_active[0]:
                            async def activate_listening_after_delay(delay: float):
                                await asyncio.sleep(max(0.0, delay))
                                if not call_done.is_set():
                                    listening_active[0] = True
                                    await _debug("listening_active", "🟢 Greeting playback completed. Listening activated!")
                            asyncio.create_task(activate_listening_after_delay(audio_duration_sec))

                    elif evt == "response.function_call_arguments.done":
                        func_name = openai_data.get("name")
                        call_id = openai_data.get("call_id")
                        args = json.loads(openai_data.get("arguments", "{}"))
                        await _debug("tool_call", f"🛠️ AI called {func_name} with args: {args}")
                        result = {}  # Default — prevents NameError if func_name is unknown
                        
                        if func_name == "transfer_call":
                            result = await handle_transfer_call(args, openai_ws, call_done, call_id, _log_adapter)
                            await openai_ws.send(json.dumps({
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": json.dumps(result)
                                }
                            }))
                            if result.get("status") == "office_closed":
                                await openai_ws.send(json.dumps({
                                    "type": "response.create",
                                    "response": {
                                        "instructions": (
                                            "CRITICAL: Respond in the EXACT SAME language (English or Bangla/Banglish) "
                                            "that the caller has been using throughout this conversation. "
                                            "Do not repeat yourself."
                                        )
                                    }
                                }))
                            continue

                        elif func_name == "book_appointment":
                            result = await handle_book_appointment(args, _log_adapter)
                        
                        elif func_name == "get_available_slots":
                            result = await handle_get_slots(args, _log_adapter)
                        
                        elif func_name == "end_call":
                            await handle_end_call(
                                args,
                                openai_ws,
                                call_done,
                                end_call_in_progress,
                                user_transcripts,  # Only user speech for language detection
                                call_id,
                                _log_adapter,
                                _end_demo_call
                            )
                            continue

                        elif func_name == "record_message":
                            result = await handle_record_message(
                                args=args,
                                contact_id="",
                                default_name="Demo Caller",
                                default_phone=phone,
                                logger_or_debug=_log_adapter
                            )

                        elif func_name == "send_link_sms":
                            result = await handle_send_link_sms(
                                args=args,
                                default_phone=phone,
                                logger_or_debug=_log_adapter
                            )

                        elif func_name == "send_link_email":
                            result = await handle_send_link_email(
                                args=args,
                                logger_or_debug=_log_adapter
                            )


                        # Send output back to OpenAI for ANY tool call
                        await openai_ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": json.dumps(result)
                            }
                        }))
                        # Trigger response — with language enforcement so AI never switches
                        # language after a tool call returns an English-language result
                        await openai_ws.send(json.dumps({
                            "type": "response.create",
                            "response": {
                                "instructions": (
                                    "CRITICAL: Respond in the EXACT SAME language (English or Bangla/Banglish) "
                                    "that the caller has been using throughout this conversation. "
                                    "DO NOT switch to English just because the tool result is in English."
                                )
                            }
                        }))

            except Exception as e:
                print(f"🔴 [Demo] OpenAI stream error: {e}")
                await _debug("openai_stream_error", f"🔴 OpenAI stream error: {str(e)[:100]}")

        # ── Task 3: Silence watchdog ──
        async def silence_watchdog():
            """If the caller is silent for 12s after the AI finishes speaking, send a gentle nudge."""
            SILENCE_TIMEOUT = 12  # seconds to wait before nudging
            while not call_done.is_set():
                await asyncio.sleep(1)
                if not watchdog_active[0] or not openai_ws:
                    continue
                t = last_ai_response_done_at[0]
                if t is None:
                    continue
                elapsed = asyncio.get_event_loop().time() - t
                if elapsed >= SILENCE_TIMEOUT and not caller_spoke_after_ai[0]:
                    # Inject a gentle nudge via OpenAI
                    try:
                        await openai_ws.send(json.dumps({
                            "type": "response.create",
                            "response": {
                                "output_modalities": ["audio"],
                                "instructions": "The caller has been silent for a while. Politely ask if they are still there (e.g. 'Are you still with me?'). IMPORTANT: Ask in the EXACT same language (English or Bangla) that the conversation is currently in. Keep it to one short natural sentence."
                            }
                        }))
                        await _debug("silence_nudge", "⏱️ Silence timeout — sending dynamic nudge")
                    except Exception:
                        pass
                    # Reset timer so we don't spam — next nudge in another 15s
                    last_ai_response_done_at[0] = asyncio.get_event_loop().time()

        # ── Run all tasks concurrently ──
        await asyncio.gather(receive_from_browser(), send_to_browser(), silence_watchdog(), return_exceptions=True)

    except WebSocketDisconnect:
        print("⚠️ [Demo] WebSocket disconnected (outer).")
        call_done.set()
    except Exception as e:
        print(f"🔴 [Demo] Unhandled error in voice-stream: {e}")
        import traceback
        traceback.print_exc()
        call_done.set()
    finally:
        call_done.set()   # ensure all tasks are released in any scenario
        if openai_ws:
            try:
                await openai_ws.close()
            except Exception:
                pass
        print("🔒 [Demo] Voice session closed.")


def _build_call_summary(user_transcripts, ai_transcripts, duration):
    """Build a summary object from the call transcripts."""
    conversation = []
    max_len = max(len(user_transcripts), len(ai_transcripts), 1)  # guard against empty lists
    
    for i in range(max_len):
        if i < len(ai_transcripts):
            conversation.append(f"AI: {ai_transcripts[i]}")
        if i < len(user_transcripts):
            conversation.append(f"Caller: {user_transcripts[i]}")
    
    return {
        "duration": f"{duration}s",
        "user_messages": len(user_transcripts),
        "ai_messages": len(ai_transcripts),
        "conversation": "\n".join(conversation) if conversation else "No transcripts (mock mode or short call)",
        "summary": f"Call lasted {duration}s with {len(user_transcripts)} caller messages and {len(ai_transcripts)} AI responses."
    }
