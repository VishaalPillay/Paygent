"""POST /api/chat — cart recovery. Layer 7.

Conversation state lives in-process. There is no `conversations` table in the schema
(CONTRACTS.md never defines a persisted Conversation object either) — fine for a
backend that runs once through a single demo and doesn't need to survive a restart
mid-conversation.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from ..agents import cart
from ..agents.llm import GeminiClient
from ..db import conn as db

router = APIRouter()

_MIN_CART_VALUE_INR = 2000.0  # same floor scanner.py uses for a cart to earn a case
_DEFAULT_CART_VALUE_INR = 3980.0  # only used if the seed has no abandoned carts at all

# conversation_id -> {history, messages, session_id, cart_value_inr}
_conversations: dict[str, dict] = {}
_llm: GeminiClient | None = None


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


def _get_llm() -> GeminiClient:
    global _llm
    if _llm is None:
        _llm = GeminiClient()
    return _llm


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick_cart(conn, conversation_id: str) -> dict:
    """A real abandoned cart from the seed, chosen deterministically from the
    conversation_id so refreshing the page doesn't swap the customer's cart."""
    rows = conn.execute(
        "SELECT session_id, cart_value_inr FROM checkout_sessions "
        "WHERE attempted = 0 AND cart_value_inr >= ? ORDER BY session_id",
        (_MIN_CART_VALUE_INR,),
    ).fetchall()
    if not rows:
        return {"session_id": None, "cart_value_inr": _DEFAULT_CART_VALUE_INR}
    idx = int(hashlib.sha256(conversation_id.encode()).hexdigest(), 16) % len(rows)
    return {"session_id": rows[idx]["session_id"], "cart_value_inr": rows[idx]["cart_value_inr"]}


@router.post("/chat")
def chat(body: ChatRequest) -> dict:
    if body.conversation_id not in _conversations:
        conn = db.connect()
        try:
            cart_info = _pick_cart(conn, body.conversation_id)
        finally:
            conn.close()
        _conversations[body.conversation_id] = {"history": [], "messages": [], **cart_info}

    convo = _conversations[body.conversation_id]
    convo["messages"].append({"role": "customer", "text": body.message, "at": _now_iso()})

    reply, offer = cart.handle_turn(
        _get_llm(), convo["history"], body.message, convo["cart_value_inr"])

    convo["history"].append({"role": "user", "text": body.message})
    convo["history"].append({"role": "model", "text": reply})
    convo["messages"].append({"role": "agent", "text": reply, "at": _now_iso()})

    return {
        "conversation_id": body.conversation_id,
        "messages": convo["messages"],
        "cart_value_inr": convo["cart_value_inr"],
        "offer": offer,
        "done": False,
    }
