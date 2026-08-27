"""Tier classification: decides whether a proposed action executes, waits for a human,
or is investigate-only.

A blocking guardrail failure forces tier 2 regardless of confidence. Otherwise confidence
and amount decide between tier 0 (auto) and tier 1 (approve). No LLM involved anywhere in
this module.
"""

from backend.guardrails import GuardrailResult

TIER_LABELS = {
    0: "AUTO",
    1: "APPROVE",
    2: "INVESTIGATE_ONLY",
}

# Below this confidence, even an unblocked action waits for a human rather than firing
# on its own. Above the amount ceiling, same thing - size alone earns a human look.
AUTO_CONFIDENCE_FLOOR = 0.85
AUTO_AMOUNT_CEILING_INR = 5000.0


def evaluate(
    checks: list[GuardrailResult],
    confidence: float,
    amount_inr: float | None,
) -> tuple[list[GuardrailResult], int, str]:
    """Run tier classification over a set of already-evaluated guardrail checks.

    checks: every GuardrailResult relevant to this action, blocking and non-blocking alike.
    confidence: the case's confidence score, 0.0-1.0.
    amount_inr: the rupee amount this action would move, or None if it moves no money.

    Returns (checks, tier, tier_label) so the caller can attach all three to the case
    and its guardrail_checks list without recomputing anything.
    """
    any_blocking_failure = any(not c.passed and c.blocking for c in checks)

    if any_blocking_failure:
        tier = 2
    elif amount_inr is not None and (
        confidence < AUTO_CONFIDENCE_FLOOR or amount_inr > AUTO_AMOUNT_CEILING_INR
    ):
        tier = 1
    elif amount_inr is None:
        # No money moves: never worse than tier 1, and only tier 0 if every non-blocking
        # check also passed.
        tier = 0 if all(c.passed for c in checks) else 1
    else:
        tier = 0

    return checks, tier, TIER_LABELS[tier]
