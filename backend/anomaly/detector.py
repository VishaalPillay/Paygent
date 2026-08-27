"""Anomaly detection — layer 3.

A different question from the consistency matrix. That engine asks *do the four
ledgers disagree in a way the legal set forbids* — a closed, deterministic question.
This one asks *is this transaction strange even though every ledger is individually
legal* — an open, statistical one.

Three kinds of finding, and the `basis` differs by kind:

  1. Business rules      — arithmetic on known fee schedules and statute.
                           `deterministic`: the number is a fact, not an estimate.
  2. Statistical outliers — IQR / z-score over the population.
                           `modelled`: the threshold is a choice, not a law.
  3. Pattern analysis     — IsolationForest over multivariate behaviour.
                           `modelled`.

(3) requires scikit-learn. It is imported lazily and skipped with a warning if the
package is absent, so the rest of the detector still runs on a bare interpreter.

Run:  python -m backend.anomaly.detector
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone

from ..db import conn as db
from ..ledgers.states import BreakType

# A settlement under-paying by more than this against its own fee schedule is not
# rounding — it is a short-pay worth chasing.
SHORT_PAY_TOLERANCE_INR = 1.0
# Outlier sensitivity. 1.5 x IQR is the conventional Tukey fence.
IQR_MULTIPLIER = 1.5
MIN_ORDERS_FOR_REFUND_PATTERN = 4


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SignalWriter:
    """Appends to the `signals` table — the layer 3 -> layer 4 seam."""

    def __init__(self, conn, now: datetime):
        self.conn = conn
        self.now = now
        # Counter is local to this source and its ids are prefixed, so re-running
        # any detector in any order can never collide with another one's rows.
        self.n = 0
        self.tally: dict[str, int] = {}
        self.skipped_zero = 0

    def emit(self, *, break_type: BreakType, business_type: str, customer_id: str,
             rupees_at_risk_inr: float, basis: str, confidence: float,
             evidence: dict, session_id=None, payment_id=None, order_id=None,
             mandate_id=None) -> None:
        # A finding worth nothing is noise in the case queue. Drop it here rather
        # than making layer 4 filter it out.
        if round(float(rupees_at_risk_inr), 2) <= 0:
            self.skipped_zero += 1
            return
        self.n += 1
        self.conn.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"sig_an_{self.n:06d}", "anomaly", break_type.value, business_type,
             customer_id, session_id, payment_id, order_id, mandate_id,
             round(float(rupees_at_risk_inr), 2), basis, round(float(confidence), 2),
             None, None, None, None, 0, json.dumps(evidence), _iso(self.now)))
        self.tally[break_type.value] = self.tally.get(break_type.value, 0) + 1


# ---------------------------------------------------------------------------
# 1. Business rules — deterministic
# ---------------------------------------------------------------------------

def detect_short_paid_settlements(conn, w: SignalWriter) -> None:
    """Settled net below what this payment's own fee schedule says it should be."""
    for r in conn.execute(
        """SELECT s.*, p.customer_id, p.method, c.business_type
           FROM settlements s
           JOIN payments p  ON p.payment_id = s.payment_id
           JOIN customers c ON c.customer_id = p.customer_id
           WHERE s.status = 'SETTLED'
             AND (s.expected_net_inr - s.net_inr) > ?""",
        (SHORT_PAY_TOLERANCE_INR,),
    ):
        shortfall = r["expected_net_inr"] - r["net_inr"]
        w.emit(
            break_type=BreakType.SETTLEMENT_SHORT_PAID,
            business_type=r["business_type"], customer_id=r["customer_id"],
            payment_id=r["payment_id"],
            rupees_at_risk_inr=shortfall,
            basis="deterministic",   # arithmetic against a published fee schedule
            confidence=1.0,
            evidence={"gross_inr": r["gross_inr"], "mdr_inr": r["mdr_inr"],
                      "fees_inr": r["fees_inr"], "tax_inr": r["tax_inr"],
                      "expected_net_inr": r["expected_net_inr"],
                      "actual_net_inr": r["net_inr"],
                      "shortfall_inr": round(shortfall, 2),
                      "method": r["method"]})


def detect_unclaimed_statutory_credits(conn, w: SignalWriter) -> None:
    """Reversed revenue whose GST was never reclaimed.

    Indian GST bars a credit note after 30 November of the following financial year.
    Past that date the tax is permanently unrecoverable — a fact, so `deterministic`.
    Inside the window it is still claimable, so the exposure is `modelled`.
    """
    for r in conn.execute(
        """SELECT a.*, o.customer_id, o.business_type
           FROM accounting_entries a
           JOIN orders o ON o.order_id = a.order_id
           WHERE a.state = 'REVERSED' AND a.credit_note_id IS NULL"""
    ):
        barred = bool(r["credit_note_barred"])
        w.emit(
            break_type=BreakType.STATUTORY_CREDIT_UNCLAIMED,
            business_type=r["business_type"], customer_id=r["customer_id"],
            order_id=r["order_id"],
            rupees_at_risk_inr=r["gst_amount_inr"] + r["tcs_inr"],
            basis="deterministic" if barred else "modelled",
            confidence=1.0 if barred else 0.55,
            evidence={"gst_rate": r["gst_rate"], "gst_amount_inr": r["gst_amount_inr"],
                      "tcs_inr": r["tcs_inr"], "credit_note_barred": barred,
                      "rule": ("credit note barred after 30 Nov of the following FY"
                               if barred else "still inside the credit-note window")})


# ---------------------------------------------------------------------------
# 2. Statistical outliers — modelled
# ---------------------------------------------------------------------------

def _tukey_upper_fence(values: list[float]) -> float | None:
    if len(values) < 8:
        return None
    vs = sorted(values)
    q1 = statistics.quantiles(vs, n=4)[0]
    q3 = statistics.quantiles(vs, n=4)[2]
    return q3 + IQR_MULTIPLIER * (q3 - q1)


def detect_unusual_discounts(conn, w: SignalWriter) -> None:
    """Discount rates beyond the upper Tukey fence for their business type."""
    rows = [dict(r) for r in conn.execute(
        """SELECT order_id, customer_id, business_type, amount_inr, discount_inr
           FROM orders WHERE amount_inr > 0 AND discount_inr > 0""")]
    if not rows:
        return
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        r["rate"] = r["discount_inr"] / r["amount_inr"]
        by_type.setdefault(r["business_type"], []).append(r)

    for business_type, group in by_type.items():
        fence = _tukey_upper_fence([r["rate"] for r in group])
        if fence is None:
            continue
        for r in group:
            if r["rate"] <= fence:
                continue
            excess = (r["rate"] - fence) * r["amount_inr"]
            w.emit(
                break_type=BreakType.UNUSUAL_DISCOUNT,
                business_type=business_type, customer_id=r["customer_id"],
                order_id=r["order_id"],
                rupees_at_risk_inr=excess,
                basis="modelled",       # the fence is a chosen threshold, not a law
                confidence=round(min(0.95, 0.5 + (r["rate"] - fence) * 2), 2),
                evidence={"discount_rate": round(r["rate"], 4),
                          "cohort_upper_fence": round(fence, 4),
                          "discount_inr": r["discount_inr"],
                          "order_value_inr": r["amount_inr"],
                          "method": "tukey_iqr_fence"})


def detect_unusual_refund_patterns(conn, w: SignalWriter) -> None:
    """Customers refunding far more often than their cohort."""
    rows = [dict(r) for r in conn.execute(
        """SELECT p.customer_id, c.business_type,
                  COUNT(*) AS total,
                  SUM(CASE WHEN p.state='REFUNDED' THEN 1 ELSE 0 END) AS refunded,
                  SUM(CASE WHEN p.state='REFUNDED' THEN p.amount_inr ELSE 0 END) AS refunded_inr
           FROM payments p JOIN customers c ON c.customer_id = p.customer_id
           GROUP BY p.customer_id HAVING total >= ?""",
        (MIN_ORDERS_FOR_REFUND_PATTERN,))]
    if not rows:
        return
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        r["rate"] = r["refunded"] / r["total"]
        by_type.setdefault(r["business_type"], []).append(r)

    for business_type, group in by_type.items():
        fence = _tukey_upper_fence([r["rate"] for r in group])
        if fence is None or fence <= 0:
            continue
        for r in group:
            if r["rate"] <= fence or r["refunded"] == 0:
                continue
            w.emit(
                break_type=BreakType.UNUSUAL_REFUND_PATTERN,
                business_type=business_type, customer_id=r["customer_id"],
                rupees_at_risk_inr=r["refunded_inr"],
                basis="modelled",
                confidence=round(min(0.9, 0.45 + (r["rate"] - fence)), 2),
                evidence={"refund_rate": round(r["rate"], 3),
                          "cohort_upper_fence": round(fence, 3),
                          "refunded_count": r["refunded"], "total_payments": r["total"],
                          "method": "tukey_iqr_fence"})


# ---------------------------------------------------------------------------
# 3. Pattern analysis — IsolationForest, optional
# ---------------------------------------------------------------------------

def detect_multivariate_outliers(conn, w: SignalWriter) -> int:
    """IsolationForest over per-transaction behaviour. Skipped if sklearn is absent."""
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        print("  [skip] pattern analysis needs scikit-learn — "
              "run: .venv/bin/pip install -r requirements.txt")
        return 0

    rows = [dict(r) for r in conn.execute(
        """SELECT o.order_id, o.customer_id, o.business_type, o.amount_inr,
                  o.discount_inr, p.amount_inr AS paid_inr,
                  COALESCE(a.gst_rate, 0) AS gst_rate
           FROM orders o
           JOIN payments p ON p.payment_id = o.payment_id
           LEFT JOIN accounting_entries a ON a.order_id = o.order_id""")]
    if len(rows) < 50:
        return 0

    X = [[r["amount_inr"], r["discount_inr"],
          r["discount_inr"] / r["amount_inr"] if r["amount_inr"] else 0.0,
          r["paid_inr"] - r["amount_inr"], r["gst_rate"]] for r in rows]
    model = IsolationForest(n_estimators=100, contamination=0.01, random_state=20260827)
    preds = model.fit_predict(X)
    scores = model.score_samples(X)

    n = 0
    for r, pred, score in zip(rows, preds, scores):
        if pred != -1:
            continue
        n += 1
        w.emit(
            break_type=BreakType.ANOMALOUS_TRANSACTION_PATTERN,
            business_type=r["business_type"], customer_id=r["customer_id"],
            order_id=r["order_id"],
            rupees_at_risk_inr=abs(r["paid_inr"] - r["amount_inr"]) or r["discount_inr"],
            basis="modelled",
            confidence=round(min(0.9, abs(float(score))), 2),
            evidence={"isolation_score": round(float(score), 4),
                      "method": "isolation_forest",
                      "order_value_inr": r["amount_inr"],
                      "discount_inr": r["discount_inr"]})
    return n


# ---------------------------------------------------------------------------

def run(conn, now: datetime | None = None) -> dict[str, int]:
    now = now or db.reference_now(conn)
    w = SignalWriter(conn, now)
    detect_short_paid_settlements(conn, w)
    detect_unclaimed_statutory_credits(conn, w)
    detect_unusual_discounts(conn, w)
    detect_unusual_refund_patterns(conn, w)
    detect_multivariate_outliers(conn, w)
    conn.commit()
    return w.tally


def main() -> None:
    conn = db.connect()
    db.clear_signals(conn, "anomaly")

    tally = run(conn, now=db.reference_now(conn))
    print(f"\n  {'anomaly type':<32} {'signals':>8}")
    print(f"  {'-' * 32} {'-' * 8}")
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {k:<32} {v:>8,}")
    print(f"  {'-' * 32} {'-' * 8}")
    print(f"  {'TOTAL':<32} {sum(tally.values()):>8,}")

    print("\n  by basis — deterministic and modelled are never summed together")
    for r in conn.execute(
        """SELECT basis, COUNT(*) n, SUM(rupees_at_risk_inr) s
           FROM signals WHERE source='anomaly' GROUP BY basis"""):
        print(f"    {r['basis']:<16} {r['n']:>6,} signals   Rs {r['s']:>14,.2f}")
    conn.close()


if __name__ == "__main__":
    main()
