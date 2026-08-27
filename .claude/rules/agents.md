---
paths:
  - "backend/agents/**"
---

# Agents

Two agents: reconciliation and cart recovery. Same shape, different tools.

## The split that defines this codebase

**The LLM reasons and proposes. A deterministic layer decides whether money moves.**

- Reconciliation may *propose* `ISSUE_REFUND`. `guardrails/` decides if it executes.
- Cart agent may *request* a step down the offer ladder. The policy engine grants or denies.
- Neither agent ever emits a rupee figure that becomes an action parameter.

If you are writing a prompt that asks the model to output an amount, a discount percentage
or an approval decision, stop. That belongs in deterministic code.

## The loop

Hand-rolled in `loop.py`, roughly 150 lines. Chosen over LangGraph deliberately: we need
total control of the trace event format because streaming that trace is the demo.
**Do not introduce an agent framework.**

Every iteration emits SSE events matching `CONTRACTS.md`:
`thinking` `tool_call` `tool_result` `guardrail` `conclusion` `done` `error`

Emit as they happen. Never buffer and flush at the end. The point is that a judge watches
it think.

## Tools

Tools in `tools.py` are read-mostly. Any tool that mutates state routes through
`guardrails/engine.py` first rather than checking permissions itself.

Keep tool results short. A tool returning 400 lines of JSON burns context and produces worse
reasoning than one returning a five-word summary.

## LLM client

`llm.py` wraps whichever provider is configured. Swapping providers is a two-line env change,
never a code change. If a rate limit hits mid-demo we set `DEMO_MODE=replay` and serve
recorded fixtures. Keep that path working.
