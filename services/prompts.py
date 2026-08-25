from services.rag_service import load_knowledge
import datetime
import json
import random
from pathlib import Path
from zoneinfo import ZoneInfo


OFFICE_TIMEZONE = "America/New_York"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
GREETINGS_PATH = DATA_DIR / "greetings.json"
FULL_PROMPT_TEMPLATE_PATH = DATA_DIR / "full_prompt_template.txt"
PROMPT_SECTIONS_DIR = DATA_DIR / "prompt_sections"

# Ordered list of section files that make up the full prompt.
# Add, remove, or reorder files here to change the assembled prompt.
SECTION_FILES = [
    "01_language_rules.txt",
    "02_noise_filtering.txt",
    "03_identity.txt",
    "04_responsibilities.txt",
    "05_caller_handling.txt",
    "06_routing.txt",
    "07_booking_flow.txt",
    "08_message_security.txt",
    "09_client_workflows.txt",
    "10_response_style.txt",
]

DEFAULT_GREETINGS = [
    # "Thank you for calling Pay Minimum Tax. I am রেবা speaking. How can I help you today?",
    # "Thank you for calling Pay Minimum Tax. This is রেবা speaking. How may I assist you today?",
    # "Thank you for calling Pay Minimum Tax. I am রেবা. What can I do for you today?",
    # "Thank you for calling Pay Minimum Tax. I am রেবা. Who do I have the pleasure of speaking with today?",
    "Dhonnobad, Thank you for calling Pay Minimum Tax, I am রেবা, How can I help you?",
    "Dhonnobad, Thank you for calling Pay Minimum Tax, I am রেবা, What could I do for you?",
    "Dhonnobad, Thank you for calling Pay Minimum Tax, I am রেবা, Who do I have the pleasure to speak with today?",
    # "Assalamu Alaikum, ami রেবা bolchi Pay Minimum Tax theke. Ami apnake kibhabe help korte pari?",
]

DEFAULT_FULL_PROMPT_TEMPLATE = """# IDENTITY

You are রেবা, the professional AI front-desk receptionist for Pay Minimum Tax.
Current Date and Time: {current_time}
Office Timezone: Eastern Time / New York ({office_timezone})

# GREETING

Use this first greeting exactly once:
"{selected_greeting}"

# KNOWLEDGE BASE RULES

Answer company/service questions only from the knowledge base below. If the answer is not there, say you do not have that specific information and the team will follow up.

# KNOWLEDGE BASE

{knowledge}
"""


def load_greetings() -> list[str]:
    try:
        greetings = json.loads(GREETINGS_PATH.read_text(encoding="utf-8"))
        clean_greetings = [str(item).strip() for item in greetings if str(item).strip()]
        return clean_greetings or DEFAULT_GREETINGS
    except Exception:
        return DEFAULT_GREETINGS


def save_greetings(greetings_text: str) -> list[str]:
    greetings = [line.strip() for line in greetings_text.splitlines() if line.strip()]
    if not greetings:
        raise ValueError("At least one greeting is required")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GREETINGS_PATH.write_text(json.dumps(greetings, indent=2, ensure_ascii=False), encoding="utf-8")
    return greetings


def load_full_prompt_template() -> str:
    """Assemble the prompt from individual section files.

    Falls back to the monolithic full_prompt_template.txt if the
    sections directory is missing, then to the hard-coded default.
    """
    # --- Primary: load from prompt_sections/ directory ---
    if PROMPT_SECTIONS_DIR.exists():
        parts: list[str] = []
        for filename in SECTION_FILES:
            path = PROMPT_SECTIONS_DIR / filename
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(content)
        if parts:
            return "\n\n".join(parts)

    # --- Fallback 1: monolithic template file ---
    try:
        template = FULL_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
        return template if template.strip() else DEFAULT_FULL_PROMPT_TEMPLATE
    except Exception:
        pass

    # --- Fallback 2: hard-coded default ---
    return DEFAULT_FULL_PROMPT_TEMPLATE


def save_full_prompt_template(template: str) -> None:
    if not template.strip():
        raise ValueError("Prompt template cannot be empty")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FULL_PROMPT_TEMPLATE_PATH.write_text(template, encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-section helpers (used by the admin panel)
# ---------------------------------------------------------------------------

# Human-readable labels for each section file shown in the admin UI.
SECTION_LABELS: dict[str, str] = {
    "01_language_rules.txt":   "Language Rules & Bangla Style",
    "02_noise_filtering.txt":  "Noise Filtering & Call Ending",
    "03_identity.txt":         "Identity & Greeting",
    "04_responsibilities.txt": "Responsibilities & Voice Rules",
    "05_caller_handling.txt":  "Caller Classification & VIP",
    "06_routing.txt":          "Call Routing & Urgent Calls",
    "07_booking_flow.txt":     "Appointment Booking Flow",
    "08_message_security.txt": "Messaging, Security & KB Rules",
    "09_client_workflows.txt": "Client Workflows & Etiquette",
    "10_response_style.txt":   "Response Style & Knowledge Base",
}


def list_sections() -> list[dict]:
    """Return ordered list of section metadata for the admin UI."""
    result = []
    for filename in SECTION_FILES:
        path = PROMPT_SECTIONS_DIR / filename
        result.append({
            "filename": filename,
            "label": SECTION_LABELS.get(filename, filename),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
        })
    return result


def load_section(filename: str) -> str:
    """Load a single prompt section file. Raises ValueError for invalid names."""
    if filename not in SECTION_FILES:
        raise ValueError(f"Unknown section: {filename}")
    path = PROMPT_SECTIONS_DIR / filename
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_section(filename: str, content: str) -> None:
    """Persist a single prompt section file. Raises ValueError on bad input."""
    if filename not in SECTION_FILES:
        raise ValueError(f"Unknown section: {filename}")
    if not content.strip():
        raise ValueError(f"Section '{filename}' cannot be empty")
    PROMPT_SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    (PROMPT_SECTIONS_DIR / filename).write_text(content, encoding="utf-8")


def render_full_prompt_template(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def system_prompt() -> tuple[str, str]:
    knowledge = load_knowledge()
    now_et = datetime.datetime.now(ZoneInfo(OFFICE_TIMEZONE))
    current_time = now_et.strftime("%A, %B %d, %Y %I:%M:%S %p %Z")

    greetings = load_greetings()
    selected_greeting = random.choice(greetings)
    template = load_full_prompt_template()
    full_prompt = render_full_prompt_template(template, {
        "current_time": current_time,
        "office_timezone": OFFICE_TIMEZONE,
        "selected_greeting": selected_greeting,
        "knowledge": knowledge,
    })
    return full_prompt, selected_greeting

