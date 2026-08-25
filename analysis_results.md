# 🔍 Voice Conversation Flow — Conflict & Problem Analysis

## Summary

Simon is right — there **ARE conflicting and redundant commands** spread across the prompt system. The voice conversation instructions are scattered across **3 layers** that often say the same thing differently, and in several cases **directly contradict each other**. This is the root cause of inconsistent AI behavior during live calls.

---

## 🏗️ Architecture: How the Prompt is Built

The AI's instructions come from **3 separate sources** that get concatenated together:

| Layer | Source | Size |
|-------|--------|------|
| **Layer 1** | 10 prompt section files (`01_language_rules.txt` → `10_response_style.txt`) | ~40KB |
| **Layer 2** | Monolithic fallback template (`full_prompt_template.txt`) | ~34KB |
| **Layer 3** | Inline session rules injected in Python code ([twilio.py L712-758](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/twilio.py#L712-L758)) | ~2KB |

> [!CAUTION]
> **Layer 1 + Layer 3** are what actually get sent to the AI in production. Layer 2 (`full_prompt_template.txt`) is the OLD monolithic version that still exists on disk as a fallback but is **NOT used** when the `prompt_sections/` directory is present. However, it **drifts** out of sync with the section files, and if the sections directory ever gets deleted, the AI would revert to stale instructions.

---

## 🔴 Critical Conflicts Found

### 1. `end_call` Tool Description vs. Prompt Instructions — **DIRECT CONTRADICTION**

> [!WARNING]
> This is the single biggest conflict causing conversation flow problems.

| Location | What it says |
|----------|-------------|
| [twilio.py L837](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/twilio.py#L837) — `end_call` tool description | `"ONLY call this AFTER you have explicitly asked the user for permission to end the call (e.g. 'Can I end the call now?') AND they have said YES"` |
| [02_noise_filtering.txt L28](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/02_noise_filtering.txt#L28) — Prompt Section | `"Do NOT ask 'Can I end the call?' or 'Ami ki call shesh kore dii?' — this step is REMOVED."` |
| [twilio.py L735](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/twilio.py#L735) — Live Session Rules | `"CRITICAL: Do NOT ask 'Can I end the call?' — this extra permission step is REMOVED."` |

**Result**: The AI receives two directly opposing instructions in the same session:
- The tool's own description says "you MUST ask permission before calling end_call"
- The prompt says "NEVER ask permission, it's REMOVED"
- This causes the AI to sometimes ask "Can I end the call?" and sometimes not — inconsistent behavior.

---

### 2. Call Ending Flow — Duplicated 3× with Slight Variations

The call ending rules appear in **three places**:

| Location | Variation |
|----------|-----------|
| [02_noise_filtering.txt](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/02_noise_filtering.txt#L9-L42) | 2-step flow with detailed triggers |
| [07_booking_flow.txt L45-67](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/07_booking_flow.txt#L45-L67) | Callback and routing rules (repeats "do NOT offer choices" rules) |
| [twilio.py L725-735](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/twilio.py#L725-L735) | Live session override (same 2-step flow, reworded) |

**Problem**: Each version phrases the rules slightly differently. The AI has to reconcile 3 versions of the same instructions, which increases token consumption and can cause confusion when wording conflicts.

---

### 3. Named Callback Requests — Duplicated 2×

| Location | |
|----------|--|
| [06_routing.txt L36-46](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/06_routing.txt#L36-L46) | Full named callback rules |
| [07_booking_flow.txt L57-67](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/07_booking_flow.txt#L57-L67) | **Exact same text** copy-pasted again |

These are identical blocks. This wastes tokens and adds noise.

---

### 4. "Never Ask for Phone Number" — Stated 6+ Times

This rule appears in **at least 6 different places**:

1. [05_caller_handling.txt L42-46](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/05_caller_handling.txt#L42-L46) — "NEVER ask the caller for their phone number"
2. [07_booking_flow.txt L39](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/07_booking_flow.txt#L39) — "NEVER ask for it"
3. [07_booking_flow.txt L72](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/07_booking_flow.txt#L72) — "Phone is ALWAYS already known — do NOT ask"
4. [09_client_workflows.txt L69](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/09_client_workflows.txt#L69) — "NEVER ask the caller to provide their phone number"
5. [twilio.py L738](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/twilio.py#L738) — "NEVER ask for it again"
6. [twilio.py L751](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/twilio.py#L751) — "(confirmed — do NOT ask)"

While repeating important rules can help, 6× is excessive and adds ~500 tokens of redundancy.

---

### 5. Transfer Failure Handling — Conflicting Behavior

| Location | What it says |
|----------|-------------|
| [06_routing.txt L26](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/06_routing.txt#L26) | "Do NOT ask to choose between 'leave a message' or 'callback'. Just confirm callback." |
| [08_message_security.txt L3-6](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/08_message_security.txt#L3-L6) | "Offer callback. Offer message taking. Offer appointment scheduling." (3 choices) |

Section `06_routing.txt` explicitly **forbids** offering choices, but `08_message_security.txt` says to **offer 3 options** (callback, message, appointment). These directly conflict.

---

### 6. Prospect Phone Number — Contradicting Info Collection

| Location | What it says |
|----------|-------------|
| [06_routing.txt L68-72](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/06_routing.txt#L68-L72) | Prospect handling: Collect "Phone Number" from prospects |
| [05_caller_handling.txt L42-46](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/05_caller_handling.txt#L42-L46) | "NEVER ask the caller for their phone number" |

The prospect section says collect phone number, but the phone rule says NEVER ask. This confuses the AI about what to do with new callers.

---

### 7. Office Status Check — Commented Out in Code

In [common_tools.py L225-240](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/common_tools.py#L225-L240), the office status context injection is **completely commented out**:

```python
# office_ctx = get_office_status_context()
# result["office_open"]   = office_ctx["office_open"]
# result["office_status"] = office_ctx["office_status"]
# result["office_note"]   = office_ctx["english_note"]
# result["banglish_note"] = office_ctx["banglish_note"]
```

But the prompt section [07_booking_flow.txt L10-30](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/07_booking_flow.txt#L10-L30) contains detailed instructions about reading `office_status`, `office_note`, and `banglish_note` from the tool result. **These fields are never sent**, so the AI will never see them — yet it's told to look for them.

---

### 8. Language Lock — Re-stated 3×

| Location | |
|----------|--|
| [01_language_rules.txt L1-11](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/01_language_rules.txt#L1-L11) | Primary language rules |
| [twilio.py L716-723](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/twilio.py#L716-L723) | Live session "LANGUAGE LOCK — CRITICAL" |
| [twilio.py L1388-1398](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/twilio.py#L1388-L1398) | Post-tool-call language enforcement |

Same rule, three places, slightly different wording each time.

---

### 9. `full_prompt_template.txt` — Dead but Dangerous

[full_prompt_template.txt](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/full_prompt_template.txt) is 651 lines of the OLD monolithic prompt. It is **never used** in production (because `prompt_sections/` directory exists and takes priority in [prompts.py L87-96](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/services/prompts.py#L87-L96)). However:

- It drifts out of sync with the section files
- If someone accidentally deletes `prompt_sections/`, the AI reverts to stale rules
- It causes confusion for developers maintaining the project

---

### 10. Noise Filtering in Wrong Section File

[02_noise_filtering.txt](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/02_noise_filtering.txt) contains **both** noise filtering rules AND the entire call ending flow. The file is labeled "Noise Filtering & Call Ending" in the admin panel, but the call ending flow is a major behavioral section that doesn't logically belong with noise filtering.

---

## 🟡 Non-Conflicting but Problematic Issues

### 11. Token Budget Bloat

The combined prompt (Layer 1 sections + Layer 3 inline) is approximately **42,000+ characters** before knowledge base injection. This is very large for a realtime voice session. Redundant/duplicate rules consume ~5,000-8,000 characters unnecessarily.

### 12. Transfer is Simulated, Not Real

The `handle_transfer_call` in [common_tools.py L247-304](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/routers/common_tools.py#L247-L304) doesn't actually dial anyone — it plays AI-spoken hold messages and then says the person is unavailable after 24 seconds. The **real** Twilio `<Dial>` transfer only happens in the `/forward-call` TwiML endpoint, but `transfer_call` tool never redirects there. This means **no actual call transfer ever happens** from the WebSocket voice session.

### 13. `01_language_rules.txt` Contains "Ami ki call ta shesh korte pari?"

In [01_language_rules.txt L42](file:///c:/JVAI%20Project/AiVoiceReceptionist.FastAPI/data/prompt_sections/01_language_rules.txt#L42), the Banglish speech examples include "Ami ki call ta shesh korte pari?" (Can I end the call?). This is listed as a **preferred style example**, but the call ending rules explicitly say this question is **REMOVED and forbidden**.

---

## ✅ Recommended Fix: Consolidate into One Consistent Set

As Simon suggested, the solution is to **gather all requirements and streamline them into one consistent set of instructions**:

### Step 1: Fix the `end_call` tool description
Change the tool description from "ONLY call this AFTER asking permission" to match the prompt's 2-step flow (say goodbye → call end_call, no permission question).

### Step 2: Eliminate duplicate rules
Each rule should exist in **exactly one place**. Currently duplicated:
- Call ending flow (3×) → consolidate into `02_noise_filtering.txt` only
- Named callback requests (2×) → keep in `06_routing.txt` only
- "Never ask phone number" (6×) → keep in `05_caller_handling.txt` + CRM profile only
- Language lock (3×) → keep in `01_language_rules.txt` only, remove from inline
- Transfer failure / callback rules (2×) → keep in `06_routing.txt` only

### Step 3: Fix the conflicts
- Remove "Phone Number" from prospect collection list in `06_routing.txt`
- Remove "Offer callback / message / appointment" from `08_message_security.txt` OR change it to match the "just confirm callback" rule
- Remove "Ami ki call ta shesh korte pari?" from Banglish examples in `01_language_rules.txt`
- Either uncomment the office_status code in `common_tools.py` or remove the office_status instructions from `07_booking_flow.txt`

### Step 4: Reduce inline session rules
The `twilio.py` inline instructions (Layer 3) should be **minimal** — just the CRM profile data and a one-line reminder about language. All behavioral rules should live in the section files only.

### Step 5: Archive or delete `full_prompt_template.txt`
Since the section files are the source of truth, the monolithic template should be removed or archived to prevent accidental fallback to stale instructions.

---

## 📊 Summary Table of All Issues

| # | Issue | Severity | Type |
|---|-------|----------|------|
| 1 | `end_call` tool description contradicts prompt | 🔴 Critical | **Conflict** |
| 2 | Call ending flow duplicated 3× | 🟡 Medium | Redundancy |
| 3 | Named callback duplicated 2× | 🟢 Low | Redundancy |
| 4 | "Never ask phone" repeated 6× | 🟡 Medium | Redundancy |
| 5 | Transfer failure: "no choices" vs "offer 3 options" | 🔴 Critical | **Conflict** |
| 6 | Prospect handling says "collect phone" vs "never ask phone" | 🔴 Critical | **Conflict** |
| 7 | Office status code commented out but prompt expects it | 🔴 Critical | **Conflict** |
| 8 | Language lock stated 3× | 🟢 Low | Redundancy |
| 9 | `full_prompt_template.txt` is stale fallback | 🟡 Medium | Maintenance risk |
| 10 | Call ending rules in "noise filtering" file | 🟢 Low | Organization |
| 11 | ~42KB+ prompt, ~5-8KB redundant | 🟡 Medium | Performance |
| 12 | Transfer never actually dials anyone | 🟡 Medium | Feature gap |
| 13 | Forbidden phrase in preferred examples | 🔴 Critical | **Conflict** |
