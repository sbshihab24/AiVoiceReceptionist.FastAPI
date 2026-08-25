import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional


KNOWN_CLIENTS_PATH = Path(__file__).resolve().parents[1] / "data" / "known_clients.json"


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return f"1{digits}"
    return digits


@lru_cache(maxsize=1)
def _load_known_clients() -> list[dict]:
    with KNOWN_CLIENTS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def list_known_clients() -> list[dict]:
    return list(_load_known_clients())


def find_known_client_by_phone(phone: str) -> Optional[dict]:
    normalized = normalize_phone(phone)
    if not normalized:
        return None

    for client in _load_known_clients():
        if normalize_phone(client.get("phone", "")) == normalized:
            return client
    return None


def find_known_client_by_email(email: str) -> Optional[dict]:
    """Find a client by email address (case-insensitive)."""
    if not email or not email.strip():
        return None
    email_lower = email.strip().lower()
    for client in _load_known_clients():
        if client.get("email", "").strip().lower() == email_lower:
            return client
    return None


def find_known_client_by_company(company: str) -> Optional[dict]:
    """Find a client by business name (case-insensitive, partial match)."""
    if not company or not company.strip():
        return None
    company_lower = company.strip().lower()
    for client in _load_known_clients():
        biz = client.get("business_name", "").strip().lower()
        if biz and (biz == company_lower or company_lower in biz or biz in company_lower):
            return client
    return None


def save_known_clients(clients: list[dict]) -> None:
    KNOWN_CLIENTS_PATH.write_text(json.dumps(clients, indent=2), encoding="utf-8")
    _load_known_clients.cache_clear()


def upsert_known_client(client: dict) -> dict:
    clients = list_known_clients()
    normalized = normalize_phone(client.get("phone", ""))
    if not normalized:
        raise ValueError("Phone number is required")

    # Format the phone number properly to E.164 (e.g. +19089067284)
    phone_raw = client.get("phone") or ""
    digits = re.sub(r"\D", "", phone_raw)
    if len(digits) == 10:
        formatted_phone = f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        formatted_phone = f"+{digits}"
    else:
        formatted_phone = phone_raw  # fallback

    clean_client = {
        "plan": client.get("plan") or "None",
        "first_name": client.get("first_name") or "",
        "last_name": client.get("last_name") or "",
        "phone": formatted_phone,
        "email": client.get("email") or "",
        "business_name": client.get("business_name") or "",
        "notes": client.get("notes") or "",
    }

    for index, existing in enumerate(clients):
        if normalize_phone(existing.get("phone", "")) == normalized:
            clients[index] = clean_client
            save_known_clients(clients)
            return clean_client

    clients.append(clean_client)
    save_known_clients(clients)
    return clean_client


def delete_known_client(phone: str) -> bool:
    normalized = normalize_phone(phone)
    clients = list_known_clients()
    remaining = [
        client for client in clients
        if normalize_phone(client.get("phone", "")) != normalized
    ]
    if len(remaining) == len(clients):
        return False
    save_known_clients(remaining)
    return True


def profile_from_known_client(client: dict) -> dict:
    first_name = (client.get("first_name") or "").strip()
    last_name = (client.get("last_name") or "").strip()
    name = f"{first_name} {last_name}".strip() or "Client"
    plan = (client.get("plan") or "").strip()
    group = plan.upper() if plan.upper() in {"A", "B", "C", "D"} else ""
    client_type = f"Class {group} Client" if group else "Known VIP Client"

    return {
        "found": True,
        "contact_id": "",
        "name": name,
        "first_name": first_name,
        "last_name": last_name,
        "group": group,
        "client_type": client_type,
        "invoice_due": False,
        "phone": client.get("phone", ""),
        "email": client.get("email", ""),
        "business_name": client.get("business_name", ""),
        "notes": client.get("notes", ""),
        "source": "known_clients",
    }
