"""Export frontend fixtures straight from the seeded database.

`VITE_USE_MOCK=true` has to carry the whole demo if the backend dies on stage, so
the fixtures cannot be hand-written guesses — they are dumped from the same rows the
API would serve. Mock drift stops being something /contract-check has to catch and
becomes structurally impossible.

Shapes follow CONTRACTS.md exactly.

Run:  python -m scripts.export_mocks
"""

from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

from backend.db import conn as db
from backend.ledgers.states import AFA_THRESHOLD_INR, MAX_ATTEMPTS_PER_CYCLE
from backend.sequencer import router as seq_router
from backend.sequencer import solver

ENGINE_BY_BREAK = {
    "SETTLEMENT_SHORT_PAID": "anomaly", "STATUTORY_CREDIT_UNCLAIMED": "anomaly",
    "UNUSUAL_DISCOUNT": "anomaly", "UNUSUAL_REFUND_PATTERN": "anomaly",
    "ANOMALOUS_TRANSACTION_PATTERN": "anomaly",
    "SUBSCRIPTION_CHURN_RISK": "ml_scorer",
}  # everything else -> consistency_matrix

OUT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "mock"


def q1(c, sql, *a):
    r = c.execute(sql, a).fetchone()
    return (r[0] or 0) if r else 0


def build_waterfall(c) -> dict:
    """The six gates from docs/Project_context.md §1, read off the ledgers.

    Each bucket declares its own basis. B4 and B6 are modelled because a pending
    settlement and an unclaimed statutory credit are both estimates, not reads.
    """
    gross = (q1(c, "SELECT SUM(cart_value_inr) FROM checkout_sessions")
             + q1(c, "SELECT SUM(debit_amount_inr) FROM mandates"))
    gates = [
        ("B1", "Checkout initiated → Payment attempted", "deterministic",
         q1(c, "SELECT SUM(cart_value_inr) FROM checkout_sessions WHERE attempted=0"),
         q1(c, "SELECT COUNT(*) FROM checkout_sessions WHERE attempted=0"),
         "Carts with no payment attempt"),
        ("B2", "Payment attempted → Payment authorised", "deterministic",
         q1(c, "SELECT SUM(amount_inr) FROM payments WHERE state='FAILED'"),
         q1(c, "SELECT COUNT(*) FROM payments WHERE state='FAILED'"),
         "Declines, technical and business"),
        ("B3", "Payment authorised → Order recognised", "deterministic",
         q1(c, """SELECT SUM(p.amount_inr) FROM payments p
                  LEFT JOIN orders o ON o.payment_id=p.payment_id
                  WHERE p.state='CAPTURED' AND o.order_id IS NULL"""),
         q1(c, """SELECT COUNT(*) FROM payments p
                  LEFT JOIN orders o ON o.payment_id=p.payment_id
                  WHERE p.state='CAPTURED' AND o.order_id IS NULL"""),
         "Money captured against no order"),
        ("B4", "Order recognised → Settled to bank", "modelled",
         q1(c, "SELECT SUM(gross_inr-net_inr) FROM settlements WHERE status='SETTLED'")
         + q1(c, "SELECT SUM(gross_inr) FROM settlements WHERE status<>'SETTLED'"),
         q1(c, "SELECT COUNT(*) FROM settlements WHERE status<>'SETTLED'"),
         "Fees, holds and short-pay"),
        ("B5", "Settled to bank → Net of reversals", "deterministic",
         q1(c, "SELECT SUM(amount_inr) FROM payments WHERE state='REFUNDED'"),
         q1(c, "SELECT COUNT(*) FROM payments WHERE state='REFUNDED'"),
         "Refunds and chargebacks"),
        ("B6", "Net of reversals → Realised", "modelled",
         q1(c, """SELECT SUM(gst_amount_inr+tcs_inr) FROM accounting_entries
                  WHERE state='REVERSED' AND credit_note_id IS NULL"""),
         q1(c, """SELECT COUNT(*) FROM accounting_entries
                  WHERE state='REVERSED' AND credit_note_id IS NULL"""),
         "Statutory credits never reclaimed"),
    ]
    buckets, running = [], gross
    for gid, stage, basis, leak, count, detail in gates:
        entering = round(running, 2)
        running -= leak
        buckets.append({
            "id": gid, "stage": stage, "basis": basis,
            "entering_inr": entering, "leaked_inr": round(leak, 2),
            "exiting_inr": round(running, 2),
            "case_count": int(count), "detail": detail,
        })
    return {"gross_intended_inr": round(gross, 2),
            "realised_inr": round(running, 2), "buckets": buckets}


def case_row(r) -> dict:
    payload = json.loads(r["payload_json"] or "{}")
    return {
        "case_id": r["case_id"], "break_type": r["break_type"], "status": r["status"],
        "business_type": r["business_type"], "title": r["title"], "summary": r["summary"],
        "customer_id": r["customer_id"], "session_id": r["session_id"],
        "payment_id": r["payment_id"], "order_id": r["order_id"],
        "mandate_id": r["mandate_id"],
        "rupees_at_risk_inr": r["rupees_at_risk_inr"], "basis": r["basis"],
        "confidence": r["confidence"],
        "deadline_at": r["deadline_at"], "deadline_reason": r["deadline_reason"],
        "priority_score": r["priority_score"], "resolver": r["resolver"],
        "tier": r["tier"], "tier_label": r["tier_label"],
        "signal_count": r["signal_count"], "is_aggregate": bool(r["is_aggregate"]),
        "ledger_snapshot": payload.get("ledger_snapshot"),
        "guardrail_checks": payload.get("guardrail_checks", []),
        "actions": payload.get("actions", []),
        "evidence": payload.get("evidence", {}),
        # Matches backend/api/cases.py::_case_to_dict exactly: whether a case CAN
        # be investigated live, derived from its resolver — not whether it HAS
        # been (payload only ever gets this key once a trace has actually run,
        # via api/stream.py's _persist_conclusion, so reading it here made every
        # never-yet-investigated case mock as trace_available=false).
        "trace_available": r["resolver"] == "RECONCILIATION",
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


# The board carries 4 hand-picked mandates, not a scan-proof wall of 40. Each one
# is a distinct persona/scenario, not a quota bucket — a judge should be able to
# read every row. Age is a demo-only display label: the schema has no such column,
# it's cosmetic scaffolding the same way seeded customer names are.
PERSONAS = [
    ("young_adult",     22, "m.state='ACTIVE' AND m.debit_amount_inr <= m.cap_inr "
                            f"AND m.debit_amount_inr <= {AFA_THRESHOLD_INR}"),
    ("middle_age",      41, "m.state='ACTIVE' AND m.debit_amount_inr <= m.cap_inr "
                            f"AND m.debit_amount_inr > {AFA_THRESHOLD_INR}"),
    ("senior_revoked",  67, "m.state='REVOKED'"),
    ("senior_over_cap", 60, "m.state='ACTIVE' AND m.debit_amount_inr > m.cap_inr"),
]


def _attempts_used(c) -> dict[str, tuple[str, int]]:
    """Attempts spent in each mandate's most recent cycle that actually has any.

    Counting the current calendar month gives zero for every mandate — the seeded
    history sits in earlier cycles — which would show all four attempts free and
    make a *retry* plan incoherent, since a retry implies the original already
    failed.
    """
    out: dict[str, tuple[str, int]] = {}
    for r in c.execute("""SELECT mandate_id, cycle, COUNT(*) n FROM mandate_attempts
                          GROUP BY mandate_id, cycle ORDER BY cycle"""):
        out[r["mandate_id"]] = (r["cycle"], r["n"])   # later cycle overwrites earlier
    return out


def _last_failure(c) -> dict[str, str]:
    return {r[0]: r[1] for r in c.execute(
        """SELECT mandate_id, failure_reason_code, MAX(slot_at) FROM mandate_attempts
           WHERE outcome='FAILED' GROUP BY mandate_id""")}


def _salary_days(c) -> dict[str, int]:
    """The signal the model rediscovered. Read through the same helper the scorer
    uses so the calendar marks the day the model actually believes in."""
    import pandas as pd
    from backend.ml.train import estimate_salary_days
    df = pd.read_sql_query(
        """SELECT a.customer_id, a.slot_at, a.outcome FROM mandate_attempts a""",
        c, parse_dates=["slot_at"])
    return {k: int(v) for k, v in estimate_salary_days(df).to_dict().items()}


def _model_card() -> dict:
    """AUC and feature importance straight out of the trained artifact — the claim
    'the model was never told salaries exist' has to be checkable, not asserted."""
    import pickle
    path = (Path(__file__).resolve().parent.parent
            / "backend" / "ml" / "artifacts" / "retry_success.pkl")
    if not path.exists():
        return {"available": False}
    with open(path, "rb") as f:
        art = pickle.load(f)
    gain = art["model"].booster_.feature_importance(importance_type="gain")
    total = float(gain.sum()) or 1.0
    ranked = sorted(zip(art["features"], gain), key=lambda t: -t[1])[:5]
    return {
        "available": True, "auc": round(float(art["auc"]), 3),
        "trained_at": art.get("trained_at"),
        "features": [{"name": n, "gain_pct": round(float(g) / total * 100, 1)}
                     for n, g in ranked],
    }


def _naive_day1_plan(c, mandate: dict, customer: dict, used: int, now) -> dict:
    """The demo's naive baseline: a biller who just retries blind on the 1st of the
    next cycle. Not solver.naive_plan()'s +24h — that lands on whatever day-of-month
    `now` happens to be, which reads as arbitrary on a 30-day cycle grid. Same real
    scorer, a fixed and legible anchor date instead. solver.py (Vishaal's file) is
    untouched; this only changes what date the export layer asks it to score."""
    from backend.sequencer import scorer as seq_scorer
    attempts_remaining = max(MAX_ATTEMPTS_PER_CYCLE - used, 0)
    if attempts_remaining == 0:
        return {"strategy": "NAIVE", "attempts": [], "expected_recovery_inr": 0.0, "basis": "modelled"}

    nxt_month = now.month + 1 if now.month < 12 else 1
    nxt_year = now.year if now.month < 12 else now.year + 1
    day1 = now.replace(year=nxt_year, month=nxt_month, day=1,
                        hour=10, minute=0, second=0, microsecond=0)
    window = "PEAK" if day1.hour in solver.PEAK_HOURS else "NON_PEAK"
    prob, basis = seq_scorer.score_slot(c, mandate, customer, day1, window, used + 1)
    return {
        "strategy": "NAIVE",
        "attempts": [{
            "attempt_no": used + 1,
            "slot_at": day1.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window": window, "predicted_success": prob,
            "reason": "Blind retry on the 1st of next cycle, no slot selection",
        }],
        "expected_recovery_inr": round(mandate["debit_amount_inr"] * prob, 2),
        "basis": basis,
    }


def build_mandates(c, now) -> dict:
    used_map = _attempts_used(c)
    fail_map = _last_failure(c)
    salary = _salary_days(c)

    picked = []
    for persona, age, where in PERSONAS:
        row = c.execute(
            f"""SELECT m.*, cu.bank, cu.segment, cu.name FROM mandates m
                JOIN customers cu ON cu.customer_id = m.customer_id
                WHERE {where} ORDER BY m.debit_amount_inr DESC LIMIT 1""").fetchone()
        if row is not None:
            picked.append((row, persona, age))

    items = []
    for r, persona, age in picked:
        m = dict(r)
        customer = {"bank": r["bank"], "segment": r["segment"]}
        cycle, used = used_map.get(m["mandate_id"], (m["cycle"], 0))
        verdict = seq_router.route(m, used)

        # Router first, always. A revoked mandate scheduling four confident retries
        # would be worse than useless — mirrors backend/api/mandates.py.
        if verdict["retryable"]:
            naive = _naive_day1_plan(c, m, customer, used, now)
            sequenced = solver.sequenced_plan(c, m, customer, used, now)
        else:
            empty = {"attempts": [], "expected_recovery_inr": 0.0, "basis": "modelled"}
            naive = {"strategy": "NAIVE", **empty}
            sequenced = {"strategy": "SEQUENCED", **empty}

        uplift = round(sequenced["expected_recovery_inr"] - naive["expected_recovery_inr"], 2)
        items.append({
            "mandate_id": m["mandate_id"], "customer_id": m["customer_id"],
            "customer_name": r["name"], "age": age, "persona": persona,
            "bank": r["bank"], "segment": r["segment"],
            "state": m["state"], "cap_inr": m["cap_inr"],
            "debit_amount_inr": m["debit_amount_inr"],
            "requires_afa": m["debit_amount_inr"] > AFA_THRESHOLD_INR,
            "cycle": cycle, "next_debit_at": m["next_debit_at"],
            "attempts_used": used,
            "attempts_remaining": max(MAX_ATTEMPTS_PER_CYCLE - used, 0),
            "last_failure_reason": fail_map.get(m["mandate_id"]),
            "predicted_salary_day": salary.get(m["customer_id"]),
            "at_risk_inr": m["debit_amount_inr"], "basis": "deterministic",
            "router_verdict": verdict,
            "predicted_success": (sequenced["attempts"][0]["predicted_success"]
                                  if sequenced["attempts"] else 0.0),
            "predicted_basis": "modelled",
            "naive": naive, "sequenced": sequenced,
            "uplift_inr": uplift, "uplift_basis": "modelled",
        })

    return {"items": items, "total": len(items),
            "search_days": solver.SEARCH_DAYS,
            "max_attempts_per_cycle": MAX_ATTEMPTS_PER_CYCLE,
            "afa_threshold_inr": AFA_THRESHOLD_INR,
            "model_card": _model_card(), "generated_at": db.get_meta(c, "reference_now")}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    c = db.connect()
    now = db.get_meta(c, "reference_now")

    summary = {
        "generated_at": now, "currency": "INR",
        "counters": {
            "recovered_inr": round(q1(c, "SELECT SUM(rupees_at_risk_inr) FROM cases WHERE status='RESOLVED'"), 2),
            "awaiting_approval_inr": round(q1(c, "SELECT SUM(rupees_at_risk_inr) FROM cases WHERE status='AWAITING_APPROVAL'"), 2),
            # Never summed together. Two fields, two figures, forever.
            "deterministic_at_risk_inr": round(q1(c, "SELECT SUM(rupees_at_risk_inr) FROM cases WHERE basis='deterministic' AND status NOT IN ('RESOLVED')"), 2),
            "modelled_at_risk_inr": round(q1(c, "SELECT SUM(rupees_at_risk_inr) FROM cases WHERE basis='modelled' AND status NOT IN ('RESOLVED')"), 2),
        },
        "case_counts": {k.lower(): q1(c, "SELECT COUNT(*) FROM cases WHERE status=?", k)
                        for k in ("OPEN", "INVESTIGATING", "AWAITING_APPROVAL",
                                  "BLOCKED", "RESOLVED", "EXPIRED")},
        "resolver_counts": {r["resolver"]: r["n"] for r in c.execute(
            "SELECT resolver, COUNT(*) n FROM cases GROUP BY resolver")},
        "waterfall": build_waterfall(c),
        # The queue is ordered by money, so it clusters on whichever break type
        # carries the largest tickets. This breakdown is how the breadth of the
        # engine stays visible without distorting that ordering.
        # Each break type is produced by exactly one detection engine — see
        # backend/ledgers/states.py BreakType comments. Grouping by engine here
        # (rather than leaving one flat list) is what makes each of the three
        # boxes in the architecture diagram visible on screen as its own thing.
        "by_break_type": [
            {"break_type": r["break_type"], "resolver": r["resolver"],
             "basis": r["basis"], "case_count": r["n"],
             "rupees_at_risk_inr": round(r["s"], 2),
             "signal_count": r["sig"],
             "engine": ENGINE_BY_BREAK.get(r["break_type"], "consistency_matrix")}
            for r in c.execute(
                """SELECT break_type, resolver, basis, COUNT(*) n,
                          SUM(rupees_at_risk_inr) s, SUM(signal_count) sig
                   FROM cases WHERE status NOT IN ('RESOLVED')
                   GROUP BY break_type, basis
                   ORDER BY SUM(rupees_at_risk_inr) DESC""")
        ],
    }

    items = [case_row(r) for r in c.execute(
        """SELECT * FROM cases WHERE is_aggregate=0 AND status NOT IN ('RESOLVED')
           ORDER BY priority_score DESC LIMIT 60""")]
    aggregates = [case_row(r) for r in c.execute(
        "SELECT * FROM cases WHERE is_aggregate=1 ORDER BY rupees_at_risk_inr DESC")]
    cases = {
        "items": items, "aggregates": aggregates,
        "total": q1(c, "SELECT COUNT(*) FROM cases WHERE is_aggregate=0"),
        "limit": 60, "offset": 0,
        "totals_at_risk": {
            "deterministic_inr": summary["counters"]["deterministic_at_risk_inr"],
            "modelled_inr": summary["counters"]["modelled_at_risk_inr"],
        },
    }

    mandates = build_mandates(c, db.reference_now(c))

    for name, payload in (("summary", summary), ("cases", cases),
                          ("waterfall", summary["waterfall"]),
                          ("mandates", mandates)):
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2))
        print(f"  wrote {path.relative_to(OUT.parent.parent.parent)}"
              f"  ({path.stat().st_size / 1024:.1f} KB)")
    c.close()


if __name__ == "__main__":
    main()
