-- Paygent — four-ledger store (layer 2) + the layer 3 -> layer 4 seam.
--
-- Conventions, matching CONTRACTS.md:
--   money      REAL, rupees, column suffixed _inr. No paise, no strings.
--   timestamps TEXT, ISO 8601 UTC with a trailing Z.
--   states     TEXT, matching the str Enums in backend/ledgers/states.py.
--
-- SQLite via stdlib sqlite3. No ORM, no migrations - demo_reset.sh drops and reseeds.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Run metadata. `reference_now` anchors every age calculation in the system.
-- The seed is generated relative to a fixed instant, so detectors must measure
-- dwell against that instant and not the wall clock -- otherwise every break
-- reclassifies itself overnight.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Customers
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customers (
    customer_id    TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL,
    phone          TEXT NOT NULL,
    business_type  TEXT NOT NULL,          -- ECOMMERCE | SAAS
    segment        TEXT NOT NULL,          -- NEW | REPEAT | VIP
    bank           TEXT NOT NULL,          -- issuing bank, drives decline behaviour
    city           TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

-- Ground truth the generator uses to SHAPE behaviour, never a model feature.
-- Physically separate so backend/ml/train.py cannot accidentally join it: the
-- scorer has to rediscover salary timing from observed outcomes, and feature
-- importance surfacing days_since_predicted_salary is only meaningful if the
-- answer was never handed over.
CREATE TABLE IF NOT EXISTS _latent_traits (
    customer_id            TEXT PRIMARY KEY REFERENCES customers(customer_id),
    salary_day             INTEGER NOT NULL,   -- 1-28, day of month wages land
    balance_volatility     REAL NOT NULL,      -- 0-1, likelihood of NSF off-cycle
    intrinsic_churn_risk   REAL NOT NULL       -- 0-1, latent propensity to lapse
);

-- ---------------------------------------------------------------------------
-- Ledger 0 (pre-ledger): checkout sessions. Feeds waterfall gate B1.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS checkout_sessions (
    session_id      TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    business_type   TEXT NOT NULL,
    cart_value_inr  REAL NOT NULL,
    item_count      INTEGER NOT NULL,
    device          TEXT NOT NULL,
    attempted       INTEGER NOT NULL,      -- 0 = abandoned before any payment attempt
    created_at      TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Ledger 1: payment
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS payments (
    payment_id           TEXT PRIMARY KEY,
    session_id           TEXT REFERENCES checkout_sessions(session_id),
    customer_id          TEXT NOT NULL REFERENCES customers(customer_id),
    mandate_id           TEXT,
    state                TEXT NOT NULL,     -- PaymentState
    amount_inr           REAL NOT NULL,
    method               TEXT NOT NULL,     -- UPI | CARD | NETBANKING | UPI_AUTOPAY
    failure_reason_code  TEXT,              -- FailureReasonCode, null unless FAILED
    utr                  TEXT,              -- bank reference, null until authorised
    webhook_received     INTEGER NOT NULL DEFAULT 1,   -- 0 simulates a dropped webhook
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments(customer_id);
CREATE INDEX IF NOT EXISTS idx_payments_state    ON payments(state);

-- ---------------------------------------------------------------------------
-- Ledger 2: order
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS orders (
    order_id       TEXT PRIMARY KEY,
    payment_id     TEXT REFERENCES payments(payment_id),
    session_id     TEXT REFERENCES checkout_sessions(session_id),
    customer_id    TEXT NOT NULL REFERENCES customers(customer_id),
    business_type  TEXT NOT NULL,
    state          TEXT NOT NULL,           -- OrderState
    amount_inr     REAL NOT NULL,
    discount_inr   REAL NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_payment ON orders(payment_id);

-- ---------------------------------------------------------------------------
-- Ledger 3: inventory. SaaS rows carry NOT_APPLICABLE.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inventory_events (
    inventory_id  TEXT PRIMARY KEY,
    order_id      TEXT REFERENCES orders(order_id),
    sku           TEXT,
    qty           INTEGER NOT NULL DEFAULT 0,
    state         TEXT NOT NULL,            -- InventoryState
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inventory_order ON inventory_events(order_id);

-- ---------------------------------------------------------------------------
-- Ledger 4: accounting
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS accounting_entries (
    entry_id        TEXT PRIMARY KEY,
    order_id        TEXT REFERENCES orders(order_id),
    state           TEXT NOT NULL,          -- AccountingState
    amount_inr      REAL NOT NULL,
    gst_rate        REAL NOT NULL,          -- GST 2.0: 0.05 | 0.18 | 0.40
    gst_amount_inr  REAL NOT NULL,
    tcs_inr         REAL NOT NULL DEFAULT 0,  -- marketplace TCS, 0.5%
    invoice_id      TEXT,
    credit_note_id  TEXT,
    credit_note_barred INTEGER NOT NULL DEFAULT 0,  -- past 30 Nov of following FY
    booked_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_accounting_order ON accounting_entries(order_id);

-- ---------------------------------------------------------------------------
-- Settlement. Feeds waterfall gate B4 - not derivable from the four ledgers.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS settlements (
    settlement_id  TEXT PRIMARY KEY,
    payment_id     TEXT NOT NULL REFERENCES payments(payment_id),
    gross_inr      REAL NOT NULL,
    mdr_inr        REAL NOT NULL DEFAULT 0,
    fees_inr       REAL NOT NULL DEFAULT 0,
    tax_inr        REAL NOT NULL DEFAULT 0,
    net_inr        REAL NOT NULL,
    expected_net_inr REAL NOT NULL,          -- what the fee schedule says it should be
    status         TEXT NOT NULL,            -- SETTLED | PENDING | ON_HOLD
    settled_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_settlements_payment ON settlements(payment_id);

-- ---------------------------------------------------------------------------
-- UPI Autopay mandates
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mandates (
    mandate_id        TEXT PRIMARY KEY,
    customer_id       TEXT NOT NULL REFERENCES customers(customer_id),
    state             TEXT NOT NULL,        -- MandateState
    cap_inr           REAL NOT NULL,
    debit_amount_inr  REAL NOT NULL,
    cycle             TEXT NOT NULL,        -- YYYY-MM
    next_debit_at     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    revoked_at        TEXT,
    expires_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_mandates_state ON mandates(state);

-- NPCI: max 4 attempts per cycle (1 original + 3 retries). An attempt is an
-- irreversible, scarce resource - which is why router.py refuses structurally
-- unretryable failures rather than spending one.
CREATE TABLE IF NOT EXISTS mandate_attempts (
    attempt_id           TEXT PRIMARY KEY,
    mandate_id           TEXT NOT NULL REFERENCES mandates(mandate_id),
    customer_id          TEXT NOT NULL REFERENCES customers(customer_id),
    cycle                TEXT NOT NULL,
    attempt_no           INTEGER NOT NULL,  -- 1 = original debit
    slot_at              TEXT NOT NULL,
    window               TEXT NOT NULL,     -- PEAK | NON_PEAK
    amount_inr           REAL NOT NULL,
    outcome              TEXT NOT NULL,     -- SUCCESS | FAILED
    failure_reason_code  TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_mandate ON mandate_attempts(mandate_id);

-- ---------------------------------------------------------------------------
-- The layer 3 -> layer 4 seam.
--
-- Nikhil's consistency matrix, anomaly detector and ML scorers write signals here.
-- Vishaal's Recovery Case Bus reads them and assembles RecoveryCase objects.
-- Writing through a table rather than a function call means either half can be
-- built and tested without the other existing yet.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS signals (
    signal_id          TEXT PRIMARY KEY,
    source             TEXT NOT NULL,       -- consistency_matrix | anomaly | ml_scorer
    break_type         TEXT NOT NULL,       -- BreakType
    business_type      TEXT NOT NULL,
    customer_id        TEXT NOT NULL REFERENCES customers(customer_id),
    session_id         TEXT,
    payment_id         TEXT,
    order_id           TEXT,
    mandate_id         TEXT,

    rupees_at_risk_inr REAL NOT NULL,
    basis              TEXT NOT NULL,       -- deterministic | modelled  (never blended)
    confidence         REAL NOT NULL,       -- 0.0 - 1.0

    -- ledger snapshot at detection, so layer 4 need not re-join the ledgers
    payment_state      TEXT,
    order_state        TEXT,
    inventory_state    TEXT,
    accounting_state   TEXT,
    age_seconds        INTEGER NOT NULL DEFAULT 0,

    evidence_json      TEXT NOT NULL DEFAULT '{}',
    detected_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_break ON signals(break_type);
CREATE INDEX IF NOT EXISTS idx_signals_basis ON signals(basis);

-- ---------------------------------------------------------------------------
-- Layer 4: the Recovery Case Bus.
--
-- One case per detected issue - but NOT one case per signal. High-value signals get
-- their own case; the long tail of a noisy break type rolls into a single aggregate
-- case carrying a count and a total. A queue of 5,000 findings is one a finance team
-- abandons.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cases (
    case_id             TEXT PRIMARY KEY,
    signal_id           TEXT REFERENCES signals(signal_id),   -- representative signal
    break_type          TEXT NOT NULL,
    status              TEXT NOT NULL,
    business_type       TEXT NOT NULL,
    title               TEXT NOT NULL,
    summary             TEXT NOT NULL,

    customer_id         TEXT,        -- null on an aggregate: many customers
    session_id          TEXT,
    payment_id          TEXT,
    order_id            TEXT,
    mandate_id          TEXT,

    rupees_at_risk_inr  REAL NOT NULL,
    basis               TEXT NOT NULL,     -- deterministic | modelled, never mixed
    confidence          REAL NOT NULL,

    deadline_at         TEXT,
    deadline_reason     TEXT,
    priority_score      REAL NOT NULL DEFAULT 0,

    resolver            TEXT,
    -- Set by backend/guardrails/engine.py (layer 6), never by the bus. A case that
    -- has not been through guardrails carries NULL, not a guessed default.
    tier                INTEGER,
    tier_label          TEXT,

    signal_count        INTEGER NOT NULL DEFAULT 1,
    is_aggregate        INTEGER NOT NULL DEFAULT 0,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_priority ON cases(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_cases_status   ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_resolver ON cases(resolver);

-- Which signals rolled into which case. Makes the bus idempotent (a signal already
-- linked is never re-cased) and keeps an aggregate case traceable to its members.
CREATE TABLE IF NOT EXISTS case_signals (
    case_id    TEXT NOT NULL REFERENCES cases(case_id),
    signal_id  TEXT NOT NULL REFERENCES signals(signal_id),
    PRIMARY KEY (case_id, signal_id)
);
CREATE INDEX IF NOT EXISTS idx_case_signals_signal ON case_signals(signal_id);
