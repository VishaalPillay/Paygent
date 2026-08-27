# Build plan — layers 1-4: ingestion, ledgers, intelligence, case bus

Owner: Nikhil · Branch: `nikhil/foundation`

Work is split by architecture layer (`docs/image.png`). This plan covers **layers 1-4**:
data ingestion, the four-ledger store, the intelligence layer, and the Recovery Case Bus.
Vishaal owns layers 5-7 (agents, guardrails, dashboard).

---

## Context

The repo was a complete scaffold with **zero implementation** — every source file 0 bytes,
including `CONTRACTS.md`. Nothing downstream could start: no contract to code against, no data
to run against, and no way to tell a break from a healthy transaction.

Layers 1-3 are the half that has to exist first. They turn nothing into a populated four-ledger
store plus a stream of scored, typed findings that layer 4 can assemble into cases.

### The riskiest assumption, and how it is handled

**That the legal-state matrix and the synthetic generator agree.** Authored separately they
drift, and the detector then flags all 12,000 rows or none of them.

The mitigation is structural: **`scripts/seed.py` imports the lifecycle paths from
`matrix.py` and walks them.** It never writes a state tuple of its own. Breaks are made by
truncating or corrupting a walk. There is one definition of a legal state in the codebase.

A second guard falls out of that. A ledger snapshot is **derived, never stored** — an orphan
payment is a payment row with no matching order row, so absence *is* the `MISSING` state.
That means some state combinations cannot be physically represented (with no order, there is
nothing to hang an inventory or accounting row on). `seed.py` asserts representability before
writing, so an unrepresentable snapshot fails loudly instead of silently reading back as
something else.

### The waterfall needs two tables the four ledgers do not provide

Gates **B1** (checkout → attempted) and **B4** (order → settled) have no source in
payment/order/inventory/accounting. The schema adds `checkout_sessions` and `settlements`.
Without them the bottom half of Command Center's headline chart would be fabricated — which is
exactly the credibility claim that screen exists to make.

---

## Ownership

| Layer | Box | Area | Owner | Status |
|---|---|---|---|---|
| 1 | Synthetic Generator | `scripts/seed.py` | Nikhil | done |
| 1 | Razorpay Webhooks | `backend/webhooks/razorpay.py` | Nikhil | done |
| 2 | Four-ledger Store | `backend/ledgers/states.py`, `backend/db/` | Nikhil | done |
| 3 | Consistency Matrix | `backend/ledgers/matrix.py`, `scanner.py` | Nikhil | done |
| 3 | Anomaly Detection | `backend/anomaly/detector.py` | Nikhil | done |
| 3 | ML Scorers | `backend/ml/train.py` | Nikhil | done |
| — | `CONTRACTS.md` | Nikhil | done |
| 4 | Recovery Case Bus | `backend/cases/model.py`, `bus.py` | Nikhil | done |
| 5 | Recovery Agents | `backend/agents/`, `backend/sequencer/` | Vishaal | — |
| 6 | Guardrail Engine | `backend/guardrails/` | Vishaal | — |
| 7 | React Dashboard | `frontend/`, `backend/api/` | Vishaal | — |

`CONTRACTS.md` has a single owner deliberately. Invariant 1 only works if exactly one person
can change a shape.

---

## The seam between the two halves

Layer 3 writes `Signal` rows. Layer 4 reads them and assembles `RecoveryCase` objects.

The handoff is a **table**, not a function call, so either half can be built and tested before
the other exists. `Signal` is defined in `CONTRACTS.md` §2 and in `backend/db/schema.sql`.

A signal states a *finding*. It never proposes an action, names a resolver, or sets a tier —
those are layer 4 and layer 6 decisions. `basis` is set by the emitter and never rewritten
downstream: a `modelled` signal cannot become a `deterministic` case.

---

## What was built

### Layer 2 — `backend/ledgers/states.py`

Four `str, Enum` ledgers plus `BusinessType`, `MandateState`, `FailureReasonCode`, `BreakType`
(18 values), `AFA_THRESHOLD_INR = 15000.0`, `MAX_ATTEMPTS_PER_CYCLE = 4`, and
`NON_TERMINAL_PAYMENT_STATES`.

That last set is defined **only here** and consumed by
`guardrails/blocks.py::refund_requires_terminal_payment`. It must not be redefined in
`guardrails/`. SaaS rows use `InventoryState.NOT_APPLICABLE` — never `None`, never `""`.

### Layer 3 — `backend/ledgers/matrix.py`

Enumerates what is **legal** and treats everything else as a break. 9 legal e-commerce states,
9 legal SaaS states, derived from the lifecycle walks the generator replays.

Time is the fourth dimension. A legal-but-only-briefly state needs an entry in **both**
`DWELL_LIMITS_SECONDS` and `DWELL_BREAKS`, or it can never break. An import-time assertion
enforces that the two dicts have identical keys.

| Tuple | Dwell | Break on exceed |
|---|---|---|
| `INITIATED + MISSING` | 15 min | `CHECKOUT_ABANDONED` |
| `PENDING + MISSING` | 30 min | `PAYMENT_PENDING_WEBHOOK_MISSING` ← Scenario 2 |
| `AUTHORIZED + CREATED` | 1 hr | `AUTHORIZED_NOT_CAPTURED` |
| `CAPTURED + CONFIRMED + NOT_BOOKED` | 1 hr | `REVENUE_NOT_BOOKED` |
| `CAPTURED + CONFIRMED + DEFERRED` (e-comm) | 2 days | `FULFILMENT_STALLED` |
| `FAILED + CONFIRMED` (SaaS grace) | 4 days | `MANDATE_DEBIT_FAILED` |

Resolution order in `classify_break` is normative: legal-and-within-dwell → `None`; legal but
overstayed → dwell break; a named illegal combination → that break; **anything else →
`UNCLASSIFIED_BREAK`**.

**Never add a row to `LEGAL_*` to silence a false positive.** That is how this engine goes blind.

### Layer 3 — `backend/ledgers/scanner.py`

Joins the four ledgers, reconstructs each snapshot, runs `classify_break`, writes signals.

Abandoned carts get a second pass, since they have no payment row at all. Only carts above
₹2,000 and under 14 days old become individual signals; the rest roll into waterfall gate B1.
A finance team abandons a tool that hands them 18,000 findings, and the cart agent can only
work the recoverable tail.

### Layer 3 — `backend/anomaly/detector.py`

A different question from the matrix: *is this strange even though every ledger is legal?*
Three kinds of finding, and `basis` differs by kind — business rules are `deterministic`
(arithmetic on a published fee schedule or statute), statistical outliers and IsolationForest
patterns are `modelled` (the threshold is a choice, not a law).

Unclaimed GST is split on the same principle: past 30 November of the following FY the credit
note is barred and the loss is a fact (`deterministic`); inside the window it is still
claimable, so the exposure is `modelled`.

### Layer 3 — `backend/ml/train.py`

Two LightGBM models, both trained from the same SQLite database, under 4 seconds total.

**The salary signal is rediscovered, never handed over.** `_latent_traits` holds each
customer's true `salary_day` and is never joined here. Instead the pay day is estimated from
*observed outcomes*, then fed in as `days_since_predicted_salary`.

The estimator inverts the generating process rather than taking an argmax: for every candidate
pay day, measure how strongly `(day - s) mod 30` correlates with failure and take the strongest.
With ~15 attempts per customer, the single best-performing day is mostly noise, and a linear
ramp has no spike to find.

Two corrections made during the build, both worth knowing about:

- **Churn was leaking.** Mandate state was drawn independently and then behaviour was forced to
  100% failure, so `failure_rate` was exactly 1.0 for every dead mandate — AUC 0.998, and the
  model was reading its own answer. Regenerating so churn is an *outcome* of mounting failures
  brought it to a believable 0.916.
- **The retry model was learning a rule that is not its job.** Over-cap, revoked and expired
  mandates fail every time by rule; `router.py` rejects them deterministically before the
  scorer runs. Training on them let `amount_to_cap_ratio` dominate. Restricting the training
  set to router-eligible attempts put `days_since_predicted_salary` at the top where it belongs.

### Layer 1 — `backend/webhooks/razorpay.py`

FastAPI router (mounted by Vishaal's `main.py`). HMAC-SHA256 signature verification, enforced
whenever `RAZORPAY_WEBHOOK_SECRET` is set. Writes are idempotent on `payment_id`, so Razorpay
redelivery cannot double-apply.

`DEMO_DROP_WEBHOOK=1` verifies and acknowledges the event, then deliberately does not apply it.
The payment stays `PENDING` with no order — past its dwell limit, exactly Scenario 2. Nothing
is faked: the on-stage break is produced by the same code path that would have resolved it.

---

## Bugs found in review, and fixed

A dedicated bug pass after the build found five defects. All are fixed and covered by the
verification below.

**1. Signal id collision — crashed on any re-run.** The three detectors each generated
`sig_000001…` from independent counters (the scanner from zero, the others from `COUNT(*)`).
Running them twice, or in a different order, hit `UNIQUE constraint failed: signals.signal_id`.
Ids are now source-prefixed (`sig_cm_`, `sig_an_`, `sig_ml_`) with a counter local to each
detector, so every one is idempotent and order-independent.

**2. Scanner JOIN fan-out — latent, but certain to fire.** `inventory_events` and
`accounting_entries` are event logs; one order legitimately carries reserve → ship → return.
The scanner joined every row, multiplying the payment and emitting a duplicate signal per
event. It happened to be 1:1 in the seed, so nothing showed — but the webhook already writes
inventory rows, and any real fulfilment flow writes several. Each ledger now contributes only
its latest row.

**3. No time anchor — every break would reclassify itself overnight.** The seed is generated
relative to a fixed instant, but detectors defaulted to `datetime.now()`. Dwell comparisons
would drift as the wall clock moved, silently reclassifying in-flight transactions as breaks.
A `meta` table now stores `reference_now` and all three detectors measure against it.

**4. Seven declared break types had no data.** `AUTHORIZED_NOT_CAPTURED`,
`REVENUE_NOT_BOOKED`, `FULFILMENT_STALLED`, `MANDATE_DEBIT_FAILED`,
`PAYMENT_ON_CANCELLED_ORDER`, `DUPLICATE_PAYMENT` and `MANDATE_UNRETRYABLE` were in the enum
and the contract but nothing produced them. `MANDATE_DEBIT_FAILED` mattered most — it is the
entire retry-sequencer story, and `age_within()` capped every failed renewal *inside* its
four-day grace so the break could never fire. The generator now injects dwell breaks
explicitly, and the scanner gained two detections it was missing: `DUPLICATE_PAYMENT` (a
second captured payment on one checkout) and `MANDATE_UNRETRYABLE` (a revoked, expired or
over-cap mandate against a still-active subscription).

`DUPLICATE_PAYMENT` takes precedence over the per-payment classification deliberately. A
duplicate also looks like an orphan, but calling it one sends the resolver down the
create-the-missing-order path when the correct action is a refund.

**5. Smaller correctness fixes.** Wildcard expansion in `matrix.py` tested truthiness rather
than `is not None`. Abandoned-cart signals hardcoded e-commerce inventory state regardless of
business type. IsolationForest findings were labelled `UNUSUAL_DISCOUNT`, conflating two
different methods — they now carry `ANOMALOUS_TRANSACTION_PATTERN`. Signals worth zero rupees
are dropped at the writer rather than left for layer 4 to filter.

---

### Layer 4 — `backend/cases/model.py` + `bus.py`

Reads `Signal` rows and assembles `RecoveryCase` objects. **Not one case per signal** — 5,601
signals would produce a queue nobody works, which is the alert-fatigue failure that kills
reconciliation tools. Per break type the highest-value signals become individual cases and the
tail rolls into one aggregate. Result: **1,601 individual cases + 9 batch actions**.

Deadlines come from the domain, not a generic SLA: the T+5 acquirer credit-adjustment window,
the NPCI 4-attempts-per-cycle limit, the 30-November GST credit-note bar, and a one-day courier
recall window for goods already in transit.

Three invariants the bus holds, each verified:

- **No case mixes bases.** `STATUTORY_CREDIT_UNCLAIMED` emits both, so the aggregation key is
  `(break_type, business_type, basis)`.
- **`tier` is left NULL.** Guardrails owns it; a fabricated tier reads downstream as "safe to
  auto-execute".
- **Every signal is accounted for** — cased or rolled up, 5,601 of 5,601, none dropped.

Four bugs found and fixed during this build:

1. **The individual cap only counted the current run**, so a second incremental pass sailed
   past it — 60 became 120. The per-group counter now seeds from existing cases.
2. **`case_id` was sequenced from `COUNT(*)`**, which collides after any delete. Now taken from
   the highest id in use.
3. **The new `case_signals` foreign key blocked every detector from clearing its own signals.**
   The whole pipeline failed on re-run — and because I had suppressed stderr, three runs
   silently did nothing while I read stale numbers as if they were fresh.
   `db.clear_signals()` now walks the dependency chain in order.
4. **Abandoned carts were booked as `deterministic` at full cart value** — Rs 35.3M of a
   Rs 62.7M "confirmed money at risk" total. The customer never paid: the full value is a real
   leak (waterfall gate B1) but the *recoverable* amount is a ~12% proposition. They now carry
   a modelled recoverable estimate, which moved the deterministic total to Rs 25.9M and put the
   largest number in the system on the correct side of the split the product is built on.

---

## Verification — all passing

```bash
python -m scripts.seed              # 12,070 payments · 28,571 sessions · 31,000+ attempts
python -m backend.ledgers.scanner   # consistency matrix -> 4,652 signals
python -m backend.anomaly.detector  # rules + outliers   -> 933 signals
python -m backend.ml.train          # LightGBM -> artifacts + churn signals
python -m backend.cases.bus         # signals -> 1,610 cases
```

Run them in that order. Each detector clears and rebuilds its own signals plus any cases
derived from them, so re-running one mid-chain is safe.

1. **Matrix, before any data exists.** All three scenarios, healthy rows, grace-period
   boundaries and deliberately unhandled tuples classify correctly. Across the full
   600-combination space, 491 fall to `UNCLASSIFIED_BREAK` — the open-world property holds.
2. **Detector agreement, exact.** Every injected break type is detected at exactly the injected
   count: 300 orphans, 150 pending-webhook, 120 out-of-band refunds, 110 stalled fulfilment,
   90 unbooked revenue, 80 uncaptured authorisations, 70 duplicates, 60 payments on cancelled
   orders, 40 unclassified. Zero misses, zero false positives on the healthy majority.
3. **No duplicate signals per payment**, including after a second inventory event is added to
   an order — the fan-out regression is covered directly.
4. **Idempotent and order-independent.** All three detectors re-run in any order; 5,601 signals,
   5,601 distinct ids, zero collisions.
5. **Determinism.** Two consecutive seeds produce a byte-identical fingerprint across all ten
   ledger tables.
6. **Signal integrity.** No zero-value signals, `basis` always one of the two legal values,
   `confidence` always within 0..1, `break_type` always in the enum.
7. **Time anchor.** `reference_now` is stored in `meta` and no ledger row postdates it.
8. **Break-type coverage.** All 19 declared break types have data behind them.
9. **Honest metrics.** retry_success AUC 0.786, churn AUC 0.926, both held out. Salary
   rediscovery 17.4% exact and 73.6% within 7 days, against 3.3% and 50% for chance, with
   `days_since_predicted_salary` the top feature by gain.
10. **Webhook.** Normal capture writes both ledgers; redelivery is idempotent (one order, not
    two); dropped mode writes nothing and produces the break; a bad signature returns 401.
11. **Case bus.** Rebuild is idempotent (1,610 → 1,610); no case mixes bases; every case has a
    resolver and a deadline; `tier` is NULL on all of them; `priority_score` recomputed
    independently from the contract formula over 500 cases with zero mismatches; the individual
    cap survives an incremental run; all detectors still re-run with cases in the database.

---

## Handoff to Vishaal

Read `CONTRACTS.md` §2 for `Signal` and `RecoveryCase`, then `SELECT * FROM signals` — 4,245
rows are already there. Layer 4 has everything it needs; it does not have to wait on anything.

Three things to know:

- `NON_TERMINAL_PAYMENT_STATES` lives in `backend/ledgers/states.py`. Do not redefine it in
  `guardrails/`.
- `_latent_traits` is off-limits to anything that trains or scores. The salary signal is
  rediscovered on purpose, and joining that table would make the result meaningless.
- `router.py` must reject structurally unretryable mandates (revoked, expired, over cap) before
  `scorer.py` is consulted. The retry model is trained on that assumption — it has never seen a
  structurally doomed attempt and will score one nonsensically.
