"""The four ledgers.

Every transaction leaves four records. When all four agree the transaction is healthy.
Every revenue leak is two of them disagreeing.

States are `str, Enum` so they serialise straight to JSON and compare as strings.
"""

from enum import Enum


class BusinessType(str, Enum):
    ECOMMERCE = "ECOMMERCE"
    SAAS = "SAAS"


class PaymentState(str, Enum):
    """Did the money move?"""

    INITIATED = "INITIATED"
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class OrderState(str, Enum):
    """Did we create the order or activate the subscription?"""

    MISSING = "MISSING"
    CREATED = "CREATED"
    CONFIRMED = "CONFIRMED"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"


class InventoryState(str, Enum):
    """Did we reserve or ship the product? SaaS uses NOT_APPLICABLE."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    SHIPPED = "SHIPPED"
    RETURNED = "RETURNED"


class AccountingState(str, Enum):
    """Did we book it as revenue?"""

    NOT_BOOKED = "NOT_BOOKED"
    DEFERRED = "DEFERRED"
    REVENUE_RECOGNIZED = "REVENUE_RECOGNIZED"
    REVERSED = "REVERSED"


# Consumed by backend/guardrails/blocks.py::refund_requires_terminal_payment.
# Do not redefine this set anywhere else.
#
# A payment in one of these states may still move on its own. In India a UPI merchant
# payment with no confirmation must receive a credit adjustment from the acquirer within
# T+5 — refunding from merchant balance inside that window risks paying the customer twice.
NON_TERMINAL_PAYMENT_STATES = frozenset(
    {
        PaymentState.INITIATED,
        PaymentState.PENDING,
        PaymentState.AUTHORIZED,
    }
)

TERMINAL_PAYMENT_STATES = frozenset(
    {
        PaymentState.CAPTURED,
        PaymentState.FAILED,
        PaymentState.REFUNDED,
    }
)


class MandateState(str, Enum):
    """UPI Autopay mandate. REVOKED and EXPIRED are structurally unretryable."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class FailureReasonCode(str, Enum):
    """Why a debit or payment failed. Drives sequencer/router.py retry eligibility."""

    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    BANK_TIMEOUT = "BANK_TIMEOUT"
    ISSUER_DECLINED = "ISSUER_DECLINED"
    TECHNICAL_DECLINE = "TECHNICAL_DECLINE"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    AMOUNT_EXCEEDS_MANDATE_CAP = "AMOUNT_EXCEEDS_MANDATE_CAP"
    AFA_NOT_COMPLETED = "AFA_NOT_COMPLETED"
    CUSTOMER_CANCELLED = "CUSTOMER_CANCELLED"


# UPI Autopay debits above this require the customer to approve via OTP every cycle.
# No exemption for subscriptions — the 1 lakh AFA-free ceiling covers only mutual funds,
# insurance and credit card bills.
AFA_THRESHOLD_INR = 15000.0

# NPCI: max 4 attempts per mandate cycle — 1 original + 3 retries. Then the cycle is dead.
MAX_ATTEMPTS_PER_CYCLE = 4
