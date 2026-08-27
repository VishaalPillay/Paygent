---
paths:
  - "frontend/**"
---

# Frontend

Four screens plus a storefront. React + Vite + Tailwind + Recharts. Nothing else.

## The visual language

**Every screen splits: AI output on one side, deterministic control on the other.**

- Case Detail: agent reasoning ↔ guardrail verdict
- Mandate Board: ML prediction ↔ router refusal
- Conversations: agent negotiation ↔ policy denial

Not decoration. It is the product thesis rendered as layout. Keep it consistent.

## Non-negotiable

- **Every number on screen is in rupees.** Never "4,231 anomalies detected."
- **Deterministic and modelled amounts never merge into one figure.** Two labels, always.
- The trace streams line by line at roughly 850ms per event. Not instant. A judge must be
  able to read it as it arrives.
- `VITE_USE_MOCK=true` keeps working right through integration. It is the fallback if the
  backend dies on stage.

## Data

Shapes come from `CONTRACTS.md`. Mocks in `src/mock/` mirror it exactly. If the backend adds
a field, the mock gets it in the same commit.

Never reshape data inside a component. If the shape is wrong it is wrong in the contract,
and that is a conversation, not a workaround.

## Scope

No state management library — `useState` and prop drilling are correct at this size.
No component library. No animation library; CSS transitions suffice.

Empty and loading states matter more than polish. A screen showing nothing during a live demo
reads as broken even when it is fine.
