"""Tools the reconciliation agent can call. Layer 5, reading layer 2/4's tables.

Every tool is read-mostly and returns a short dict plus a one-sentence summary — a
tool returning 400 lines of JSON burns context and produces worse reasoning than one
returning five words. The one tool that can move money, `propose_action`, never
computes a rupee figure itself and never decides anything: it hands the proposal to
`guardrails/engine.py` and returns whatever that decides. An LLM never picks a number
that moves money.

Reads go straight through `backend/db/conn.py` against the real schema in
`backend/db/schema.sql` — the four ledgers, `signals`, and `cases`. No ORM, no
in-memory fixture layer: the DB is real and stable enough to build against directly.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from ..db import conn as db
from ..guardrails import engine as guardrail_engine
from ..guardrails.blocks import no_duplicate_order_exists, refund_requires_terminal_payment

# A duplicate-order candidate must fall within this window of the payment under
# investigation. Wide enough to catch a same-session re-order, narrow enough that an
# unrelated later purchase by the same customer doesn't look like a duplicate.
DUPLICATE_SEARCH_WINDOW_SECONDS = 3600


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


def get_case(conn: sqlite3.Connection, case_id: str) -> tuple[dict | None, str]:
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        return None, f"No case {case_id}."
    case = dict(row)
    case["payload"] = json.loads(case.pop("payload_json") or "{}")
    return case, f"{case['title']} — Rs {case['rupees_at_risk_inr']:,.2f} at risk."


def get_payment(conn: sqlite3.Connection, payment_id: str) -> tuple[dict | None, str]:
    row = conn.execute(
        "SELECT * FROM payments WHERE payment_id = ?", (payment_id,)
    ).fetchone()
    if row is None:
        return None, f"No payment {payment_id}."
    p = dict(row)
    return p, f"Payment {payment_id} is {p['state']}, Rs {p['amount_inr']:,.2f} via {p['method']}."


def get_ledger_snapshot(conn: sqlite3.Connection, payment_id: str) -> tuple[dict | None, str]:
    """The four ledgers for one payment, reconstructed the same way the scanner does:
    absence of a row is the MISSING/NOT_APPLICABLE state, not a null to special-case."""
    row = conn.execute(
        """SELECT p.state AS payment_state, p.updated_at, p.amount_inr,
                  c.business_type,
                  o.order_id, o.state AS order_state,
                  i.state AS inventory_state,
                  a.state AS accounting_state
           FROM payments p
           JOIN customers c ON c.customer_id = p.customer_id
           LEFT JOIN orders o ON o.order_id = (
               SELECT order_id FROM orders x WHERE x.payment_id = p.payment_id
               ORDER BY x.updated_at DESC, x.order_id DESC LIMIT 1)
           LEFT JOIN inventory_events i ON i.inventory_id = (
               SELECT inventory_id FROM inventory_events x WHERE x.order_id = o.order_id
               ORDER BY x.updated_at DESC, x.inventory_id DESC LIMIT 1)
           LEFT JOIN accounting_entries a ON a.entry_id = (
               SELECT entry_id FROM accounting_entries x WHERE x.order_id = o.order_id
               ORDER BY x.booked_at DESC, x.entry_id DESC LIMIT 1)
           WHERE p.payment_id = ?""",
        (payment_id,),
    ).fetchone()
    if row is None:
        return None, f"No payment {payment_id}."
    now = db.reference_now(conn)
    age_seconds = int((now - _parse(row["updated_at"])).total_seconds())
    snapshot = {
        "payment": row["payment_state"],
        "order": row["order_state"] or "MISSING",
        "inventory": row["inventory_state"]
        or ("NOT_APPLICABLE" if row["business_type"] == "SAAS" else "AVAILABLE"),
        "accounting": row["accounting_state"] or "NOT_BOOKED",
        "age_seconds": age_seconds,
        "observed_at": _iso(now),
    }
    summary = (
        f"payment={snapshot['payment']} order={snapshot['order']} "
        f"inventory={snapshot['inventory']} accounting={snapshot['accounting']}, "
        f"held for {age_seconds // 60} min."
    )
    return snapshot, summary


def search_orders_for_payment(
    conn: sqlite3.Connection, payment_id: str
) -> tuple[list[dict], str]:
    """Scenario 1's investigation path: does an order already exist for this intent,
    just not linked to this payment? Matches by same customer, amount within 1%, and
    a timestamp window around the payment — the closest this schema gets to
    'receipt/notes, then phone+email, then amount+timestamp' without a notes field.
    """
    payment = conn.execute(
        "SELECT customer_id, amount_inr, session_id, updated_at FROM payments WHERE payment_id = ?",
        (payment_id,),
    ).fetchone()
    if payment is None:
        return [], f"No payment {payment_id}."

    when = _parse(payment["updated_at"])
    window_start = _iso(when - timedelta(seconds=DUPLICATE_SEARCH_WINDOW_SECONDS))
    window_end = _iso(when + timedelta(seconds=DUPLICATE_SEARCH_WINDOW_SECONDS))
    amount = payment["amount_inr"]

    rows = conn.execute(
        """SELECT order_id, payment_id, session_id, state, amount_inr, created_at
           FROM orders
           WHERE customer_id = ?
             AND ABS(amount_inr - ?) <= ? * 0.01
             AND created_at BETWEEN ? AND ?
             AND state != 'CANCELLED'""",
        (payment["customer_id"], amount, amount, window_start, window_end),
    ).fetchall()
    matches = [dict(r) for r in rows]
    if not matches:
        return [], "No matching order found by customer, amount and timestamp window."
    same_session = [m for m in matches if m["session_id"] == payment["session_id"]]
    summary = (
        f"{len(matches)} matching order(s), including {len(same_session)} from the same "
        f"checkout session." if same_session else f"{len(matches)} matching order(s) found."
    )
    return matches, summary


def get_customer_contact(conn: sqlite3.Connection, customer_id: str) -> tuple[dict | None, str]:
    row = conn.execute(
        "SELECT customer_id, name, phone, email, segment FROM customers WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    if row is None:
        return None, f"No customer {customer_id}."
    c = dict(row)
    return c, f"{c['name']} ({c['segment']}), {c['phone']}."


def get_mandate_status(conn: sqlite3.Connection, mandate_id: str) -> tuple[dict | None, str]:
    row = conn.execute("SELECT * FROM mandates WHERE mandate_id = ?", (mandate_id,)).fetchone()
    if row is None:
        return None, f"No mandate {mandate_id}."
    m = dict(row)
    used = conn.execute(
        "SELECT COUNT(*) AS n FROM mandate_attempts WHERE mandate_id = ? AND cycle = ?",
        (mandate_id, m["cycle"]),
    ).fetchone()["n"]
    m["attempts_used_this_cycle"] = used
    return m, f"Mandate {mandate_id} is {m['state']}, {used} attempt(s) used this cycle."


def get_order_fulfilment_stage(conn: sqlite3.Connection, order_id: str) -> tuple[dict | None, str]:
    row = conn.execute(
        """SELECT o.order_id, o.state AS order_state, i.state AS inventory_state
           FROM orders o
           LEFT JOIN inventory_events i ON i.inventory_id = (
               SELECT inventory_id FROM inventory_events x WHERE x.order_id = o.order_id
               ORDER BY x.updated_at DESC, x.inventory_id DESC LIMIT 1)
           WHERE o.order_id = ?""",
        (order_id,),
    ).fetchone()
    if row is None:
        return None, f"No order {order_id}."
    d = dict(row)
    return d, f"Order {order_id} is {d['order_state']}, inventory {d['inventory_state'] or 'N/A'}."


# ---------------------------------------------------------------------------
# The one mutating tool. Routes through guardrails, decides nothing itself.
# ---------------------------------------------------------------------------


def propose_action(
    conn: sqlite3.Connection,
    action_type: str,
    payment_id: str | None,
    order_id: str | None,
    confidence: float,
    reasoning: str,
) -> tuple[dict, str]:
    """The agent proposes an action type and cites its reasoning. It never supplies a
    rupee amount or an approval decision — those come from the ledger rows themselves
    and from guardrails/engine.py, never from the model.
    """
    checks = []
    amount_inr: float | None = None

    if payment_id:
        payment, _ = get_payment(conn, payment_id)
        if payment:
            amount_inr = payment["amount_inr"]
            if action_type == "ISSUE_REFUND":
                checks.append(refund_requires_terminal_payment(payment))

    if action_type == "CREATE_ORDER" and payment_id:
        matches, _ = search_orders_for_payment(conn, payment_id)
        checks.append(no_duplicate_order_exists(matches))

    if action_type == "NO_ACTION":
        amount_inr = None

    checks, tier, tier_label = guardrail_engine.evaluate(checks, confidence, amount_inr)
    blocked_by = [c.name for c in checks if not c.passed and c.blocking]
    status = "BLOCKED" if blocked_by else ("PROPOSED" if tier > 0 else "EXECUTED")
    params = {k: v for k, v in (("payment_id", payment_id), ("order_id", order_id)) if v}

    action = {
        "action_id": f"act_{secrets.token_hex(4)}",
        "type": action_type,
        "status": status,
        "amount_inr": amount_inr,
        "basis": "deterministic" if amount_inr is not None else None,
        "proposed_by": "reconciliation",
        "decided_by": "guardrails",
        "params": params,
        "blocked_by": blocked_by,
        "idempotency_key": f"recon:{payment_id or order_id}:{action_type}",
        "created_at": _iso(datetime.now(timezone.utc)),
        # Not in CONTRACTS.md's Action shape — carried for loop.py's own bookkeeping
        # (tier/guardrail_checks belong on the case, not the action, once persisted).
        "tier": tier,
        "tier_label": tier_label,
        "guardrail_checks": [c.to_dict() for c in checks],
        "reasoning": reasoning,
    }
    summary = (
        f"{action_type} blocked by {', '.join(blocked_by)}."
        if blocked_by
        else f"{action_type} decided: {tier_label}."
    )
    return action, summary


# ---------------------------------------------------------------------------
# Gemini function-calling declarations
# ---------------------------------------------------------------------------

TOOL_SPECS = [
    {
        "name": "get_ledger_snapshot",
        "description": "Get the four-ledger snapshot (payment/order/inventory/accounting state) for a payment.",
        "parameters": {
            "type": "object",
            "properties": {"payment_id": {"type": "string"}},
            "required": ["payment_id"],
        },
    },
    {
        "name": "search_orders_for_payment",
        "description": "Find orders that might match this payment's intent by customer, amount and timing — use before concluding an order is truly missing.",
        "parameters": {
            "type": "object",
            "properties": {"payment_id": {"type": "string"}},
            "required": ["payment_id"],
        },
    },
    {
        "name": "get_customer_contact",
        "description": "Get a customer's name, phone, email and segment.",
        "parameters": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "get_mandate_status",
        "description": "Get a UPI Autopay mandate's state and attempts used this cycle.",
        "parameters": {
            "type": "object",
            "properties": {"mandate_id": {"type": "string"}},
            "required": ["mandate_id"],
        },
    },
    {
        "name": "get_order_fulfilment_stage",
        "description": "Check whether an order's goods have already shipped, before proposing a cancellation or refund.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "propose_action",
        "description": (
            "Propose a resolution: CREATE_ORDER, ISSUE_REFUND, CANCEL_ORDER, "
            "ISSUE_CREDIT_NOTE, or NO_ACTION. Never supply an amount — the deterministic "
            "layer computes it. Guardrails decide whether it executes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["CREATE_ORDER", "ISSUE_REFUND", "CANCEL_ORDER",
                             "ISSUE_CREDIT_NOTE", "NO_ACTION"],
                },
                # No "type" on these two, deliberately: both are optional (not in
                # "required" below) and genuinely absent for some action types
                # (CREATE_ORDER has no order_id yet). Gemini omits an inapplicable
                # optional arg outright, but Groq's models sometimes emit it as
                # explicit JSON null — which a declared "string" type rejects with
                # a hard 400 (Groq validates tool-call args against the schema
                # server-side; Gemini doesn't). An untyped property accepts any
                # value, null included, on both providers — verified directly
                # against Groq's API, not just reasoned about.
                "payment_id": {"description": "The payment id, if this action concerns one."},
                "order_id": {"description": "The order id, if this action concerns one."},
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["action_type", "confidence", "reasoning"],
        },
    },
]

DISPATCH = {
    "get_ledger_snapshot": lambda conn, a: get_ledger_snapshot(conn, a["payment_id"]),
    "search_orders_for_payment": lambda conn, a: search_orders_for_payment(conn, a["payment_id"]),
    "get_customer_contact": lambda conn, a: get_customer_contact(conn, a["customer_id"]),
    "get_mandate_status": lambda conn, a: get_mandate_status(conn, a["mandate_id"]),
    "get_order_fulfilment_stage": lambda conn, a: get_order_fulfilment_stage(conn, a["order_id"]),
    "propose_action": lambda conn, a: propose_action(
        conn, a["action_type"], a.get("payment_id"), a.get("order_id"),
        a["confidence"], a["reasoning"],
    ),
}


def call_tool(conn: sqlite3.Connection, name: str, args: dict) -> tuple[dict | list | None, str]:
    if name not in DISPATCH:
        return None, f"Unknown tool {name}."
    return DISPATCH[name](conn, args)
