"""The legal-state matrix — the keystone of the product.

We do **not** enumerate every way a transaction can break. That list is unbounded.
We enumerate every combination that is **legal**, and treat everything else as a break.

That is the whole trick. It is why this engine catches failures nobody explicitly coded
for, and why `UNCLASSIFIED_BREAK` is a feature we demo rather than a fallback we apologise
for.

Time is the fourth dimension. `PENDING + MISSING` is healthy for a customer mid-checkout;
it becomes `PAYMENT_PENDING_WEBHOOK_MISSING` only once it has persisted past its dwell
limit. Any state that is legal-but-only-briefly needs an entry in **both**
`DWELL_LIMITS_SECONDS` and `DWELL_BREAKS`. A legal state with no dwell limit can never break.

`scripts/seed.py` imports the lifecycle paths below and walks them, so the generator and
the matrix cannot drift apart. Do not restate a state tuple anywhere else.
"""

from __future__ import annotations

from .states import (
    AccountingState as A,
    BreakType,
    BusinessType,
    InventoryState as I,
    OrderState as O,
    PaymentState as P,
)

# A snapshot of all four ledgers at one instant.
Snapshot = tuple[P, O, I, A]


# ---------------------------------------------------------------------------
# E-commerce lifecycle
# ---------------------------------------------------------------------------
# Each of these is a state a genuinely healthy e-commerce transaction passes through.
# Justify any addition here: adding a row to silence a false positive is how this engine
# goes blind.

E_CHECKOUT_STARTED   = (P.INITIATED,  O.MISSING,   I.AVAILABLE, A.NOT_BOOKED)
E_AWAITING_COLLECT   = (P.PENDING,    O.MISSING,   I.AVAILABLE, A.NOT_BOOKED)
E_AUTHORIZED         = (P.AUTHORIZED, O.CREATED,   I.RESERVED,  A.NOT_BOOKED)
E_CAPTURED_UNBOOKED  = (P.CAPTURED,   O.CONFIRMED, I.RESERVED,  A.NOT_BOOKED)
E_AWAITING_FULFIL    = (P.CAPTURED,   O.CONFIRMED, I.RESERVED,  A.DEFERRED)
E_COMPLETE           = (P.CAPTURED,   O.FULFILLED, I.SHIPPED,   A.REVENUE_RECOGNIZED)
E_CLEAN_DECLINE      = (P.FAILED,     O.MISSING,   I.AVAILABLE, A.NOT_BOOKED)
E_REFUNDED_PRE_SHIP  = (P.REFUNDED,   O.CANCELLED, I.AVAILABLE, A.REVERSED)
E_RETURNED           = (P.REFUNDED,   O.CANCELLED, I.RETURNED,  A.REVERSED)

LEGAL_ECOMMERCE: frozenset[Snapshot] = frozenset({
    E_CHECKOUT_STARTED,
    E_AWAITING_COLLECT,
    E_AUTHORIZED,
    E_CAPTURED_UNBOOKED,
    E_AWAITING_FULFIL,
    E_COMPLETE,
    E_CLEAN_DECLINE,
    E_REFUNDED_PRE_SHIP,
    E_RETURNED,
})


# ---------------------------------------------------------------------------
# SaaS lifecycle — inventory is always NOT_APPLICABLE
# ---------------------------------------------------------------------------

S_CHECKOUT_STARTED  = (P.INITIATED,  O.MISSING,   I.NOT_APPLICABLE, A.NOT_BOOKED)
S_AWAITING_COLLECT  = (P.PENDING,    O.MISSING,   I.NOT_APPLICABLE, A.NOT_BOOKED)
S_AUTHORIZED        = (P.AUTHORIZED, O.CREATED,   I.NOT_APPLICABLE, A.NOT_BOOKED)
S_CAPTURED_UNBOOKED = (P.CAPTURED,   O.CONFIRMED, I.NOT_APPLICABLE, A.NOT_BOOKED)
S_ACTIVE_DEFERRED   = (P.CAPTURED,   O.CONFIRMED, I.NOT_APPLICABLE, A.DEFERRED)
S_ACTIVE_RECOGNIZED = (P.CAPTURED,   O.CONFIRMED, I.NOT_APPLICABLE, A.REVENUE_RECOGNIZED)
S_DEBIT_FAILED      = (P.FAILED,     O.CONFIRMED, I.NOT_APPLICABLE, A.DEFERRED)
S_CLEAN_DECLINE     = (P.FAILED,     O.MISSING,   I.NOT_APPLICABLE, A.NOT_BOOKED)
S_CANCELLED         = (P.REFUNDED,   O.CANCELLED, I.NOT_APPLICABLE, A.REVERSED)

LEGAL_SAAS: frozenset[Snapshot] = frozenset({
    S_CHECKOUT_STARTED,
    S_AWAITING_COLLECT,
    S_AUTHORIZED,
    S_CAPTURED_UNBOOKED,
    S_ACTIVE_DEFERRED,
    S_ACTIVE_RECOGNIZED,
    S_DEBIT_FAILED,
    S_CLEAN_DECLINE,
    S_CANCELLED,
})


# ---------------------------------------------------------------------------
# Time as the fourth dimension
# ---------------------------------------------------------------------------
# A legal state that lasts longer than this is no longer healthy.
# Every key here MUST have a matching entry in DWELL_BREAKS.

_HOUR = 3600
_DAY = 86400

DWELL_LIMITS_SECONDS: dict[Snapshot, int] = {
    # Customer is mid-checkout. Normal for minutes, a lost cart after that.
    E_CHECKOUT_STARTED:   15 * 60,
    S_CHECKOUT_STARTED:   15 * 60,
    # UPI collect request outstanding. The webhook should have landed by now.
    E_AWAITING_COLLECT:   30 * 60,
    S_AWAITING_COLLECT:   30 * 60,
    # Authorised but never captured — the money is on hold and expiring.
    E_AUTHORIZED:         1 * _HOUR,
    S_AUTHORIZED:         1 * _HOUR,
    # Captured but accounting never booked it. Batch jobs run hourly.
    E_CAPTURED_UNBOOKED:  1 * _HOUR,
    S_CAPTURED_UNBOOKED:  1 * _HOUR,
    # Paid and reserved but never shipped.
    E_AWAITING_FULFIL:    2 * _DAY,
    # Autopay debit failed; the subscription sits in grace before it is a real leak.
    S_DEBIT_FAILED:       4 * _DAY,
}

DWELL_BREAKS: dict[Snapshot, BreakType] = {
    E_CHECKOUT_STARTED:   BreakType.CHECKOUT_ABANDONED,
    S_CHECKOUT_STARTED:   BreakType.CHECKOUT_ABANDONED,
    E_AWAITING_COLLECT:   BreakType.PAYMENT_PENDING_WEBHOOK_MISSING,
    S_AWAITING_COLLECT:   BreakType.PAYMENT_PENDING_WEBHOOK_MISSING,
    E_AUTHORIZED:         BreakType.AUTHORIZED_NOT_CAPTURED,
    S_AUTHORIZED:         BreakType.AUTHORIZED_NOT_CAPTURED,
    E_CAPTURED_UNBOOKED:  BreakType.REVENUE_NOT_BOOKED,
    S_CAPTURED_UNBOOKED:  BreakType.REVENUE_NOT_BOOKED,
    E_AWAITING_FULFIL:    BreakType.FULFILMENT_STALLED,
    S_DEBIT_FAILED:       BreakType.MANDATE_DEBIT_FAILED,
}


# ---------------------------------------------------------------------------
# Named breaks — illegal combinations we can explain
# ---------------------------------------------------------------------------
# `None` in a slot means "any value". Patterns are matched most-specific-first, and
# expanded into a flat lookup at import so classification stays a dict hit.

_WILDCARD_PATTERNS: list[tuple[tuple, BreakType]] = [
    # Scenario 1 — money moved, nothing was created.
    ((P.CAPTURED, O.MISSING, None, None), BreakType.ORPHAN_PAYMENT_NO_ORDER),
    # Scenario 3 — out-of-band refund, goods already gone.
    ((P.REFUNDED, O.FULFILLED, None, None), BreakType.REFUND_AFTER_SHIPMENT),
    # Scenario 3 — out-of-band refund, order still active and revenue still booked.
    ((P.REFUNDED, O.CONFIRMED, None, None), BreakType.REFUND_WITHOUT_CANCELLATION),
    ((P.REFUNDED, O.CREATED, None, None), BreakType.REFUND_WITHOUT_CANCELLATION),
    # Money captured against an order somebody cancelled.
    ((P.CAPTURED, O.CANCELLED, None, None), BreakType.PAYMENT_ON_CANCELLED_ORDER),
]


def _expand(patterns: list[tuple[tuple, BreakType]]) -> dict[Snapshot, BreakType]:
    """Flatten wildcard patterns into an exact-match table. First pattern wins."""
    table: dict[Snapshot, BreakType] = {}
    for (p_pat, o_pat, i_pat, a_pat), break_type in patterns:
        # `is not None`, not truthiness: an enum member that happened to be
        # falsy would silently expand into a wildcard.
        for p in ([p_pat] if p_pat is not None else list(P)):
            for o in ([o_pat] if o_pat is not None else list(O)):
                for i in ([i_pat] if i_pat is not None else list(I)):
                    for a in ([a_pat] if a_pat is not None else list(A)):
                        table.setdefault((p, o, i, a), break_type)
    return table


NAMED_BREAKS: dict[Snapshot, BreakType] = _expand(_WILDCARD_PATTERNS)


# ---------------------------------------------------------------------------
# Lifecycle paths — imported by scripts/seed.py
# ---------------------------------------------------------------------------
# The generator walks these. It never writes a state tuple of its own. This is the
# single guard against the matrix and the synthetic data disagreeing.

ECOMMERCE_PATHS: dict[str, list[Snapshot]] = {
    "FULFILLED": [
        E_CHECKOUT_STARTED, E_AWAITING_COLLECT, E_AUTHORIZED,
        E_CAPTURED_UNBOOKED, E_AWAITING_FULFIL, E_COMPLETE,
    ],
    "CLEAN_DECLINE": [
        E_CHECKOUT_STARTED, E_AWAITING_COLLECT, E_CLEAN_DECLINE,
    ],
    "REFUNDED_PRE_SHIP": [
        E_CHECKOUT_STARTED, E_AWAITING_COLLECT, E_AUTHORIZED,
        E_CAPTURED_UNBOOKED, E_AWAITING_FULFIL, E_REFUNDED_PRE_SHIP,
    ],
    "RETURNED": [
        E_CHECKOUT_STARTED, E_AWAITING_COLLECT, E_AUTHORIZED,
        E_CAPTURED_UNBOOKED, E_AWAITING_FULFIL, E_COMPLETE, E_RETURNED,
    ],
}

SAAS_PATHS: dict[str, list[Snapshot]] = {
    "ACTIVE": [
        S_CHECKOUT_STARTED, S_AWAITING_COLLECT, S_AUTHORIZED,
        S_CAPTURED_UNBOOKED, S_ACTIVE_DEFERRED, S_ACTIVE_RECOGNIZED,
    ],
    "CLEAN_DECLINE": [
        S_CHECKOUT_STARTED, S_AWAITING_COLLECT, S_CLEAN_DECLINE,
    ],
    "RENEWAL_FAILED": [
        S_ACTIVE_RECOGNIZED, S_DEBIT_FAILED,
    ],
    "CANCELLED": [
        S_CHECKOUT_STARTED, S_AWAITING_COLLECT, S_AUTHORIZED,
        S_CAPTURED_UNBOOKED, S_ACTIVE_DEFERRED, S_ACTIVE_RECOGNIZED, S_CANCELLED,
    ],
}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def legal_states(business_type: BusinessType) -> frozenset[Snapshot]:
    return LEGAL_SAAS if business_type == BusinessType.SAAS else LEGAL_ECOMMERCE


def is_legal(snapshot: Snapshot, business_type: BusinessType) -> bool:
    """Legal as a state, ignoring how long it has been held."""
    return snapshot in legal_states(business_type)


def dwell_limit_seconds(snapshot: Snapshot) -> int | None:
    """None means this legal state may be held indefinitely."""
    return DWELL_LIMITS_SECONDS.get(snapshot)


def classify_break(
    payment: P,
    order: O,
    inventory: I,
    accounting: A,
    age_seconds: float,
    business_type: BusinessType,
) -> BreakType | None:
    """Return the break, or None if the transaction is healthy.

    Resolution order is normative:
      1. legal and within dwell (or no dwell limit) -> None
      2. legal but dwell exceeded                   -> DWELL_BREAKS[snapshot]
      3. a named illegal combination                -> that break type
      4. anything else                              -> UNCLASSIFIED_BREAK

    Step 4 is the open-world property. If it stops firing for a combination nobody
    coded for, the central product claim is no longer true.
    """
    snapshot: Snapshot = (payment, order, inventory, accounting)

    if snapshot in legal_states(business_type):
        limit = DWELL_LIMITS_SECONDS.get(snapshot)
        if limit is None or age_seconds <= limit:
            return None
        # A legal state that overstayed. Every dwell-limited state has a break.
        return DWELL_BREAKS[snapshot]

    named = NAMED_BREAKS.get(snapshot)
    if named is not None:
        return named

    return BreakType.UNCLASSIFIED_BREAK


# Fail loudly at import if a dwell limit was added without its break, rather than
# letting a legal state silently become uncatchable.
assert set(DWELL_LIMITS_SECONDS) == set(DWELL_BREAKS), (
    "Every dwell-limited state needs a matching entry in DWELL_BREAKS: "
    f"{set(DWELL_LIMITS_SECONDS) ^ set(DWELL_BREAKS)}"
)
