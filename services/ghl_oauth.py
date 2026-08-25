"""
GHL V2 OAuth Token Manager
--------------------------
Handles automatic refresh of GHL V2 access tokens before they expire (24h TTL).

Usage:
  - Call `start_token_refresh_loop()` from main.py on startup.
  - Call `get_v2_access_token()` anywhere to get the current valid token.
  - Store GHL_CLIENT_ID, GHL_CLIENT_SECRET, GHL_V2_ACCESS_TOKEN, GHL_V2_REFRESH_TOKEN in .env
"""

import asyncio
import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

GHL_TOKEN_URL = "https://services.leadconnectorhq.com/oauth/token"

# In-memory token state
_token_state = {
    "access_token": "",
    "refresh_token": "",
    "expires_at": 0.0,  # unix timestamp when access_token expires
}

# How many seconds before expiry we proactively refresh (30 minutes)
REFRESH_BEFORE_EXPIRY_SECS = 30 * 60


def _load_from_env() -> None:
    """Load token state from environment variables on startup."""
    _token_state["access_token"] = os.getenv("GHL_V2_ACCESS_TOKEN", "")
    _token_state["refresh_token"] = os.getenv("GHL_V2_REFRESH_TOKEN", "")
    # If we don't know the expiry, assume it's valid for now but refresh soon
    expires_at_env = os.getenv("GHL_V2_TOKEN_EXPIRES_AT", "0")
    try:
        _token_state["expires_at"] = float(expires_at_env)
    except ValueError:
        _token_state["expires_at"] = 0.0


def get_v2_access_token() -> str:
    """Return the current GHL V2 access token from memory."""
    return _token_state["access_token"]


def is_token_expiring_soon() -> bool:
    """Returns True if the token will expire within REFRESH_BEFORE_EXPIRY_SECS."""
    if not _token_state["access_token"]:
        return True
    return time.time() >= (_token_state["expires_at"] - REFRESH_BEFORE_EXPIRY_SECS)


def _persist_tokens(access_token: str, refresh_token: str, expires_at: float) -> None:
    """Write new token values back to .env and update in-memory state."""
    _token_state["access_token"] = access_token
    _token_state["refresh_token"] = refresh_token
    _token_state["expires_at"] = expires_at

    # Update os.environ in-memory immediately
    os.environ["GHL_V2_ACCESS_TOKEN"] = access_token
    os.environ["GHL_V2_REFRESH_TOKEN"] = refresh_token
    os.environ["GHL_V2_TOKEN_EXPIRES_AT"] = str(expires_at)

    # Also persist to .env file on disk for restart survival
    _update_env_file({
        "GHL_V2_ACCESS_TOKEN": access_token,
        "GHL_V2_REFRESH_TOKEN": refresh_token,
        "GHL_V2_TOKEN_EXPIRES_AT": str(expires_at),
    })


def _update_env_file(updates: dict) -> None:
    """Update specific keys in the .env file without touching other values."""
    env_path = "/app/.env" if os.path.exists("/app/.env") else ".env"
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    new_lines = []
    keys_updated = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                keys_updated.add(key)
                continue
        new_lines.append(line)

    for key, value in updates.items():
        if key not in keys_updated:
            new_lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


async def refresh_access_token() -> bool:
    """
    Call GHL token endpoint with refresh_token to get a new access_token.
    Returns True on success, False on failure.
    """
    client_id = os.getenv("GHL_CLIENT_ID", "")
    client_secret = os.getenv("GHL_CLIENT_SECRET", "")
    refresh_token = _token_state["refresh_token"] or os.getenv("GHL_V2_REFRESH_TOKEN", "")

    if not client_id or not client_secret or not refresh_token:
        logger.warning("⚠️ [GHL OAuth] Missing credentials for token refresh. "
                       "Set GHL_CLIENT_ID, GHL_CLIENT_SECRET, GHL_V2_REFRESH_TOKEN in .env")
        return False

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                GHL_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if resp.status_code == 200:
            data = resp.json()
            new_access_token = data.get("access_token", "")
            new_refresh_token = data.get("refresh_token", refresh_token)  # GHL may rotate refresh token
            expires_in = data.get("expires_in", 86400)  # seconds, default 24h
            expires_at = time.time() + expires_in

            _persist_tokens(new_access_token, new_refresh_token, expires_at)

            logger.info(f"✅ [GHL OAuth] Token refreshed successfully. "
                        f"Expires in {expires_in // 3600}h {(expires_in % 3600) // 60}m. "
                        f"Next refresh in ~{(expires_in - REFRESH_BEFORE_EXPIRY_SECS) // 3600}h.")
            return True
        else:
            logger.error(f"❌ [GHL OAuth] Token refresh failed: {resp.status_code} — {resp.text[:300]}")
            return False

    except Exception as e:
        logger.error(f"❌ [GHL OAuth] Token refresh exception: {e}")
        return False


async def start_token_refresh_loop() -> None:
    """
    Background loop that runs forever.
    Checks every 5 minutes if the token needs refreshing.
    Refreshes if expiry is within 30 minutes.
    """
    _load_from_env()

    if not _token_state["access_token"]:
        logger.info("ℹ️ [GHL OAuth] No GHL V2 token configured — SMS will use Twilio fallback.")
        return

    logger.info("🔄 [GHL OAuth] Token refresh loop started.")

    # If token state has no known expiry, refresh immediately on startup
    if _token_state["expires_at"] == 0.0:
        logger.info("🔄 [GHL OAuth] No expiry known — refreshing token on startup...")
        await refresh_access_token()

    while True:
        try:
            await asyncio.sleep(5 * 60)  # Check every 5 minutes
            if is_token_expiring_soon():
                logger.info("⏰ [GHL OAuth] Token expiring soon — refreshing...")
                success = await refresh_access_token()
                if not success:
                    logger.error("❌ [GHL OAuth] Refresh failed — will retry in 5 minutes.")
        except asyncio.CancelledError:
            logger.info("🛑 [GHL OAuth] Token refresh loop cancelled.")
            break
        except Exception as e:
            logger.error(f"❌ [GHL OAuth] Unexpected error in refresh loop: {e}")
            await asyncio.sleep(60)  # Wait 1 min before retrying on unexpected error
