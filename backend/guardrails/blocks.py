"""The ten guardrails that decide whether an action touching money is allowed to fire.

Each function asserts the safe state, not the failure — no_duplicate_order_exists, not
check_duplicates. Every message is plain English with the reason inside it: it is read
aloud on stage, not logged.

Pure functions over plain dicts (CONTRACTS.md shapes). No I/O, no LLM calls, no network
calls. Must evaluate offline and instantly.
"""

from datetime import datetime

from backend.guardrails import GuardrailResult
from backend.ledgers.states import (
    AFA_THRESHOLD_INR,
    MAX_ATTEMPTS_PER_CYCLE,
    NON_TERMINAL_PAYMENT_STATES,
)

# Section 34(2) CGST Act: a credit note must be declared by 30 November following the
# END of the FY the original invoice fell in (or the annual return filing date, if
# earlier - we only track the November cutoff here). India's FY runs April 1 to March 31,
# so an invoice dated within FY2024-25 (year starts 2024) has cutoff 30 Nov 2025.
_GST_CREDIT_NOTE_CUTOFF_MONTH = 11
_GST_CREDIT_NOTE_CUTOFF_DAY = 30


def refund_requires_terminal_payment(payment: dict) -> GuardrailResult:
    """The demo centrepiece.

    A UPI merchant payment with no confirmation must receive a credit adjustment from the
    acquirer within T+5 days. Refunding from merchant balance while the payment is still
    non-terminal risks paying the customer twice when that auto-reversal fires.
    """
    state = payment.get("state")
    passed = state not in NON_TERMINAL_PAYMENT_STATES
    if passed:
        message = f"Payment is {state} - terminal. Safe to refund."
    else:
        message = (
            f"Payment is still {state}, not yet terminal. In India the acquirer must "
            "auto-reverse an unconfirmed UPI payment to the customer within T+5 days. "
            "Refunding from merchant balance now risks paying this customer twice."
        )
    return GuardrailResult(
        name="refund_requires_terminal_payment",
        passed=passed,
        blocking=True,
        message=message,
    )


def no_duplicate_order_exists(matching_orders: list[dict]) -> GuardrailResult:
    """Scenario 1: creating a second order for the same intent would double-fulfil."""
    passed = len(matching_orders) == 0
    if passed:
        message = (
            "No existing order matches this payment on receipt, contact, amount or "
            "timestamp window. Safe to create one."
        )
    else:
        message = (
            f"{len(matching_orders)} order(s) already match this payment (receipt, "
            "contact, amount or timestamp window). Creating another would double-fulfil "
            "the same intent - this is a duplicate-payment refund, not a missing order."
        )
    return GuardrailResult(
        name="no_duplicate_order_exists",
        passed=passed,
        blocking=True,
        message=message,
    )


def order_not_already_shipped(order: dict, inventory_state: str) -> GuardrailResult:
    """Never auto-cancel or auto-reclaim an order whose goods already shipped."""
    order_id = order.get("order_id")
    passed = inventory_state != "SHIPPED"
    if passed:
        message = f"Order {order_id} has not shipped. Safe to cancel or adjust."
    else:
        message = (
            f"Order {order_id} has already shipped. Auto-cancelling risks goods in "
            "transit that were already refunded; a human must check fulfilment stage "
            "before any reversal."
        )
    return GuardrailResult(
        name="order_not_already_shipped",
        passed=passed,
        blocking=True,
        message=message,
    )


def credit_note_within_gst_window(
    original_invoice_date: datetime, now: datetime
) -> GuardrailResult:
    """Indian GST bars a credit note after 30 Nov of the FY following the invoice's FY.

    If this fails, the GST is permanently unrecoverable - a statutory (B6) leak to
    surface explicitly, not something to silently reconcile.
    """
    invoice_fy_start_year = (
        original_invoice_date.year
        if original_invoice_date.month >= 4
        else original_invoice_date.year - 1
    )
    cutoff = original_invoice_date.replace(
        year=invoice_fy_start_year + 1,
        month=_GST_CREDIT_NOTE_CUTOFF_MONTH,
        day=_GST_CREDIT_NOTE_CUTOFF_DAY,
    )
    passed = now <= cutoff
    if passed:
        message = f"Credit note window is open until {cutoff.date().isoformat()}. Safe to issue."
    else:
        message = (
            f"Credit note window closed on {cutoff.date().isoformat()}. GST bars a "
            "credit note past this date - the input tax credit is permanently "
            "unrecoverable and must be surfaced as a statutory leak, not reconciled away."
        )
    return GuardrailResult(
        name="credit_note_within_gst_window",
        passed=passed,
        blocking=True,
        message=message,
    )


def mandate_is_active(mandate: dict) -> GuardrailResult:
    """Revoked or expired mandates are structurally unretryable - never spend an attempt."""
    mandate_id = mandate.get("mandate_id")
    state = mandate.get("state")
    passed = state == "ACTIVE"
    if passed:
        message = f"Mandate {mandate_id} is ACTIVE. Safe to schedule a retry."
    else:
        message = (
            f"Mandate {mandate_id} is {state}. This is structural, not a timing "
            "failure - no retry can succeed. Do not spend an attempt on it."
        )
    return GuardrailResult(
        name="mandate_is_active",
        passed=passed,
        blocking=True,
        message=message,
    )


def amount_within_mandate_cap(debit_amount_inr: float, mandate: dict) -> GuardrailResult:
    """A debit above the mandate's cap cannot succeed - the bank will decline it."""
    cap = mandate.get("cap_inr", 0.0)
    passed = debit_amount_inr <= cap
    if passed:
        message = f"Rs {debit_amount_inr:,.2f} is within the Rs {cap:,.2f} mandate cap."
    else:
        message = (
            f"Rs {debit_amount_inr:,.2f} exceeds the Rs {cap:,.2f} mandate cap. The "
            "bank will decline this every time - retrying wastes an attempt on a "
            "failure that cannot resolve itself."
        )
    return GuardrailResult(
        name="amount_within_mandate_cap",
        passed=passed,
        blocking=True,
        message=message,
    )


def attempts_remain_in_cycle(attempts_used: int) -> GuardrailResult:
    """NPCI: max 4 attempts per cycle (1 original + 3 retries). A hard ceiling."""
    passed = attempts_used < MAX_ATTEMPTS_PER_CYCLE
    remaining = max(MAX_ATTEMPTS_PER_CYCLE - attempts_used, 0)
    if passed:
        message = f"{remaining} attempt(s) remain this cycle."
    else:
        message = (
            f"All {MAX_ATTEMPTS_PER_CYCLE} attempts for this cycle are used. NPCI "
            "rules make this cycle dead until the next billing period - no retry can "
            "be scheduled."
        )
    return GuardrailResult(
        name="attempts_remain_in_cycle",
        passed=passed,
        blocking=True,
        message=message,
    )


def execution_in_non_peak_window(window: str) -> GuardrailResult:
    """NPCI requires Autopay executions to avoid morning peak entirely."""
    passed = window == "NON_PEAK"
    if passed:
        message = "Slot falls in a non-peak window. Safe to schedule."
    else:
        message = (
            "Slot falls in a peak window. NPCI requires Autopay executions to run "
            "non-peak - this slot must be moved before it can be scheduled."
        )
    return GuardrailResult(
        name="execution_in_non_peak_window",
        passed=passed,
        blocking=True,
        message=message,
    )


def debit_below_afa_threshold(debit_amount_inr: float) -> GuardrailResult:
    """Above Rs 15,000, UPI Autopay needs customer OTP approval every cycle.

    Not blocking - this downgrades the tier to require a human, it does not forbid the
    retry outright.
    """
    passed = debit_amount_inr <= AFA_THRESHOLD_INR
    if passed:
        message = (
            f"Rs {debit_amount_inr:,.2f} is at or below the Rs {AFA_THRESHOLD_INR:,.0f} "
            "AFA threshold - no extra approval needed."
        )
    else:
        message = (
            f"Rs {debit_amount_inr:,.2f} exceeds the Rs {AFA_THRESHOLD_INR:,.0f} AFA "
            "threshold. UPI Autopay requires the customer to approve via OTP every "
            "cycle above this amount - no subscription exemption applies."
        )
    return GuardrailResult(
        name="debit_below_afa_threshold",
        passed=passed,
        blocking=False,
        message=message,
    )


def discount_within_margin_floor(
    requested_discount_pct: float, margin_floor_pct: float, current_margin_pct: float
) -> GuardrailResult:
    """The Conversations denial beat: an agent may request a rung, this decides it."""
    resulting_margin_pct = current_margin_pct - requested_discount_pct
    passed = resulting_margin_pct >= margin_floor_pct
    if passed:
        message = (
            f"A {requested_discount_pct:.0f}% discount leaves {resulting_margin_pct:.1f}% "
            f"margin, at or above the {margin_floor_pct:.1f}% floor. Safe to grant."
        )
    else:
        message = (
            f"A {requested_discount_pct:.0f}% discount breaches the "
            f"{margin_floor_pct:.1f}% margin floor on this cart. Denied - the agent "
            "must hold the line or offer a smaller rung."
        )
    return GuardrailResult(
        name="discount_within_margin_floor",
        passed=passed,
        blocking=True,
        message=message,
    )
