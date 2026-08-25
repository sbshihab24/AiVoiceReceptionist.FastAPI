import logging
import os
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

# AI Model Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")


async def generate_ai_response(user_message: str, system_context: Optional[str] = None) -> str:
    """
    Generates a text-based AI response using the OpenAI Chat Completions API.
    Used for dashboard insights and other non-realtime AI generation tasks.
    Falls back to a canned response if no API key is set.
    """
    from services.prompts import system_prompt
    if system_context is None:
        system_context, _ = system_prompt()

    if OPENAI_API_KEY:
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_context},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 300
        }
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(OPENAI_API_URL, json=payload, headers=headers, timeout=15.0)
                if response.status_code == 200:
                    return response.json()["choices"][0]["message"]["content"]
                else:
                    logger.error(f"OpenAI API error {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Error calling OpenAI API: {e}")

    # Fallback response when API key is missing or call fails
    return "I'm sorry, I'm unable to generate a response right now. Please contact the team directly."
