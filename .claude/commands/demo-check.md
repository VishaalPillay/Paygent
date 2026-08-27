---
description: Walk the full demo path and find what breaks in front of judges
---

Walk the demo end to end and report what fails. The path:

1. Command Center loads. Waterfall renders. Counters show rupees, split into deterministic
   and modelled.
2. A payment arrives via the Razorpay webhook. A case appears within a few seconds.
3. Case Detail opens. The reasoning trace streams event by event, paced for reading.
4. The guardrail block fires visibly and blocks the refund.
5. Mandate Board toggles between naive and sequenced. The router shows zero retries scheduled
   for an unretryable mandate.
6. Conversations: typing as the customer gets a reply. Requesting the top offer rung is denied
   by the policy engine.

For each step report: works / broken / untested, and what specifically fails.

Then check the failure modes: does `VITE_USE_MOCK=true` still carry the whole demo if the
backend dies? Does `DEMO_MODE=replay` work if the LLM rate-limits? Does any screen render
blank on an empty result?
