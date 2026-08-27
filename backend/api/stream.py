"""GET /api/cases/{case_id}/stream — the reasoning trace, live or replayed.

`agents/loop.py` yields events as it computes them; this module's only job is pacing
those events onto the wire (~850ms apart) so a judge can read the trace as it arrives
rather than seeing it dumped instantly. `DEMO_MODE=replay` serves
`frontend/src/mock/trace.json` at the same pacing — the fallback if Gemini
rate-limits mid-demo, and it must keep working.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import config
from ..agents import tools
from ..agents.llm import GeminiClient
from ..agents.loop import run as run_reconciliation
from ..db import conn as db

router = APIRouter()

PACING_SECONDS = 0.85
TRACE_FIXTURE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "mock" / "trace.json"

_llm: GeminiClient | None = None


def _get_llm() -> GeminiClient:
    global _llm
    if _llm is None:
        _llm = GeminiClient()
    return _llm


def _sse_line(event: dict) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event)}\n\n"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _persist_conclusion(conn, case_id: str, conclusion: dict | None, done: dict | None) -> None:
    """The SSE trace is not the system of record — the `cases` table is. Without this,
    a case's tier/status/actions would reset to NULL/OPEN/[] on every page refresh,
    and there would be nothing for the approve endpoint to act on.
    """
    if conclusion is None and done is None:
        return
    action = conclusion.get("recommended_action") if conclusion else None
    row = conn.execute("SELECT payload_json FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        return
    payload = json.loads(row["payload_json"] or "{}")
    if action is not None:
        payload["actions"] = [action]
        payload["guardrail_checks"] = action.get("guardrail_checks", [])
    payload["trace_available"] = True
    conn.execute(
        "UPDATE cases SET status = ?, tier = ?, tier_label = ?, payload_json = ?, updated_at = ? "
        "WHERE case_id = ?",
        (
            done["status"] if done else "BLOCKED",
            action["tier"] if action else None,
            action.get("tier_label") if action else None,
            json.dumps(payload),
            _now_iso(),
            case_id,
        ),
    )
    conn.commit()


def _live_events(conn, case: dict):
    conclusion = None
    done = None
    try:
        for event in run_reconciliation(conn, case, _get_llm()):
            if event["event"] == "conclusion":
                conclusion = event
            elif event["event"] == "done":
                done = event
            yield event
    finally:
        _persist_conclusion(conn, case["case_id"], conclusion, done)
        conn.close()


def _replay_events(case_id: str):
    if not TRACE_FIXTURE.exists():
        yield {"event": "error", "case_id": case_id, "seq": 0, "at": "",
               "code": "INTERNAL_ERROR", "message": "No replay fixture available."}
        yield {"event": "done", "case_id": case_id, "seq": 1, "at": "",
               "status": "BLOCKED", "duration_ms": 0}
        return
    yield from json.loads(TRACE_FIXTURE.read_text())


def _paced(events):
    for event in events:
        yield _sse_line(event)
        time.sleep(PACING_SECONDS)


@router.get("/cases/{case_id}/stream")
def stream_case(case_id: str):
    conn = db.connect()
    case, _ = tools.get_case(conn, case_id)
    if case is None:
        conn.close()
        raise HTTPException(status_code=404, detail={
            "error": {"code": "CASE_NOT_FOUND", "message": f"No case with id {case_id}."}})

    if config.DEMO_MODE == "replay":
        conn.close()
        events = _replay_events(case_id)
    else:
        events = _live_events(conn, case)

    return StreamingResponse(_paced(events), media_type="text/event-stream")
