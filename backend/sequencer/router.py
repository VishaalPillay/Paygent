"""Should we retry at all? Pure rules on the failure reason code — no LLM, no scoring.

Three attempts across N slots is enumerable; deciding whether to spend one at all is
even simpler: a structurally dead mandate (revoked, expired, over cap) fails by rule
every time, so router.py rejects it before `scorer.py` ever runs. Reuses the guardrails
already built in `backend/guardrails/blocks.py` rather than re-deriving the same checks
under a different name — `mandate_is_active` and `amount_within_mandate_cap` are exactly
this decision.
"""

from __future__ import annotations

from ..guardrails.blocks import (
    amount_within_mandate_cap,
    attempts_remain_in_cycle,
    mandate_is_active,
)


def route(mandate: dict, attempts_used_this_cycle: int) -> dict:
    """Returns {"retryable": bool, "reason": str, "checks": [GuardrailResult, ...]}.

    Any blocking failure here means the failure is structural, not a timing issue —
    `scorer.py` must never see this attempt (see .claude/rules/sequencer.md: the model
    is trained only on router-eligible attempts and scores a structurally-doomed one
    nonsensically).
    """
    checks = [
        mandate_is_active(mandate),
        amount_within_mandate_cap(mandate["debit_amount_inr"], mandate),
        attempts_remain_in_cycle(attempts_used_this_cycle),
    ]
    blocking_failure = next((c for c in checks if not c.passed and c.blocking), None)
    reason = (
        blocking_failure.message if blocking_failure
        else "Failure looks like a timing issue, not a structural one — eligible for retry."
    )
    return {
        "retryable": blocking_failure is None,
        "reason": reason,
        "checks": [c.to_dict() for c in checks],
    }
