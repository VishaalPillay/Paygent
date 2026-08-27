"""When should we retry? P(success | customer, amount, slot, reason, bank).

Loads `backend/ml/artifacts/retry_success.pkl` once, at import. If it hasn't been
trained yet (`python -m backend.ml.train` not run), falls back to a small deterministic
heuristic rather than hard-blocking this whole feature on Nikhil's ML pipeline having
already run — both paths label their output `modelled`, since neither is a fact read
off a ledger row.

Reuses `estimate_salary_days`/`cyclic_gap` from `backend/ml/train.py` rather than
re-deriving the same salary-rediscovery logic under a different name.
"""

from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..ledgers.states import AFA_THRESHOLD_INR
from ..ml.train import cyclic_gap, estimate_salary_days

ARTIFACT_PATH = Path(__file__).resolve().parents[1] / "ml" / "artifacts" / "retry_success.pkl"

# Same eligibility filter build_retry_dataset() trains on — reproduced here (not
# imported, since train.py doesn't expose it as a function) only to derive an
# identical bank/segment category-code mapping. The model's `bank_code`/`segment_code`
# were assigned by pandas over exactly this query's result set; scoring against a
# different universe of values would silently shift every code.
_ELIGIBLE_ATTEMPTS_SQL = """
    SELECT a.customer_id, a.slot_at, a.outcome, c.bank, c.segment
    FROM mandate_attempts a
    JOIN mandates  m ON m.mandate_id  = a.mandate_id
    JOIN customers c ON c.customer_id = a.customer_id
    WHERE m.debit_amount_inr <= m.cap_inr
      AND (a.failure_reason_code IS NULL
           OR a.failure_reason_code NOT IN
              ('AMOUNT_EXCEEDS_MANDATE_CAP', 'MANDATE_REVOKED', 'MANDATE_EXPIRED'))
"""

_artifact: dict | None = None
_salary_days: dict[str, int] | None = None
_bank_codes: dict[str, int] | None = None
_segment_codes: dict[str, int] | None = None
_loaded = False


def _load(conn) -> None:
    """Lazy, once-per-process. Never re-trains, never re-reads per request."""
    global _artifact, _salary_days, _bank_codes, _segment_codes, _loaded
    if _loaded:
        return
    _loaded = True

    if ARTIFACT_PATH.exists():
        with open(ARTIFACT_PATH, "rb") as f:
            _artifact = pickle.load(f)

    df = pd.read_sql_query(_ELIGIBLE_ATTEMPTS_SQL, conn, parse_dates=["slot_at"])
    _salary_days = estimate_salary_days(df).to_dict()
    _bank_codes = {v: i for i, v in enumerate(df["bank"].astype("category").cat.categories)}
    _segment_codes = {v: i for i, v in enumerate(df["segment"].astype("category").cat.categories)}


def _heuristic_score(
    days_since_salary: float, is_non_peak: int, prior_failures: int, amount_to_cap_ratio: float
) -> float:
    """Used only when retry_success.pkl hasn't been trained yet."""
    score = 0.75
    score -= min(days_since_salary, 25) / 25 * 0.35  # further from payday, less likely
    score += 0.05 if is_non_peak else -0.05
    score -= 0.08 * prior_failures
    score -= 0.15 if amount_to_cap_ratio > 0.9 else 0.0
    return max(0.02, min(0.95, score))


def _feature_row(mandate: dict, customer: dict, slot_at: datetime, window: str, attempt_no: int) -> dict:
    salary_day = _salary_days.get(mandate["customer_id"], 15)  # mid-month fallback
    days_since_salary = float(cyclic_gap(slot_at.day, salary_day))
    return {
        "days_since_predicted_salary": days_since_salary,
        "amount_inr": mandate["debit_amount_inr"],
        "attempt_no": attempt_no,
        "is_non_peak": 1 if window == "NON_PEAK" else 0,
        "hour": slot_at.hour,
        "day_of_month": slot_at.day,
        "requires_afa": 1 if mandate["debit_amount_inr"] > AFA_THRESHOLD_INR else 0,
        "amount_to_cap_ratio": mandate["debit_amount_inr"] / mandate["cap_inr"],
        "prior_failures_this_cycle": attempt_no - 1,
        "bank_code": _bank_codes.get(customer["bank"], -1),
        "segment_code": _segment_codes.get(customer["segment"], -1),
    }


def score_slot(
    conn, mandate: dict, customer: dict, slot_at: datetime, window: str, attempt_no: int,
) -> tuple[float, str]:
    """P(this attempt succeeds) at this slot. Always basis 'modelled' — a scorer's
    output is never presented as a fact, regardless of which path produced it.

    For scoring many mandates at once (e.g. the Mandate Board list), use
    `score_slots_batch` instead — LightGBM's per-call overhead dominates at that
    volume; ~2000 individual calls costs seconds, one batched call costs milliseconds.
    """
    return score_slots_batch(conn, [(mandate, customer, slot_at, window, attempt_no)])[0]


def score_slots_batch(
    conn, rows: list[tuple[dict, dict, datetime, str, int]],
) -> list[tuple[float, str]]:
    """Same as `score_slot`, batched. One DataFrame, one `predict_proba` call."""
    if not rows:
        return []
    _load(conn)

    features = [_feature_row(m, c, s, w, a) for m, c, s, w, a in rows]

    if _artifact is not None:
        df = pd.DataFrame(features)[_artifact["features"]]
        probs = _artifact["model"].predict_proba(df)[:, 1]
        return [(round(float(p), 4), "modelled") for p in probs]

    results = []
    for f in features:
        prob = _heuristic_score(
            f["days_since_predicted_salary"], f["is_non_peak"],
            f["prior_failures_this_cycle"], f["amount_to_cap_ratio"])
        results.append((round(prob, 4), "modelled"))
    return results
