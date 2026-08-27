"""Place the attempts. Constrained selection over eligible non-peak slots — a solver,
not a prompt, because the decision space is fully enumerable (see
.claude/rules/sequencer.md). Produces both the `naive` baseline and the `sequenced`
plan CONTRACTS.md's Mandate Board toggle compares.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..ledgers.states import MAX_ATTEMPTS_PER_CYCLE
from . import scorer

# Morning-through-early-afternoon rush — NPCI requires avoiding it entirely.
PEAK_HOURS = set(range(6, 14))
# Candidate hours to evaluate per day, spanning afternoon, evening and late night.
CANDIDATE_HOURS = [15, 18, 20, 22, 1, 3]
SEARCH_DAYS = 14


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(hour: int) -> str:
    return "PEAK" if hour in PEAK_HOURS else "NON_PEAK"


def _candidate_slots(start: datetime, days: int) -> list[datetime]:
    slots = []
    for d in range(days):
        day = start + timedelta(days=d)
        for h in CANDIDATE_HOURS:
            slots.append(day.replace(hour=h, minute=30, second=0, microsecond=0))
    return sorted(s for s in slots if s > start)


def _empty_plan(strategy: str) -> dict:
    return {"strategy": strategy, "attempts": [], "expected_recovery_inr": 0.0, "basis": "modelled"}


def naive_plan(conn, mandate: dict, customer: dict, attempts_used: int, now: datetime) -> dict:
    """The baseline this feature beats: fixed +24h retry, no slot selection."""
    attempts_remaining = max(MAX_ATTEMPTS_PER_CYCLE - attempts_used, 0)
    if attempts_remaining == 0:
        return _empty_plan("NAIVE")

    slot_at = now + timedelta(hours=24)
    window = _window(slot_at.hour)
    attempt_no = attempts_used + 1
    prob, basis = scorer.score_slot(conn, mandate, customer, slot_at, window, attempt_no)

    return {
        "strategy": "NAIVE",
        "attempts": [{
            "attempt_no": attempt_no, "slot_at": _iso(slot_at), "window": window,
            "predicted_success": prob, "reason": "Fixed +24h retry, no slot selection",
        }],
        "expected_recovery_inr": round(mandate["debit_amount_inr"] * prob, 2),
        "basis": basis,
    }


def sequenced_plan(conn, mandate: dict, customer: dict, attempts_used: int, now: datetime) -> dict:
    """Rank every eligible non-peak slot over the next two weeks by predicted success,
    take the best one per day, up to the attempts remaining this cycle."""
    attempts_remaining = max(MAX_ATTEMPTS_PER_CYCLE - attempts_used, 0)
    if attempts_remaining == 0:
        return _empty_plan("SEQUENCED")

    candidates = [s for s in _candidate_slots(now, SEARCH_DAYS) if _window(s.hour) == "NON_PEAK"]
    scored = [
        (slot_at, *scorer.score_slot(conn, mandate, customer, slot_at, "NON_PEAK", attempts_used + 1))
        for slot_at in candidates
    ]
    scored.sort(key=lambda t: -t[1])

    chosen: list[tuple[datetime, float, str]] = []
    used_days: set = set()
    for slot_at, prob, basis in scored:
        if slot_at.date() in used_days:
            continue
        chosen.append((slot_at, prob, basis))
        used_days.add(slot_at.date())
        if len(chosen) == attempts_remaining:
            break
    chosen.sort(key=lambda t: t[0])  # present in chronological order

    attempts = []
    expected_recovery = 0.0
    surviving_prob = 1.0  # each later attempt only pays off if every earlier one failed
    basis = "modelled"
    for i, (slot_at, prob, b) in enumerate(chosen, start=1):
        basis = b
        attempts.append({
            "attempt_no": attempts_used + i, "slot_at": _iso(slot_at), "window": "NON_PEAK",
            "predicted_success": prob,
            "reason": "Highest predicted success among non-peak slots this cycle",
        })
        expected_recovery += mandate["debit_amount_inr"] * prob * surviving_prob
        surviving_prob *= (1 - prob)

    return {
        "strategy": "SEQUENCED", "attempts": attempts,
        "expected_recovery_inr": round(expected_recovery, 2), "basis": basis,
    }
