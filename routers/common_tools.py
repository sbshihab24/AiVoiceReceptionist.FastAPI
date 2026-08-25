import json
import random
import asyncio
import os
import base64
import httpx
from zoneinfo import ZoneInfo
from datetime import datetime, time as datetime_time
from services.booking_service import book_appointment, get_slots

OFFICE_TIMEZONE = "America/New_York"
OFFICE_TZ = ZoneInfo(OFFICE_TIMEZONE)
OFFICE_OPEN = datetime_time(10, 0)
OFFICE_CLOSE = datetime_time(16, 0)

def is_office_open() -> bool:
    now_et = datetime.now(OFFICE_TZ)
    if now_et.weekday() >= 5:  # Saturday or Sunday
        return False
    return OFFICE_OPEN <= now_et.time() < OFFICE_CLOSE


def get_office_status_context() -> dict:
    """
    Returns a structured dict describing whether the office is open or closed right now,
    with ready-made English + Banglish messages for the AI to use.
    """
    now_et = datetime.now(OFFICE_TZ)
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_name = weekday_names[now_et.weekday()]

    if now_et.weekday() >= 5:  # Weekend
        return {
            "office_open": False,
            "office_status": "OFFICE_CLOSED_WEEKEND",
            "english_note": (
                f"IMPORTANT: The office is CLOSED today ({day_name}). "
                "Tell the caller FIRST: 'Our office is currently closed for the weekend. "
                "However, I can still schedule you an appointment for the coming week.' "
                "Then show future weekday slots only."
            ),
            "banglish_note": (
                f"IMPORTANT: Office ekhon CLOSED ({day_name}, weekend). "
                "Caller ke PROTHOME bolun: 'Amader office ekhon weekend-e bondho. "
                "Tobe ami apnake agami saptaher jonno appointment book korte parbo.' "
                "Taarpor future weekday slots dekhaan."
            ),
        }

    if now_et.time() < OFFICE_OPEN:  # Before hours
        open_time_str = OFFICE_OPEN.strftime("%I:%M %p")
        return {
            "office_open": False,
            "office_status": "OFFICE_CLOSED_BEFORE_HOURS",
            "english_note": (
                f"IMPORTANT: The office is CLOSED right now (not yet open — opens at {open_time_str} ET). "
                "Tell the caller FIRST: 'Our office hasn't opened yet — we open at 10:00 AM Eastern Time. "
                "I can still book an appointment for later today or another day.' "
                "Then show available slots."
            ),
            "banglish_note": (
                f"IMPORTANT: Office ekhon CLOSED (abhi khuleni — {open_time_str} ET-te khulbe). "
                "Caller ke PROTHOME bolun: 'Amader office ekhon khuleni — amra ET {open_time_str}-e khuli. "
                "Tobe ami apnake aaj porey ba onnyo diner jonno appointment book korte parbo.' "
                "Taarpor available slots dekhaan."
            ),
        }

    if now_et.time() >= OFFICE_CLOSE:  # After hours
        return {
            "office_open": False,
            "office_status": "OFFICE_CLOSED_AFTER_HOURS",
            "english_note": (
                "IMPORTANT: The office is CLOSED for today (office hours are 10 AM – 4 PM ET, Mon–Fri). "
                "Tell the caller FIRST: 'Our office is now closed for the day. "
                "But I can still help you book an appointment for tomorrow or a future date.' "
                "Then show available future slots."
            ),
            "banglish_note": (
                "IMPORTANT: Office aaj-er jonno CLOSED (office hours: ET 10 AM – 4 PM, Mon–Fri). "
                "Caller ke PROTHOME bolun: 'Amader office aaj-er moto bondho hoye geche. "
                "Tobe ami apnake agamikal ba onnyo diner jonno appointment schedule korte pari.' "
                "Taarpor future slots dekhaan."
            ),
        }

    # Office is open
    return {
        "office_open": True,
        "office_status": "OFFICE_OPEN",
        "english_note": "Office is currently OPEN (Mon–Fri, 10 AM – 4 PM ET). Proceed normally.",
        "banglish_note": "Office ekhon OPEN (Mon–Fri, ET 10 AM – 4 PM). Normally proceed korun.",
    }

async def send_sms(to_number: str, message_body: str, logger_or_debug=None) -> bool:
    """Send an SMS using GoHighLevel or Twilio REST API."""
    sms_provider = os.getenv("SMS_PROVIDER", "ghl").lower()
    
    clean_to = to_number.strip()
    if clean_to and not clean_to.startswith("+"):
        if len(clean_to) == 10 and clean_to.isdigit():
            clean_to = f"+1{clean_to}"
        elif clean_to.startswith("1") and len(clean_to) == 11 and clean_to.isdigit():
            clean_to = f"+{clean_to}"
        else:
            clean_to = f"+{clean_to}"

    # Try sending via GHL if configured as provider
    if sms_provider == "ghl":
        from services.ghl import send_sms_via_ghl
        try:
            success = await send_sms_via_ghl(clean_to, message_body)
            if success:
                msg = f"✅ [SMS] Sent successfully via GHL to {clean_to}."
                if logger_or_debug:
                    await logger_or_debug("sms_success", msg)
                else:
                    print(msg)
                return True
            else:
                msg = f"⚠️ [SMS] GHL SMS failed. Falling back to Twilio..."
                if logger_or_debug:
                    await logger_or_debug("sms_warn", msg)
                else:
                    print(msg)
        except Exception as e:
            msg = f"⚠️ [SMS] Exception in GHL SMS: {e}. Falling back to Twilio..."
            if logger_or_debug:
                await logger_or_debug("sms_warn", msg)
            else:
                print(msg)

    # Twilio / Fallback Code
    twilio_sid = os.getenv("TWILIO_SID", "")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_number = os.getenv("TWILIO_NUMBER", "")

    if not twilio_sid or not twilio_token or not twilio_number:
        msg = "❌ [SMS] Twilio credentials not configured. Cannot send SMS."
        if logger_or_debug:
            await logger_or_debug("sms_error", msg)
        else:
            print(msg)
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
    auth_header = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "To": clean_to,
        "From": twilio_number,
        "Body": message_body
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, data=data)
            if resp.status_code in (200, 201):
                msg = f"✅ [SMS] Sent successfully via Twilio fallback to {clean_to}."
                if logger_or_debug:
                    await logger_or_debug("sms_success", msg)
                else:
                    print(msg)
                return True
            else:
                msg = f"❌ [SMS] Failed to send to {clean_to} via Twilio fallback: {resp.status_code} - {resp.text}"
                if logger_or_debug:
                    await logger_or_debug("sms_error", msg)
                else:
                    print(msg)
                return False
    except Exception as e:
        msg = f"❌ [SMS] Exception sending to {clean_to} via Twilio fallback: {e}"
        if logger_or_debug:
            await logger_or_debug("sms_exception", msg)
        else:
            print(msg)
        return False

ADS = [
    "Stop overpaying. Join our waitlist for a free tax savings review with our CPA. We'll reach out as soon as a spot opens up.",
    "We don't just find savings; we help you keep them. Our team guides you through the entire process, ensuring you never feel left behind.",
    "Personalized tax strategies, not generic templates. We build a plan around your needs and execute it with precision.",
    "Paying over $30k in business taxes or earning $50k+ on a 1099? You're likely overpaying. Contact us for a complimentary CPA review to see how much you could save."
]

async def handle_book_appointment(args: dict, logger_or_debug) -> dict:
    try:
        name  = args.get("name", "Caller")
        phone = args.get("phone", "")
        result = await book_appointment(
            name=name,
            email=args.get("email", ""),
            phone=phone,
            booking_slot=args.get("booking_slot", ""),
            calendar_type=args.get("calendar_type", "follow_up_b"),
            call_summary=args.get("call_summary", ""),
        )
        status = result.get("status")
        await logger_or_debug("tool_result", f"✅ Booking result: {status}")

        # booking_service.py already fires email + SMS (via GHL PIT token) for
        # payment_required cases. Log the delivery outcomes for debugging.
        if status == "payment_required":
            email_ok = result.get("email_sent", False)
            sms_ok   = result.get("sms_sent",   False)
            await logger_or_debug(
                "payment_delivery",
                f"📧 Email: {'✅' if email_ok else '❌'}  📱 SMS (GHL): {'✅' if sms_ok else '❌'}  "
                f"→ {phone}"
            )

        return result
    except Exception as e:
        await logger_or_debug("tool_error", f"🔴 Booking exception: {e}")
        return {"status": "error", "message": "Sorry, there was a technical issue booking your appointment. Please try again later."}



async def handle_get_slots(args: dict, logger_or_debug) -> dict:
    try:
        # # ── Step 1: Determine live office status ──────────────────────────────
        # office_ctx = get_office_status_context()
        # await logger_or_debug("office_status", f"🏢 Office status: {office_ctx['office_status']}")

        # ── Step 2: Fetch available slots from GHL ────────────────────────────
        result = await get_slots(
            calendar_type=args.get("calendar_type", "follow_up_b")
        )

        # # ── Step 3: Inject office context so AI knows the status before speaking
        # result["office_open"]   = office_ctx["office_open"]
        # result["office_status"] = office_ctx["office_status"]
        # result["office_note"]   = office_ctx["english_note"]
        # result["banglish_note"] = office_ctx["banglish_note"]

        # await logger_or_debug("tool_result", f"✅ Slots fetched. Office: {office_ctx['office_status']}")
        await logger_or_debug("tool_result", "✅ Slots fetched successfully.")
        return result
    except Exception as e:
        await logger_or_debug("tool_error", f"🔴 Slot fetch exception: {e}")
        return {"status": "error", "message": "Could not fetch available slots."}

async def handle_transfer_call(args: dict, openai_ws, call_done, call_id: str, logger_or_debug) -> dict:
    target = args.get("target", "tanzina").lower()
    
    if not is_office_open():
        await logger_or_debug("transfer_closed", f"📲 [Transfer] Transfer to {target} blocked: Office is closed.")
        return {"status": "office_closed", "message": "Office is closed. Will callback tomorrow."}

    reason = args.get("reason", "").lower()
    urgent_keywords = ["irs", "notice", "audit", "urgent", "deadline", "compliance", "penalty"]
    is_urgent = any(kw in reason for kw in urgent_keywords)
    
    # Skip ad if the call reason is urgent
    if is_urgent:
        ad_msg = ""
        await logger_or_debug("transfer_call", f"📲 Urgent transfer to {target}. Skipping ad.")
        instructions = f"Say this in the SAME LANGUAGE the user is currently speaking: (English: 'Let me try to reach {target} for you. Please hold on.' or Bangla: 'Ektu hold korun, ami {target}-ke connect korchi.'). Do not paraphrase."
    else:
        ad_msg = random.choice(ADS)
        await logger_or_debug("transfer_call", f"📲 Transfer started to {target}. Simulating hold flow with ad...")
        instructions = f"Say this in the SAME LANGUAGE the user is currently speaking: (English: 'Let me try to reach {target} for you. Please hold on.' or Bangla: 'Ektu hold korun, ami {target}-ke connect korchi.'). Do not paraphrase. Then, switch to ENGLISH and say this advertisement naturally: '{ad_msg}'"
    
    # Intro (+ optional Ad)
    await openai_ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "output_modalities": ["audio"],
            "instructions": instructions
        }
    }))

    async def _simulated_transfer_flow():
        await asyncio.sleep(12)
        if call_done.is_set():
            return
        await logger_or_debug("transfer_hold", "⏳ Still trying to connect (12s mark)...")
        await openai_ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "output_modalities": ["audio"],
                "instructions": f"In the SAME LANGUAGE the user is speaking, say this exact text: (English: 'I am sorry, they haven\'t picked up yet. I am still trying to connect, please stay on the line.', Bangla: 'Sorry. Ami connect korar try korchi, ektu line-e thakun.'). Do not paraphrase."
            }
        }))
        
        await asyncio.sleep(12)
        if call_done.is_set():
            return
        await logger_or_debug("transfer_fail", "❌ Transfer failed (target unavailable).")
        await openai_ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "output_modalities": ["audio"],
                "instructions": f"In the SAME LANGUAGE the user is speaking, say this exact text: (English: 'I am sorry, {target} is not available right now.', Bangla: 'Sorry, {target} ekhon available nai.'). Do not paraphrase. Then, wait for the caller's response. If the caller asks to speak to someone else, IMMEDIATELY try the next available person in this order: Tanzina, Alex, Nafi by calling transfer_call again with the new target. Do NOT keep repeating that {target} is unavailable. If the caller just wants to leave a message, offer a callback."
            }
        }))
    
    asyncio.create_task(_simulated_transfer_flow())
    
    return {"status": "success", "message": f"Transferring to {target}"}

async def handle_end_call(
    args: dict, 
    openai_ws, 
    call_done, 
    end_call_in_progress, 
    transcript_history, 
    call_id: str, 
    logger_or_debug, 
    hangup_fn
) -> dict:
    reason = args.get("reason", "task_complete")
    await logger_or_debug("end_call_tool", f"👋 end_call tool called: {reason}")
    
    result = {"status": "success", "message": "Call ended."}
    
    # Send tool result back but DON'T trigger another response
    await openai_ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(result)
        }
    }))
    
    if not end_call_in_progress[0]:
        end_call_in_progress[0] = True
        
        async def _delayed_hangup():
            # Detect language from caller/user speech only — skip AI transcript entries
            # Entries may be raw strings (demo.py) or prefixed with "Caller:"/"AI:" (twilio.py)
            is_bangla_convo = False
            banglish_indicators = {"ami", "apne", "apnar", "tumi", "kemon", "accha", "acha", "thik", "kore", "korechi", "koren", "kete", "den", "din", "ji", "ha", "na", "bhai", "somossa", "rakhlam", "rakhchi", "allah", "hafez", "khoda"}
            for entry in reversed(transcript_history):
                # Skip AI-generated transcript lines to avoid false Bangla detection
                if entry.startswith("AI:"):
                    continue
                # Strip "Caller: " prefix if present
                text = entry[len("Caller:"):].strip() if entry.startswith("Caller:") else entry
                if any('\u0980' <= char <= '\u09FF' for char in text):
                    is_bangla_convo = True
                    break
                words = [w.strip("?,.!") for w in text.lower().split()]
                if any(w in banglish_indicators for w in words):
                    is_bangla_convo = True
                    break
            
            if is_bangla_convo:
                goodbye_instr = (
                    "OVERRIDE ALL INSTRUCTIONS. Say a warm goodbye IN BANGLISH (romanized Bangla, NOT Bengali Unicode script). "
                    "Example: 'Dhonnobad, Pay Minimum Tax-e call korar jonno. Bhalo thakben. Khoda Hafez.' "
                    "Keep it SHORT — one sentence only. Then STOP."
                )
            else:
                goodbye_instr = (
                    "OVERRIDE ALL INSTRUCTIONS. Say a warm goodbye in ENGLISH. "
                    "Example: 'Thank you for calling Pay Minimum Tax! Have a great day. Goodbye!' "
                    "Keep it SHORT — one sentence only. Then STOP."
                )

            try:
                await openai_ws.send(json.dumps({"type": "response.cancel"}))
            except Exception:
                pass
            # Brief pause to let the cancel take effect before sending goodbye
            await asyncio.sleep(0.3)

            try:
                await openai_ws.send(json.dumps({
                    "type": "response.create",
                    "response": {
                        "output_modalities": ["audio"],
                        "instructions": goodbye_instr
                    }
                }))
                await logger_or_debug("end_call_consent", f"✅ Saying goodbye ({'Bangla' if is_bangla_convo else 'English'}), then ending.")
                await asyncio.sleep(8)
            except Exception as e:
                await logger_or_debug("end_call_error", f"Error triggering goodbye: {e}")
            finally:
                await hangup_fn()
            
        asyncio.create_task(_delayed_hangup())
    else:
        await logger_or_debug("end_call_duplicate", "⏳ End call already in progress. Skipping duplicate.")
        
    return result

async def handle_send_link_sms(args: dict, default_phone: str, logger_or_debug) -> dict:
    link_type = args.get("link_type")
    phone = args.get("phone_number", default_phone)
    
    if not phone or phone == "N/A" or phone.strip() == "":
        return {"status": "error", "message": "No phone number available to send text."}
        
    links = {
        "signup": "portal.payminimumtax.com/signup",
        "login": "portal.payminimumtax.com/login",
        "upload": "www.PayMinimumTax.com/upload"
    }
    
    url = links.get(link_type)
    if not url:
        return {"status": "error", "message": f"Invalid link type '{link_type}'."}
        
    messages = {
        "signup": f"Here is the link to signup for Pay Minimum Tax services: {url}",
        "login": f"Here is the link to access your client portal: {url}",
        "upload": f"Please upload your tax notice directly using this link: {url}"
    }
    
    body = messages[link_type]
    sent = await send_sms(phone, body, logger_or_debug)
    if sent:
        return {
            "status": "success",
            "sms_sent": True,
            "message": f"Text message sent successfully to {phone}."
        }
    else:
        return {
            "status": "sms_failed",
            "sms_sent": False,
            "message": (
                f"SMS_DELIVERY_FAILED: The text message could NOT be delivered to {phone}. "
                f"Inform the caller that the link could not be texted at this time, "
                f"and our team will send it manually."
            )
        }


async def handle_send_link_email(args: dict, logger_or_debug) -> dict:
    """
    Send a portal/payment/calendar link to the caller via email.
    Called when the caller says they don't have their phone or prefers email.
    """
    from services.email_service import send_email

    to_email   = (args.get("email") or "").strip()
    link_type  = (args.get("link_type") or "custom").lower().strip()
    custom_url = (args.get("custom_url") or "").strip()
    caller_name = (args.get("name") or "Valued Client").strip()

    if not to_email:
        return {
            "status": "error",
            "message": "EMAIL_MISSING: No email address provided. Ask the caller for their email address first.",
        }

    # ── Resolve the URL ────────────────────────────────────────────────────
    preset_links = {
        "signup":  ("Client Portal Sign-Up",     "https://portal.payminimumtax.com/signup"),
        "login":   ("Client Portal Login",        "https://portal.payminimumtax.com/login"),
        "upload":  ("Document Upload",            "https://www.payminimumtax.com/upload"),
        "payment": ("Payment Link",               custom_url),
        "custom":  ("Requested Link",             custom_url),
    }

    label, url = preset_links.get(link_type, ("Requested Link", custom_url))
    if not url:
        return {
            "status": "error",
            "message": f"No URL available for link_type='{link_type}'. Provide custom_url.",
        }

    # ── Build a clean HTML email ────────────────────────────────────────────
    subject   = f"🔗 Your {label} — Pay Minimum Tax"
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px;">
  <div style="max-width: 560px; margin: auto; background: white; border-radius: 12px;
              padding: 32px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
    <h2 style="color: #6B3FA0;">👋 Hi {caller_name}!</h2>
    <p>As requested during your call with our AI receptionist, here is your <strong>{label}</strong>:</p>

    <div style="text-align: center; margin: 28px 0;">
      <a href="{url}"
         style="background: linear-gradient(135deg, #6B3FA0, #9B59B6); color: white;
                padding: 14px 32px; border-radius: 50px; text-decoration: none;
                font-size: 16px; font-weight: bold; display: inline-block;">
        🔗 Open {label}
      </a>
    </div>

    <p style="color: #555; font-size: 14px;">Or copy this link into your browser:</p>
    <p style="background: #f0eaff; padding: 10px; border-radius: 6px; word-break: break-all;
              font-size: 13px; color: #6B3FA0;">{url}</p>

    <p style="margin-top: 24px; color: #888; font-size: 12px;">
      This link was sent by Reba, the AI receptionist at Pay Minimum Tax.
      If you did not request this, please ignore this email.
    </p>
  </div>
</body>
</html>"""

    await logger_or_debug("send_email_link", f"📧 Sending {label} to {to_email}...")
    sent = await send_email(to_email, subject, html_body)

    if sent:
        await logger_or_debug("send_email_link", f"✅ Email delivered to {to_email}")
        return {
            "status": "success",
            "email_sent": True,
            "message": (
                f"EMAIL_SENT: The {label} has been sent to {to_email}. "
                f"Tell the caller: 'I've emailed the link to {to_email}. "
                f"Please check your inbox — and your spam folder if you don't see it within a minute.'"
            ),
        }
    else:
        await logger_or_debug("send_email_link", f"❌ Email delivery failed to {to_email}")
        return {
            "status": "email_failed",
            "email_sent": False,
            "message": (
                f"EMAIL_DELIVERY_FAILED: Could not send the {label} to {to_email}. "
                f"Inform the caller that email delivery failed and our team will send it manually. "
                f"The link is: {url}"
            ),
        }



async def handle_record_message(args: dict, contact_id: str, default_name: str, default_phone: str, logger_or_debug) -> dict:
    caller_name_arg  = args.get("caller_name", default_name) or "Caller"
    caller_phone_arg = args.get("caller_phone", default_phone) or "N/A"
    message_text     = args.get("message", "")
    call_reason_arg  = args.get("call_reason", "other")
    
    await logger_or_debug("record_message_start", f"📝 [CRM] Recording message from {caller_name_arg}: {message_text}")
    note = (
        f"📞 Missed Call Note\n"
        f"Name: {caller_name_arg}\n"
        f"Phone: {caller_phone_arg}\n"
        f"Reason: {call_reason_arg}\n"
        f"Message: {message_text}"
    )
    
    # Try to resolve contact_id and email if not provided or to get the email
    from services.ghl import add_crm_note, get_contact
    from services.ghl_search import search_contact_by_phone_or_email
    
    resolved_contact_id = contact_id
    resolved_email = ""
    
    try:
        contact_obj = None
        if resolved_contact_id:
            contact_obj = await get_contact(resolved_contact_id)
        elif caller_phone_arg and caller_phone_arg != "N/A":
            contact_obj = await search_contact_by_phone_or_email(phone=caller_phone_arg)
            
        if contact_obj:
            resolved_contact_id = contact_obj.get("id") or contact_obj.get("contactId") or resolved_contact_id
            resolved_email = contact_obj.get("email", "")
    except Exception as e:
        await logger_or_debug("record_message_resolve_err", f"⚠️ Error resolving contact details: {e}")

    saved = False
    if resolved_contact_id:
        try:
            saved = await add_crm_note(resolved_contact_id, note)
        except Exception as e:
            await logger_or_debug("record_message_err", f"⚠️ Failed to save note to GHL: {e}")
            
    if saved:
        result = {"status": "success", "message": "Message recorded in CRM."}
    else:
        await logger_or_debug("record_message_local", f"📝 [CRM] No contact ID or CRM error, logging locally:\n{note}")
        result = {"status": "success", "message": "Message noted. Team will follow up."}
        
    # ── Save callback request / message in CALENDAR_TEST calendar ──
    async def _book_callback_in_calendar():
        try:
            email = resolved_email
            if not email:
                clean_phone = "".join(c for c in caller_phone_arg if c.isalnum())
                email = f"callback_{clean_phone}@payminimumtax.com"

            # Get first available slot in test_calendar
            from services.booking_service import get_slots, book_appointment
            slots_res = await get_slots(calendar_type="test_calendar")
            
            booking_slot = ""
            if slots_res.get("status") == "success" and slots_res.get("available_slots"):
                def extract_first_slot(data) -> str:
                    if isinstance(data, list):
                        for item in data:
                            val = extract_first_slot(item)
                            if val:
                                return val
                    elif isinstance(data, dict):
                        for k in sorted(data.keys()):
                            val = extract_first_slot(data[k])
                            if val:
                                return val
                            if isinstance(k, str) and len(k) >= 19 and "T" in k:
                                return k
                    elif isinstance(data, str):
                        if len(data) >= 19 and "T" in data:
                            return data
                    return ""
                
                booking_slot = extract_first_slot(slots_res["available_slots"])
                
            if not booking_slot:
                from zoneinfo import ZoneInfo
                from datetime import datetime, timedelta
                OFFICE_TIMEZONE = "America/New_York"
                OFFICE_TZ = ZoneInfo(OFFICE_TIMEZONE)
                now_et = datetime.now(OFFICE_TZ)
                check_dt = (now_et + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
                while True:
                    if check_dt.weekday() < 5:
                        break
                    check_dt += timedelta(days=1)
                utc_dt = check_dt.astimezone(ZoneInfo("UTC"))
                booking_slot = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                
            await logger_or_debug("record_message_calendar", f"📅 [Calendar] Booking callback on CALENDAR_TEST for slot: {booking_slot}")
            
            appt_res = await book_appointment(
                name=caller_name_arg,
                email=email,
                phone=caller_phone_arg,
                booking_slot=booking_slot,
                call_summary=f"Callback Request / Message left:\n{message_text}\nReason: {call_reason_arg}",
                calendar_type="test_calendar",
                is_known_client=True
            )
            await logger_or_debug("record_message_calendar_result", f"📅 [Calendar] Callback booking result: {appt_res.get('status')}")
        except Exception as e:
            await logger_or_debug("record_message_calendar_error", f"⚠️ Failed to save callback in CALENDAR_TEST calendar: {e}")

    asyncio.create_task(_book_callback_in_calendar())

    # Send real-time SMS alerts to team members if mentioned
    target_lower = message_text.lower() + " " + call_reason_arg.lower()
    from config import FORWARD_SIMON, FORWARD_TANZINA, FORWARD_ALEX, FORWARD_NAFI
    alert_number = None
    alert_name = None
    if "simon" in target_lower:
        alert_number = FORWARD_SIMON
        alert_name = "Simon"
    elif "tanzina" in target_lower:
        alert_number = FORWARD_TANZINA
        alert_name = "Tanzina"
    elif "alex" in target_lower:
        alert_number = FORWARD_ALEX
        alert_name = "Alex"
    elif "nafi" in target_lower:
        alert_number = FORWARD_NAFI
        alert_name = "Nafi"

    if alert_number:
        alert_body = f"🔔 [PMT Alert] {caller_name_arg} ({caller_phone_arg}) left a message for {alert_name}: '{message_text}'"
        await send_sms(alert_number, alert_body, logger_or_debug)
        
    return result
