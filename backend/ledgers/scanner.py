"""Consistency Matrix scanner — layer 3.

`matrix.py` holds the classification logic and knows nothing about storage.
This module joins the four ledgers, reconstructs a snapshot per transaction, runs
`classify_break`, and writes `Signal` rows for everything that disagrees.

**A snapshot is derived, never stored.** An orphan payment is a payment row with no
matching order row — so the absence of a row IS the MISSING state. That is what makes
this a real reconciliation engine rather than a status column being read back.

Signals land in the `signals` table, which is the layer 3 -> layer 4 seam defined in
CONTRACTS.md. The Recovery Case Bus consumes them.

Run:  python -m backend.ledgers.scanner
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..db import conn as db
from . import matrix as M
from .states import (
    AccountingState as A,
    BreakType,
    BusinessType,
    InventoryState as I,
    OrderState as O,
    PaymentState as P,
)

# Abandoned carts below this value, or older than this, roll into the waterfall
# aggregate rather than becoming individual cases. A finance team abandons a tool
# that hands them 18,000 findings; the cart agent can only work the recoverable tail.
CART_CASE_MIN_INR = 2000.0
CART_CASE_MAX_AGE_DAYS = 14

# An abandoned cart is not money we are holding — the customer never paid. The full
# cart value is a real leak and belongs in waterfall gate B1, but what a case can
# actually RECOVER is a fraction of it. Indian cart-recovery campaigns over
# WhatsApp/email land around 12%.
#
# So the signal carries the modelled recoverable estimate, not the cart value, and
# its basis is `modelled`. Booking the full cart as `deterministic` would put the
# single largest number in the system on the wrong side of the split the entire
# product is built on.
CART_RECOVERY_RATE = 0.12
CART_RECOVERY_CONFIDENCE = 0.5

# Each ledger contributes its LATEST row, never all of them. `inventory_events` and
# `accounting_entries` are event logs — one order can carry reserve -> ship -> return,
# and joining every row multiplies the payment and emits a duplicate signal per event.
# Same for orders: a duplicated order is a break to report, not a reason to fan out.
_JOIN_SQL = """
SELECT
    p.payment_id, p.customer_id, p.session_id, p.mandate_id,
    p.state         AS payment_state,
    p.amount_inr, p.method, p.failure_reason_code, p.webhook_received,
    p.updated_at    AS payment_updated_at,
    o.order_id,
    o.state         AS order_state,
    i.state         AS inventory_state,
    a.state         AS accounting_state,
    c.business_type AS customer_business_type
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
"""

# The 2nd and later captured payment on one checkout. Detected up front because it
# outranks the per-payment classification: such a payment also looks like an orphan,
# but calling it an orphan sends the resolver down the create-the-missing-order path
# when the correct action is a refund. Reporting the wrong break here is worse than
# reporting none.
_DUPLICATE_SQL = """
SELECT payment_id FROM (
    SELECT payment_id,
           ROW_NUMBER() OVER (PARTITION BY session_id
                              ORDER BY created_at, payment_id) AS rn
    FROM payments
    WHERE state = 'CAPTURED' AND session_id IS NOT NULL
) WHERE rn > 1
"""


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_from_row(row) -> tuple:
    """Reconstruct the four-ledger snapshot. Absence of a row is a state, not a gap."""
    business_type = BusinessType(row["customer_business_type"])
    payment = P(row["payment_state"])
    order = O(row["order_state"]) if row["order_state"] else O.MISSING
    if row["inventory_state"]:
        inventory = I(row["inventory_state"])
    else:
        inventory = (I.NOT_APPLICABLE if business_type == BusinessType.SAAS
                     else I.AVAILABLE)
    accounting = A(row["accounting_state"]) if row["accounting_state"] else A.NOT_BOOKED
    return payment, order, inventory, accounting, business_type


def scan(conn, now: datetime | None = None) -> dict[str, int]:
    """Scan every transaction, emit a signal per break. Returns a break-type tally."""
    now = now or db.reference_now(conn)
    cur = conn.cursor()
    tally: dict[str, int] = {}
    n = 0
    duplicates = {r["payment_id"] for r in conn.execute(_DUPLICATE_SQL)}

    for row in conn.execute(_JOIN_SQL):
        payment, order, inventory, accounting, business_type = snapshot_from_row(row)
        age = (now - _parse(row["payment_updated_at"])).total_seconds()

        if row["payment_id"] in duplicates:
            break_type = BreakType.DUPLICATE_PAYMENT
        else:
            break_type = M.classify_break(
                payment, order, inventory, accounting, age, business_type)
        if break_type is None:
            continue

        snapshot = (payment, order, inventory, accounting)
        evidence = {
            "dwell_limit_seconds": M.DWELL_LIMITS_SECONDS.get(snapshot),
            "observed_age_seconds": int(age),
            "webhook_received": bool(row["webhook_received"]),
            "method": row["method"],
            "failure_reason_code": row["failure_reason_code"],
            "state_is_legal": M.is_legal(snapshot, business_type),
        }

        n += 1
        cur.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"sig_cm_{n:06d}", "consistency_matrix", break_type.value,
             business_type.value, row["customer_id"], row["session_id"],
             row["payment_id"], row["order_id"], row["mandate_id"],
             # The ledgers either disagree or they do not — no estimation involved.
             round(float(row["amount_inr"]), 2), "deterministic", 1.0,
             payment.value, order.value, inventory.value, accounting.value,
             int(age), json.dumps(evidence), _iso(now)))
        tally[break_type.value] = tally.get(break_type.value, 0) + 1

    # --- abandoned carts: no payment row exists, so they need their own pass ---
    for row in conn.execute(
        """SELECT s.*, c.business_type AS customer_business_type
           FROM checkout_sessions s
           JOIN customers c ON c.customer_id = s.customer_id
           WHERE s.attempted = 0 AND s.cart_value_inr >= ?""",
        (CART_CASE_MIN_INR,),
    ):
        age = (now - _parse(row["created_at"])).total_seconds()
        if age > CART_CASE_MAX_AGE_DAYS * 86400:
            continue    # stale — counts in waterfall gate B1, not as a case
        n += 1
        cur.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"sig_cm_{n:06d}", "consistency_matrix",
             BreakType.CHECKOUT_ABANDONED.value, row["customer_business_type"],
             row["customer_id"], row["session_id"], None, None, None,
             round(float(row["cart_value_inr"]) * CART_RECOVERY_RATE, 2),
             "modelled", CART_RECOVERY_CONFIDENCE,
             P.INITIATED.value, O.MISSING.value,
             (I.NOT_APPLICABLE if row["customer_business_type"] == BusinessType.SAAS.value
              else I.AVAILABLE).value, A.NOT_BOOKED.value, int(age),
             json.dumps({"item_count": row["item_count"], "device": row["device"],
                         "observed_age_seconds": int(age),
                         "cart_value_inr": round(float(row["cart_value_inr"]), 2),
                         "recovery_rate": CART_RECOVERY_RATE,
                         "note": "cart value is the leak; this is the recoverable estimate"}),
             _iso(now)))
        tally[BreakType.CHECKOUT_ABANDONED.value] = (
            tally.get(BreakType.CHECKOUT_ABANDONED.value, 0) + 1)

    # --- structurally dead mandates against live subscriptions ----------------
    # A revoked, expired or over-cap mandate cannot be debited by any retry. If the
    # subscription is still CONFIRMED, service is being delivered with no way left to
    # bill for it. This is the signal that lets router.py refuse to spend an attempt.
    for row in conn.execute(
        """SELECT m.mandate_id, m.customer_id, m.state, m.cap_inr,
                  m.debit_amount_inr, c.business_type,
                  o.order_id, o.amount_inr
           FROM mandates m
           JOIN customers c ON c.customer_id = m.customer_id
           JOIN orders o    ON o.order_id = (
                SELECT order_id FROM orders x
                WHERE x.customer_id = m.customer_id AND x.state = 'CONFIRMED'
                ORDER BY x.updated_at DESC LIMIT 1)
           WHERE m.state IN ('REVOKED', 'EXPIRED')
              OR m.debit_amount_inr > m.cap_inr"""
    ):
        if row["state"] == "REVOKED":
            reason = "mandate revoked by the customer"
        elif row["state"] == "EXPIRED":
            reason = "mandate expired"
        else:
            reason = "debit amount exceeds the mandate cap"
        n += 1
        cur.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"sig_cm_{n:06d}", "consistency_matrix",
             BreakType.MANDATE_UNRETRYABLE.value, row["business_type"],
             row["customer_id"], None, None, row["order_id"], row["mandate_id"],
             # One billing cycle we definitively cannot collect. Not an estimate.
             round(float(row["debit_amount_inr"]), 2), "deterministic", 1.0,
             None, None, None, None, 0,
             json.dumps({"mandate_state": row["state"], "reason": reason,
                         "cap_inr": row["cap_inr"],
                         "debit_amount_inr": row["debit_amount_inr"],
                         "retryable": False,
                         "attempts_would_be_wasted": 4}), _iso(now)))
        tally[BreakType.MANDATE_UNRETRYABLE.value] = (
            tally.get(BreakType.MANDATE_UNRETRYABLE.value, 0) + 1)

    conn.commit()
    return tally


def main() -> None:
    conn = db.connect()
    db.clear_signals(conn, "consistency_matrix")

    total_txn = conn.execute("SELECT COUNT(*) AS n FROM payments").fetchone()["n"]
    tally = scan(conn, now=db.reference_now(conn))
    total_breaks = sum(tally.values())

    print(f"scanned {total_txn:,} transactions\n")
    print(f"  {'break type':<36} {'signals':>8}")
    print(f"  {'-' * 36} {'-' * 8}")
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {k:<36} {v:>8,}")
    print(f"  {'-' * 36} {'-' * 8}")
    print(f"  {'TOTAL':<36} {total_breaks:>8,}")

    at_risk = conn.execute(
        "SELECT basis, SUM(rupees_at_risk_inr) AS s FROM signals GROUP BY basis"
    ).fetchall()
    print("\n  rupees at risk (never blended across basis)")
    for r in at_risk:
        print(f"    {r['basis']:<16} Rs {r['s']:>16,.2f}")
    conn.close()


if __name__ == "__main__":
    main()
