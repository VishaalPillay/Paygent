# Backend plan — guardrails, agents, sequencer, API

Owner: Vishaal · Branch: `vishaal/backend`

The bottom half of the architecture diagram: **Recovery Agents → Guardrail Engine → API**,
plus the sequencer and the offer-policy engine. Sits downstream of Nikhil's `docs/PLAN.md`.

---

## Context — the fact that shapes this plan

As of now the repo contains exactly three implemented things: `CONTRACTS.md`,
`docs/PLAN.md`, and `backend/ledgers/states.py`. **Every other file is 0 bytes** —
`matrix.py`, `backend/db/`, `scripts/seed.py`, `requirements.txt`, `.env.example`, and the
entire frontend. Nikhil's Phases 3–5 have not started.

So there is **no data, no schema, and no case object**, and there will not be for hours.
A plan that starts by reading from SQLite is a plan that idles.

Everything below is ordered so that the first ~6 hours of my work has **zero dependency on
Nikhil's output**, and the integration is one small, late, well-defined swap.

## The riskiest assumption, stated up front

**That I can build the whole bottom half against a fixture reader and swap in real SQLite in
half an hour.**

That holds only if Nikhil's `schema.sql` can answer the queries my agent tools need. If it
lands without, say, a way to search orders by phone + amount + timestamp window, the swap
is not 30 minutes — it is a schema change at hour 14, which is exactly the kind of wrong turn
CLAUDE.md warns about.

**Mitigation, and it is time-critical: send Nikhil the tool query list *before* he writes
`schema.sql`.** See "What Nikhil needs from me, today" below. This is the single
highest-leverage thing to do in the next 30 minutes.

## The decoupling move

Every module I write takes **plain dicts in the `CONTRACTS.md` shape**, never a DB cursor and
never Nikhil's `RecoveryCase` class. The only place that knows SQLite exists is one adapter:

```python
class LedgerReader(Protocol):        # backend/agents/tools.py
    def get_payment(self, payment_id: str) -> dict | None: ...
    def get_ledger_snapshot(self, payment_id: str) -> dict: ...
    def search_orders(self, **criteria) -> list[dict]: ...
    def get_cart_snapshot(self, session_id: str) -> dict | None: ...
    def get_mandate(self, mandate_id: str) -> dict | None: ...
    def list_cases(self, **filters) -> list[dict]: ...
```

Two implementations: `FixtureReader` (mine, today) and `SqliteReader` (30 min, once
`schema.sql` lands). Nothing else in my half changes at integration time.

Consequence worth stating plainly: **the API comes up and serves real-shaped JSON on day one**,
so Nikhil's frontend can point at a live backend hours before the seed exists. That is worth
more to him than anything else I could hand over early.

---

## Ownership amendments (Nikhil's call — CLAUDE.md and PLAN.md both need editing)

Confirmed change from the diagram split:

| Area | Was | Now |
|---|---|---|
| `backend/ml/` | Vishaal | **Nikhil** — he has the seed data, shortest path to a trained model |
| `backend/webhooks/` | Vishaal | **Nikhil** — Razorpay ingestion writes into his ledger store |

I still *consume* `backend/ml/artifacts/*.pkl` from `sequencer/scorer.py`. I do not train.
`backend/anomaly/` stays mine but is cut-first (see cut order).

Nikhil: `CLAUDE.md`'s ownership table and `docs/PLAN.md` "Ownership change" section are now
both stale on these two rows. Yours to edit.

---

## Contract gaps — raise before building on them

`CONTRACTS.md` is frozen and I am not editing it. Four things I cannot implement as written:

1. **The offer ladder has no enum.** `POST /api/chat` returns `requested_rung` /
   `granted_rung` with values `TIER_3_20_PCT` and `TIER_1_FREE_SHIPPING`, but section 1
   (Enums) never defines the rung set. I need the full ladder, named, before writing the
   policy engine.
2. **`window` (`PEAK` | `NON_PEAK`) is used in `/plan` but is not in section 1.** Minor, but
   the frontend has to switch on it.
3. **Nothing can approve a tier-1 action.** `GET /api/summary` returns
   `awaiting_approval_inr` and `ActionStatus` has `APPROVED` and `EXECUTED`, but there is no
   endpoint that moves an action from `PROPOSED` to either. Demo priority #1 shows a case
   held for approval and then nothing can act on it. Needs a `POST /api/actions/{id}/approve`
   or equivalent.
4. **No `Conversation` object in section 2**, only the `POST /api/chat` response. Fine if
   chat is stateless server-side; I will assume it is and keep conversation state in the
   request.

Items 1 and 3 block Phases 5 and 3 respectively. The rest I can work around.

---

## Phases

Time budget assumes ~18h of build left. Totals ~12h, leaving slack for integration and
demo rehearsal. Ordered by CLAUDE.md's demo priority, not by architectural tidiness.

### Phase 0 — Unblock both of us · 20 min · **do first**

- Cut `vishaal/backend` from `main`.
- **Write `requirements.txt`.** It is empty and unowned, which means invariant 6 ("no new
  dependencies") currently forbids everything. Mine: `fastapi`, `uvicorn[standard]`,
  `google-genai`, `python-dotenv`. Nikhil appends `lightgbm` / `scikit-learn` / whatever the
  seed needs. SSE is hand-rolled on `StreamingResponse` — no `sse-starlette`, no extra dep.
- **Write `.env.example`**: `GEMINI_API_KEY`, `DEMO_MODE=live|replay`, `PAYGENT_DB`,
  `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`.
- Note for the record: this host is **Python 3.13.4**, not 3.14. PLAN.md 4a's concern about
  LightGBM/pandas wheels not resolving is moot — cp313 wheels exist. The stdlib-only seed is
  still the right call, just not for that reason.

### Phase 1 — `backend/guardrails/` · 1h30 · zero dependencies, demo centrepiece

Pure Python, imports only `states.py`. Buildable right now, and it is the strongest 60
seconds of the demo.

- `guardrails/__init__.py` — the `GuardrailResult` dataclass, and **nothing else**. No
  imports of `blocks`/`engine` from here, so there is no import cycle. Nikhil: import it
  from `backend.guardrails`, do not redefine it in `cases/model.py`.
- `guardrails/blocks.py` — each function named as an assertion of the *safe* state:

  | guardrail | blocking | why |
  |---|---|---|
  | `refund_requires_terminal_payment` | yes | **the centrepiece.** T+5 acquirer auto-reversal — refunding now risks paying twice |
  | `no_duplicate_order_exists` | yes | Scenario 1; creating a second order double-fulfils |
  | `order_not_already_shipped` | yes | never auto-cancel a shipped order |
  | `credit_note_within_gst_window` | yes | GST bars credit notes after 30 Nov of the following FY |
  | `mandate_is_active` | yes | revoked/expired is structurally unretryable |
  | `amount_within_mandate_cap` | yes | over-cap debit cannot succeed |
  | `attempts_remain_in_cycle` | yes | NPCI 4/cycle is a hard ceiling |
  | `execution_in_non_peak_window` | yes | NPCI requires non-peak |
  | `debit_below_afa_threshold` | **no** | above ₹15,000 needs OTP each cycle — downgrades tier, does not forbid |
  | `discount_within_margin_floor` | yes | the Conversations denial beat |

  Every `message` is a sentence a finance person reads aloud, with the reason inside it.
- `guardrails/engine.py` — `evaluate(action, context) -> (checks, tier, tier_label)`.
  Any blocking failure forces tier 2 regardless of confidence. Otherwise confidence and
  amount decide 0/1.

**Verify before moving on:** all three scenarios from Project_context section 6 by hand, plus
a healthy row. Scenario 2 must come back `blocking=True` on
`refund_requires_terminal_payment`.

### Phase 2 — `backend/agents/` + the trace · 2h30 · demo priority #1

- `agents/llm.py` — Gemini wrapper (`google-genai`, AI Studio key, free tier, native
  function-calling). One interface: `complete(messages, tools) -> text | tool_call`. Provider
  swap is env-only. `DEMO_MODE=replay` short-circuits the whole client.
- `agents/tools.py` — the `LedgerReader` protocol above, `FixtureReader`, and the read-mostly
  tools. **Tool results are one short dict plus a one-sentence summary** — 400 lines of JSON
  burns context and produces worse reasoning. The one mutating tool, `propose_action`, routes
  through `guardrails/engine.py` rather than checking anything itself.
- `agents/loop.py` — hand-rolled, ~150 lines, capped at ~6 iterations. Yields event dicts
  with the `{case_id, seq, at}` envelope: `thinking` `tool_call` `tool_result` `guardrail`
  `conclusion` `done` `error`. **Yields as it goes — never accumulates and returns.** `done`
  is always last, including after `error`. No agent framework.
- `agents/reconciliation.py` — system prompt + tool set for the three scenarios. The prompt
  never asks the model for an amount, a percentage, or an approval. It proposes; guardrails
  decide.

### Phase 3 — `backend/api/` + `backend/main.py` · 1h30 · makes it visible

- `main.py` — FastAPI app, CORS for `:5173`, routers mounted, `.env` loaded, scorer artifact
  loaded once at startup. Ownership of this file was never assigned; taking it, since it is
  the entrypoint for `api/`.
- `api/stream.py` — `GET /api/cases/{id}/stream`, `StreamingResponse`, **~850ms between
  events**, `done` always last. `DEMO_MODE=replay` serves `frontend/src/mock/trace.json` at
  identical pacing. That path stays working — it is the fallback if Gemini rate-limits on
  stage.
- `api/cases.py` — list with all seven filters and the three sorts; detail with 404
  `CASE_NOT_FOUND`. `totals_at_risk` is **two figures, never summed**.
- `api/summary.py` — four separate counters, six waterfall buckets B1–B6, chain invariants
  asserted in code rather than assumed.
- `api/mandates.py`, `api/chat.py` — thin, backed by Phases 4 and 5.
- Error envelope; `ACTION_BLOCKED_BY_GUARDRAIL` is a **409 carrying `blocked_by`**, never a
  500.

Running against `FixtureReader`, this is a live backend Nikhil's frontend can hit today.

### Phase 4 — `backend/sequencer/` · 1h30 · demo priority #4

No LLM here, deliberately. Three attempts across N slots is enumerable — that is a solver,
not a prompt, and being able to say why is itself a pitch point.

1. `router.py` — pure rules on `FailureReasonCode`. `MANDATE_REVOKED`, `MANDATE_EXPIRED`,
   `AMOUNT_EXCEEDS_MANDATE_CAP` — **never spend an attempt.** Returns
   `{retryable, reason, checks}` where `checks` are real `GuardrailResult`s.
2. `scorer.py` — loads Nikhil's `backend/ml/artifacts/retry_success.pkl` if present.
   **If absent, falls back to a ~25-line deterministic heuristic** (salary proximity,
   non-peak, reason code, attempt number, bank). Both paths label output `modelled`.
   Without this fallback, demo priority #4 is hard-blocked on Nikhil's ML — with it, it is
   not.
3. `solver.py` — enumerate eligible non-peak slots, place at most 3 attempts to maximise
   expected recovery under `MAX_ATTEMPTS_PER_CYCLE`. Naive plan is fixed +24h. `uplift_inr`
   is the difference, `modelled` on both sides.

For a revoked mandate: `sequenced.attempts: []`, `expected_recovery_inr: 0.0`. Zero scheduled
retries is the demo beat, not an empty state.

### Phase 5 — cart agent + offer policy · 1h30 · demo priority #5

**Blocked on contract gap 1** (the rung enum). Everything else is ready.

- `agents/cart.py` — conversational loop reusing `loop.py`, with serviceability and inventory
  tools. The agent **requests** a rung; it never computes a rupee figure.
- The policy engine lives in `guardrails/` (`discount_within_margin_floor`), not in the agent.
  `granted_rung` is authoritative; the requested one is displayed only to make the denial
  visible.
- Degrade path per CLAUDE.md: two scripted turns if time runs short.

### Phase 6 — scripts + narrator · 1h · partly cuttable

- `scripts/run.sh`, `scripts/demo_reset.sh` — needed for the demo. `scripts/setup.sh` less
  so.
- `backend/explain/narrator.py` — plain-English case summaries. Cuttable.
- `backend/anomaly/detector.py` — needs seed data and sklearn. **Cut first.** It is not on
  the demo priority list at all.

### Phase 7 — Integration · 30 min · when `schema.sql` lands

Write `SqliteReader` against the `LedgerReader` protocol, flip one constructor in `main.py`,
run `/contract-check`. If the query list below was sent in time, this is the whole job.

---

## What Nikhil needs from me, today — send within the hour

1. **The exact queries my agent tools issue**, so `schema.sql` can serve them. Scenario 1's
   investigation path is `search_orders` by receipt/notes, then phone + email, then
   amount + timestamp window, then session/device id. If those are not indexable, my tools do
   not work.
2. `GuardrailResult` lives in `backend/guardrails/__init__.py`. Import it in `cases/model.py`;
   do not define a second one.
3. The four contract gaps above. Items 1 and 3 block me.
4. `requirements.txt` now exists — append ML deps rather than replacing it.
5. `ml/` and `webhooks/` moving to him means `CLAUDE.md` + `PLAN.md` need amending.

## Cut order, worst case

Cut from the bottom, in this order: `anomaly/` → `explain/narrator.py` → `scripts/setup.sh`
→ Phase 5 down to two scripted turns → Phase 4 down to router-only (drop the solver, keep
the refusal). **Phases 1, 2 and 3 are never cut** — they are demo priority #1.
