"""Generates frontend/src/mock/trace.json — the DEMO_MODE=replay fallback for
GET /api/cases/{case_id}/stream. Not a committed pipeline step: run once, by hand,
whenever agents/loop.py's event shape changes.

Uses a scripted (not live-LLM) run so the fixture is deterministic and reviewable —
it still exercises the real loop.py, tools.py and guardrails code, just with a
scripted model standing in for Gemini. This is a real Scenario 2 investigation
against a real seeded case, not hand-written JSON.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from backend.agents import loop, tools
from backend.agents.llm import Completion, ToolCall
from backend.db import conn as db

OUT_PATH = Path(__file__).resolve().parent.parent / "frontend" / "src" / "mock" / "trace.json"


@dataclass
class ScriptedLLM:
    step: int = 0

    def complete(self, system_prompt, messages, tool_specs):
        self.step += 1
        if self.step == 1:
            return Completion(
                text="Payment is pending with no order on file. Let me check the ledger "
                     "before concluding anything — a customer claiming a debit with no "
                     "order is Scenario 2, and the right move here is almost always to "
                     "wait, not refund.",
                tool_calls=[ToolCall(id="call_1", name="get_ledger_snapshot",
                                      args={"payment_id": PAYMENT_ID})],
            )
        if self.step == 2:
            return Completion(
                text="Confirmed: payment is still PENDING, no order exists. In India an "
                     "unconfirmed UPI payment must be auto-reversed by the acquirer within "
                     "T+5 days. If I refund now from merchant balance and that auto-reversal "
                     "then fires, the customer gets paid twice. The correct action is to "
                     "wait for the payment to reach a terminal state.",
                tool_calls=[ToolCall(id="call_2", name="propose_action", args={
                    "action_type": "NO_ACTION", "payment_id": PAYMENT_ID,
                    "confidence": 0.92,
                    "reasoning": "Payment is still PENDING; the T+5 acquirer auto-reversal "
                                 "window has not closed. Refunding now risks paying the "
                                 "customer twice.",
                })],
            )
        raise AssertionError("scripted run should conclude by step 2")


conn = db.connect()
row = conn.execute(
    "SELECT case_id, payment_id FROM cases WHERE break_type='PAYMENT_PENDING_WEBHOOK_MISSING' "
    "AND is_aggregate=0 ORDER BY case_id LIMIT 1"
).fetchone()
CASE_ID, PAYMENT_ID = row["case_id"], row["payment_id"]

case, _ = tools.get_case(conn, CASE_ID)
events = list(loop.run(conn, case, ScriptedLLM()))
conn.close()

OUT_PATH.write_text(json.dumps(events, indent=2))
print(f"wrote {len(events)} events to {OUT_PATH}")
for e in events:
    print(" ", e["seq"], e["event"])
