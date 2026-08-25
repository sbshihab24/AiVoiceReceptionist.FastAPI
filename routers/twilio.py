import logging
logger = logging.getLogger(__name__)

import os,json,base64,asyncio,httpx,datetime,html,random,re,urllib.parse
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
import websockets
from database import SessionLocal
from models.activity_models import CallLog
from config import FORWARD_SIMON, FORWARD_TANZINA, FORWARD_ALEX, FORWARD_NAFI
from services.known_clients import find_known_client_by_phone, find_known_client_by_email, find_known_client_by_company, profile_from_known_client
from services.openai_realtime import get_openai_realtime_model, get_openai_realtime_ws_url
from routers.common_tools import (
    ADS,
    handle_book_appointment,
    handle_get_slots,
    handle_transfer_call,
    handle_end_call,
    handle_send_link_sms,
    handle_send_link_email,
    handle_record_message,
)

router = APIRouter(
    prefix="/api/twilio",
    tags=["Twilio Webhooks"]
)

# Configuration for Twilio API
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_SID = os.getenv("TWILIO_SID", "")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER", "")

# Map of forward targets to phone numbers
FORWARD_MAP = {
    "simon":   FORWARD_SIMON,
    "tanzina": FORWARD_TANZINA,
    "alex":    FORWARD_ALEX,
    "nafi":    FORWARD_NAFI,
}

# Configuration for real-time conversational AI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = get_openai_realtime_model()
OPENAI_WS_URL = get_openai_realtime_ws_url()

IGNORED_TRANSCRIPT_WORDS = {
    "hello",
    "hi",
    "hey",
    "hmm",
    "um",
    "uh",
    "hola",
}

MEANINGFUL_SHORT_WORDS = {
    "yes",
    "yeah",
    "yep",
    "no",
    "ok",
    "okay",
    "bye",
}


END_CALL_CONSENT_WORDS = {
    "yes",
    "yeah",
    "yep",
    "ya",
    "ok",
    "okay",
    "sure",
    "ji",
    "jee",
    "jii",
    "ha",
    "haan",
    "hya",
    "acha",
    "accha",
    "kato",
    "katen",
    "katun",
    "cut",
    "bye",
}

END_CALL_CONSENT_PHRASES = (
    "go ahead",
    "you can",
    "cut it",
    "cut the call",
    "end it",
    "end the call",
    "hang up",
    "disconnect",
    "no more",
    "nothing else",
    "that's all",
    "thats all",
    "all good",
    "kete dao",
    "kete den",
    "kete din",
    "cut kore den",
    "kat kore den",
    "কেটে দাও",
    "কেটে দেন",
    "কেটে দিন",
    "কল কেটে",
    "কলটা কেটে",
    "আর কিছু না",
    "আর লাগবে না",
    "কিছু লাগবে না",
)

END_CALL_BANGLA_CONSENT = (
    "হ্যাঁ",
    "হ্যা",
    "হা",
    "জি",
    "জী",
    "ঠিক আছে",
    "আচ্ছা",
    "কাটো",
    "কাটেন",
    "কাটুন",
    "কেটে",
    "শেষ",
)

# Phrases the AI says when wrapping up — "anything else?" type questions
# When the Python layer detects these in the AI transcript, it enters
# call_close_state=1 ("wrap-up mode"). The NEXT negative caller reply
# directly triggers goodbye+hangup — no second permission question needed.
END_CALL_WRAPUP_CUES = (
    "anything else",
    "anything more",
    "is there anything",
    "can i help you with anything",
    "how else can i help",
    "help you with anything else",
    "ar kono help",
    "ar kichu lagbe",
    "ar kono",
    "ar kichu",
    "ar kivabe",
    "help lagbe",
    "kichu lagbe",
    "আর কিছু লাগবে",
    "আর কোনো",
    "আর কিছু",
)


def _normalized_words(text: str) -> list[str]:
    cleaned = re.sub(r"[^A-Za-z0-9\u0980-\u09FF\s']", " ", text or "").lower()
    return [word for word in cleaned.split() if word]


def _normalized_text(text: str) -> str:
    return " ".join(_normalized_words(text))


def _asks_anything_else(text: str) -> bool:
    """Returns True when the AI asks if the caller needs anything else (wrap-up cue)."""
    lowered = (text or "").lower()
    normalized = _normalized_text(text)
    return any(cue in lowered or cue in normalized for cue in END_CALL_WRAPUP_CUES)


def _is_end_call_consent(text: str) -> bool:
    lowered = (text or "").lower()
    normalized = _normalized_text(text)
    words = set(normalized.split())

    if words & END_CALL_CONSENT_WORDS:
        return True
    if any(phrase in lowered or phrase in normalized for phrase in END_CALL_CONSENT_PHRASES):
        return True
    if any(phrase in (text or "") for phrase in END_CALL_BANGLA_CONSENT):
        return True
    return False


# Words/phrases that mean "No" / "done" / "nothing else needed"
# We exclude highly ambiguous words like 'ha' or 'ma' from direct word-set matching
# to prevent false hangups in longer sentences.
_NEGATIVE_WORDS = {
    # English
    "no", "nope", "nah", "not", "never", "nothing", "none", "done", "finished",
    "that's", "thats",  # "that's all", "that's it"
    # Banglish romanized
    "na", "naa", "nah", "nei",
    # Unicode Bangla
    "না", "নাহ", "নো",
}
_NEGATIVE_PHRASES = (
    # English — clear, short end-of-conversation phrases only
    "no thank", "no need", "not yet", "not right", "that's all", "thats all",
    "that's it", "thats it", "all done", "all good", "i'm good", "im good",
    "nothing else", "no more",
    # Banglish — unambiguous "done / no more" phrases
    "ar lagbe na", "ar kichu na", "ar kono na", "ar na", "lagbe na",
    "kono help na", "kichu lagbe na", "bole diechi", "dhonnobad", "thank you", "thanks",
    # NOTE: removed "i have", "actually", "wait", "hold on", "one more" — these are
    # too ambiguous and caused false hangups when callers asked follow-up questions.
    # NOTE: removed Bengali "আরো", "আরও", "একটু", "বলতে চাই" — these mean "more" /
    # "I want to say something" which is POSITIVE, not end-of-call.
)

def _is_negative_response(text: str) -> bool:
    """Returns True if the user is saying NO / wants to end the conversation."""
    lowered = (text or "").lower().strip()
    normalized = _normalized_text(text)
    words = set(normalized.split())
    word_list = normalized.split()

    # ── LONG SENTENCE GUARD ──────────────────────────────────────────────────
    # If the caller says more than 5 words, they are almost certainly asking a
    # question or making a new request — NOT saying goodbye.
    # Only match against unambiguous, multi-word end-of-conversation phrases.
    # Do NOT fire on a single embedded negative word like "no" or "not".
    if len(word_list) > 5:
        clear_end_phrases = (
            "that's all", "thats all", "that's it", "thats it",
            "all done", "nothing else", "no more",
            "ar lagbe na", "ar kichu na", "lagbe na", "kichu lagbe na",
        )
        return any(phrase in lowered or phrase in normalized for phrase in clear_end_phrases)

    # 1. Direct match on clear negative words (short responses only)
    if words & _NEGATIVE_WORDS:
        return True

    # 2. Match on clear negative phrases
    if any(phrase in lowered or phrase in normalized for phrase in _NEGATIVE_PHRASES):
        return True

    # 3. Handle common single-word Whisper mishearings of 'na'
    # If the response is EXACTLY one of these ambiguous/misheard words, treat it as "no".
    # This prevents false hangups when these words appear in longer positive sentences.
    single_word_mishearings = {"ma", "mha", "ba", "da", "ha", "ah", "uh", "oh", "now", "know"}
    if normalized in single_word_mishearings:
        return True

    # 4. Unicode fallback
    if any(char in (text or "") for char in ["না", "নাহ"]):
        return True

    return False


def _is_meaningful_transcript(text: str) -> bool:
    cleaned = re.sub(r"[^A-Za-z0-9\u0980-\u09FF\s]", " ", text or "").strip()
    if not cleaned:
        return False

    words = [word.lower() for word in cleaned.split()]
    if not words:
        return False

    has_bangla = any("\u0980" <= char <= "\u09FF" for char in cleaned)
    has_alnum = any(char.isalnum() for char in cleaned)
    if not has_bangla and not has_alnum:
        return False

    if len(words) == 1 and words[0] in MEANINGFUL_SHORT_WORDS:
        return True
    if len(words) == 1 and words[0] in IGNORED_TRANSCRIPT_WORDS:
        return False
    if len(cleaned) < 4 and not has_bangla:
        return False
    return True


@router.post("/session")
async def create_session():
    """
    Generate an ephemeral session token for WebRTC/WebSocket real-time client use.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key is not set")

    from services.prompts import system_prompt
    instructions, _ = system_prompt()
    
    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    
    data = {
        "type": "realtime",
        "model": OPENAI_REALTIME_MODEL,
        "output_modalities": ["audio"],
        "instructions": instructions,
        "audio": {
            "output": {
                "voice": "shimmer",
            },
        },
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=data)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))



@router.post("/make-call")
async def make_outbound_call(request: Request):
    """
    Triggers an outbound call via Twilio and bridges it to the Realtime AI voice stream.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    to_number = body.get("to")
    if not to_number:
        raise HTTPException(status_code=400, detail="Missing 'to' phone number")

    if not TWILIO_SID or not TWILIO_AUTH_TOKEN or not TWILIO_NUMBER:
        raise HTTPException(status_code=500, detail="Twilio credentials are not set")

    # Define the TwiML webhook URL for the outbound call
    host = request.headers.get("host", request.base_url.hostname)
    protocol = "https" if ("localhost" not in host or "ngrok" in host) else "http"
    if "ngrok" in host:
        protocol = "https"
    twiml_url = f"{protocol}://{host}/api/twilio/incoming-call"

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls.json"
    
    # Twilio uses Form URL Encoded data for outbound calls
    auth_header = base64.b64encode(f"{TWILIO_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    data = {
        "To": to_number,
        "From": TWILIO_NUMBER,
        "Url": twiml_url
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, data=data)
            if response.status_code not in (200, 201):
                raise HTTPException(status_code=response.status_code, detail=response.text)
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))





def _public_base_url(host: str, default_protocol: str = "https") -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    if host.startswith(("http://", "https://")):
        return host

    is_local = host.startswith("localhost") or host.startswith("127.0.0.1")
    protocol = "http" if is_local else default_protocol
    return f"{protocol}://{host}"


def _public_twilio_url(host: str, path: str, query: dict | None = None) -> str:
    base_url = _public_base_url(host)
    if not base_url:
        return ""

    path = path if path.startswith("/") else f"/{path}"
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    return url


def _forward_number_from_query(raw_number: str) -> str:
    raw_number = raw_number or ""
    # Repair old unescaped URLs where "+1555..." arrived from the query as " 1555...".
    if raw_number.startswith(" ") and raw_number.strip():
        return f"+{raw_number.strip()}"
    return raw_number.strip()


def _twiml_text(text: str) -> str:
    return html.escape(text or "", quote=False)


@router.post("/forward-call")
async def forward_call(request: Request):
    """
    TwiML endpoint Twilio calls when redirecting a call to a team member.

    Flow:
      attempt=1 → "Please hold on for a moment while I connect you..." + Ad → Dial(15s)
      attempt=2 → "I am sorry, they haven't picked up yet. I am still trying to connect, please stay on the line." → Dial(15s)
      attempt≥3 → "I am sorry, they are not available right now. I'll make sure they get your message. Is there anything else I can help you with today?"

    Ads always play at least once (attempt 1) before any failure message.
    """
    to_number = _forward_number_from_query(request.query_params.get("to", ""))
    attempt   = int(request.query_params.get("attempt", "1"))
    host      = os.getenv("PUBLIC_HOST", request.headers.get("host", request.base_url.hostname))

    logger.info(f"📲 [Forward] to={to_number} attempt={attempt}")

    if not to_number:
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Sorry, we could not connect your call at this time. Please try again later.</Say>
</Response>"""
        return Response(content=twiml, media_type="text/xml")

    # Build the fallback URL that carries to/attempt forward
    fallback_url = _public_twilio_url(
        host,
        "/api/twilio/forward-fallback",
        {"to": to_number, "attempt": attempt},
    )
    fallback_url_xml = html.escape(fallback_url, quote=True)
    to_number_xml = html.escape(to_number, quote=False)

    reason = request.query_params.get("reason", "").lower()
    urgent_keywords = ["irs", "notice", "audit", "urgent", "deadline", "compliance", "penalty"]
    is_urgent = any(kw in reason for kw in urgent_keywords)

    if attempt == 1:
        # First attempt: check if urgent to skip ad
        if is_urgent:
            logger.info(f"📣 [Forward] Urgent call detected. Skipping ad for {to_number}")
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Let me try to reach someone for you. Please hold on.</Say>
    <Say voice="Polly.Joanna">I'm still trying to connect you, please wait.</Say>
    <Dial callerId="{TWILIO_NUMBER}" timeout="15" action="{fallback_url_xml}">
        <Number>{to_number_xml}</Number>
    </Dial>
</Response>"""
        else:
            ad_message = random.choice(ADS)
            logger.info(f"📣 [Forward] Ad selected: {ad_message[:50]}...")
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Please hold on for a moment while I connect you.</Say>
    <Say voice="Polly.Joanna">{_twiml_text(ad_message)}</Say>
    <Say voice="Polly.Joanna">I'm still trying to connect you, please wait.</Say>
    <Dial callerId="{TWILIO_NUMBER}" timeout="15" action="{fallback_url_xml}">
        <Number>{to_number_xml}</Number>
    </Dial>
</Response>"""

    elif attempt == 2:
        # Second attempt: brief hold message, then dial again
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">They haven't picked up yet. I'm still trying — please stay on the line.</Say>
    <Dial callerId="{TWILIO_NUMBER}" timeout="15" action="{fallback_url_xml}">
        <Number>{to_number_xml}</Number>
    </Dial>
</Response>"""

    else:
        # All attempts exhausted — person is unavailable
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">I am sorry, they are not available right now. I'll make sure they get your message. Is there anything else I can help you with today?</Say>
</Response>"""

    return Response(content=twiml, media_type="text/xml")


@router.post("/forward-fallback")
async def forward_fallback(request: Request):
    """
    Called by Twilio when a Dial attempt ends (no-answer, busy, failed).
    Increments attempt counter and redirects back to /forward-call.
    """
    form_data   = await request.form()
    dial_status = form_data.get("DialCallStatus", "no-answer")
    to_number   = _forward_number_from_query(request.query_params.get("to", ""))
    attempt     = int(request.query_params.get("attempt", "1"))
    host        = os.getenv("PUBLIC_HOST", request.headers.get("host", request.base_url.hostname))

    logger.info(f"📵 [Fallback] DialCallStatus={dial_status} to={to_number} attempt={attempt}")

    if dial_status == "completed":
        # Call was answered and finished normally — just hang up cleanly
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response></Response>"""
    else:
        # Not answered — try again or give up
        next_attempt  = attempt + 1
        next_url = _public_twilio_url(
            host,
            "/api/twilio/forward-call",
            {"to": to_number, "attempt": next_attempt},
        )
        next_url_xml = html.escape(next_url, quote=False)
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Redirect method="POST">{next_url_xml}</Redirect>
</Response>"""

    return Response(content=twiml, media_type="text/xml")



@router.post("/incoming-call")
async def incoming_call(request: Request):
    host = request.headers.get("host", str(request.base_url.hostname))
    form_data = await request.form()
    caller_number = form_data.get("From", "Unknown")
    from services.ghl import get_contact_profile_by_phone
    import urllib.parse

    # Full GHL profile lookup
    contact_name = "Prospect"
    client_type = "Prospect"
    group = ""
    contact_id = ""
    invoice_due = "false"
    email = ""
    business_name = ""
    client_notes = ""
    try:
        known_client = find_known_client_by_phone(caller_number)
        profile = profile_from_known_client(known_client) if known_client else await get_contact_profile_by_phone(caller_number)
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
            logger.info(f"📌 [Client:{source}] {caller_number} -> {contact_name} | {client_type} | Group:{group} | Invoice:{invoice_due}")
    except Exception as e:
        logger.error(f"Error fetching contact profile: {e}")

    # Always wss for public/ngrok hosts, ws only for pure localhost
    is_local = host.startswith("localhost") or host.startswith("127.0.0.1")
    ws_protocol = "ws" if is_local else "wss"
    params = urllib.parse.urlencode({
        "caller_number": caller_number,
        "contact_name": contact_name,
        "client_type": client_type,
        "group": group,
        "contact_id": contact_id,
        "invoice_due": invoice_due,
        "email": email,
        "business_name": business_name,
        "client_notes": client_notes,
        "public_host": host,
    })
    stream_url = f"{ws_protocol}://{host}/api/twilio/stream?{params}"
    stream_url_xml = html.escape(stream_url, quote=True)
    logger.info(f"📞 [Incoming] {caller_number} ({contact_name}) -> {stream_url}")

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url_xml}">
            <Parameter name="callerNumber" value="{html.escape(caller_number)}" />
            <Parameter name="contactName" value="{html.escape(contact_name)}" />
            <Parameter name="clientType" value="{html.escape(client_type)}" />
            <Parameter name="group" value="{html.escape(group)}" />
            <Parameter name="contactId" value="{html.escape(contact_id)}" />
            <Parameter name="invoiceDue" value="{html.escape(invoice_due)}" />
            <Parameter name="email" value="{html.escape(email)}" />
            <Parameter name="businessName" value="{html.escape(business_name)}" />
            <Parameter name="clientNotes" value="{html.escape(client_notes)}" />
        </Stream>
    </Connect>
</Response>"""

    return Response(content=twiml_response, media_type="text/xml")


@router.websocket("/stream")
async def twilio_stream(websocket: WebSocket):
    """
    Full-duplex WebSocket endpoint for routing live bidirectional audio 
    between Twilio and the OpenAI Realtime API.
    """
    await websocket.accept()

    # Initialize variables with default fallback values
    caller_number = "Unknown"
    contact_name  = "Prospect"
    client_type   = "Prospect"
    group         = ""
    contact_id    = ""
    invoice_due   = False
    email         = ""
    business_name = ""
    client_notes  = ""
    stream_sid    = None
    call_sid      = None

    # Wait for the start event from Twilio (which is always the first message)
    try:
        first_message = await websocket.receive_text()
        data = json.loads(first_message)
        if data.get("event") == "start":
            stream_sid = data["start"]["streamSid"]
            call_sid = data["start"].get("callSid")
            custom_params = data["start"].get("customParameters", {})

            caller_number = custom_params.get("callerNumber", "Unknown")
            contact_name  = custom_params.get("contactName", "Prospect")
            client_type   = custom_params.get("clientType", "Prospect")
            group         = custom_params.get("group", "")
            contact_id    = custom_params.get("contactId", "")
            invoice_due   = custom_params.get("invoiceDue", "false") == "true"
            email         = custom_params.get("email", "")
            business_name = custom_params.get("businessName", "")
            client_notes  = custom_params.get("clientNotes", "")

            logger.info(f"🎬 [Twilio -> Server] Handshake received. Stream SID: [{stream_sid}], Call SID: [{call_sid}], Caller: [{caller_number}]")
            logger.info(f"📌 [Caller Profile] Name: {contact_name} | Type: {client_type} | Group: {group} | Notes: {client_notes}")
        else:
            # Fallback if first message is not 'start'
            caller_number = websocket.query_params.get("caller_number", "Unknown")
            contact_name  = websocket.query_params.get("contact_name", "Prospect")
            client_type   = websocket.query_params.get("client_type", "Prospect")
            group         = websocket.query_params.get("group", "")
            contact_id    = websocket.query_params.get("contact_id", "")
            invoice_due   = websocket.query_params.get("invoice_due", "false") == "true"
            email         = websocket.query_params.get("email", "")
            business_name = websocket.query_params.get("business_name", "")
            client_notes  = websocket.query_params.get("client_notes", "")
    except Exception as e:
        logger.error(f"Error processing Twilio start event: {e}")
        # Fallback to query parameters
        caller_number = websocket.query_params.get("caller_number", "Unknown")
        contact_name  = websocket.query_params.get("contact_name", "Prospect")
        client_type   = websocket.query_params.get("client_type", "Prospect")
        group         = websocket.query_params.get("group", "")
        contact_id    = websocket.query_params.get("contact_id", "")
        invoice_due   = websocket.query_params.get("invoice_due", "false") == "true"
        email         = websocket.query_params.get("email", "")
        business_name = websocket.query_params.get("business_name", "")
        client_notes  = websocket.query_params.get("client_notes", "")

    logger.info(f"\n🎙️ [WebSocket] {caller_number} | {contact_name} | {client_type} | Group:{group} | Invoice:{invoice_due}")
    
    # Metadata for call logging
    transcript_accumulator = []
    openai_ws = None
    start_time_dt = datetime.datetime.utcnow()
    call_done = asyncio.Event()  # signals all tasks to stop cleanly

    # Silence watchdog state
    last_ai_response_done_at: list = [None]
    caller_spoke_after_ai: list = [False]
    watchdog_active: list = [False]
    # 3-state call-close tracker: 0=idle, 1=end_call_permission_asked
    call_close_state: list = [0]
    listening_active: list = [False]

    # Connect to OpenAI Realtime API if the API key is present
    if OPENAI_API_KEY:
        try:
            logger.info("🤖 [OpenAI] Attempting to connect to OpenAI Realtime API...")
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            }
            openai_ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers)
            logger.info("🟢 [OpenAI] Successfully connected to OpenAI Realtime API.")

            from services.prompts import system_prompt
            instructions, selected_greeting = system_prompt()

            # Greet known client by notes if available, else by name
            greeting_name = client_notes.strip() if (client_notes and client_notes.strip()) else contact_name
            if greeting_name and greeting_name != "Prospect":
                # Check greetings list to keep branding, but personalize it
                if "Dhonnobad, Thank you for calling Pay Minimum Tax" in selected_greeting:
                    selected_greeting = selected_greeting.replace("How can I help you?", f"Hello, {greeting_name}! How can I help you today?")
                    selected_greeting = selected_greeting.replace("What could I do for you?", f"Hello, {greeting_name}! What can I do for you today?")
                    selected_greeting = selected_greeting.replace("Who do I have the pleasure to speak with today?", f"Hello, {greeting_name}! How can I help you today?")
                else:
                    selected_greeting = f"Dhonnobad, Thank you for calling Pay Minimum Tax, I am রেবা. Hello, {greeting_name}! How can I help you today?"

            # Send session configuration to OpenAI
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
                    - The caller's phone number is ALREADY KNOWN: {caller_number}. NEVER ask for it again.
                    - CRITICAL: If the caller has ALREADY provided their name or phone number earlier in this conversation, you MUST use that information. NEVER re-ask. Check the conversation history before requesting any detail. This applies even after a failed transfer or context switch.
                    - If the caller's email is in the CRM profile, or if they have already provided it in this conversation, NEVER ask for it again.
                    - Only collect email once if it is missing from both the CRM profile and the conversation history. Once provided, remember it.
                    - NEVER ask for information already visible in the CALLER CRM PROFILE or already collected during this call.

                    ## PAYMENT MODEL
                    - For paid appointments (virtual_cpa_45, office_cpa_45): payment is a PRE-PAYMENT credited to their invoice. It is NOT an extra charge.
                    - English: "This payment will be credited towards your invoice — it's not an extra charge."
                    - Banglish: "Ei payment ta apnar invoice-e credit hoye jabe — eta alada kono charge na."

                    ## CALLER CRM PROFILE
                    - Name: {contact_name}
                    - Phone: {caller_number} (confirmed — do NOT ask)
                    - Client Type: {client_type}
                    - Group: {group}
                    - Contact ID: {contact_id}
                    - Email: {email if email else 'Not Provided — ask once if needed'}
                    - Business Name: {business_name if business_name else 'Not Provided'}
                    - Client Notes: {client_notes if client_notes else 'None'}
                    - Has Invoice Due: {invoice_due}
                    """,
                    
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcmu"},
                            "transcription": {
                                "model": "whisper-1"
                                # NOTE: Do NOT set "language" here. Callers speak Banglish
                                # (romanized Bengali in Latin script). Forcing "bn" causes
                                # Whisper to drop transcripts entirely for mixed speech.
                            },
                            # Auto-detect when the caller finishes speaking and trigger a response
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.65,        # lower = less likely to fire on partial speech/pauses
                                "prefix_padding_ms": 400, # capture start of speech more reliably
                                "silence_duration_ms": 800, # wait longer before treating silence as turn-end (especially for Bangla)
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcmu"},
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
                                    "target": {"type": "string", "enum": ["simon", "tanzina", "alex", "nafi"]},
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
                            "description": "Record a callback request or message for a team member in the CRM. The caller's phone number is automatically included from caller ID — you do NOT need to collect it again. Use the caller's name as they introduced themselves during the call.",
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
            logger.info("📝 [OpenAI] Sent session configuration update with turn detection.")

            initial_greeting = {
                "type": "response.create",
                "response": {
                    "output_modalities": ["audio"],
                    "instructions": f"Greet the caller by saying: \"{selected_greeting}\". Speak it naturally and warmly. IMPORTANT: Use ONLY English or Bangla. NEVER use any other language."
                }
            }
            await openai_ws.send(json.dumps(initial_greeting))
            logger.info("🗣️ [OpenAI] Sent initial greeting trigger.")
            watchdog_active[0] = True

        except Exception as e:
            logger.info(f"🔴 [OpenAI] Error connecting to OpenAI Realtime API: {e}. Falling back to echo/mock.")
            openai_ws = None
    else:
        logger.info("⚠️ [OpenAI] OPENAI_API_KEY not found. Operating in fallback mode.")

    async def receive_from_twilio():
        nonlocal stream_sid, call_sid, caller_number
        media_count = 0
        try:
            while True:
                message = await websocket.receive_text()
                data = json.loads(message)

                if data.get("event") == "start":
                    stream_sid = data["start"]["streamSid"]
                    call_sid = data["start"].get("callSid")
                    # Use caller_number from query params as primary, but check custom params too
                    new_caller = data["start"].get("customParameters", {}).get("callerNumber")
                    if new_caller and new_caller != "Unknown":
                        caller_number = new_caller
                    logger.info(f"🎬 [Twilio -> Server] Media stream started. Stream SID: [{stream_sid}], Call SID: [{call_sid}], Caller: [{caller_number}]")

                elif data.get("event") == "media":
                    payload = data["media"]["payload"]
                    media_count += 1
                    
                    if media_count % 100 == 0:
                        logger.info(f"🔊 [Twilio -> Server] Received {media_count} audio chunks from caller...")
                    
                    if openai_ws and listening_active[0]:
                        # Stream raw audio buffer directly to OpenAI
                        openai_payload = {
                            "type": "input_audio_buffer.append",
                            "audio": payload
                        }
                        await openai_ws.send(json.dumps(openai_payload))
                    else:
                        # Fallback/Mock - Echo back a tiny beep or silence to prove connection
                        if media_count % 50 == 0:
                            logger.info(f"🛠️ [Mock Mode] Echoing dummy response for chunk {media_count}")
                            mock_response = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": payload # Echoing back user audio as a test
                                }
                            }
                            await websocket.send_text(json.dumps(mock_response))

                elif data.get("event") == "stop":
                    logger.info(f"🛑 [Twilio -> Server] Media stream stopped. Total chunks: {media_count}")
                    call_done.set()   # Signal all tasks to stop
                    break

        except WebSocketDisconnect:
            logger.info("⚠️ [Twilio WebSocket] Disconnected from Twilio.")
            call_done.set()
        except Exception as e:
            logger.info(f"🔴 [Twilio WebSocket] Error reading from Twilio: {e}")
            call_done.set()

    async def send_to_twilio():
        nonlocal stream_sid
        if not openai_ws:
            await call_done.wait()
            return

        # ── Helper: hang up via Twilio REST + set call_done ──
        async def _hangup_call():
            if call_done.is_set():
                return   # already hanging up
            call_done.set()
            if call_sid:
                try:
                    end_url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls/{call_sid}.json"
                    auth = (TWILIO_SID, TWILIO_AUTH_TOKEN)
                    async with httpx.AsyncClient(timeout=5) as client:
                        resp = await client.post(end_url, data={"Status": "completed"}, auth=auth)
                    if resp.status_code == 200:
                        logger.info(f"✅ [Hangup] Twilio call {call_sid} terminated.")
                    else:
                        logger.error(f"🔴 [Hangup] Twilio error: {resp.status_code} {resp.text[:80]}")
                except Exception as e:
                    logger.error(f"🔴 [Hangup] Exception: {e}")

        openai_media_count = 0
        current_response_id = None
        last_assistant_item_id = None
        response_audio_sent_ms = 0
        interrupt_event = asyncio.Event()
        end_call_in_progress = [False]
        # After a decline, block the AI's re-ask for 30s to prevent permission loop
        end_call_blocked_until = [0.0]

        async def _hangup_after_consent():
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
            try:
                # Detect conversation language: check caller transcripts first,
                # then AI transcripts as fallback.
                # A score >= 2 from Banglish indicators counts as Bangla conversation.
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
                    "bhai", "vai", "apa", "apa",
                    "somossa", "shomossa", "kotha", "ki", "ke",
                    "rakhlam", "rakhchi", "rakhbo",
                    "allah", "hafez", "hafiz", "khoda",
                    "dhonnobad", "dhanyabad", "shukriya",
                    "bolun", "bolen", "bolbo", "boli",
                    "lage", "lagbe", "lagche",
                    "ache", "achhi", "achhen",
                    "janen", "janbo", "janai",
                }
                for entry in transcript_accumulator:
                    text = entry[entry.index(":")+1:].strip() if ":" in entry else entry
                    # Definitive: any Unicode Bangla character
                    if any('\u0980' <= char <= '\u09FF' for char in text):
                        is_bangla_convo = True
                        break
                    # Probabilistic: count Banglish indicator words
                    words = [w.strip("?,.!।") for w in text.lower().split()]
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

                await openai_ws.send(json.dumps({
                    "type": "response.create",
                    "response": {
                        "output_modalities": ["audio"],
                        "instructions": goodbye_instr
                    }
                }))
                logger.info(f"✅ [EndCall] Caller gave permission. Saying goodbye ({'Bangla' if is_bangla_convo else 'English'}), then hanging up.")
                await asyncio.sleep(7)
            finally:
                await _hangup_call()

        # Define once — reused for every tool call, avoids per-iteration closure issues
        async def _log_adapter(event, message):
            logger.info(f"[{event}] {message}")

        try:
            async for openai_message in openai_ws:
                if call_done.is_set():
                    break
                openai_data = json.loads(openai_message)
                event_type = openai_data.get("type", "")

                # Track when a new response starts generating
                if event_type == "response.created":
                    resp_obj = openai_data.get("response", {})
                    current_response_id = resp_obj.get("id")
                    interrupt_event.clear()  # Allow audio for this new response
                    response_audio_sent_ms = 0  # Reset audio counter
                    logger.info(f"🟢 [OpenAI] New response started: {current_response_id}")

                # Process assistant's generated audio response
                elif event_type in ("response.audio.delta", "response.output_audio.delta"):
                    # DROP all audio chunks if caller interrupted
                    if interrupt_event.is_set():
                        continue

                    # Track item_id for conversation truncation on interruption
                    item_id = openai_data.get("item_id")
                    if item_id:
                        last_assistant_item_id = item_id

                    audio_chunk = openai_data["delta"]
                    openai_media_count += 1

                    # Track audio duration for accurate truncation
                    # G.711 μ-law: 8000 Hz, 1 byte/sample → 8 bytes per ms
                    try:
                        raw_bytes = len(base64.b64decode(audio_chunk))
                        response_audio_sent_ms += raw_bytes / 8
                    except Exception:
                        response_audio_sent_ms += 20  # ~20ms fallback

                    if openai_media_count % 100 == 0:
                        logger.info(f"🎙️ [OpenAI -> Server] Received {openai_media_count} audio chunks from AI...")

                    if stream_sid:
                        twilio_payload = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_chunk
                            }
                        }
                        await websocket.send_text(json.dumps(twilio_payload))

                # Handle user interruption: Stop AI and clear Twilio buffer
                elif event_type == "input_audio_buffer.speech_started":
                    # When end-call is in progress or greeting is still playing, let the audio play uninterrupted.
                    if end_call_in_progress[0] or not listening_active[0]:
                        logger.info("🌐 [OpenAI] Speech detected (ignored — goodbye/greeting in progress)")
                    else:
                        logger.info("🛑 [OpenAI] User interrupted! Stopping AI immediately...")
                        interrupt_event.set()

                        async def _clear_twilio_buffer(sid):
                            try:
                                await websocket.send_text(json.dumps({"event": "clear", "streamSid": sid}))
                                logger.info("🧹 [Twilio] Cleared audio playback buffer.")
                            except Exception as e:
                                logger.error(f"🔴 [Twilio] Failed to clear buffer: {e}")

                        if stream_sid:
                            asyncio.create_task(_clear_twilio_buffer(stream_sid))

                        try:
                            await openai_ws.send(json.dumps({"type": "response.cancel"}))
                            logger.info("🛑 [OpenAI] Sent response.cancel to stop AI generation.")
                        except Exception as cancel_err:
                            logger.error(f"🔴 [OpenAI] Failed to send response.cancel: {cancel_err}")

                        if last_assistant_item_id:
                            try:
                                await openai_ws.send(json.dumps({
                                    "type": "conversation.item.truncate",
                                    "item_id": last_assistant_item_id,
                                    "content_index": 0,
                                    "audio_end_ms": int(response_audio_sent_ms)
                                }))
                                logger.info(f"✂️ [OpenAI] Truncated item at {int(response_audio_sent_ms)}ms")
                            except Exception as trunc_err:
                                logger.error(f"🔴 [OpenAI] Failed to truncate: {trunc_err}")
                    
                    # Caller spoke — reset silence tracking
                    if listening_active[0]:
                        caller_spoke_after_ai[0] = True

                elif event_type == "input_audio_buffer.speech_stopped":
                    logger.info("🔇 [OpenAI] Speech ended, waiting for transcript check...")

                # Response finished or cancelled — clear interrupt so next response plays normally
                elif event_type in ("response.done", "response.cancelled"):
                    resp_obj = openai_data.get("response", {})
                    done_id = resp_obj.get("id") or current_response_id
                    interrupt_event.clear()  # Ready for next response
                    
                    # Start silence timer: AI finished generating, but audio takes time to play.
                    # We add the audio duration to the current time so the watchdog only starts counting
                    # AFTER the caller actually finishes hearing the audio.
                    audio_duration_sec = response_audio_sent_ms / 1000.0
                    last_ai_response_done_at[0] = asyncio.get_event_loop().time() + audio_duration_sec
                    caller_spoke_after_ai[0] = False
                    logger.info(f"✅ [OpenAI] Response {done_id} finished ({event_type}). Audio Duration: {audio_duration_sec:.2f}s")

                    if not listening_active[0]:
                        async def activate_listening_after_delay(delay: float):
                            await asyncio.sleep(max(0.0, delay))
                            if not call_done.is_set():
                                listening_active[0] = True
                                logger.info("🟢 [OpenAI] Greeting playback completed. Listening activated!")
                        asyncio.create_task(activate_listening_after_delay(audio_duration_sec))
                
                # Additional debug logging for other important OpenAI events
                elif event_type in (
                    "response.text.done",
                    "response.output_text.done",
                    "response.audio_transcript.done",
                    "response.output_audio_transcript.done",
                ):
                    text = openai_data.get("text") or openai_data.get("transcript")
                    if text:
                        logger.info(f"\n🤖 [AI Reply]: {text}")
                        transcript_accumulator.append(f"AI: {text}")
                        if _asks_anything_else(text):
                            import time as _time
                            if _time.time() < end_call_blocked_until[0]:
                                # Still in cooldown — AI already asked, suppress duplicate
                                logger.info("🚫 [EndCall] AI tried to re-ask 'anything else' during cooldown — suppressed.")
                            elif end_call_in_progress[0]:
                                logger.info("🚫 [EndCall] End-call already in progress — ignoring wrap-up cue.")
                            else:
                                call_close_state[0] = 1
                                logger.info("📋 [EndCall] Wrap-up question asked — next negative reply will trigger goodbye.")


                # Catch the user's speech transcript + detect caller goodbye
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    user_text = openai_data.get("transcript")
                    if user_text:
                        logger.info(f"\n👤 [Caller]: {user_text}")
                        transcript_accumulator.append(f"Caller: {user_text}")

                        # Explicit hangup cues always trigger hangup regardless of state
                        is_explicit = any(cue in user_text.lower() for cue in ["kete dao", "kete den", "kete din", "cut kore den", "kat kore den", "কেটে দাও", "কেটে দেন", "কেটে দিন", "কল কেটে", "কলটা কেটে", "cut the call", "hang up", "allah hafez", "khoda hafez", "রাখলাম", "রাখছি", "rakhlam", "rakhchi", "bye bye", "allah hafiz"])

                        if is_explicit:
                            asyncio.create_task(_hangup_after_consent())
                        elif call_close_state[0] == 1:
                            # Wrap-up mode: AI asked "anything else?" — evaluate reply
                            is_negative = _is_negative_response(user_text)
                            is_positive  = _is_end_call_consent(user_text)
                            if is_negative:
                                # Caller doesn't need anything else → goodbye + hang up directly
                                logger.info("✅ [EndCall] Caller said no to 'anything else' — triggering goodbye.")
                                call_close_state[0] = 0
                                asyncio.create_task(_hangup_after_consent())
                            elif is_positive:
                                # Caller has more to say — keep talking
                                import time as _time
                                call_close_state[0] = 0
                                end_call_blocked_until[0] = _time.time() + 30
                                logger.info("↩️ [EndCall] Caller has more — continuing conversation (blocked for 30s).")
                            else:
                                # Check if the transcript is actually meaningful (e.g. a new request or question)
                                if _is_meaningful_transcript(user_text):
                                    logger.info("📝 [EndCall] Caller spoke a meaningful new sentence. Letting AI handle naturally.")
                                    call_close_state[0] = 0
                                else:
                                    # Truly unclear / noise — force AI to ask caller to REPEAT
                                    logger.info("❓ [EndCall] Unclear wrap-up response — forcing AI to ask caller to repeat.")
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
                        

                # Handle tool calls
                elif event_type == "response.function_call_arguments.done":
                    func_name = openai_data.get("name")
                    call_id = openai_data.get("call_id")
                    args = json.loads(openai_data.get("arguments", "{}"))
                    logger.info(f"\n🛠️ [OpenAI] AI called tool '{func_name}' with args: {args}")
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
                            transcript_accumulator,
                            call_id,
                            _log_adapter,
                            _hangup_call
                        )
                        continue

                    elif func_name == "record_message":
                        result = await handle_record_message(
                            args,
                            contact_id,
                            contact_name,
                            caller_number,
                            _log_adapter
                        )

                    elif func_name == "send_link_sms":
                        result = await handle_send_link_sms(
                            args,
                            caller_number,
                            _log_adapter
                        )

                    elif func_name == "send_link_email":
                        result = await handle_send_link_email(
                            args,
                            _log_adapter
                        )

                    # Send output back to OpenAI

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
            logger.info(f"🔴 [OpenAI -> Twilio] Error streaming response from OpenAI to Twilio: {e}")

    # Silence watchdog for Twilio calls
    async def silence_watchdog():
        """After 12s of caller silence post-AI response, inject a gentle nudge."""
        SILENCE_TIMEOUT = 12
        while not call_done.is_set():
            await asyncio.sleep(1)
            if not watchdog_active[0] or not openai_ws:
                continue
            t = last_ai_response_done_at[0]
            if t is None:
                continue
            elapsed = asyncio.get_event_loop().time() - t
            if elapsed >= SILENCE_TIMEOUT and not caller_spoke_after_ai[0]:
                try:
                    await openai_ws.send(json.dumps({
                        "type": "response.create",
                        "response": {
                            "output_modalities": ["audio"],
                            "instructions": "The caller has been silent for a while. Politely ask if they are still there (e.g. 'Are you still with me?'). IMPORTANT: Ask in the EXACT same language (English or Bangla) that the conversation is currently in. Keep it to one short natural sentence."
                        }
                    }))
                    logger.info("⏱️ [Watchdog] Dynamic silence nudge sent.")
                except Exception:
                    pass
                last_ai_response_done_at[0] = asyncio.get_event_loop().time()

    # Orchestrate bidirectional async tasks
    try:
        await asyncio.gather(
            receive_from_twilio(),
            send_to_twilio(),
            silence_watchdog(),
            return_exceptions=True
        )
    finally:
        call_done.set()   # make sure all tasks are unblocked in edge cases
        if openai_ws:
            try:
                await openai_ws.close()
                logger.info("🔒 [OpenAI] WebSocket closed cleanly.")
            except Exception:
                pass
        logger.info("🔒 [Twilio] Bidirectional voice session closed.")
        if transcript_accumulator:
            full_transcript = "\n".join(transcript_accumulator)
            try:
                # Generate AI Summary/Outcome
                summary = "AI handled the call."
                intent = "General Inquiry"
                outcome = "Completed"
                
                if OPENAI_API_KEY:
                    from services.ai_call import generate_ai_response
                    analysis_prompt = f"""
                    Analyze this call transcript between an AI Receptionist and a Caller.
                    Return a JSON object with: 
                    "summary" (concise 2-3 sentences), 
                    "reason" (EXACTLY 2-3 words summary of the call purpose),
                    "intent" (short string like "Tax Preparation"), 
                    "outcome" (If an appointment was booked, the date like "May 12", else "Completed", "Inquiry", etc.),
                    "lead_status" (one of: Qualified Lead, Warm Lead, Cold Lead),
                    "tags" (list of strings).
                    
                    Transcript:
                    {full_transcript}
                    """
                    try:
                        analysis_raw = await generate_ai_response(analysis_prompt, system_context="You are a call analyst. Return JSON ONLY.")
                        # Strip markdown if present
                        if "```json" in analysis_raw:
                            analysis_raw = analysis_raw.split("```json")[1].split("```")[0].strip()
                        analysis = json.loads(analysis_raw)
                        summary = analysis.get("summary", summary)
                        reason = analysis.get("reason", "Inquiry")
                        intent = analysis.get("intent", intent)
                        outcome = analysis.get("outcome", outcome)
                        lead_status = analysis.get("lead_status")
                        tags = ",".join(analysis.get("tags", []))
                    except Exception as e:
                        reason = "Inquiry"
                        lead_status = "Inquiry"
                        tags = ""
            except Exception as e:
                logger.error(f"🔴 [Analysis] Error in post-call analysis: {e}")
                summary = "Error analyzing call."
                reason = "Unknown"
                intent = "Unknown"
                outcome = "Unknown"
                lead_status = "Unknown"
                tags = ""
        else:
            full_transcript = ""
            summary = "Call was too short or no transcript available."
            reason = "Short Call"
            intent = "Unknown"
            outcome = "Missed/Dropped"
            lead_status = "Unknown"
            tags = ""

        try:
            db = SessionLocal()
            try:
                new_log = CallLog(
                    call_sid=call_sid or stream_sid,
                    caller_number=caller_number,
                    transcript=full_transcript,
                    summary=summary,
                    reason=reason,
                    intent=intent,
                    outcome=outcome,
                    lead_status=lead_status,
                    tags=tags,
                    start_time=start_time_dt,
                    end_time=datetime.datetime.utcnow(),
                    duration=int((datetime.datetime.utcnow() - start_time_dt).total_seconds())
                )
                db.add(new_log)
                db.commit()
                logger.info(f"💾 [Database] Call log saved for SID: {call_sid or stream_sid}")
            except Exception as db_err:
                logger.error(f"🔴 [Database] Error saving call log: {db_err}")
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"🔴 [Database] Error in post-call processing: {e}")

        logger.info("Bidirectional voice session closed.")
