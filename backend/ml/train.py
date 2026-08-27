"""ML scorers — layer 3.

Two LightGBM models, both trained from the same SQLite database the ledgers live in:

  1. retry_success  P(debit succeeds | customer, amount, slot, reason, bank)
                    consumed by backend/sequencer/scorer.py to rank retry slots.
  2. churn          P(subscription lapses) -> revenue at risk, always `modelled`.

**The salary signal is rediscovered, never handed over.** `scripts/seed.py` gives each
customer a latent `salary_day` and clusters debit failures around it. That column lives
in `_latent_traits`, and nothing here joins that table. Instead we estimate each
customer's pay day from *observed outcomes* — the day of month their debits actually
succeed — and feed `days_since_predicted_salary` as a derived feature. The model then
ranks it near the top of its own accord.

At the end we compare the estimate against `_latent_traits` purely to report how close
it got. That is a measurement, not a feature: it happens after training and never
touches the matrix.

LightGBM and IsolationForest only. No deep learning, no embeddings, no fine-tuning.
Total training budget under two minutes.

Run:  python -m backend.ml.train
"""

from __future__ import annotations

import json
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from ..db import conn as db
from ..ledgers.states import AFA_THRESHOLD_INR, BreakType

ARTIFACTS = Path(__file__).parent / "artifacts"
SEED = 20260827
CHURN_SIGNAL_THRESHOLD = 0.45      # emit a signal above this predicted probability
MONTHS_AT_RISK = 12                # annualise the exposure of a lapsing subscription


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Rediscovering the pay cycle from observed behaviour
# ---------------------------------------------------------------------------

def estimate_salary_days(attempts: pd.DataFrame) -> pd.Series:
    """Estimate each customer's pay day from when their debits actually succeed.

    The generator makes failure probability rise roughly linearly with the number of
    days since wages landed. So we invert that: for every candidate pay day s, measure
    how strongly `(day - s) mod 30` correlates with failure, and take the s where that
    correlation is strongest. An argmax over per-day success rate would be far worse —
    with ~10 attempts per customer, the single best day is mostly noise, and a linear
    ramp has no single spike to find.

    Reads only `mandate_attempts`. `_latent_traits` is never joined.
    """
    df = attempts.copy()
    df["day"] = df["slot_at"].dt.day.to_numpy()
    df["fail"] = (df["outcome"] != "SUCCESS").astype(float)

    candidates = np.arange(1, 31)
    out: dict[str, int] = {}
    population_fallback = int(df["day"].mode().iloc[0]) if len(df) else 1

    for customer_id, g in df.groupby("customer_id", sort=False):
        fail = g["fail"].to_numpy()
        # Needs both outcomes present, or correlation is undefined.
        if len(fail) < 4 or fail.std() == 0:
            out[customer_id] = population_fallback
            continue
        day = g["day"].to_numpy()
        gaps = (day[None, :] - candidates[:, None]) % 30       # (30, n_attempts)
        gaps = gaps - gaps.mean(axis=1, keepdims=True)
        denom = np.sqrt((gaps ** 2).sum(axis=1)) * np.sqrt(((fail - fail.mean()) ** 2).sum())
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = np.where(denom > 0, (gaps @ (fail - fail.mean())) / denom, 0.0)
        out[customer_id] = int(candidates[int(np.argmax(corr))])

    return pd.Series(out, name="predicted_salary_day")


def cyclic_gap(day: pd.Series, salary_day: pd.Series) -> pd.Series:
    """Days elapsed since the pay day, wrapping around the month."""
    return (day - salary_day) % 30


# ---------------------------------------------------------------------------
# Model 1 — retry success
# ---------------------------------------------------------------------------

RETRY_FEATURES = [
    "days_since_predicted_salary",
    "amount_inr",
    "attempt_no",
    "is_non_peak",
    "hour",
    "day_of_month",
    "requires_afa",
    "amount_to_cap_ratio",
    "prior_failures_this_cycle",
    "bank_code",
    "segment_code",
]


def build_retry_dataset(conn) -> tuple[pd.DataFrame, pd.Series]:
    # Train only on attempts the router would actually have allowed. A mandate
    # above its cap, revoked, or expired fails by rule every time — router.py
    # rejects those deterministically and the scorer never sees them. Leaving them
    # in teaches the model a rule it is not supposed to own, and it dominates the
    # split purely because it is a perfect separator.
    attempts = pd.read_sql_query(
        """SELECT a.*, m.cap_inr, c.bank, c.segment
           FROM mandate_attempts a
           JOIN mandates  m ON m.mandate_id  = a.mandate_id
           JOIN customers c ON c.customer_id = a.customer_id
           WHERE m.debit_amount_inr <= m.cap_inr
             AND (a.failure_reason_code IS NULL
                  OR a.failure_reason_code NOT IN
                     ('AMOUNT_EXCEEDS_MANDATE_CAP','MANDATE_REVOKED','MANDATE_EXPIRED'))""",
        conn, parse_dates=["slot_at"])

    salary = estimate_salary_days(attempts)
    attempts["predicted_salary_day"] = attempts["customer_id"].map(salary)

    attempts["day_of_month"] = attempts["slot_at"].dt.day
    attempts["hour"] = attempts["slot_at"].dt.hour
    attempts["days_since_predicted_salary"] = cyclic_gap(
        attempts["day_of_month"], attempts["predicted_salary_day"])
    attempts["is_non_peak"] = (attempts["window"] == "NON_PEAK").astype(int)
    attempts["requires_afa"] = (attempts["amount_inr"] > AFA_THRESHOLD_INR).astype(int)
    attempts["amount_to_cap_ratio"] = attempts["amount_inr"] / attempts["cap_inr"]
    attempts["prior_failures_this_cycle"] = attempts["attempt_no"] - 1
    attempts["bank_code"] = attempts["bank"].astype("category").cat.codes
    attempts["segment_code"] = attempts["segment"].astype("category").cat.codes

    y = (attempts["outcome"] == "SUCCESS").astype(int)
    return attempts[RETRY_FEATURES], y


# ---------------------------------------------------------------------------
# Model 2 — churn
# ---------------------------------------------------------------------------

CHURN_FEATURES = [
    "debit_amount_inr",
    "requires_afa",
    "amount_to_cap_ratio",
    "tenure_days",
    "total_attempts",
    "failed_attempts",
    "failure_rate",
    "consecutive_recent_failures",
    "refund_count",
    "bank_code",
    "segment_code",
]


def build_churn_dataset(conn) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    df = pd.read_sql_query(
        """SELECT m.mandate_id, m.customer_id, m.state, m.cap_inr,
                  m.debit_amount_inr, m.created_at,
                  c.bank, c.segment, c.business_type,
                  (SELECT COUNT(*) FROM mandate_attempts a
                    WHERE a.mandate_id = m.mandate_id) AS total_attempts,
                  (SELECT COUNT(*) FROM mandate_attempts a
                    WHERE a.mandate_id = m.mandate_id AND a.outcome='FAILED')
                    AS failed_attempts,
                  (SELECT COUNT(*) FROM payments p
                    WHERE p.customer_id = m.customer_id AND p.state='REFUNDED')
                    AS refund_count
           FROM mandates m
           JOIN customers c ON c.customer_id = m.customer_id""",
        conn, parse_dates=["created_at"])

    now = pd.Timestamp(db.reference_now(conn))
    df["tenure_days"] = (now - df["created_at"]).dt.days
    df["requires_afa"] = (df["debit_amount_inr"] > AFA_THRESHOLD_INR).astype(int)
    df["amount_to_cap_ratio"] = df["debit_amount_inr"] / df["cap_inr"]
    df["failure_rate"] = df["failed_attempts"] / df["total_attempts"].clip(lower=1)
    df["consecutive_recent_failures"] = df["failed_attempts"].clip(upper=4)
    df["bank_code"] = df["bank"].astype("category").cat.codes
    df["segment_code"] = df["segment"].astype("category").cat.codes

    # Label: the mandate is gone. Mandate state is the target, so it is never a feature.
    y = df["state"].isin(["REVOKED", "EXPIRED"]).astype(int)
    return df[CHURN_FEATURES], y, df


# ---------------------------------------------------------------------------

def fit(name: str, X: pd.DataFrame, y: pd.Series) -> tuple[lgb.LGBMClassifier, float]:
    Xtr, Xte, ytr, yte = train_test_split(
        X, y, test_size=0.25, random_state=SEED, stratify=y)
    model = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, num_leaves=31,
        random_state=SEED, verbose=-1,
        # Gain, not split count. The default counts how often a feature is split
        # on, which flatters high-cardinality continuous features regardless of
        # how much they actually explain.
        importance_type="gain")
    model.fit(Xtr, ytr)
    auc = roc_auc_score(yte, model.predict_proba(Xte)[:, 1])
    print(f"\n  {name}: {len(Xtr):,} train / {len(Xte):,} held out"
          f"   positive rate {y.mean():.1%}")
    print(f"  held-out AUC {auc:.3f}")
    imp = sorted(zip(X.columns, model.feature_importances_), key=lambda t: -t[1])
    total = sum(s for _, s in imp) or 1.0
    print("  feature importance (by gain)")
    for feat, score in imp[:6]:
        share = score / total
        print(f"    {feat:<30} {share:>6.1%}  {'#' * int(40 * score / imp[0][1])}")
    return model, auc


def emit_churn_signals(conn, model, X, meta, now) -> int:
    probs = model.predict_proba(X)[:, 1]
    n = 0          # local counter; ids are prefixed per source
    emitted = 0
    for prob, (_, row) in zip(probs, meta.iterrows()):
        if prob < CHURN_SIGNAL_THRESHOLD or row["state"] in ("REVOKED", "EXPIRED"):
            continue
        n += 1
        emitted += 1
        conn.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"sig_ml_{n:06d}", "ml_scorer", BreakType.SUBSCRIPTION_CHURN_RISK.value,
             row["business_type"], row["customer_id"], None, None, None,
             row["mandate_id"],
             # Exposure is an estimate, so it is modelled and never blended with
             # a deterministic figure downstream.
             round(float(row["debit_amount_inr"]) * MONTHS_AT_RISK * float(prob), 2),
             "modelled", round(float(prob), 2),
             None, None, None, None, 0,
             json.dumps({"predicted_churn_probability": round(float(prob), 3),
                         "months_at_risk": MONTHS_AT_RISK,
                         "failure_rate": round(float(row["failure_rate"]), 3),
                         "failed_attempts": int(row["failed_attempts"]),
                         "model": "lightgbm_churn"}),
             _iso(now)))
    conn.commit()
    return emitted


def report_salary_rediscovery(conn) -> None:
    """Measurement only — run after training, never fed back as a feature."""
    attempts = pd.read_sql_query(
        "SELECT customer_id, slot_at, outcome FROM mandate_attempts",
        conn, parse_dates=["slot_at"])
    predicted = estimate_salary_days(attempts)
    truth = pd.read_sql_query(
        "SELECT customer_id, salary_day FROM _latent_traits", conn
    ).set_index("customer_id")["salary_day"]

    joined = pd.DataFrame({"pred": predicted}).join(truth, how="inner").dropna()
    gap = (joined["pred"] - joined["salary_day"]).abs()
    gap = np.minimum(gap, 30 - gap)          # circular distance across the month
    print(f"\n  salary-day rediscovery over {len(joined):,} customers")
    print(f"    exact match          {(gap == 0).mean():>6.1%}")
    print(f"    within 3 days        {(gap <= 3).mean():>6.1%}")
    print(f"    within 7 days        {(gap <= 7).mean():>6.1%}")
    print(f"    median error         {gap.median():>6.1f} days")
    print("    (the model was never given salary_day — _latent_traits is not joined)")


def main() -> None:
    t0 = time.time()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    db.clear_signals(conn, "ml_scorer")
    now = db.reference_now(conn)

    Xr, yr = build_retry_dataset(conn)
    retry_model, retry_auc = fit("retry_success", Xr, yr)

    Xc, yc, meta = build_churn_dataset(conn)
    churn_model, churn_auc = fit("churn", Xc, yc)

    for name, model, feats, auc in (
        ("retry_success", retry_model, RETRY_FEATURES, retry_auc),
        ("churn", churn_model, CHURN_FEATURES, churn_auc),
    ):
        with open(ARTIFACTS / f"{name}.pkl", "wb") as fh:
            pickle.dump({"model": model, "features": feats, "auc": auc,
                         "trained_at": _iso(now), "seed": SEED}, fh)

    emitted = emit_churn_signals(conn, churn_model, Xc, meta, now)
    print(f"\n  emitted {emitted:,} churn signals (basis: modelled)")

    report_salary_rediscovery(conn)
    print(f"\n  artifacts -> {ARTIFACTS}")
    print(f"  total training time {time.time() - t0:.1f}s")
    conn.close()


if __name__ == "__main__":
    main()
