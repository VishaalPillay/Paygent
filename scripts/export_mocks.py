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
from pathlib import Path

from backend.db import conn as db

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
        "trace_available": payload.get("trace_available", False),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


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
        "by_break_type": [
            {"break_type": r["break_type"], "resolver": r["resolver"],
             "basis": r["basis"], "case_count": r["n"],
             "rupees_at_risk_inr": round(r["s"], 2),
             "signal_count": r["sig"]}
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

    for name, payload in (("summary", summary), ("cases", cases),
                          ("waterfall", summary["waterfall"])):
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2))
        print(f"  wrote {path.relative_to(OUT.parent.parent.parent)}"
              f"  ({path.stat().st_size / 1024:.1f} KB)")
    c.close()


if __name__ == "__main__":
    main()
