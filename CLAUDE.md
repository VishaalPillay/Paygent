# Paygent

AI agents that detect and autonomously recover revenue leakage for Indian e-commerce and SaaS.
Hackathon build. Read this fully before your first edit.

<!-- Maintainers: keep this file under 200 lines. Detail belongs in .claude/rules/. -->

## Hard constraints

- **24-hour build window. Two developers.** Working beats elegant. Every time.
- Free tools only. No paid APIs, no cloud infra, no managed services.
- The deliverable is a **live demo**, not a production system.
- If a choice costs more than 30 minutes and does not appear on screen during the demo, it is the wrong choice.

## What this is

Every online transaction leaves four records: **payment, order, inventory, accounting**.
When all four agree, the transaction is healthy. **Every revenue leak is two of them disagreeing.**

We codified the legal state combinations. Anything else is a break. Each break becomes a
`RecoveryCase` carrying rupees, confidence and a deadline. Cases route to resolvers.
A deterministic guardrail layer decides whether anything is allowed to touch money.

**Four ledgers → one case object → three resolvers → one gate.**

## Layout

```
backend/ledgers/      four-ledger model + legal-state matrix      ← the core
backend/cases/        break detection → RecoveryCase emission
backend/guardrails/   tier classification + hard blocks on money  ← the safety story
backend/agents/       LLM tool-use loop: reconciliation, cart
backend/sequencer/    autopay retry — rules + ML, deliberately no LLM
backend/anomaly/      unusual discounts, refunds, transactions
backend/ml/           LightGBM scorers (retry success, churn)
backend/api/          FastAPI routes + SSE streaming
frontend/src/pages/   CommandCenter, CaseDetail, MandateBoard, Conversations
frontend/src/mock/    fixtures matching CONTRACTS.md
```

Detailed conventions per area live in `.claude/rules/` and load automatically when you
open files in that area. Do not duplicate them here.

## Invariants — never violate these

1. **`CONTRACTS.md` is frozen.** It defines every API shape. If you believe it must change,
   stop and say so out loud. Never silently alter a field name, type or enum value.
2. **Never blend `deterministic` and `modelled` amounts into one number.** Not in the API,
   not in the UI, not in a log line. They are different epistemic objects.
3. **All money is a float, in rupees, with an `_inr` suffix.** No paise. No strings. No `Decimal`.
4. **An LLM never picks a number that moves money.** It may propose. A deterministic layer decides.
5. **Guardrails contain no LLM calls.** `backend/guardrails/` is pure Python logic, forever.
6. **No new dependencies** beyond `requirements.txt` and `package.json` without asking first.
   One agreed exception: `framer-motion` in the frontend. CSS transitions cannot do spring
   physics, layout animation or exit animation, and the interface being visibly hand-crafted
   is a judged quality here. Nothing else gets added on that reasoning.
7. All timestamps are ISO 8601 UTC with a trailing `Z`.

## Do not build

Auth, login, user management, Docker, CI, deployment, tests, settings pages, onboarding flows,
mobile responsive, dark mode, database migrations, rate limiting, caching layers.

None of these appear in the demo. Building them is time stolen from what does.

## Ownership

Split by architecture layer (`docs/image.png`). Nikhil owns layers 1-3 (data + intelligence),
Vishaal owns layers 4-7 (orchestration + UI).

| Layer | Box | Area | Owner |
|---|---|---|---|
| 1 | Synthetic Generator, Razorpay Webhooks | `scripts/seed.py`, `backend/webhooks/` | Nikhil |
| 2 | Four-ledger Store | `backend/ledgers/`, `backend/db/` | Nikhil |
| 3 | Consistency Matrix | `backend/ledgers/matrix.py` | Nikhil |
| 3 | Anomaly Detection | `backend/anomaly/` | Nikhil |
| 3 | ML Scorers | `backend/ml/` | Nikhil |
| 4 | Recovery Case Bus | `backend/cases/` | Vishaal |
| 5 | Recovery Agents | `backend/agents/`, `backend/sequencer/` | Vishaal |
| 6 | Guardrail Engine | `backend/guardrails/` | Vishaal |
| 7 | React Dashboard | `backend/api/`, `backend/explain/` | Vishaal |
| 7 | Frontend UI | `frontend/` | Nikhil |
| — | `CONTRACTS.md` | Nikhil | |
| — | `README.md` | Both, together | |

Do not edit files outside your area. If you need a change there, say so and let the owner make it.

`CONTRACTS.md` has a single owner on purpose. Invariant 1 only works if exactly one person can
change a shape - if you need a field added or altered, raise it with Nikhil rather than editing.

**The layer 3 -> layer 4 seam is the interface between the two halves.** Nikhil's detectors and
scorers emit `Signal` objects; Vishaal's Case Bus consumes them and assembles `RecoveryCase`
objects. Both shapes are defined in `CONTRACTS.md`.

## Commands

```bash
./scripts/setup.sh          # deps + seed + train models
./scripts/run.sh            # backend :8000, frontend :5173
./scripts/demo_reset.sh     # wipe to clean demo state
python -m scripts.seed              # regenerate synthetic data with labelled breaks
python -m backend.ledgers.scanner   # consistency matrix -> signals
python -m backend.anomaly.detector  # rules + outliers -> signals
python -m backend.ml.train          # retrain LightGBM, writes to backend/ml/artifacts/

cd frontend && VITE_USE_MOCK=true npm run dev   # frontend with no backend
```

## How I want you to behave

**Challenge before you build.** Before implementing anything non-trivial, state in one or two
sentences the riskiest assumption in the approach. If you think the plan is wrong, say so plainly
and immediately. Do not implement around a design flaw silently and mention it afterwards.
A wrong turn caught in 30 seconds costs 30 seconds. Caught at hour 18 it costs the project.

**Be blunt.** Do not soften technical disagreement. Do not open with praise. If code I wrote is
broken, say "this is broken because X" and show the fix.

**Do not gold-plate.** If twenty lines work, stop at twenty lines. No abstraction layers for a
single caller. No config for a value that will never change. No premature generalisation.

**Ask before refactoring anything that already runs.** Working code is an asset with hours
sunk into it. Improving it is usually not worth the regression risk at this stage.

**Prefer editing over creating.** New files fragment a codebase two people are moving through fast.

**Say when you are unsure.** "I think this is right but I have not verified the Razorpay webhook
payload shape" is far more useful than confident wrong code.

## Demo priority

If time runs short, protect these in order. Cut from the bottom.

1. Case Detail + streaming reasoning trace + guardrail block  ← never cut
2. Command Center + waterfall chart
3. Storefront + live Razorpay test payment
4. Mandate Board + naive-vs-sequenced toggle
5. Conversations + offer policy panel   ← degrade to two scripted turns
6. Feature-importance chart             ← cut freely

## Reference

- `@CONTRACTS.md` — every API shape, frozen
- `README.md` — the pitch, the architecture, what is real vs simulated
