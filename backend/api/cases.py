"""GET /api/cases and GET /api/cases/{case_id}.

Serves straight from the `cases` table Nikhil's Case Bus populated — no separate
in-memory model. `signal_id` is a DB-internal column, not a CONTRACTS.md field, and
is dropped before the response leaves this module.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from ..db import conn as db

router = APIRouter()

_SORT_COLUMNS = {
    "-priority_score": "priority_score DESC",
    "-created_at": "created_at DESC",
    "-rupees_at_risk_inr": "rupees_at_risk_inr DESC",
}


def _case_to_dict(row: sqlite3.Row) -> dict:
    case = dict(row)
    payload = json.loads(case.pop("payload_json") or "{}")
    case.pop("signal_id", None)
    case["ledger_snapshot"] = payload.get("ledger_snapshot")
    case["guardrail_checks"] = payload.get("guardrail_checks", [])
    case["actions"] = payload.get("actions", [])
    case["is_aggregate"] = bool(case["is_aggregate"])
    # Only the reconciliation agent streams a reasoning trace today — sequencer and
    # cart resolve differently (a plan, a conversation), not an SSE trace. Computed
    # here, not stored, so it stays true to what the backend can actually stream.
    case["trace_available"] = case["resolver"] == "RECONCILIATION"
    return case


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _not_found(case_id: str) -> None:
    raise HTTPException(status_code=404, detail={
        "error": {"code": "CASE_NOT_FOUND", "message": f"No case with id {case_id}."}})


@router.get("/cases")
def list_cases(
    status: str | None = None,
    break_type: str | None = None,
    resolver: str | None = None,
    business_type: str | None = None,
    basis: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = "-priority_score",
    include_aggregates: bool = False,
    aggregates_only: bool = False,
) -> dict:
    conn = db.connect()
    try:
        where: list[str] = []
        params: list = []
        for column, value in (
            ("status", status), ("break_type", break_type), ("resolver", resolver),
            ("business_type", business_type), ("basis", basis),
        ):
            if value:
                where.append(f"{column} = ?")
                params.append(value)

        if aggregates_only:
            where.append("is_aggregate = 1")
        elif not include_aggregates:
            where.append("is_aggregate = 0")

        clause = f"WHERE {' AND '.join(where)}" if where else ""

        total = conn.execute(f"SELECT COUNT(*) AS n FROM cases {clause}", params).fetchone()["n"]

        totals = {"deterministic_inr": 0.0, "modelled_inr": 0.0}
        for r in conn.execute(
            f"SELECT basis, SUM(rupees_at_risk_inr) AS s FROM cases {clause} GROUP BY basis",
            params,
        ):
            key = f"{r['basis']}_inr"
            if key in totals:
                totals[key] = round(r["s"] or 0.0, 2)

        order = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["-priority_score"])
        rows = conn.execute(
            f"SELECT * FROM cases {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

        return {
            "items": [_case_to_dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "totals_at_risk": totals,
        }
    finally:
        conn.close()


@router.get("/cases/{case_id}")
def get_case(case_id: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            _not_found(case_id)
        return _case_to_dict(row)
    finally:
        conn.close()


@router.post("/cases/{case_id}/actions/{action_id}/approve")
def approve_action(case_id: str, action_id: str) -> dict:
    """Moves one Action from PROPOSED to EXECUTED and the case from AWAITING_APPROVAL
    to RESOLVED. Never re-runs guardrails and never lets a human override a block —
    a blocked action stays blocked regardless of who asks.
    """
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            _not_found(case_id)

        payload = json.loads(row["payload_json"] or "{}")
        actions = payload.get("actions", [])
        action = next((a for a in actions if a.get("action_id") == action_id), None)
        if action is None:
            raise HTTPException(status_code=404, detail={"error": {
                "code": "ACTION_NOT_FOUND",
                "message": f"No action {action_id} on case {case_id}."}})

        if action.get("blocked_by"):
            raise HTTPException(status_code=409, detail={"error": {
                "code": "ACTION_BLOCKED_BY_GUARDRAIL",
                "message": "This action was blocked by a guardrail and cannot be approved.",
                "blocked_by": action["blocked_by"]}})

        action["status"] = "EXECUTED"
        conn.execute(
            "UPDATE cases SET status = 'RESOLVED', payload_json = ?, updated_at = ? "
            "WHERE case_id = ?",
            (json.dumps(payload), _now_iso(), case_id),
        )
        conn.commit()

        updated = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        return _case_to_dict(updated)
    finally:
        conn.close()
