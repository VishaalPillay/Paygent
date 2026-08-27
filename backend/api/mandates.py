"""GET /api/mandates and GET /api/mandates/{mandate_id}/plan — the Mandate Board.

Wires the three sequencer layers together over HTTP. The one rule that matters here
and lives nowhere else: for an unretryable mandate, `router.route()` is checked
*before* any plan is computed, and both `naive` and `sequenced` come back empty. A
revoked mandate scheduling four confident-looking retries would be worse than useless
on stage — the demo beat is that the router refuses to spend a single attempt on it.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from ..db import conn as db
from ..ledgers.states import AFA_THRESHOLD_INR, MAX_ATTEMPTS_PER_CYCLE
from ..sequencer import router as seq_router
from ..sequencer import scorer, solver

router = APIRouter()

_LAST_FAILURE_SQL = """
    SELECT mandate_id, failure_reason_code, MAX(slot_at)
    FROM mandate_attempts
    WHERE outcome = 'FAILED'
    GROUP BY mandate_id
"""


def _not_found(mandate_id: str) -> None:
    raise HTTPException(status_code=404, detail={
        "error": {"code": "MANDATE_NOT_FOUND", "message": f"No mandate with id {mandate_id}."}})


def _attempts_used_map(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    return {
        (r["mandate_id"], r["cycle"]): r["n"]
        for r in conn.execute(
            "SELECT mandate_id, cycle, COUNT(*) AS n FROM mandate_attempts GROUP BY mandate_id, cycle")
    }


def _last_failure_map(conn: sqlite3.Connection) -> dict[str, str]:
    # SQLite's documented "bare column" behaviour: with a single MAX() and no other
    # aggregate, the non-aggregated columns come from the row where the max occurs.
    return {r[0]: r[1] for r in conn.execute(_LAST_FAILURE_SQL)}


def _mandate_to_item(
    mandate: sqlite3.Row, attempts_used: int, last_failure: str | None,
    verdict: dict, prob: float, basis: str,
) -> dict:
    m = dict(mandate)
    return {
        "mandate_id": m["mandate_id"],
        "customer_id": m["customer_id"],
        "state": m["state"],
        "cap_inr": m["cap_inr"],
        "debit_amount_inr": m["debit_amount_inr"],
        "requires_afa": m["debit_amount_inr"] > AFA_THRESHOLD_INR,
        "cycle": m["cycle"],
        "next_debit_at": m["next_debit_at"],
        "attempts_used": attempts_used,
        "attempts_remaining": max(MAX_ATTEMPTS_PER_CYCLE - attempts_used, 0),
        "last_failure_reason": last_failure,
        "at_risk_inr": m["debit_amount_inr"],
        "basis": "deterministic",
        "router_verdict": verdict,
        "predicted_success": prob,
        "predicted_basis": basis,
    }


@router.get("/mandates")
def list_mandates() -> dict:
    conn = db.connect()
    try:
        mandates = conn.execute("SELECT * FROM mandates").fetchall()
        if not mandates:
            return {"items": [], "total": 0}

        customer_ids = list({m["customer_id"] for m in mandates})
        customers = {
            r["customer_id"]: dict(r)
            for r in conn.execute(
                f"SELECT * FROM customers WHERE customer_id IN ({','.join('?' * len(customer_ids))})",
                customer_ids)
        }
        used_map = _attempts_used_map(conn)
        failure_map = _last_failure_map(conn)
        now = db.reference_now(conn)

        # Scored as one batch — LightGBM's per-call overhead dominates at 2000 rows;
        # individually this endpoint took ~6s, batched it's under a second.
        used_list = [used_map.get((m["mandate_id"], m["cycle"]), 0) for m in mandates]
        batch = [
            (dict(m), customers[m["customer_id"]], now, "NON_PEAK", used + 1)
            for m, used in zip(mandates, used_list)
        ]
        scores = scorer.score_slots_batch(conn, batch)

        items = [
            _mandate_to_item(
                m, used, failure_map.get(m["mandate_id"]),
                seq_router.route(dict(m), used), prob, basis,
            )
            for m, used, (prob, basis) in zip(mandates, used_list, scores)
        ]
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


@router.get("/mandates/{mandate_id}/plan")
def get_plan(mandate_id: str) -> dict:
    conn = db.connect()
    try:
        mandate = conn.execute(
            "SELECT * FROM mandates WHERE mandate_id = ?", (mandate_id,)).fetchone()
        if mandate is None:
            _not_found(mandate_id)
        customer = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (mandate["customer_id"],)).fetchone()

        m = dict(mandate)
        attempts_used = conn.execute(
            "SELECT COUNT(*) AS n FROM mandate_attempts WHERE mandate_id = ? AND cycle = ?",
            (mandate_id, m["cycle"]),
        ).fetchone()["n"]
        verdict = seq_router.route(m, attempts_used)
        now = db.reference_now(conn)

        if not verdict["retryable"]:
            empty = {"strategy": None, "attempts": [], "expected_recovery_inr": 0.0,
                     "basis": "modelled"}
            return {
                "mandate_id": mandate_id, "cycle": m["cycle"], "retryable": False,
                "router_verdict": verdict,
                "naive": {**empty, "strategy": "NAIVE"},
                "sequenced": {**empty, "strategy": "SEQUENCED"},
                "uplift_inr": 0.0, "uplift_basis": "modelled",
            }

        naive = solver.naive_plan(conn, m, dict(customer), attempts_used, now)
        sequenced = solver.sequenced_plan(conn, m, dict(customer), attempts_used, now)
        uplift = round(sequenced["expected_recovery_inr"] - naive["expected_recovery_inr"], 2)

        return {
            "mandate_id": mandate_id, "cycle": m["cycle"], "retryable": True,
            "router_verdict": verdict,
            "naive": naive, "sequenced": sequenced,
            "uplift_inr": uplift, "uplift_basis": "modelled",
        }
    finally:
        conn.close()
