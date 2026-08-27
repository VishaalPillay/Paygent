# Foundation plan — contracts, ledger, seed data, Command Center

Owner: Nikhil · Branch: `nikhil/foundation`

This is the dependency-ordered plan for the work that unblocks everything else. Vishaal's
backend work (`guardrails/`, `agents/`, `sequencer/`, `ml/`, `api/`) sits downstream of it.

---

## Context

The repo was a complete scaffold with **zero implementation** — every source file 0 bytes,
including `CONTRACTS.md`. Three things blocked parallel work:

1. **`CONTRACTS.md` was empty** but declared frozen and referenced by every rule file. Nobody
   could write an API handler or a mock fixture without it.
2. **No data existed.** The four-ledger model is the keystone (`docs/Project_context.md` §2) and
   nothing downstream — detector, guardrails, agents, sequencer, ML — can run against nothing.
3. **No screen rendered**, so no contract had ever been validated by a real consumer.

The order below writes those in dependency order, so the contract is proven by a real producer
(seed + ledger) and a real consumer (Command Center) rather than guessed at.

### The riskiest assumption, stated up front

**That the legal-state matrix and the synthetic generator agree.** If they are authored
separately they will drift, and the detector will either flag all 10k rows or none of them.

The mitigation is structural and is the spine of this plan: **`scripts/seed.py` imports the
lifecycles from `matrix.py` and walks them** rather than restating them. Breaks are produced by
deliberately corrupting a walk — never by hand-writing a state tuple. The two cannot diverge
because there is only one definition.

### Second flag — the waterfall needs two tables the four ledgers don't provide

The six-gate waterfall (`docs/Project_context.md` §1) cannot be derived from the four ledgers
alone. Gate **B4** (settled to bank) and gate **B6** (statutory credits) have no source in
payment/order/inventory/accounting. The schema therefore adds `checkout_sessions` (for B1) and
`settlements` (for B4). Without them the bottom half of Command Center's headline chart would be
fabricated — which is precisely the credibility claim that screen exists to make.

---

## Ownership change

`CLAUDE.md`'s table assigned all of `backend/` and `scripts/` to Vishaal, and `CONTRACTS.md` to
"Both, together." Both were amended:

| Area | Owner | Branch |
|---|---|---|
| `CONTRACTS.md` | Nikhil | `nikhil/foundation` |
| `backend/ledgers/`, `backend/cases/`, `backend/db/`, `scripts/seed.py` | Nikhil — foundation | `nikhil/foundation` |
| `frontend/` | Nikhil | `nikhil/frontend` |
| `backend/guardrails/`, `agents/`, `sequencer/`, `ml/`, `api/`, `anomaly/`, `explain/`, `webhooks/` | Vishaal | `vishaal/backend` |
| rest of `scripts/` | Vishaal | `vishaal/backend` |
| `README.md` | Both, together | — |

`CONTRACTS.md` has a single owner deliberately. Invariant 1 ("the contract is frozen") only
works if exactly one person can change a shape.

---

## Phase 0 — Ownership + branch · **done**

Branch `nikhil/foundation` cut from `main`; `CLAUDE.md` ownership table rewritten as above.

Land this first so Vishaal can start on `guardrails/` — pure Python with zero dependency on
data or contract — while the rest is in flight.

## Phase 1 — `backend/ledgers/states.py` · **done**

Four `str, Enum` ledgers so they serialise straight to JSON and compare as strings:

```
PaymentState      INITIATED PENDING AUTHORIZED CAPTURED FAILED REFUNDED
OrderState        MISSING CREATED CONFIRMED FULFILLED CANCELLED
InventoryState    NOT_APPLICABLE AVAILABLE RESERVED SHIPPED RETURNED
AccountingState   NOT_BOOKED DEFERRED REVENUE_RECOGNIZED REVERSED
```

Plus `BusinessType`, `MandateState`, `FailureReasonCode`, `AFA_THRESHOLD_INR = 15000.0`,
`MAX_ATTEMPTS_PER_CYCLE = 4`, and `NON_TERMINAL_PAYMENT_STATES`.

`NON_TERMINAL_PAYMENT_STATES` is defined **only here** and consumed by
`guardrails/blocks.py::refund_requires_terminal_payment`. It must not be redefined elsewhere.
SaaS rows use `InventoryState.NOT_APPLICABLE` — never `None`, never `""`.

## Phase 2 — `CONTRACTS.md` · **done**

Moved ahead of the seed so Vishaal is unblocked earlier. Six sections: conventions, enums, core
objects, REST, SSE, fixture map. Highlights:

- **Money**: float, rupees, `_inr` suffix. **Time**: ISO 8601 UTC with trailing `Z`.
- **`basis`** (`deterministic` | `modelled`) sits beside every rupee figure that could be
  either, and the two are never summed. Where a total is needed the contract returns *two*
  totals — `GET /api/cases` returns `totals_at_risk.deterministic_inr` and `.modelled_inr`.
- **`GET /api/summary`** returns four separate counters: `recovered_inr`,
  `awaiting_approval_inr`, `deterministic_at_risk_inr`, `modelled_at_risk_inr`.
- **Waterfall buckets carry `basis` per bucket** (B1–B6), plus chain invariants
  (`buckets[i].exiting_inr == buckets[i+1].entering_inr`) that are directly checkable and that
  the frontend may assume.
- **`priority_score`** is defined normatively — `rupees_at_risk_inr × urgency × confidence` over
  a 7-day deadline horizon — so backend and frontend sort identically and no LLM touches it.
- **SSE** defines all seven event types with a common `{case_id, seq, at}` envelope, `seq`
  ordering, and `done` always last including after `error`.

## Phase 3 — `backend/ledgers/matrix.py` — the keystone

Enumerate what is **legal**; treat everything else as a break. Legality is derived from healthy
lifecycles, not a 600-cell hand audit.

`LEGAL_ECOMMERCE` (~9 tuples) and `LEGAL_SAAS` (~9, inventory pinned to `NOT_APPLICABLE`),
expressed as ordered walks that `seed.py` imports and replays.

**Time is the fourth dimension.** A legal-but-only-briefly tuple needs an entry in **both**
`DWELL_LIMITS_SECONDS` and `DWELL_BREAKS`, or it can never break:

| Tuple | Dwell | Break on exceed |
|---|---|---|
| `INITIATED + MISSING` | 900s | `CHECKOUT_ABANDONED` |
| `PENDING + MISSING` | 1800s | `PAYMENT_PENDING_WEBHOOK_MISSING` ← **Scenario 2** |
| `AUTHORIZED + CREATED` | 3600s | `AUTHORIZED_NOT_CAPTURED` |
| `CAPTURED + CONFIRMED + NOT_BOOKED` | 3600s | `REVENUE_NOT_BOOKED` |
| `CAPTURED + CONFIRMED + DEFERRED` (e-comm) | 2 days | `FULFILMENT_STALLED` |
| `FAILED + CONFIRMED` (SaaS grace) | 4 days | `MANDATE_DEBIT_FAILED` |

`NAMED_BREAKS` covers the illegal tuples we can name — `ORPHAN_PAYMENT_NO_ORDER` (Scenario 1),
`REFUND_WITHOUT_CANCELLATION` and `REFUND_AFTER_SHIPMENT` (Scenario 3),
`PAYMENT_ON_CANCELLED_ORDER`.

```python
def classify_break(payment, order, inventory, accounting,
                   age_seconds, business_type) -> BreakType | None
```

Resolution order, exactly this:

1. In the legal set **and** within dwell (or no dwell limit) → `None`
2. In the legal set but dwell exceeded → `DWELL_BREAKS[tuple]`
3. In `NAMED_BREAKS` → that break type
4. **Anything else → `UNCLASSIFIED_BREAK`**

Step 4 is the open-world property — a demo point, not a fallback. `None` means healthy: not
`False`, not `""`.

**Never add a row to `LEGAL_*` to silence a false positive.** That is how this engine goes blind.

## Phase 4 — Schema + seed

### 4a. `backend/db/schema.sql` + `conn.py`

SQLite via stdlib `sqlite3`. **No pandas, no numpy in the seed path** — this host runs Python
3.14, where LightGBM/pandas wheels may not resolve. A stdlib-only seed means the foundation is
never blocked on Vishaal's ML environment.

| Table | Purpose |
|---|---|
| `customers` | id, name, email, phone, business_type, segment, bank |
| `_latent_traits` | `salary_day` per customer — **separate table by design** |
| `checkout_sessions` | feeds waterfall gate B1 |
| `payments` | state, amount_inr, method, failure_reason_code, utr, timestamps |
| `orders` | state, payment_id, amount_inr |
| `inventory_events` | state, sku, qty |
| `accounting_entries` | state, amount_inr, gst_rate, invoice_id, credit_note_id |
| `settlements` | gross / mdr / fees / net — feeds gate B4 |
| `mandates` | state, cap_inr, next_debit_at |
| `mandate_attempts` | cycle, attempt_no, slot_at, outcome |
| `cases` | persisted `RecoveryCase` |

`_latent_traits` is physically separate so `backend/ml/train.py` cannot accidentally join it.
The model must **rediscover** salary timing — feature importance putting
`days_since_predicted_salary` on top is a demo beat, and it is worthless if the column was
handed to the model.

### 4b. `scripts/seed.py`

10k orders, 2k subscriptions. Imports lifecycles from `matrix.py` and walks them.

- Healthy majority ~85%, walked to a terminal legal tuple.
- ~65% checkout abandonment, feeding gate B1.
- Labelled breaks, made by truncating or corrupting a walk: ~300 Scenario 1 orphan payments,
  ~150 Scenario 2 pending + webhook missing, ~120 Scenario 3 refund without cancellation, and
  **~40 deliberately unhandled tuples** that must surface as `UNCLASSIFIED_BREAK`.
- Mandates mixing ACTIVE with REVOKED / EXPIRED / above-cap, so `sequencer/router.py` has
  structurally unretryable cases to refuse.
- Failures clustered against each customer's latent `salary_day`.
- Amounts straddling **₹15,000** so both sides of the AFA threshold are represented.
- Fixed RNG seed — `demo_reset.sh` must reproduce identical data.

Ground-truth facts the generator respects (`docs/Project_context.md` §4): max 4 attempts per
cycle, non-peak execution windows, GST slabs 5/18/40%, TCS 0.5%. **No equalisation levy** — it
is abolished, and a rule for it would be a confident, permanent false positive.

### 4c. `backend/cases/model.py` + `detector.py`

`RecoveryCase` dataclass matching the contract, then a detector that scans joined ledger rows,
calls `classify_break`, and emits one case per break. `priority_score` computed here, in
deterministic code.

### 4d. Mock derivation

The same script dumps `frontend/src/mock/*.json` from the identical rows it wrote to SQLite.
Both demo paths carry real data, `VITE_USE_MOCK=true` stays truthful, and mock drift becomes
structurally impossible rather than something `/contract-check` has to catch.

## Phase 5 — Screen 1: Command Center

### 5a. Bootstrap the frontend

`package.json`, `vite.config.js`, `index.html`, `main.jsx`, `App.jsx`, `index.css` and both
config files are all 0 bytes — the app does not exist yet.

- **Pin Tailwind `3.4.x`.** The scaffold's `tailwind.config.js` + `postcss.config.js` pair is
  the v3 layout; v4 drops the config file and moves the PostCSS plugin to
  `@tailwindcss/postcss`. Not worth hackathon time.
- React + Vite + Tailwind + Recharts, nothing else. No state library, no component library, no
  animation library.
- `api.js` switches on `VITE_USE_MOCK` between `fetch` and the seeded fixtures.

### 5b. The screen

Command Center proves the problem exists *before* any product is shown. It contains **zero AI
features by design** — its only job is making an invisible gap visible.

- **`Waterfall.jsx`** — six gates, each labelled in rupees with its `basis`. Recharts has no
  native waterfall: build it as a stacked `BarChart` with a transparent lower segment and the
  visible segment as the loss. The one non-obvious piece; comment it.
- **`RecoveredCounter.jsx`** — `recovered_inr` (the only number here that proves the product
  *works* rather than merely observes), `awaiting_approval_inr`, and an at-risk card rendering
  deterministic and modelled as **two labelled figures, never added**.
- **`CaseCard.jsx` + feed** — sorted by `priority_score` descending. A prioritised queue, not a
  log; finance teams abandon tools that dump 4,000 findings on them.
- **`Nav.jsx`** — routes for all five screens; the four unbuilt ones render a clean placeholder,
  not a blank page.

### 5c. Empty and loading states

Every component needs both. A screen showing nothing mid-demo reads as broken even when it is
fine. This matters more than polish.

**Every number on screen is in rupees.** Never "4,231 anomalies detected."

---

## Verification

Run in order; do not move past a failing step.

1. **Matrix, before any data exists.** All three scenarios from §6 of the context doc, plus a
   healthy row, plus a deliberately unhandled tuple. If `UNCLASSIFIED_BREAK` stops firing on the
   unhandled case, the open-world property is broken and the demo claim is now false.

2. **Seed and inspect** — `python -m scripts.seed`, then via `sqlite3`: 10k orders / 2k subs,
   healthy:break ratio ≈ 85:15, every intended break type present at least once,
   `UNCLASSIFIED_BREAK` count non-zero.

3. **Detector agreement.** Zero cases on rows the generator walked as healthy; a case on every
   row it deliberately corrupted. A mismatch means matrix and generator have diverged — fix the
   matrix, never the label.

4. **Waterfall arithmetic.** Sum the six gate losses straight from SQLite and check the chain
   invariants reconcile against `gross_intended_inr` and `realised_inr`. Confirm no bucket mixes
   deterministic and modelled rupees into one figure.

5. **Determinism.** `demo_reset.sh` then re-seed reproduces identical data.

6. **Screen 1, both paths.** `cd frontend && VITE_USE_MOCK=true npm run dev` — six gates render,
   counters show rupees with the at-risk split intact, feed ordered by `priority_score`. Then
   repeat against the live backend and confirm the two render identically. That is the real
   proof the contract holds.

7. **Force the empty path.** Point the app at an empty result set. No screen renders blank.

8. **`/contract-check`** — backend, mocks and contract agree before handing off.

---

## Handoff to Vishaal

Once this lands he is unblocked, in demo-priority order: `guardrails/blocks.py`
(`refund_requires_terminal_payment` — the centrepiece), `agents/loop.py` plus the SSE stream,
Case Detail's live half, then `sequencer/` and `ml/`.

Two things to tell him explicitly:

- `NON_TERMINAL_PAYMENT_STATES` lives in `backend/ledgers/states.py`. Do not redefine it in
  `guardrails/`.
- `_latent_traits` is off-limits to `ml/train.py`. The salary signal must be rediscovered.
