"""Cart recovery agent — layer 5, second of the two real agents.

Same split as reconciliation: the agent may **request** a rung on the offer ladder;
`guardrails/blocks.py::discount_within_margin_floor` (the policy engine) decides what's
actually granted. The agent never computes a discount amount — it only asks for a
named rung and receives back whatever the policy decided.

Unlike reconciliation, a chat turn is not a multi-step investigation — it is one
request, at most one tool call, one reply. No SSE trace; `POST /api/chat` returns
the whole turn as a single JSON response.
"""

from __future__ import annotations

from ..guardrails.blocks import discount_within_margin_floor

# Fixed policy for the demo — a real system would look up per-SKU cost data. Values
# chosen so the CONTRACTS.md worked example (request TIER_3, grant TIER_1) reproduces
# exactly: a 10% ask would also breach an 18% floor on a 25% margin, so the policy
# skips straight past TIER_2 to the only rung that doesn't touch margin at all.
MARGIN_FLOOR_PCT = 18.0
ASSUMED_CURRENT_MARGIN_PCT = 25.0
SHIPPING_FEE_INR = 79.0

RUNG_ORDER = ["TIER_0_HOLD_FIRM", "TIER_1_FREE_SHIPPING", "TIER_2_10_PCT", "TIER_3_20_PCT"]
RUNG_DISCOUNT_PCT = {
    "TIER_0_HOLD_FIRM": 0.0,
    "TIER_1_FREE_SHIPPING": 0.0,  # a shipping waiver, not a product discount
    "TIER_2_10_PCT": 10.0,
    "TIER_3_20_PCT": 20.0,
}

SYSTEM_PROMPT = """You are Paygent's cart recovery agent, texting a customer whose cart
was abandoned before checkout. Be brief and warm. Your job is to get them to complete
the purchase.

If they ask for a better price or free shipping, call request_offer_rung with the
rung that matches what they're asking for (TIER_1_FREE_SHIPPING for a shipping ask,
TIER_2_10_PCT or TIER_3_20_PCT for a discount ask). You never decide what they
actually get — the policy engine does, and it may grant less than requested. Once you
have the result, tell the customer exactly what was granted, never what you asked for.
If the policy denies the full ask, hold the line politely — do not apologise
excessively or imply you could do better if you tried harder.
"""

TOOL_SPECS = [{
    "name": "request_offer_rung",
    "description": "Request a rung on the offer ladder for this customer. The policy engine decides what is actually granted.",
    "parameters": {
        "type": "object",
        "properties": {"rung": {"type": "string", "enum": RUNG_ORDER}},
        "required": ["rung"],
    },
}]


def decide_offer(requested_rung: str, cart_value_inr: float) -> dict:
    """The policy engine. Grants the highest rung at or below the request that does
    not breach the margin floor — matches CONTRACTS.md's OfferRung section exactly.
    """
    if requested_rung not in RUNG_ORDER:
        requested_rung = "TIER_0_HOLD_FIRM"

    requested_check = discount_within_margin_floor(
        RUNG_DISCOUNT_PCT[requested_rung], MARGIN_FLOOR_PCT, ASSUMED_CURRENT_MARGIN_PCT)

    if requested_check.passed:
        granted_rung = requested_rung
        reason = requested_check.message
    else:
        granted_rung = "TIER_0_HOLD_FIRM"
        for rung in reversed(RUNG_ORDER[: RUNG_ORDER.index(requested_rung)]):
            check = discount_within_margin_floor(
                RUNG_DISCOUNT_PCT[rung], MARGIN_FLOOR_PCT, ASSUMED_CURRENT_MARGIN_PCT)
            if check.passed:
                granted_rung = rung
                break
        reason = requested_check.message  # explain why the original ask was denied

    discount_inr = round(cart_value_inr * RUNG_DISCOUNT_PCT[granted_rung] / 100, 2)
    shipping_waived_inr = SHIPPING_FEE_INR if granted_rung == "TIER_1_FREE_SHIPPING" else 0.0

    return {
        "requested_rung": requested_rung,
        "granted_rung": granted_rung,
        "granted": granted_rung == requested_rung,
        "discount_inr": discount_inr,
        "shipping_waived_inr": shipping_waived_inr,
        "reason": reason,
        "margin_floor_pct": MARGIN_FLOOR_PCT,
        "decided_by": "policy_engine",
    }


def handle_turn(
    llm, history: list[dict], message: str, cart_value_inr: float,
) -> tuple[str, dict | None]:
    """One chat turn. Returns (agent_reply_text, offer_dict_or_None)."""
    messages = [*history, {"role": "user", "text": message}]
    completion = llm.complete(SYSTEM_PROMPT, messages, TOOL_SPECS)

    if not completion.tool_calls:
        return completion.text or "Let me see what I can do for you.", None

    call = completion.tool_calls[0]
    offer = decide_offer(call.args.get("rung", "TIER_0_HOLD_FIRM"), cart_value_inr)

    messages.append({"role": "model", "tool_calls": completion.tool_calls, "text": completion.text or ""})
    messages.append({
        "role": "tool", "call_id": call.id, "name": call.name,
        "response": f"Granted {offer['granted_rung']}: {offer['reason']}",
    })
    followup = llm.complete(SYSTEM_PROMPT, messages, TOOL_SPECS)
    return followup.text or "Here's what I can offer.", offer
