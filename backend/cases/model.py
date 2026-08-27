"""RecoveryCase — the case object, and the policy that shapes it.

Layer 4. Everything here is deterministic: routing, deadlines and priority are
lookup tables and arithmetic. No LLM touches any of it, because every one of these
values ends up steering money.

Split of responsibility, deliberately narrow:
  - the bus sets break_type, resolver, deadline, priority, rupees and basis
  - `guardrails/engine.py` (layer 6) sets tier
  - `agents/` (layer 5) sets actions
A case that has not been through guardrails carries `tier = None`, never a guessed
default — a fabricated tier reads as "safe to auto-execute" to everything downstream.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from ..ledgers.states import BreakType

# --- normative, from CONTRACTS.md. Both ends must sort identically. ---
DEADLINE_HORIZON_SECONDS = 604800          # 7 days
URGENCY_FLOOR = 0.05

_DAY = 86400


class CaseStatus:
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


class Resolver:
    RECONCILIATION = "RECONCILIATION"
    SEQUENCER = "SEQUENCER"
    CART = "CART"


# ---------------------------------------------------------------------------
# Routing — which resolver owns which break
# ---------------------------------------------------------------------------

RESOLVER_BY_BREAK: dict[str, str] = {
    # Ledger disagreements needing investigation: an unbounded search space, so an agent.
    BreakType.ORPHAN_PAYMENT_NO_ORDER.value:       Resolver.RECONCILIATION,
    BreakType.PAYMENT_PENDING_WEBHOOK_MISSING.value: Resolver.RECONCILIATION,
    BreakType.DUPLICATE_PAYMENT.value:             Resolver.RECONCILIATION,
    BreakType.REFUND_WITHOUT_CANCELLATION.value:   Resolver.RECONCILIATION,
    BreakType.REFUND_AFTER_SHIPMENT.value:         Resolver.RECONCILIATION,
    BreakType.PAYMENT_ON_CANCELLED_ORDER.value:    Resolver.RECONCILIATION,
    BreakType.AUTHORIZED_NOT_CAPTURED.value:       Resolver.RECONCILIATION,
    BreakType.REVENUE_NOT_BOOKED.value:            Resolver.RECONCILIATION,
    BreakType.FULFILMENT_STALLED.value:            Resolver.RECONCILIATION,
    BreakType.SETTLEMENT_SHORT_PAID.value:         Resolver.RECONCILIATION,
    BreakType.STATUTORY_CREDIT_UNCLAIMED.value:    Resolver.RECONCILIATION,
    BreakType.UNUSUAL_DISCOUNT.value:              Resolver.RECONCILIATION,
    BreakType.UNUSUAL_REFUND_PATTERN.value:        Resolver.RECONCILIATION,
    BreakType.ANOMALOUS_TRANSACTION_PATTERN.value: Resolver.RECONCILIATION,
    BreakType.UNCLASSIFIED_BREAK.value:            Resolver.RECONCILIATION,
    # Scarce-resource scheduling over an enumerable slot space: a solver, not a prompt.
    BreakType.MANDATE_DEBIT_FAILED.value:          Resolver.SEQUENCER,
    BreakType.MANDATE_UNRETRYABLE.value:           Resolver.SEQUENCER,
    BreakType.SUBSCRIPTION_CHURN_RISK.value:       Resolver.SEQUENCER,
    # A live human replying unpredictably: needs conversation.
    BreakType.CHECKOUT_ABANDONED.value:            Resolver.CART,
}


# ---------------------------------------------------------------------------
# Deadlines — from the domain, not a generic SLA
# ---------------------------------------------------------------------------
# (seconds_from_detection, reason). `None` seconds means a computed statutory date.

DEADLINE_RULES: dict[str, tuple[int | None, str]] = {
    # In India an unconfirmed UPI payment must be auto-reversed by the acquirer within
    # T+5. That window is the real clock on anything holding un-reconciled money.
    BreakType.PAYMENT_PENDING_WEBHOOK_MISSING.value:
        (5 * _DAY, "T+5 acquirer credit adjustment window closes"),
    BreakType.ORPHAN_PAYMENT_NO_ORDER.value:
        (5 * _DAY, "T+5 acquirer credit adjustment window closes"),
    BreakType.DUPLICATE_PAYMENT.value:
        (5 * _DAY, "T+5 window — refund the duplicate before the auto-reversal fires"),
    BreakType.PAYMENT_ON_CANCELLED_ORDER.value:
        (5 * _DAY, "T+5 acquirer credit adjustment window closes"),
    BreakType.AUTHORIZED_NOT_CAPTURED.value:
        (5 * _DAY, "Authorisation expires and the hold on the customer's funds is released"),
    # Goods already moving. The recall window is hours, not days.
    BreakType.REFUND_AFTER_SHIPMENT.value:
        (1 * _DAY, "Goods in transit — courier recall window closes"),
    BreakType.FULFILMENT_STALLED.value:
        (2 * _DAY, "Fulfilment SLA breach"),
    BreakType.CHECKOUT_ABANDONED.value:
        (3 * _DAY, "Cart recovery window — purchase intent decays sharply after this"),
    BreakType.REVENUE_NOT_BOOKED.value:
        (30 * _DAY, "Month-end close"),
    BreakType.UNUSUAL_DISCOUNT.value:
        (30 * _DAY, "Month-end close"),
    BreakType.UNUSUAL_REFUND_PATTERN.value:
        (30 * _DAY, "Month-end review"),
    BreakType.ANOMALOUS_TRANSACTION_PATTERN.value:
        (30 * _DAY, "Month-end review"),
    BreakType.SETTLEMENT_SHORT_PAID.value:
        (60 * _DAY, "Acquirer settlement dispute window"),
    BreakType.UNCLASSIFIED_BREAK.value:
        (7 * _DAY, "Default investigation SLA — no named rule covers this combination"),
    # Statutory: Indian GST bars a credit note after 30 November of the following FY.
    # Past that the tax is permanently unrecoverable, so the deadline is a real date.
    BreakType.REFUND_WITHOUT_CANCELLATION.value:
        (None, "GST credit note barred after 30 November of the following financial year"),
    BreakType.STATUTORY_CREDIT_UNCLAIMED.value:
        (None, "GST credit note barred after 30 November of the following financial year"),
    # NPCI allows 4 attempts per cycle. When the cycle ends, unused attempts die with it.
    BreakType.MANDATE_DEBIT_FAILED.value:
        (None, "Billing cycle ends — the 4-attempt NPCI limit expires with it"),
    BreakType.MANDATE_UNRETRYABLE.value:
        (None, "Next debit date — the mandate must be re-authorised before it"),
    BreakType.SUBSCRIPTION_CHURN_RISK.value:
        (None, "Next renewal date"),
}


def gst_credit_note_bar(dt: datetime) -> datetime:
    """30 November of the financial year following the one containing `dt`.

    The Indian FY runs 1 April to 31 March. A refund in FY2026-27 must have its
    credit note raised by 30 Nov 2027; after that the GST is unrecoverable.
    """
    fy_start_year = dt.year if dt.month >= 4 else dt.year - 1
    return datetime(fy_start_year + 1, 11, 30, 23, 59, 59, tzinfo=timezone.utc)


def compute_deadline(break_type: str, now: datetime,
                     cycle_end: datetime | None = None) -> tuple[datetime | None, str]:
    seconds, reason = DEADLINE_RULES.get(
        break_type, (7 * _DAY, "Default investigation SLA"))
    if seconds is not None:
        return now + timedelta(seconds=seconds), reason
    if "GST" in reason:
        return gst_credit_note_bar(now), reason
    # Cycle- or renewal-bound. Fall back to a month if the date is unknown.
    return (cycle_end or now + timedelta(days=30)), reason


def compute_priority_score(rupees_at_risk_inr: float, confidence: float,
                           deadline_at: datetime | None, now: datetime) -> float:
    """Normative — see CONTRACTS.md. Backend and frontend must agree exactly."""
    if deadline_at is None:
        urgency = URGENCY_FLOOR
    else:
        seconds_left = (deadline_at - now).total_seconds()
        if seconds_left <= 0:
            urgency = 1.0
        else:
            urgency = 1.0 - (seconds_left / DEADLINE_HORIZON_SECONDS)
            urgency = max(URGENCY_FLOOR, min(1.0, urgency))
    return round(rupees_at_risk_inr * urgency * confidence, 2)


# ---------------------------------------------------------------------------
# Human-readable case text. Deterministic templates, no LLM.
# ---------------------------------------------------------------------------

TITLES: dict[str, str] = {
    BreakType.ORPHAN_PAYMENT_NO_ORDER.value:       "Money captured, no order created",
    BreakType.PAYMENT_PENDING_WEBHOOK_MISSING.value: "Customer debited, confirmation never arrived",
    BreakType.DUPLICATE_PAYMENT.value:             "Customer charged twice for one checkout",
    BreakType.REFUND_WITHOUT_CANCELLATION.value:   "Refunded, but the order is still active",
    BreakType.REFUND_AFTER_SHIPMENT.value:         "Refunded after the goods shipped",
    BreakType.PAYMENT_ON_CANCELLED_ORDER.value:    "Payment captured against a cancelled order",
    BreakType.AUTHORIZED_NOT_CAPTURED.value:       "Authorised but never captured",
    BreakType.REVENUE_NOT_BOOKED.value:            "Captured revenue never booked",
    BreakType.FULFILMENT_STALLED.value:            "Paid and reserved, never shipped",
    BreakType.MANDATE_DEBIT_FAILED.value:          "Autopay debit failed, subscription in grace",
    BreakType.MANDATE_UNRETRYABLE.value:           "Dead mandate against a live subscription",
    BreakType.SUBSCRIPTION_CHURN_RISK.value:       "Subscription predicted to lapse",
    BreakType.CHECKOUT_ABANDONED.value:            "Cart abandoned before payment",
    BreakType.SETTLEMENT_SHORT_PAID.value:         "Settlement below the agreed fee schedule",
    BreakType.STATUTORY_CREDIT_UNCLAIMED.value:    "GST credit never reclaimed",
    BreakType.UNUSUAL_DISCOUNT.value:              "Discount outside the normal range",
    BreakType.UNUSUAL_REFUND_PATTERN.value:        "Refund rate far above the cohort",
    BreakType.ANOMALOUS_TRANSACTION_PATTERN.value: "Transaction pattern flagged as anomalous",
    BreakType.UNCLASSIFIED_BREAK.value:            "Ledger combination outside the legal set",
}


def build_summary(break_type: str, snapshot: dict, rupees: float,
                  evidence: dict) -> str:
    """One plain sentence a finance person can act on. Facts only."""
    money = f"Rs {rupees:,.2f}"
    age_h = (evidence.get("observed_age_seconds") or snapshot.get("age_seconds") or 0) / 3600

    if break_type == BreakType.PAYMENT_PENDING_WEBHOOK_MISSING.value:
        return (f"{money} pending for {age_h:.0f} hours with no order created. "
                f"Payment confirmation never reached us.")
    if break_type == BreakType.ORPHAN_PAYMENT_NO_ORDER.value:
        return f"{money} captured {age_h:.0f} hours ago against no order record."
    if break_type == BreakType.DUPLICATE_PAYMENT.value:
        return f"{money} captured a second time on a checkout already paid and fulfilled."
    if break_type == BreakType.MANDATE_UNRETRYABLE.value:
        return (f"{money} per cycle uncollectable: {evidence.get('reason', 'mandate is dead')}, "
                f"but the subscription is still active.")
    if break_type == BreakType.MANDATE_DEBIT_FAILED.value:
        return f"{money} autopay debit failed and the grace period has run out."
    if break_type == BreakType.SUBSCRIPTION_CHURN_RISK.value:
        p = evidence.get("predicted_churn_probability")
        return f"{money} of annualised subscription value at {p:.0%} predicted churn risk."
    if break_type == BreakType.SETTLEMENT_SHORT_PAID.value:
        return (f"Settled {money} below the fee schedule for this payment "
                f"(expected Rs {evidence.get('expected_net_inr', 0):,.2f}).")
    if break_type == BreakType.STATUTORY_CREDIT_UNCLAIMED.value:
        barred = evidence.get("credit_note_barred")
        return (f"{money} of GST on a reversed sale was never reclaimed"
                + (" and the credit note is now time-barred." if barred else "."))
    if break_type == BreakType.CHECKOUT_ABANDONED.value:
        cart = evidence.get("cart_value_inr")
        rate = evidence.get("recovery_rate")
        if cart and rate:
            return (f"Rs {cart:,.2f} cart abandoned {age_h:.0f} hours ago with no payment "
                    f"attempt. {money} is the recoverable estimate at a {rate:.0%} rate.")
        return f"{money} cart abandoned {age_h:.0f} hours ago with no payment attempt."
    if break_type == BreakType.UNCLASSIFIED_BREAK.value:
        return (f"{money} in a ledger state no rule covers: "
                f"payment {snapshot.get('payment')}, order {snapshot.get('order')}, "
                f"inventory {snapshot.get('inventory')}, accounting {snapshot.get('accounting')}.")
    return (f"{money} at risk. Ledgers disagree: payment {snapshot.get('payment')}, "
            f"order {snapshot.get('order')}, accounting {snapshot.get('accounting')}.")


# ---------------------------------------------------------------------------

@dataclass
class RecoveryCase:
    """Matches CONTRACTS.md §2 exactly. Do not add a field without updating it."""

    case_id: str
    break_type: str
    status: str
    business_type: str
    title: str
    summary: str

    rupees_at_risk_inr: float
    basis: str
    confidence: float

    created_at: str
    updated_at: str

    signal_id: str | None = None
    customer_id: str | None = None
    session_id: str | None = None
    payment_id: str | None = None
    order_id: str | None = None
    mandate_id: str | None = None

    deadline_at: str | None = None
    deadline_reason: str | None = None
    priority_score: float = 0.0

    resolver: str | None = None
    tier: int | None = None
    tier_label: str | None = None

    signal_count: int = 1
    is_aggregate: bool = False

    ledger_snapshot: dict | None = None
    guardrail_checks: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    trace_available: bool = False

    def to_row(self) -> tuple:
        """Column order must match the `cases` table in schema.sql."""
        payload = json.dumps({
            "ledger_snapshot": self.ledger_snapshot,
            "guardrail_checks": self.guardrail_checks,
            "actions": self.actions,
            "evidence": self.evidence,
            "trace_available": self.trace_available,
        })
        return (
            self.case_id, self.signal_id, self.break_type, self.status,
            self.business_type, self.title, self.summary,
            self.customer_id, self.session_id, self.payment_id, self.order_id,
            self.mandate_id, self.rupees_at_risk_inr, self.basis, self.confidence,
            self.deadline_at, self.deadline_reason, self.priority_score,
            self.resolver, self.tier, self.tier_label,
            self.signal_count, int(self.is_aggregate), payload,
            self.created_at, self.updated_at,
        )

    def to_dict(self) -> dict:
        return asdict(self)
