"""POST /api/chat — cart recovery. Layer 7.

Conversation state lives in-process. There is no `conversations` table in the schema
(CONTRACTS.md never defines a persisted Conversation object either) — fine for a
backend that runs once through a single demo and doesn't need to survive a restart
mid-conversation.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from ..agents import cart
from ..agents.llm import GeminiClient, GroqClient, get_llm
from ..db import conn as db

router = APIRouter()

_MIN_CART_VALUE_INR = 2000.0  # same floor scanner.py uses for a cart to earn a case
_DEFAULT_CART_VALUE_INR = 3980.0  # only used if the seed has no abandoned carts at all

# conversation_id -> {history, messages, session_id, cart_value_inr}
_conversations: dict[str, dict] = {}
_llm: GeminiClient | GroqClient | None = None

# This route is a sync `def`, so FastAPI runs it in a threadpool — two requests for
# the *same* conversation_id can genuinely execute concurrently, not just interleave
# on an event loop. That happens for real: React StrictMode's dev double-invoke (and,
# in principle, two tabs polling the same cart) can fire the opening call for a
# brand-new conversation twice within milliseconds of each other. Without a lock,
# both threads can read `is_new = True` before either writes to `_conversations`,
# both call the LLM, and whichever response the frontend applies last can be the one
# that read `convo["messages"]` before the other thread's append — silently
# reverting a populated conversation back to empty. One global lock serializes every
# `/api/chat` call; at this traffic (one demo, effectively one shopper at a time)
# that costs nothing observable and removes the race entirely.
_LOCK = threading.Lock()


class ChatRequest(BaseModel):
    conversation_id: str
    message: str = ""
    session_id: str | None = None
    cart_value_inr: float | None = None
    # Purely additive — CONTRACTS.md's POST /api/chat example never enumerated its
    # request fields exhaustively (session_id/cart_value_inr above are the same kind
    # of addition). Which product is in the cart, so the policy engine can apply
    # that product's own margin floor instead of one fixed number for every cart.
    sku: str | None = None


def _get_llm() -> GeminiClient | GroqClient:
    global _llm
    if _llm is None:
        _llm = get_llm()
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
    with _LOCK:
        is_new = body.conversation_id not in _conversations
        if is_new:
            if body.session_id and body.cart_value_inr:
                # Bound to a real cart the caller already knows about — the live
                # abandonment beat, not a seeded fixture.
                cart_info = {
                    "session_id": body.session_id, "cart_value_inr": body.cart_value_inr,
                    "sku": body.sku,
                }
            else:
                conn = db.connect()
                try:
                    cart_info = _pick_cart(conn, body.conversation_id)
                finally:
                    conn.close()
            _conversations[body.conversation_id] = {"history": [], "messages": [], **cart_info}

        convo = _conversations[body.conversation_id]

        if not body.message:
            if is_new:
                # No customer message yet — the agent opens. Cart recovery reaches
                # out after the customer has already gone, it doesn't wait to be
                # spoken to.
                reply = cart.open_conversation(_get_llm(), convo["cart_value_inr"])
                convo["history"].append({"role": "model", "text": reply})
                convo["messages"].append({"role": "agent", "text": reply, "at": _now_iso()})
            # An empty message on an existing conversation is a keep-alive/poll, not
            # a turn — never call the LLM for it. Whatever's already in `messages`
            # is the correct response either way — the lock above means a second,
            # near-simultaneous open for a brand-new conversation waits for the
            # first one's LLM call to finish instead of reading it mid-flight.
            return {
                "conversation_id": body.conversation_id,
                "messages": convo["messages"],
                "cart_value_inr": convo["cart_value_inr"],
                "offer": None,
                "done": False,
            }

        convo["messages"].append({"role": "customer", "text": body.message, "at": _now_iso()})

        reply, offer = cart.handle_turn(
            _get_llm(), convo["history"], body.message, convo["cart_value_inr"], convo.get("sku"))

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
