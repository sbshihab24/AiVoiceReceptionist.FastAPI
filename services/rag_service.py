import os
from pathlib import Path


KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / "data" / "knowledge.txt"
DEFAULT_KNOWLEDGE = (
    "Pay Minimum Tax is an ultra-premium AI receptionist that assists "
    "businesses in handling calls efficiently."
)


def save_knowledge(content: str) -> None:
    """Persist the company knowledge base used by the AI prompt."""
    KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_PATH.write_text(content, encoding="utf-8")

def load_knowledge() -> str:
    """
    Loads knowledge base text from the data directory.
    """
    # Look for knowledge.txt in data/ or root directory
    paths_to_try = [
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "knowledge.txt"),
        os.path.join(os.getcwd(), "data", "knowledge.txt"),
        "data/knowledge.txt",
        "knowledge.txt"
    ]
    for p in paths_to_try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return DEFAULT_KNOWLEDGE
