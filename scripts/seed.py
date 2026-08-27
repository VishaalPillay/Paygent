"""Synthetic generator — layer 1 of the architecture.

10k e-commerce transactions, 2k SaaS subscriptions, written into the four-ledger
store as raw ledger rows. Stdlib only: no pandas, no numpy, so this runs on any
interpreter regardless of the ML environment.

The important property: **this script never writes a state tuple of its own.** It
imports the lifecycle paths from `backend/ledgers/matrix.py` and walks them. Breaks
are produced by truncating a walk, or by deliberately corrupting one. If the matrix
and the generator ever disagree, it is a bug in one file, not a drift between two.

A ledger snapshot is *derived*, never stored. An orphan payment is created by writing
a payment row and simply not writing an order row — exactly how the real failure
looks. `backend/ledgers/scanner.py` reconstructs the four-ledger view by joining.

Run:  python -m scripts.seed
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from backend.db import conn as db
from backend.ledgers import matrix as M
from backend.ledgers.states import (
    AFA_THRESHOLD_INR,
    AccountingState as A,
    BusinessType,
    FailureReasonCode as FR,
    InventoryState as I,
    MandateState,
    OrderState as O,
    PaymentState as P,
)

# --- reproducibility -------------------------------------------------------
SEED = 20260827
NOW = datetime(2026, 8, 27, 18, 0, 0, tzinfo=timezone.utc)
rng = random.Random(SEED)

# --- volumes ---------------------------------------------------------------
N_ECOM_TXN = 10_000          # attempted e-commerce transactions
N_SAAS_SUBS = 2_000          # subscriptions, each with a mandate
ABANDON_RATE = 0.65          # share of all checkout sessions never attempting payment

# --- deliberate break injection (counts, taken out of the healthy pool) -----
N_SCENARIO_1 = 300           # captured payment, no order            (orphan)
N_SCENARIO_2 = 150           # pending payment, webhook never landed
N_SCENARIO_3 = 120           # refund issued out of band
N_UNCLASSIFIED = 40          # combinations nobody coded for

# Dwell breaks — legal states that overstayed. Without these, four declared break
# types have no examples and neither the demo nor layer 4 can exercise them.
N_AUTH_NOT_CAPTURED = 80     # authorised, never captured
N_REVENUE_NOT_BOOKED = 90    # captured and confirmed, accounting never booked
N_FULFILMENT_STALLED = 110   # paid and reserved, never shipped
N_PAYMENT_ON_CANCELLED = 60  # money captured against a cancelled order
N_DUPLICATE_PAYMENT = 70     # two captured payments, one checkout

# --- reference data --------------------------------------------------------
BANKS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak", "PNB", "BoB", "Yes"]
CITIES = ["Mumbai", "Bengaluru", "Delhi", "Chennai", "Pune", "Hyderabad", "Kolkata", "Jaipur"]
SEGMENTS = ["NEW", "REPEAT", "VIP"]
DEVICES = ["android", "ios", "web"]
SKUS = [f"SKU-{i:04d}" for i in range(1, 121)]
GST_SLABS = [0.05, 0.18, 0.40]          # GST 2.0, from 22 Sep 2025
GST_WEIGHTS = [0.35, 0.60, 0.05]
TCS_RATE = 0.005                         # marketplace TCS, reduced from 1% in Jul 2024
FIRST_NAMES = ["Aarav", "Diya", "Rohan", "Ananya", "Kabir", "Meera", "Arjun", "Isha",
               "Vivaan", "Sara", "Advait", "Kiara", "Reyansh", "Anika", "Vihaan", "Tara"]
LAST_NAMES = ["Sharma", "Patel", "Reddy", "Nair", "Iyer", "Gupta", "Singh", "Das",
              "Mehta", "Bose", "Rao", "Khan", "Joshi", "Menon"]

_counters: dict[str, int] = {}


def nid(prefix: str) -> str:
    _counters[prefix] = _counters.get(prefix, 0) + 1
    return f"{prefix}_{_counters[prefix]:06d}"


def iso(dt: datetime) -> str:
    """ISO 8601 UTC with a trailing Z, per CONTRACTS.md."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def money(x: float) -> float:
    """Rupees, two decimals. Never paise, never a string."""
    return round(float(x), 2)


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def gen_customers(cur, n_ecom: int, n_saas: int) -> tuple[list[dict], list[dict]]:
    ecom, saas = [], []
    for business_type, bucket, count in (
        (BusinessType.ECOMMERCE, ecom, n_ecom),
        (BusinessType.SAAS, saas, n_saas),
    ):
        for _ in range(count):
            cid = nid("cus")
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            row = {
                "customer_id": cid,
                "name": f"{first} {last}",
                "email": f"{first.lower()}.{last.lower()}{rng.randint(1, 9999)}@example.in",
                "phone": f"+919{rng.randint(100000000, 999999999)}",
                "business_type": business_type.value,
                "segment": rng.choices(SEGMENTS, weights=[0.45, 0.45, 0.10])[0],
                "bank": rng.choice(BANKS),
                "city": rng.choice(CITIES),
                "created_at": iso(NOW - timedelta(days=rng.randint(30, 900))),
            }
            bucket.append(row)
            cur.execute(
                "INSERT INTO customers VALUES (:customer_id,:name,:email,:phone,"
                ":business_type,:segment,:bank,:city,:created_at)", row)

            # Latent traits: shape behaviour, never exposed as a model feature.
            cur.execute(
                "INSERT INTO _latent_traits VALUES (?,?,?,?)",
                (cid, rng.randint(1, 28), round(rng.uniform(0.1, 0.9), 3),
                 round(rng.uniform(0.02, 0.55), 3)))
    return ecom, saas


# ---------------------------------------------------------------------------
# Writing a snapshot into the four ledgers
# ---------------------------------------------------------------------------

def default_inventory(business_type: BusinessType) -> I:
    return I.NOT_APPLICABLE if business_type == BusinessType.SAAS else I.AVAILABLE


def representable(snapshot, business_type: BusinessType) -> bool:
    """Can these four states actually be written as ledger rows and read back?

    Inventory and accounting rows hang off an order. With no order there is nothing
    to attach them to, so the snapshot reads back as the ledger defaults. Generating
    an unrepresentable combination silently produces different data than intended —
    which is exactly the generator/matrix drift this whole design exists to prevent.
    """
    _, o_state, i_state, a_state = snapshot
    if o_state == O.MISSING:
        return i_state == default_inventory(business_type) and a_state == A.NOT_BOOKED
    return True


def write_snapshot(cur, *, customer, snapshot, age_seconds, session_id,
                   amount_inr, business_type, webhook_received=1,
                   failure_reason=None, mandate_id=None) -> dict:
    """Write the ledger rows implied by one four-ledger snapshot.

    Rows are written only where the state is non-default. Absence IS the signal:
    no order row means OrderState.MISSING, no accounting row means NOT_BOOKED.
    """
    p_state, o_state, i_state, a_state = snapshot
    assert representable(snapshot, business_type), (
        f"snapshot {[x.value for x in snapshot]} cannot be represented as ledger rows "
        f"for {business_type.value} — it would read back as something else")
    updated = NOW - timedelta(seconds=age_seconds)
    created = updated - timedelta(seconds=rng.randint(30, 600))
    is_ecom = business_type == BusinessType.ECOMMERCE

    payment_id = nid("pay")
    method = ("UPI_AUTOPAY" if mandate_id else
              rng.choices(["UPI", "CARD", "NETBANKING"], weights=[0.72, 0.22, 0.06])[0])
    utr = f"{rng.randint(10**11, 10**12 - 1)}" if p_state in (
        P.AUTHORIZED, P.CAPTURED, P.REFUNDED) else None

    cur.execute(
        "INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (payment_id, session_id, customer["customer_id"], mandate_id, p_state.value,
         money(amount_inr), method,
         failure_reason.value if failure_reason else None,
         utr, webhook_received, iso(created), iso(updated)))

    order_id = None
    if o_state != O.MISSING:
        order_id = nid("ord")
        # Continuous, with a deliberate tail: most orders carry no discount, a
        # broad middle carries a normal one, and ~1.5% breach any sane policy.
        # That tail is what backend/anomaly/ has to find without being told.
        u = rng.random()
        if u < 0.55:
            rate = 0.0
        elif u < 0.985:
            rate = rng.uniform(0.02, 0.20)
        else:
            rate = rng.uniform(0.28, 0.55)
        discount = money(amount_inr * rate)
        cur.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
            (order_id, payment_id, session_id, customer["customer_id"],
             business_type.value, o_state.value, money(amount_inr), discount,
             iso(created), iso(updated)))

        cur.execute(
            "INSERT INTO inventory_events VALUES (?,?,?,?,?,?)",
            (nid("inv"), order_id,
             rng.choice(SKUS) if is_ecom else None,
             rng.randint(1, 3) if is_ecom else 0,
             i_state.value, iso(updated)))

    if a_state != A.NOT_BOOKED and order_id:
        gst_rate = rng.choices(GST_SLABS, weights=GST_WEIGHTS)[0]
        gst_amount = money(amount_inr * gst_rate / (1 + gst_rate))
        # Indian GST bars credit notes after 30 Nov of the following FY. Older
        # reversals may be permanently unrecoverable — a statutory (B6) leak.
        barred = 1 if (a_state == A.REVERSED and age_seconds > 300 * 86400) else 0
        cur.execute(
            "INSERT INTO accounting_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (nid("ae"), order_id, a_state.value, money(amount_inr), gst_rate,
             gst_amount, money(amount_inr * TCS_RATE) if is_ecom else 0.0,
             f"INV-{order_id[-6:]}",
             (f"CN-{order_id[-6:]}"
              if a_state == A.REVERSED and rng.random() > 0.30 else None),
             barred, iso(updated)))

    # Settlement exists only once money was actually captured.
    if p_state in (P.CAPTURED, P.REFUNDED):
        mdr = money(amount_inr * (0.0 if method.startswith("UPI") else 0.0195))
        fees = money(amount_inr * 0.0025)
        tax = money((mdr + fees) * 0.18)
        expected_net = money(amount_inr - mdr - fees - tax)
        # ~3% of settlements are short-paid — an anomaly, not a ledger disagreement.
        short = money(amount_inr * rng.uniform(0.01, 0.04)) if rng.random() < 0.03 else 0.0
        status = rng.choices(["SETTLED", "PENDING", "ON_HOLD"], weights=[0.90, 0.08, 0.02])[0]
        cur.execute(
            "INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?,?,?)",
            (nid("stl"), payment_id, money(amount_inr), mdr, fees, tax,
             money(expected_net - short), expected_net, status,
             iso(updated + timedelta(days=1)) if status == "SETTLED" else None))

    return {"payment_id": payment_id, "order_id": order_id}


def age_within(snapshot) -> int:
    """A healthy age for this state: inside its dwell limit, if it has one."""
    limit = M.DWELL_LIMITS_SECONDS.get(snapshot)
    if limit is None:
        return rng.randint(2 * 86400, 240 * 86400)
    return rng.randint(30, max(60, int(limit * 0.8)))


def age_beyond(snapshot) -> int:
    """An age past this state's dwell limit, so it classifies as a break."""
    limit = M.DWELL_LIMITS_SECONDS.get(snapshot, 3600)
    return rng.randint(int(limit * 1.5), int(limit * 12))


# ---------------------------------------------------------------------------
# E-commerce transactions
# ---------------------------------------------------------------------------

ECOM_PATH_WEIGHTS = {
    "FULFILLED": 0.74,
    "CLEAN_DECLINE": 0.12,
    "REFUNDED_PRE_SHIP": 0.09,
    "RETURNED": 0.05,
}


def gen_ecommerce(cur, customers: list[dict]) -> dict[str, int]:
    stats: dict[str, int] = {}

    def bump(k):
        stats[k] = stats.get(k, 0) + 1

    # --- abandoned sessions: gate B1, no payment ever attempted -------------
    n_abandoned = int(N_ECOM_TXN * ABANDON_RATE / (1 - ABANDON_RATE))
    for _ in range(n_abandoned):
        c = rng.choice(customers)
        cur.execute(
            "INSERT INTO checkout_sessions VALUES (?,?,?,?,?,?,?,?)",
            (nid("ses"), c["customer_id"], BusinessType.ECOMMERCE.value,
             money(rng.uniform(299, 24999)), rng.randint(1, 6),
             rng.choice(DEVICES), 0,
             iso(NOW - timedelta(seconds=rng.randint(3600, 90 * 86400)))))
        bump("sessions_abandoned")

    # --- how many of the attempted transactions are deliberate breaks ------
    plan_counts = {
        "S1": N_SCENARIO_1, "S2": N_SCENARIO_2, "S3": N_SCENARIO_3,
        "UNC": N_UNCLASSIFIED, "AUTH": N_AUTH_NOT_CAPTURED,
        "UNBOOKED": N_REVENUE_NOT_BOOKED, "STALLED": N_FULFILMENT_STALLED,
        "CANCELLED": N_PAYMENT_ON_CANCELLED, "DUP": N_DUPLICATE_PAYMENT,
    }
    injected = sum(plan_counts.values())
    assert injected < N_ECOM_TXN, "injected breaks exceed the transaction budget"
    plan = [k for k, n in plan_counts.items() for _ in range(n)]
    plan += ["HEALTHY"] * (N_ECOM_TXN - injected)
    rng.shuffle(plan)

    path_names = list(ECOM_PATH_WEIGHTS)
    path_weights = [ECOM_PATH_WEIGHTS[k] for k in path_names]

    for kind in plan:
        c = rng.choice(customers)
        amount = money(rng.uniform(299, 24999))
        session_id = nid("ses")
        cur.execute(
            "INSERT INTO checkout_sessions VALUES (?,?,?,?,?,?,?,?)",
            (session_id, c["customer_id"], BusinessType.ECOMMERCE.value,
             amount, rng.randint(1, 6), rng.choice(DEVICES), 1,
             iso(NOW - timedelta(seconds=rng.randint(600, 90 * 86400)))))

        common = dict(cur=cur, customer=c, session_id=session_id,
                      amount_inr=amount, business_type=BusinessType.ECOMMERCE)

        if kind == "HEALTHY":
            path = M.ECOMMERCE_PATHS[rng.choices(path_names, weights=path_weights)[0]]
            # Most transactions have finished; a minority are legitimately in flight.
            idx = len(path) - 1 if rng.random() < 0.88 else rng.randrange(len(path))
            snap = path[idx]
            fr = (rng.choices(list(FR)[:4], weights=[0.55, 0.2, 0.15, 0.1])[0]
                  if snap[0] == P.FAILED else None)
            write_snapshot(snapshot=snap, age_seconds=age_within(snap),
                           failure_reason=fr, **common)
            bump("healthy")

        elif kind == "S1":
            # Scenario 1 — money moved, no order row was ever written.
            write_snapshot(snapshot=(P.CAPTURED, O.MISSING, I.AVAILABLE, A.NOT_BOOKED),
                           age_seconds=rng.randint(2 * 3600, 20 * 86400),
                           webhook_received=0, **common)
            bump("break_orphan_payment")

        elif kind == "S2":
            # Scenario 2 — customer debited, webhook never landed, still PENDING.
            snap = M.E_AWAITING_COLLECT
            write_snapshot(snapshot=snap, age_seconds=age_beyond(snap),
                           webhook_received=0, **common)
            bump("break_pending_webhook_missing")

        elif kind == "S3":
            # Scenario 3 — refund issued from the gateway, order never told.
            snap = (rng.choice([
                (P.REFUNDED, O.CONFIRMED, I.RESERVED, A.REVENUE_RECOGNIZED),
                (P.REFUNDED, O.FULFILLED, I.SHIPPED, A.REVENUE_RECOGNIZED),
            ]))
            write_snapshot(snapshot=snap,
                           age_seconds=rng.randint(3 * 86400, 400 * 86400), **common)
            bump("break_out_of_band_refund")

        elif kind in ("AUTH", "UNBOOKED", "STALLED"):
            # Legal states held past their dwell limit. Age is what makes these
            # breaks, so they must be generated with age_beyond, not age_within.
            snap = {"AUTH": M.E_AUTHORIZED,
                    "UNBOOKED": M.E_CAPTURED_UNBOOKED,
                    "STALLED": M.E_AWAITING_FULFIL}[kind]
            write_snapshot(snapshot=snap, age_seconds=age_beyond(snap), **common)
            bump({"AUTH": "break_authorized_not_captured",
                  "UNBOOKED": "break_revenue_not_booked",
                  "STALLED": "break_fulfilment_stalled"}[kind])

        elif kind == "CANCELLED":
            # Money captured against an order somebody cancelled, revenue still booked.
            write_snapshot(
                snapshot=(P.CAPTURED, O.CANCELLED, I.AVAILABLE, A.REVENUE_RECOGNIZED),
                age_seconds=rng.randint(2 * 86400, 90 * 86400), **common)
            bump("break_payment_on_cancelled_order")

        elif kind == "DUP":
            # The customer paid twice for one checkout. The first payment produced a
            # complete order; the second is money we are holding against nothing.
            # Ages must not overlap: the scanner calls the chronologically later
            # captured payment the duplicate, so the two have to be unambiguously
            # ordered or the pair reclassifies itself at random.
            write_snapshot(snapshot=M.E_COMPLETE,
                           age_seconds=rng.randint(10 * 86400, 60 * 86400), **common)
            write_snapshot(snapshot=(P.CAPTURED, O.MISSING, I.AVAILABLE, A.NOT_BOOKED),
                           age_seconds=rng.randint(3600, 5 * 86400), **common)
            bump("break_duplicate_payment")

        else:  # UNC — combinations nobody coded for
            snap = _random_illegal(BusinessType.ECOMMERCE)
            write_snapshot(snapshot=snap,
                           age_seconds=rng.randint(3600, 60 * 86400), **common)
            bump("break_unclassified")

    return stats


def _random_illegal(business_type: BusinessType):
    """A four-ledger combination outside the legal set and outside every named
    break — the open-world case the engine must still catch."""
    legal = M.legal_states(business_type)
    inv_pool = ([I.NOT_APPLICABLE] if business_type == BusinessType.SAAS
                else [s for s in I if s != I.NOT_APPLICABLE])
    for _ in range(2000):
        snap = (rng.choice(list(P)), rng.choice(list(O)),
                rng.choice(inv_pool), rng.choice(list(A)))
        if (snap not in legal and snap not in M.NAMED_BREAKS
                and representable(snap, business_type)):
            return snap
    raise RuntimeError("could not find an unclassified combination")


# ---------------------------------------------------------------------------
# SaaS subscriptions + UPI Autopay mandates
# ---------------------------------------------------------------------------

MANDATE_STATE_WEIGHTS = {
    MandateState.ACTIVE: 0.78,
    MandateState.REVOKED: 0.12,   # ~20M revocations/month nationally, mostly NSF-driven
    MandateState.EXPIRED: 0.07,
    MandateState.PAUSED: 0.03,
}
SAAS_PATH_WEIGHTS = {"ACTIVE": 0.70, "RENEWAL_FAILED": 0.17,
                     "CLEAN_DECLINE": 0.06, "CANCELLED": 0.07}
PEAK_HOURS = set(range(9, 13)) | set(range(18, 22))


def _churn_traits(cur, customer_id: str) -> tuple[int, float]:
    """Latent churn propensity. Shapes the outcome; never becomes a model feature."""
    r = cur.execute(
        "SELECT salary_day, intrinsic_churn_risk FROM _latent_traits WHERE customer_id=?",
        (customer_id,)).fetchone()
    return r["salary_day"], r["intrinsic_churn_risk"]


def _latent(cur, customer_id: str) -> tuple[int, float]:
    r = cur.execute(
        "SELECT salary_day, balance_volatility FROM _latent_traits WHERE customer_id=?",
        (customer_id,)).fetchone()
    return r["salary_day"], r["balance_volatility"]


def gen_saas(cur, customers: list[dict]) -> dict[str, int]:
    stats: dict[str, int] = {}

    def bump(k):
        stats[k] = stats.get(k, 0) + 1

    path_names = list(SAAS_PATH_WEIGHTS)
    path_weights = [SAAS_PATH_WEIGHTS[k] for k in path_names]
    m_states = list(MANDATE_STATE_WEIGHTS)
    m_weights = [MANDATE_STATE_WEIGHTS[k] for k in m_states]

    for c in customers:
        cid = c["customer_id"]
        salary_day, volatility = _latent(cur, cid)

        # 30% of debits sit above the 15,000 AFA threshold, where UPI Autopay
        # requires customer OTP approval every single cycle.
        debit = money(rng.uniform(AFA_THRESHOLD_INR, 45000) if rng.random() < 0.30
                      else rng.uniform(299, 9999))
        # A minority of mandates were set up with a cap below the current price.
        cap = money(debit * (rng.uniform(0.6, 0.95) if rng.random() < 0.06
                             else rng.uniform(1.2, 3.0)))
        # --- does this subscription lapse, and why? -------------------------
        # Churn is an OUTCOME of behaviour, not a label stamped on independently.
        # A mandate bills normally for a while, failures mount, then it is revoked.
        # Generating it the other way round makes failure_rate a perfect proxy for
        # the label, and the churn model learns nothing but its own answer.
        _, intrinsic = _churn_traits(cur, cid)
        churn_pressure = intrinsic + (0.18 if debit > AFA_THRESHOLD_INR else 0.0) + 0.12 * volatility
        churned = rng.random() < min(churn_pressure, 0.80)
        over_cap = debit > cap

        n_cycles = rng.randint(6, 15)
        lapse_cycle = rng.randint(2, n_cycles) if churned else None
        if churned:
            state = rng.choices([MandateState.REVOKED, MandateState.EXPIRED],
                                weights=[0.72, 0.28])[0]
        else:
            state = rng.choices([MandateState.ACTIVE, MandateState.PAUSED],
                                weights=[0.94, 0.06])[0]

        mandate_id = nid("mdt")
        created = NOW - timedelta(days=30 * n_cycles + rng.randint(5, 40))

        cur.execute(
            "INSERT INTO mandates VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mandate_id, cid, state.value, cap, debit, NOW.strftime("%Y-%m"),
             iso(NOW + timedelta(days=rng.randint(1, 28))), iso(created),
             iso(NOW - timedelta(days=30 * (n_cycles - lapse_cycle) + 1))
             if state == MandateState.REVOKED else None,
             iso(NOW - timedelta(days=30 * (n_cycles - lapse_cycle) + 1))
             if state == MandateState.EXPIRED else None))

        # --- debit attempt history, oldest cycle first ----------------------
        for cycle_idx in range(n_cycles):
            if lapse_cycle is not None and cycle_idx >= lapse_cycle:
                break                      # the mandate is gone; no further debits
            cycle_start = NOW - timedelta(days=30 * (n_cycles - cycle_idx))
            cycle = cycle_start.strftime("%Y-%m")
            # Trouble builds in the cycle or two before a lapse.
            distress = 0.0
            if lapse_cycle is not None:
                distress = max(0.0, 0.30 - 0.12 * (lapse_cycle - 1 - cycle_idx))

            for attempt_no in range(1, M_MAX_ATTEMPTS + 1):
                at = cycle_start + timedelta(
                    days=rng.randint(0, 27), hours=rng.randint(0, 23))
                window = "PEAK" if at.hour in PEAK_HOURS else "NON_PEAK"

                if over_cap:
                    # Structurally unretryable — and the merchant burns all four
                    # attempts on it anyway, which is exactly why router.py exists.
                    outcome, reason = "FAILED", FR.AMOUNT_EXCEEDS_MANDATE_CAP
                else:
                    # The latent signal the scorer has to rediscover: money is
                    # present just after wages land and thins across the month.
                    days_since_salary = (at.day - salary_day) % 30
                    p_fail = 0.04 + 0.60 * (days_since_salary / 29.0) * (0.40 + volatility)
                    p_fail += distress
                    if window == "PEAK":
                        p_fail += 0.06
                    if debit > AFA_THRESHOLD_INR:
                        p_fail += 0.09          # AFA drop-off above the threshold
                    if rng.random() < min(p_fail, 0.88):
                        outcome = "FAILED"
                        reason = (FR.AFA_NOT_COMPLETED
                                  if debit > AFA_THRESHOLD_INR and rng.random() < 0.35
                                  else rng.choices(
                                      [FR.INSUFFICIENT_FUNDS, FR.BANK_TIMEOUT,
                                       FR.ISSUER_DECLINED, FR.TECHNICAL_DECLINE],
                                      weights=[0.62, 0.16, 0.14, 0.08])[0])
                    else:
                        outcome, reason = "SUCCESS", None

                cur.execute(
                    "INSERT INTO mandate_attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (nid("att"), mandate_id, cid, cycle, attempt_no, iso(at),
                     window, debit, outcome, reason.value if reason else None))
                bump("mandate_attempts")

                if outcome == "SUCCESS":
                    break
                if over_cap and attempt_no == M_MAX_ATTEMPTS:
                    bump("attempts_burned_on_unretryable")

        # --- the subscription's own four-ledger position -------------------
        path = M.SAAS_PATHS[rng.choices(path_names, weights=path_weights)[0]]
        idx = len(path) - 1 if rng.random() < 0.9 else rng.randrange(len(path))
        snap = path[idx]
        fr = None
        if snap[0] == P.FAILED:
            fr = _structural_failure(state, debit, cap) or FR.INSUFFICIENT_FUNDS
        # A failed renewal sits in grace for four days. Some are still inside it
        # (healthy); the rest have exhausted it and are a real MANDATE_DEBIT_FAILED
        # leak. Generating only the former left that break type with no examples.
        overdue = snap == M.S_DEBIT_FAILED and rng.random() < 0.45
        write_snapshot(cur=cur, customer=c, snapshot=snap,
                       age_seconds=age_beyond(snap) if overdue else age_within(snap),
                       session_id=None,
                       amount_inr=debit, business_type=BusinessType.SAAS,
                       failure_reason=fr, mandate_id=mandate_id)
        bump("saas_subscriptions")

    return stats


M_MAX_ATTEMPTS = 4   # NPCI: 1 original + 3 retries, then the cycle is dead


def _structural_failure(state: MandateState, debit: float, cap: float):
    """Failures no retry can fix. router.py must never spend an attempt on these."""
    if state == MandateState.REVOKED:
        return FR.MANDATE_REVOKED
    if state == MandateState.EXPIRED:
        return FR.MANDATE_EXPIRED
    if debit > cap:
        return FR.AMOUNT_EXCEEDS_MANDATE_CAP
    return None


# ---------------------------------------------------------------------------

def main() -> None:
    print(f"seeding with SEED={SEED}, reference now={iso(NOW)}")
    conn = db.reset()
    cur = conn.cursor()
    # Anchor every downstream dwell calculation to this instant.
    db.set_meta(conn, "reference_now", iso(NOW))
    db.set_meta(conn, "seed", SEED)

    n_ecom_customers = max(1, N_ECOM_TXN // 4)
    ecom_customers, saas_customers = gen_customers(cur, n_ecom_customers, N_SAAS_SUBS)
    print(f"  customers        {len(ecom_customers)} e-commerce, {len(saas_customers)} SaaS")

    ecom_stats = gen_ecommerce(cur, ecom_customers)
    saas_stats = gen_saas(cur, saas_customers)
    conn.commit()

    print("\n  generation summary")
    for k, v in sorted(ecom_stats.items()):
        print(f"    {k:<34} {v:>7,}")
    for k, v in sorted(saas_stats.items()):
        print(f"    {k:<34} {v:>7,}")

    print("\n  table counts")
    for t, n in sorted(db.table_counts(conn).items()):
        print(f"    {t:<24} {n:>8,}")

    conn.close()
    print(f"\n  written to {db.DB_PATH}")


if __name__ == "__main__":
    main()
