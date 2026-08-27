---
paths:
  - "backend/guardrails/**"
---

# Guardrails

The safety story and the strongest 60 seconds of the demo. Treat it as such.

## Absolute rules

- **No LLM calls in this directory. Ever.** Not for classification, not for message text,
  not for a smarter edge case. Pure deterministic Python.
- **No network calls.** Guardrails must evaluate offline and instantly.
- Every guardrail returns a `GuardrailResult`. Never a bare bool. Never an exception for
  the normal blocking path.
- `blocking=True` means the action cannot proceed under any circumstance.
  `blocking=False` with `passed=False` downgrades the tier, it does not forbid the action.

## The one that matters most

`refund_requires_terminal_payment` is the demo centrepiece.

In India, a UPI merchant payment with no confirmation must receive a credit adjustment from
the acquirer within T+5. If the merchant refunds from its own balance inside that window and
the auto-reversal then fires, the merchant has paid out twice.

Its `message` field is read aloud on stage. Keep it plain English and keep the reason inside
it. It is not a log line, it is a sentence a judge reads.

## Adding a guardrail

1. Name it as an assertion of the safe state, not the failure.
   `no_duplicate_order_exists`, not `check_duplicates`.
2. The `message` must explain why, in a sentence a finance person understands.
3. Wire it into `engine.py` tier classification and into the case's `guardrail_checks`
   so it renders in the UI.
4. Never make it advisory. If it is worth writing, it is worth enforcing.
