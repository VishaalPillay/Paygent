"""Generates frontend/src/mock/conversations.json — seeded POST /api/chat turns,
keyed by conversation_id, for VITE_USE_MOCK=true. Not a pipeline step: run once by
hand against the real Gemini API whenever agents/cart.py's behaviour changes.

Matches CLAUDE.md's degrade path for Conversations: two scripted turns is the whole
demo beat if time runs short, so that's exactly what this fixture captures — a real
run, not hand-written JSON.
"""

import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from backend.api.chat import ChatRequest, chat  # noqa: E402 — must follow load_dotenv()

OUT_PATH = Path(__file__).resolve().parent.parent / "frontend" / "src" / "mock" / "conversations.json"
CONVERSATION_ID = "cnv_0007"

turns = [
    "can you do better on price?",
]

last = None
for message in turns:
    last = chat(ChatRequest(conversation_id=CONVERSATION_ID, message=message))

fixture = {CONVERSATION_ID: last}
OUT_PATH.write_text(json.dumps(fixture, indent=2))
print(f"wrote conversation {CONVERSATION_ID} ({len(last['messages'])} messages) to {OUT_PATH}")
print(json.dumps(last, indent=2))
