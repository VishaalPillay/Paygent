"""Live demo scenario endpoints — the storefront's reconciliation buttons and the
cart-abandonment beacon.

This writes real rows into the four ledgers and classifies them through the exact
same code path the batch scanner uses (`classify_break` over the same `_JOIN_SQL`,
the same duplicate-payment precedence check, the same `_build_individual` case
assembly) rather than a parallel, hand-rolled version of any of it. If the matrix's
rules ever change, this endpoint reflects that automatically instead of drifting.

Every timestamp is anchored to `db.reference_now`, not the wall clock — the same
anchor every other detector in the system uses, so a payment "created now" doesn't
compute a negative or wildly large age against the seed's fixed reference instant.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

from ..cases import model as m
from ..cases.bus import _INSERT as CASE_INSERT
from ..cases.bus import _build_individual
from ..db import conn as db
from ..ledgers import matrix as M
from ..ledgers.scanner import CART_RECOVERY_CONFIDENCE, CART_RECOVERY_RATE, _DUPLICATE_SQL
from ..ledgers.scanner import _JOIN_SQL as SCANNER_JOIN_SQL
from ..ledgers.scanner import snapshot_from_row
from ..ledgers.states import BreakType, BusinessType
from ..webhooks.razorpay import _ensure_customer

router = APIRouter()

_ONE_JOIN_SQL = SCANNER_JOIN_SQL + " WHERE p.payment_id = ?"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _next_case_seq(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(case_id, 6) AS INTEGER)) AS mx FROM cases"
    ).fetchone()
    return (row["mx"] or 0)


def _ensure_demo_customer(cur, session_id: str) -> str:
    """`_ensure_customer` keys a walk-in customer off the last 8 characters of
    `entity['id']` — fine for a real Razorpay payment id, which has enough entropy
    there, but two session ids that happen to share a suffix (e.g. two test runs
    both ending in "_payment") would silently collapse into the same customer and
    let the agent find an order that belongs to a different scenario. Hash first so
    this can't happen regardless of what the caller names the session.
    """
    fingerprint = hashlib.sha256(session_id.encode()).hexdigest()
    return _ensure_customer(cur, {"id": fingerprint, "notes": {}})


def _classify_and_case(conn: sqlite3.Connection, payment_id: str, now: datetime) -> dict:
    """Run the payment just written through the exact scanner code path: the same
    duplicate-payment precedence check, the same classify_break call, the same
    case assembly. Returns the built case's summary fields plus a step-by-step
    audit trail for the shop's event log.
    """
    events: list[dict] = []
    cur = conn.cursor()

    duplicates = {r["payment_id"] for r in conn.execute(_DUPLICATE_SQL)}
    row = conn.execute(_ONE_JOIN_SQL, (payment_id,)).fetchone()
    payment, order, inventory, accounting, business_type = snapshot_from_row(row)
    age = (now - datetime.strptime(row["payment_updated_at"], "%Y-%m-%dT%H:%M:%SZ")
           .replace(tzinfo=timezone.utc)).total_seconds()

    events.append({"text": f"ledger snapshot: payment={payment.value} order={order.value} "
                            f"inventory={inventory.value} accounting={accounting.value}",
                    "tone": "neutral"})

    if payment_id in duplicates:
        break_type = BreakType.DUPLICATE_PAYMENT
        events.append({"text": "second CAPTURED payment on this checkout session — "
                                "classified as a duplicate before the per-payment rule "
                                "even runs (an orphan-order fix here would double-fulfil)",
                        "tone": "warn"})
    else:
        break_type = M.classify_break(payment, order, inventory, accounting, age, business_type)
        if break_type is None:
            events.append({"text": "four ledgers agree — no break", "tone": "good"})
            return {"events": events, "case": None}
        legal = M.is_legal((payment, order, inventory, accounting), business_type)
        events.append({
            "text": (f"not in the legal state set — classify_break -> {break_type.value}"
                     if not legal else f"legal state, past dwell -> {break_type.value}"),
            "tone": "bad",
        })

    evidence = {
        "dwell_limit_seconds": M.DWELL_LIMITS_SECONDS.get((payment, order, inventory, accounting)),
        "observed_age_seconds": int(age),
        "webhook_received": bool(row["webhook_received"]),
        "method": row["method"],
        "failure_reason_code": row["failure_reason_code"],
        "state_is_legal": M.is_legal((payment, order, inventory, accounting), business_type),
    }

    signal_id = _new_id("sig_demo")
    cur.execute(
        "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (signal_id, "consistency_matrix", break_type.value, business_type.value,
         row["customer_id"], row["session_id"], row["payment_id"], row["order_id"],
         row["mandate_id"], round(float(row["amount_inr"]), 2), "deterministic", 1.0,
         payment.value, order.value, inventory.value, accounting.value,
         int(age), json.dumps(evidence), _iso(now)),
    )
    conn.commit()

    sig = conn.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,)).fetchone()
    seq = _next_case_seq(conn) + 1
    case = _build_individual(sig, seq, now, {})
    cur.execute(CASE_INSERT, case.to_row())
    cur.execute("INSERT INTO case_signals VALUES (?,?)", (case.case_id, sig["signal_id"]))
    conn.commit()

    events.append({
        "text": f"case {case.case_id} opened · Rs {case.rupees_at_risk_inr:,.2f} · "
                f"routed to {case.resolver.lower()}",
        "tone": "bad",
    })

    return {
        "events": events,
        "case": {
            "case_id": case.case_id, "break_type": case.break_type,
            "rupees_at_risk_inr": case.rupees_at_risk_inr, "basis": case.basis,
            "resolver": case.resolver,
            "ledger_snapshot": {"payment": payment.value, "order": order.value,
                                 "inventory": inventory.value, "accounting": accounting.value},
        },
    }


# ---------------------------------------------------------------------------
# POST /api/demo/scenario
# ---------------------------------------------------------------------------

SCENARIOS = {"orphan_payment", "failed_but_confirmed", "duplicate_payment"}


@router.post("/demo/scenario")
def run_scenario(body: dict) -> dict:
    scenario = body.get("scenario")
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail={"error": {
            "code": "INVALID_PARAMETER",
            "message": f"scenario must be one of {sorted(SCENARIOS)}."}})

    session_id = body.get("session_id") or _new_id("ses_live")
    amount_inr = round(float(body.get("cart_value_inr") or 4299.0), 2)

    conn = db.connect()
    try:
        now = db.reference_now(conn)
        cur = conn.cursor()
        customer_id = _ensure_demo_customer(cur, session_id)

        if not conn.execute(
            "SELECT 1 FROM checkout_sessions WHERE session_id = ?", (session_id,)
        ).fetchone():
            cur.execute(
                "INSERT INTO checkout_sessions VALUES (?,?,?,?,?,?,?,?)",
                (session_id, customer_id, BusinessType.ECOMMERCE.value, amount_inr,
                 int(body.get("item_count") or 1), "web", 1, _iso(now)))

        events = [{"text": f"order created · Rs {amount_inr:,.2f} · session {session_id}",
                   "tone": "neutral"}]
        payment_id = _new_id("pay_live")

        if scenario == "orphan_payment":
            # Payment captured; the webhook that would have created the order is
            # dropped. No order row at all — absence is the MISSING state.
            events.append({"text": "UPI collect request sent to customer", "tone": "neutral"})
            cur.execute(
                "INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (payment_id, session_id, customer_id, None, "CAPTURED", amount_inr,
                 "UPI", None, _new_id("utr"), 0, _iso(now), _iso(now)))
            events.append({"text": f"payment captured at gateway · {payment_id}", "tone": "good"})
            events.append({"text": "webhook dropped — order was never created", "tone": "warn"})

        elif scenario == "failed_but_confirmed":
            # The reverse: the gateway declined the payment, but the order system
            # already confirmed the order and reserved stock — two systems that
            # disagree about whether this sale happened at all.
            cur.execute(
                "INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (payment_id, session_id, customer_id, None, "FAILED", amount_inr,
                 "UPI", "ISSUER_DECLINED", None, 1, _iso(now), _iso(now)))
            events.append({"text": f"payment declined at gateway · {payment_id}", "tone": "bad"})
            order_id = _new_id("ord_live")
            cur.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                (order_id, payment_id, session_id, customer_id, BusinessType.ECOMMERCE.value,
                 "CONFIRMED", amount_inr, 0.0, _iso(now), _iso(now)))
            cur.execute(
                "INSERT INTO inventory_events VALUES (?,?,?,?,?,?)",
                (_new_id("inv_live"), order_id, "SKU-0417", 1, "RESERVED", _iso(now)))
            cur.execute(
                "INSERT INTO accounting_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_new_id("acc_live"), order_id, "DEFERRED", amount_inr, 0.18,
                 round(amount_inr * 0.18, 2), 0.0, None, None, 0, _iso(now)))
            events.append({"text": "order confirmed and stock reserved anyway", "tone": "warn"})

        else:  # duplicate_payment
            # A healthy first purchase, fully fulfilled — then a second capture on
            # the same checkout session. The scanner's precedence check must catch
            # this before the per-payment rule calls it an orphan.
            first_payment_id = _new_id("pay_live")
            first_order_id = _new_id("ord_live")
            cur.execute(
                "INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (first_payment_id, session_id, customer_id, None, "CAPTURED", amount_inr,
                 "UPI", None, _new_id("utr"), 1, _iso(now - timedelta(seconds=5)),
                 _iso(now - timedelta(seconds=5))))
            cur.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                (first_order_id, first_payment_id, session_id, customer_id,
                 BusinessType.ECOMMERCE.value, "CONFIRMED", amount_inr, 0.0,
                 _iso(now - timedelta(seconds=5)), _iso(now - timedelta(seconds=5))))
            events.append({"text": f"payment captured · {first_payment_id}", "tone": "good"})
            events.append({"text": "order confirmed", "tone": "good"})

            cur.execute(
                "INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (payment_id, session_id, customer_id, None, "CAPTURED", amount_inr,
                 "UPI", None, _new_id("utr"), 1, _iso(now), _iso(now)))
            events.append({"text": f"gateway retried the same request · second capture "
                                    f"{payment_id} on the same checkout", "tone": "bad"})

        conn.commit()
        result = _classify_and_case(conn, payment_id, now)
        events.extend(result["events"])

        return {"session_id": session_id, "payment_id": payment_id,
                "events": events, "case": result["case"]}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cart abandonment
# ---------------------------------------------------------------------------

# How long a live-abandoned cart stays eligible to be "the latest" one. Without this,
# a cart from an earlier rehearsal/test never expires — a browser tab opened hours
# later, with nothing in its own cart, polls `/carts/abandoned/latest`, finds that old
# case still sitting there as the most recent row, and the agent opens a conversation
# nobody in this session triggered. Real demo pacing is seconds (see useCart.js's
# ABANDON_AFTER_MS); this window only needs to be wide enough to survive normal
# on-stage pauses, not to remember yesterday's test run.
_LATEST_CART_WINDOW_MINUTES = 10

# session_id -> sku of the first item in that cart. In-process, same reasoning as
# chat.py's `_conversations`: no `cart_items` table in the schema, and this only needs
# to survive one demo run, not a restart. Lets the cart agent apply the *correct*
# product's margin floor instead of one fixed number for every cart (see
# agents/cart.py::PRODUCT_MARGINS).
_LIVE_CART_SKUS: dict[str, str] = {}


@router.post("/carts/abandoned")
async def cart_abandoned(request: Request) -> dict:
    """`useCart.js` posts here via `sendBeacon` on tab close/hide, which arrives as
    a Blob with no guaranteed Content-Type header — parse the raw body rather than
    relying on FastAPI's JSON body binding.
    """
    try:
        body = json.loads(await request.body())
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail={
            "error": {"code": "INVALID_PARAMETER", "message": "Body is not JSON."}})

    session_id = body.get("session_id")
    cart_value_inr = round(float(body.get("cart_value_inr") or 0), 2)
    if not session_id or cart_value_inr <= 0:
        return {"ok": True, "case_id": None}

    conn = db.connect()
    try:
        # Real wall-clock time, not `reference_now` — this is the one place a
        # live timestamp actually needs to be distinct and increasing. Everything
        # else anchors to the frozen instant so dwell math never drifts; nothing
        # here depends on dwell, and `latest_abandoned_cart` needs a genuine
        # ordering across however many carts get abandoned live in one session.
        now = datetime.now(timezone.utc)
        cur = conn.cursor()
        customer_id = _ensure_demo_customer(cur, session_id)

        if conn.execute(
            "SELECT 1 FROM checkout_sessions WHERE session_id = ?", (session_id,)
        ).fetchone():
            return {"ok": True, "case_id": None}  # already reported, idempotent

        cur.execute(
            "INSERT INTO checkout_sessions VALUES (?,?,?,?,?,?,?,?)",
            (session_id, customer_id, BusinessType.ECOMMERCE.value, cart_value_inr,
             int(body.get("item_count") or 1), "web", 0, _iso(now)))

        items = body.get("items") or []
        if items and items[0].get("sku"):
            _LIVE_CART_SKUS[session_id] = items[0]["sku"]

        recoverable = round(cart_value_inr * CART_RECOVERY_RATE, 2)
        signal_id = _new_id("sig_demo")
        cur.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, "consistency_matrix", BreakType.CHECKOUT_ABANDONED.value,
             BusinessType.ECOMMERCE.value, customer_id, session_id, None, None, None,
             recoverable, "modelled", CART_RECOVERY_CONFIDENCE,
             "INITIATED", "MISSING", "AVAILABLE", "NOT_BOOKED", 0,
             json.dumps({"item_count": body.get("item_count"), "device": "web",
                         "observed_age_seconds": 0, "cart_value_inr": cart_value_inr,
                         "recovery_rate": CART_RECOVERY_RATE,
                         "note": "cart value is the leak; this is the recoverable estimate"}),
             _iso(now)))
        conn.commit()

        sig = conn.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,)).fetchone()
        seq = _next_case_seq(conn) + 1
        case = _build_individual(sig, seq, now, {})
        cur.execute(CASE_INSERT, case.to_row())
        cur.execute("INSERT INTO case_signals VALUES (?,?)", (case.case_id, sig["signal_id"]))
        conn.commit()

        return {"ok": True, "case_id": case.case_id}
    finally:
        conn.close()


@router.get("/carts/abandoned/latest")
def latest_abandoned_cart() -> dict | None:
    conn = db.connect()
    try:
        # `session_id LIKE 'ses_live_%'` is load-bearing, not cosmetic: every
        # detector — including the 2,655 CHECKOUT_ABANDONED cases seed.py already
        # wrote — shares the same `reference_now` timestamp, so without this
        # filter "latest" would nondeterministically return an arbitrary seeded
        # cart instead of one this session actually just abandoned, and the panel
        # would open a conversation nobody triggered.
        #
        # The `created_at >= ?` bound is the other half of that same guarantee: it
        # keeps a genuinely old live cart (an earlier test run) from being "the
        # latest" forever — see `_LATEST_CART_WINDOW_MINUTES` above.
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(minutes=_LATEST_CART_WINDOW_MINUTES))
        row = conn.execute(
            """SELECT c.case_id, c.session_id, c.created_at, s.cart_value_inr, s.item_count
               FROM cases c JOIN checkout_sessions s ON s.session_id = c.session_id
               WHERE c.break_type = ? AND c.is_aggregate = 0
                 AND c.session_id LIKE 'ses_live_%'
                 AND c.created_at >= ?
               ORDER BY c.created_at DESC LIMIT 1""",
            (BreakType.CHECKOUT_ABANDONED.value, cutoff),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["sku"] = _LIVE_CART_SKUS.get(result["session_id"])
        return result
    finally:
        conn.close()
