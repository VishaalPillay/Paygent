"""Razorpay webhook receiver — layer 1, the live half of data ingestion.

The synthetic generator produces history; this produces the transaction the audience
watches happen. A real test-mode payment made on stage lands here, is written into the
four ledgers, and is picked up by the consistency matrix on the next scan.

**The dropped-webhook demo.** With `DEMO_DROP_WEBHOOK=1` the endpoint verifies and
acknowledges the event, then deliberately does not apply it. The payment row stays
`PENDING` with no order — which after its dwell limit is exactly
`PAYMENT_PENDING_WEBHOOK_MISSING`, Scenario 2. Nothing is faked: the broken state is
produced by the same code path that would have fixed it.

Mounted by backend/main.py (Vishaal). This module owns the route, not the app.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from .. import config
from ..db import conn as db
from ..ledgers.states import (
    AccountingState as A,
    BusinessType,
    FailureReasonCode as FR,
    InventoryState as I,
    OrderState as O,
    PaymentState as P,
)

router = APIRouter()

# Razorpay reports amounts in paise. We store rupee floats everywhere else.
PAISE = 100.0

_EVENT_TO_PAYMENT_STATE = {
    "payment.authorized": P.AUTHORIZED,
    "payment.captured": P.CAPTURED,
    "payment.failed": P.FAILED,
    "refund.created": P.REFUNDED,
    "refund.processed": P.REFUNDED,
}

_FAILURE_REASON = {
    "BAD_REQUEST_ERROR": FR.ISSUER_DECLINED,
    "GATEWAY_ERROR": FR.TECHNICAL_DECLINE,
    "SERVER_ERROR": FR.BANK_TIMEOUT,
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def verify_signature(body: bytes, signature: str | None) -> bool:
    """Razorpay signs the raw body with HMAC-SHA256 over the webhook secret."""
    if not config.VERIFY_WEBHOOK_SIGNATURE:
        return True
    if not signature:
        return False
    expected = hmac.new(
        config.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _ensure_customer(cur, entity: dict) -> str:
    """Razorpay notes carry our own ids. Fall back to a walk-in customer record."""
    notes = entity.get("notes") or {}
    customer_id = notes.get("customer_id")
    if customer_id and cur.execute(
        "SELECT 1 FROM customers WHERE customer_id=?", (customer_id,)
    ).fetchone():
        return customer_id

    customer_id = f"cus_live_{entity.get('id', 'unknown')[-8:]}"
    cur.execute(
        "INSERT OR IGNORE INTO customers VALUES (?,?,?,?,?,?,?,?,?)",
        (customer_id, notes.get("name", "Live Demo Customer"),
         entity.get("email") or "demo@example.in",
         entity.get("contact") or "+910000000000",
         BusinessType.ECOMMERCE.value, "NEW",
         (entity.get("bank") or "HDFC"), "Mumbai", _now()))
    return customer_id


def apply_event(conn, event: str, entity: dict) -> dict:
    """Write the event into the four ledgers. Idempotent on payment_id."""
    cur = conn.cursor()
    payment_state = _EVENT_TO_PAYMENT_STATE.get(event)
    if payment_state is None:
        return {"applied": False, "reason": f"unhandled event {event}"}

    payment_id = entity.get("payment_id") or entity.get("id")
    amount_inr = round(float(entity.get("amount", 0)) / PAISE, 2)
    customer_id = _ensure_customer(cur, entity)
    notes = entity.get("notes") or {}
    business_type = BusinessType(
        notes.get("business_type", BusinessType.ECOMMERCE.value))
    reason = _FAILURE_REASON.get(entity.get("error_code")) if payment_state == P.FAILED else None

    existing = cur.execute(
        "SELECT payment_id FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
    if existing:
        cur.execute(
            "UPDATE payments SET state=?, updated_at=?, webhook_received=1,"
            " failure_reason_code=COALESCE(?, failure_reason_code) WHERE payment_id=?",
            (payment_state.value, _now(), reason.value if reason else None, payment_id))
    else:
        cur.execute(
            "INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (payment_id, notes.get("session_id"), customer_id, notes.get("mandate_id"),
             payment_state.value, amount_inr, (entity.get("method") or "UPI").upper(),
             reason.value if reason else None, entity.get("acquirer_data", {}).get("rrn"),
             1, _now(), _now()))

    # A captured payment should create an order. If this write never happens - because
    # the webhook was dropped - the ledgers disagree, which is the whole point.
    if payment_state == P.CAPTURED and not cur.execute(
        "SELECT 1 FROM orders WHERE payment_id=?", (payment_id,)
    ).fetchone():
        order_id = f"ord_live_{payment_id[-8:]}"
        cur.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
            (order_id, payment_id, notes.get("session_id"), customer_id,
             business_type.value, O.CONFIRMED.value, amount_inr, 0.0, _now(), _now()))
        cur.execute(
            "INSERT INTO inventory_events VALUES (?,?,?,?,?,?)",
            (f"inv_live_{payment_id[-8:]}", order_id, notes.get("sku"), 1,
             (I.NOT_APPLICABLE if business_type == BusinessType.SAAS
              else I.RESERVED).value, _now()))

    conn.commit()
    return {"applied": True, "payment_id": payment_id,
            "state": payment_state.value, "amount_inr": amount_inr}


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
):
    """Acknowledge fast. Detection runs on the next scan, not in this handler."""
    body = await request.body()
    if not verify_signature(body, x_razorpay_signature):
        raise HTTPException(status_code=401, detail={
            "error": {"code": "INVALID_SIGNATURE",
                      "message": "Webhook signature did not verify."}})

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail={
            "error": {"code": "INVALID_PARAMETER", "message": "Body is not JSON."}})

    event = payload.get("event", "")
    entity = (payload.get("payload", {}).get("payment", {}).get("entity")
              or payload.get("payload", {}).get("refund", {}).get("entity")
              or {})

    # The demo switch. Verified, acknowledged, and deliberately not applied.
    if config.DEMO_DROP_WEBHOOK:
        return {"ok": True, "dropped": True, "event": event,
                "note": "DEMO_DROP_WEBHOOK is on — event acknowledged, not applied"}

    conn = db.connect()
    try:
        result = apply_event(conn, event, entity)
    finally:
        conn.close()
    return {"ok": True, "dropped": False, "event": event, **result}
