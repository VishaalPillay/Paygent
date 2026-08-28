"""Hand-rolled reconciliation agent loop. Chosen over an agent framework (LangGraph
etc.) deliberately — see .claude/rules/agents.md — because owning the SSE trace event
format completely is the demo, not a convenience feature a framework would cost us.

Yields event dicts shaped exactly per CONTRACTS.md's SSE section as it computes them —
never buffers and returns at the end. `done` is always the last event, including after
`error`. Pacing (~850ms between events) is the streaming layer's job (`api/stream.py`),
not this loop's.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

from . import tools
from .llm import GeminiClient, GroqClient
from .reconciliation import SYSTEM_PROMPT, build_initial_prompt

MAX_ITERATIONS = 6


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(conn: sqlite3.Connection, case: dict, llm: GeminiClient | GroqClient):
    """Generator of SSE-shaped event dicts investigating one case."""
    seq = 0
    started = time.monotonic()

    def emit(event: str, **fields) -> dict:
        nonlocal seq
        payload = {
            "event": event, "case_id": case["case_id"], "seq": seq,
            "at": _now_iso(), **fields,
        }
        seq += 1
        return payload

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    messages = [{"role": "user", "text": build_initial_prompt(case)}]

    try:
        for _ in range(MAX_ITERATIONS):
            completion = llm.complete(SYSTEM_PROMPT, messages, tools.TOOL_SPECS)

            if completion.text:
                yield emit("thinking", text=completion.text)
                messages.append({"role": "model", "text": completion.text})

            if not completion.tool_calls:
                break

            messages.append({"role": "model", "tool_calls": completion.tool_calls, "text": ""})

            reached_conclusion = False
            for call in completion.tool_calls:
                yield emit("tool_call", call_id=call.id, tool=call.name, args=call.args)
                result, summary = tools.call_tool(conn, call.name, call.args)
                ok = call.name in tools.DISPATCH
                yield emit("tool_result", call_id=call.id, tool=call.name, ok=ok, summary=summary)
                messages.append({
                    "role": "tool", "call_id": call.id, "name": call.name, "response": summary,
                })

                if call.name == "propose_action" and result is not None:
                    action = result
                    for check in action["guardrail_checks"]:
                        yield emit("guardrail", **check)
                    yield emit(
                        "conclusion",
                        break_type=case["break_type"],
                        text=action["reasoning"],
                        recommended_action=action,
                        confidence=call.args.get("confidence", case["confidence"]),
                    )
                    status = (
                        "BLOCKED" if action["blocked_by"]
                        else "AWAITING_APPROVAL" if action["tier"] != 0
                        else "RESOLVED"
                    )
                    yield emit("done", status=status, duration_ms=elapsed_ms())
                    reached_conclusion = True
                    break

            if reached_conclusion:
                return

        # Iteration budget exhausted without a proposed action — investigate-only,
        # not a failure. A human looks at it; nothing about this is an error.
        yield emit(
            "conclusion",
            break_type=case["break_type"],
            text="Investigation did not reach a confident recommendation within the "
                 "iteration budget. Routed for manual review.",
            recommended_action=None,
            confidence=case["confidence"],
        )
        yield emit("done", status="AWAITING_APPROVAL", duration_ms=elapsed_ms())

    except Exception as exc:  # noqa: BLE001 — done must still follow error
        yield emit("error", code="INTERNAL_ERROR", message=str(exc))
        yield emit("done", status="BLOCKED", duration_ms=elapsed_ms())
