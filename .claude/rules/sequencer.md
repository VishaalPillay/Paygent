---
paths:
  - "backend/sequencer/**"
  - "backend/ml/**"
---

# Sequencer and ML

## The sequencer is deliberately not an agent

Three retry attempts across N eligible slots is an enumerable decision space. That is a solver,
not a prompt. Do not add an LLM here. Being able to explain why we didn't is a pitch point.

Three layers, in order:

1. `router.py` — **should we retry at all?** Pure rules on the failure reason code.
2. `scorer.py` — **when?** LightGBM `P(success | customer, amount, slot, reason, bank)`.
3. `solver.py` — **place the attempts.** Constrained selection over eligible slots.

## The NPCI constraints are hard limits, not preferences

- **Max 4 attempts per mandate cycle: 1 original + 3 retries.** After that the cycle is dead.
- Executions must be scheduled in **non-peak windows**. Avoid morning peak entirely.
- A 45–60 second cooldown applies before checking status after initiating.

An attempt is an irreversible, scarce resource. `router.py` exists so we never spend one on a
structurally unretryable failure — revoked mandate, expired mandate, amount above the mandate
cap. Highest-value logic in the feature and the easiest to demo.

## ML rules

- LightGBM and IsolationForest only. **No deep learning, no fine-tuning, no embeddings.**
- Total training budget under two minutes. If a model takes longer, simplify it.
- Train in `train.py`, pickle to `artifacts/`, load once at startup. Never train per request.
- Validate on a held-out seed and report real AUC. Data is synthetic; the metric must be honest.
- The generator injects a latent `salary_day` per customer. The model must rediscover it, not
  be handed it. Feature importance showing `days_since_predicted_salary` on top is a demo beat.
