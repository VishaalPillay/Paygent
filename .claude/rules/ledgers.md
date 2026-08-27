---
paths:
  - "backend/ledgers/**"
  - "backend/cases/**"
---

# The four-ledger core

The keystone of the product. Everything downstream reads from it. Move carefully.

## The design, and why it is this way

We do **not** enumerate every way a transaction can break. That list is unbounded.
We enumerate every combination that is **legal**, and treat everything else as a break.

This is the whole trick. It is why the engine catches failures nobody explicitly coded for,
and why `UNCLASSIFIED_BREAK` is a feature we demo, not a fallback we apologise for.

When adding a row to `LEGAL_ECOMMERCE` or `LEGAL_SAAS` you must be able to justify why that
combination is genuinely healthy. Adding a row to silence a false positive is how this engine
goes blind.

## Time is the fourth dimension

Some breaks are not a state, they are a state that has lasted too long.

`PAYMENT: PENDING + ORDER: MISSING` is healthy for a customer mid-checkout. It becomes
`PAYMENT_PENDING_WEBHOOK_MISSING` only after exceeding its dwell limit.

Any state pair that is legal-but-only-briefly needs an entry in both `DWELL_LIMITS_SECONDS`
and `DWELL_BREAKS`. A legal state with no dwell limit can never break.

## Conventions

- States are `str, Enum` so they serialise straight to JSON and compare as strings.
- `NON_TERMINAL_PAYMENT_STATES` is consumed by `backend/guardrails/blocks.py`. Do not
  redefine that set anywhere else.
- SaaS uses `InventoryState.NOT_APPLICABLE`. Never `None` or an empty string.
- `classify_break` returns `None` for healthy. Not `False`, not `""`.

## Checking a change here

Run all three demo scenarios, plus a healthy row, plus one deliberately unhandled state.
If `UNCLASSIFIED_BREAK` stops firing for the unhandled case, the open-world property is
broken and our demo claim is now false.
