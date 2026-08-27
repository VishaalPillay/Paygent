# Project context

This is the reasoning behind Paygent, not just the rules. CLAUDE.md tells you what to do.
This tells you why, so you can extend decisions correctly instead of just following them
literally. Read this before your first substantial piece of work.

---

## 1. The problem, precisely

A business in India tries to collect ₹100. It realises roughly ₹70–84. The gap doesn't leave
in one place — it leaks across six sequential gates, each owned by a different company's
system, and no merchant has visibility across more than one of them.

**The waterfall (illustrative numbers, not real data):**

```
Checkout initiated  → Payment attempted   (lost to abandonment, bad UX)
Payment attempted   → Payment authorised  (lost to declines — technical + business)
Payment authorised  → Order recognised    (lost to dropped webhooks, orphan payments)
Order recognised    → Settled to bank     (lost to fees, short-pay, holds)
Settled to bank      → Net of reversals    (lost to refunds, chargebacks)
Net of reversals    → Realised            (lost to unclaimed statutory credits)
```

Individually each gate looks small enough to ignore. Compounded, ~15–30% of intended revenue
vanishes with no single owner responsible for noticing.

**Why nobody sees this today:** one online payment passes through roughly seven systems
(customer's UPI app, NPCI, issuing bank, acquiring bank, payment aggregator, escrow, merchant).
The merchant has logs for exactly one of them. Everything upstream is a status code with no
explanation attached.

## 2. The core model: four ledgers

Every transaction leaves four records that must agree:

1. **Payment** — did the money move? (initiated / pending / authorised / captured / failed / refunded)
2. **Order** — did we create the order or activate the subscription?
3. **Inventory** — did we reserve or ship the product? (not applicable for SaaS)
4. **Accounting** — did we book it as revenue?

**Every revenue leak is two of these four disagreeing.** We do not try to enumerate every way
a transaction can break — that list is unbounded. We enumerate every combination that is
*legal* (`backend/ledgers/matrix.py`) and treat everything outside that set as a break. This
is why the system catches failures nobody explicitly coded for — `UNCLASSIFIED_BREAK` is a
demo point, not a fallback to apologise for.

**Time is a fourth dimension, not just state.** `PENDING + MISSING` is healthy for a customer
mid-checkout. It only becomes a break once it has persisted past a dwell limit (see
`DWELL_LIMITS_SECONDS` in matrix.py). Getting this wrong either floods the system with false
positives on ordinary in-flight checkouts, or misses genuine breaks that never resolve.

## 3. Scope: why SaaS + e-commerce, why UPI-centric

We narrowed from "all Indian business revenue leakage" (a near-infinite catalogue — discount
abuse, courier weight disputes, GST reconciliation, channel leakage, etc.) down to businesses
whose primary revenue engine is **online transactions**: e-commerce sellers and SaaS companies
on UPI, cards, and recurring mandates. This covers the majority of India's digitally-transacting
businesses and lets one payment-rail model serve two business types.

E-commerce and SaaS diverge in one critical way: a failed e-commerce payment is immediately
terminal (retry now or the customer is gone) and has a fulfilment-side consequence (COD/RTO).
A failed SaaS payment is discovered 30 days after the customer already decided to stay — the
customer isn't present, so retry timing is a scheduling problem, not a UX problem. This is why
cart recovery (e-commerce) and the retry sequencer (SaaS) are architecturally different features
rather than one generic "recover the payment" module.

## 4. Ground-truth facts — do not contradict these

These were researched and verified during planning. Treat them as fixed unless re-verified.

- **UPI Autopay retry rule (effective Aug 2025):** a maximum of **4 attempts per cycle** —
  1 original + 3 retries. After that, the cycle is dead until the next billing period.
  Executions must run in **non-peak windows**. A 45–60 second cooldown applies before checking
  status after initiating. This is why the sequencer is a scarce-resource allocation problem,
  not a "retry until it works" loop.
- **The ₹15,000 AFA threshold:** UPI Autopay debits above ₹15,000 require the customer to
  approve via OTP every single cycle — no exemption for SaaS. (The ₹1 lakh AFA-free ceiling
  applies only to mutual funds, insurance and credit card bills — not subscriptions.)
- **T+5 credit adjustment window:** when a UPI payment shows no confirmation at the merchant,
  the acquirer must auto-reverse it to the customer within T+5 days, with ₹100/day compensation
  for delay. **This is the exact mechanism behind our most important guardrail** — refunding
  from merchant balance during this window risks paying the customer twice when the auto-reversal
  fires. See `backend/guardrails/blocks.py::refund_requires_terminal_payment`.
- **UPI Autopay failure rate runs 8–15%** (vs 2–3% for card mandates), because UPI payment
  flows are stateless while card mandates are bank-managed. ~20M mandate revocations/month,
  mostly insufficient-funds driven.
- **UPI MDR is changing (Aug 2026):** after six years free, legislation now permits MDR on
  UPI/RuPay above certain thresholds. This means merchants have zero historical tooling for
  monitoring UPI transaction costs — a brand-new, currently invisible leak category. Useful as
  a timeliness hook in the pitch; not something to build detection for unless time allows.
- **GST 2.0 (from 22 Sep 2025):** collapsed to two main slabs (5%, 18%) plus a 40% luxury/sin
  slab. Any price/tax master not remapped after that date is a live leakage source.
- **GST TCS on marketplaces is 0.5%** (reduced from 1% in July 2024).
- **Equalisation levy is fully abolished** (2% e-commerce levy: gone Aug 2024; 6% ad levy: gone
  Apr 2025). Do not build a rule for it — it would be a confident, permanent false positive.

## 5. Why agents in two places, and deliberately not a third

An agent earns its place only where the investigation path genuinely branches based on what
it finds. A fixed decision space is a solver, not a prompt.

| Component | Agent? | Reasoning |
|---|---|---|
| Reconciliation | **Yes** | Finding a duplicate order flips the entire recovery strategy mid-investigation. Unbounded search space. |
| Cart recovery | **Yes** | A human replies with unpredictable things; needs live tool calls (inventory, serviceability) and negotiation. |
| Retry sequencer | **No** | Three attempts across N eligible non-peak slots is fully enumerable. LLM adds latency and risk for zero accuracy gain. |

Being able to explain *why* the sequencer isn't an agent is itself a pitch point — most teams
reflexively bolt an LLM onto everything.

**The pattern that repeats across both real agents:** the LLM reasons and proposes; a
deterministic layer decides whether anything touching money actually executes. The
reconciliation agent can propose `ISSUE_REFUND`; guardrails decide if it fires. The cart agent
can request a discount tier; the policy engine grants or denies. An LLM never outputs a number
that becomes a money-moving parameter. This split is the entire safety story and should be
visually obvious on every relevant screen (see §7).

## 6. The three reconciliation scenarios, in detail

These are the concrete cases the reconciliation agent must handle. Each is a specific
disagreement between two of the four ledgers.

**Scenario 1 — Payment CAPTURED, Order MISSING, Inventory AVAILABLE**
Money moved, nothing was created. The trap is concluding "missing" too fast — most missing
orders are *unlinked*, not absent. Correct investigation order: re-search by receipt/notes,
phone+email, amount+timestamp window, session/device ID → check whether the customer already
re-ordered (if yes, this is now a duplicate-payment refund, not an order-creation case, because
creating a second order would double-fulfil) → reconstruct intent from the cart snapshot →
score confidence before acting. Guardrail: `no_duplicate_order_exists` must pass before order
creation. Idempotency key derived from `payment_id` so a retried agent run can never create
two orders.

**Scenario 2 — Payment PENDING, Webhook MISSING, customer claims debit**
The best scenario, because the correct action is *inaction*. A human under pressure refunds
immediately; that's wrong, because of the T+5 auto-reversal mechanism in §4 — refunding now
risks a double payout. Correct behaviour: hard-block any refund while payment state is
non-terminal (`refund_requires_terminal_payment`), independently verify the debit via UTR/RRN,
poll for a terminal state respecting the 45–60s cooldown, then route to Scenario 1's logic once
terminal. This is the scenario to lead the live demo with.

**Scenario 3 — Refund SUCCESS, Order ACTIVE, Accounting REVENUE_RECOGNIZED**
Root cause is almost always an out-of-band refund (issued from the gateway dashboard, never
told to the order/billing system). Diverges by business model: e-commerce risks shipping goods
that were already refunded (urgency depends on fulfilment stage — never auto-cancel a shipped
order); SaaS risks unbounded free service on an active subscription. If there's no matching
cancellation/return request in the system, this is also an internal-control alert, not just a
state break — flag who issued the refund. Accounting complication: reversing recognised revenue
needs a credit note, and Indian GST bars credit notes after 30 November of the following FY —
if the refund is old enough, the GST may be permanently unrecoverable, which the agent should
surface explicitly as a statutory (B6-bucket) leak rather than silently reconciling it.

## 7. The demo, and why each screen exists

Five screens. Each demonstrates a different claim; none are interchangeable.

**Command Center** — proves the problem exists before any product is shown. The waterfall
chart + three counters (`recovered`, `awaiting_approval`, `deterministic_at_risk` vs
`modelled_at_risk` — *never blended into one number*). This screen contains zero AI features
by design; its only job is making an invisible gap visible.

**Live storefront + Razorpay test payment** — the credibility move. A real payment, made on
stage, with a deliberately dropped webhook, produces a real broken state that the audience
watched happen. This is what separates the demo from every competitor running on canned data.

**Case Detail** — the money shot. Split screen: four ledger boxes (two glow red on disagreement)
on one side, streaming agent reasoning trace on the other, guardrail verdict bar across the
bottom. Built around Scenario 2: the agent investigates, then the guardrail visibly blocks a
refund. The line to say out loud: *"A human would have refunded here. Our agent knew not to."*
Trace streams at ~850ms/event — paced for reading, not dumped instantly.

**Mandate Board** — the UPI retry story compressed into something demoable in 60 seconds
(the real cycle is 30 days, so this is a calendar view with a naive-vs-sequenced toggle, not a
live wait). Must also show the router refusing to schedule any retry against a revoked mandate
— "half of all failures are structurally unretryable, and everyone burns their three attempts
on them anyway."

**Conversations** — cart recovery chat in a phone frame beside the offer-policy panel. Live
typed interaction. The demo beat: the agent requests the top discount tier, the policy panel
visibly **denies** it (margin floor or dependency-tracking violation), and the agent has to
hold the line. This mirrors the Case Detail guardrail moment on purpose — same pattern, second
proof point.

**The closing line that ties all five together:** *"Three features, one pattern. The AI
proposes. Something that can't hallucinate decides."*

## 8. The UVP problem — read this before touching the pitch deck

**This happened and matters:** the original three-feature framing (cart recovery agent, retry
dunning, reconciliation tool) was reviewed by mentors and marked down for having no unique value
proposition — each feature already exists as a shipping product (Wigzo/Bik for cart recovery,
Chargebee/Razorpay dunning, Recko-style reconciliation tools). One mentor also remarked they
expected blockchain, which was really a proxy for "we saw no technical novelty," not a literal
ask for a blockchain.

**The reframe that followed, and its actual status:**

The deeper insight discussed: every company holding proof of a merchant's revenue leak
(gateway, marketplace, courier, billing platform) has a conflict of interest in surfacing it.
The proposed differentiator was "we audit the counterparty, not just the merchant's own data,"
with two possible extensions — a cross-merchant network-health layer (pooled anonymised
failure signals catch a degrading bank before any single merchant could) and a Merkle-anchored
evidence chain for disputes (tamper-evident proof, anchored to a public testnet like Polygon
Amoy, so a dispute claim is provable rather than merely asserted).

**Where this landed operationally:** the pitch-deck content (Innovation & Existing Solutions
slide) uses the lighter version of this argument — the conflict-of-interest framing and
"safety as a feature, not an afterthought" — as the spoken UVP. **The network layer and the
Merkle-anchored evidence chain were NOT incorporated into the 24-hour build plan.** They appear
in the pitch deck's "Future Scope" section only. `CONTRACTS.md` and the actual repo structure
reflect the simpler, buildable architecture: four ledgers → cases → guardrails → three
resolvers. Do not start building network pooling or blockchain anchoring unless explicitly
asked — it would be scope creep against an already tight 24-hour budget, and it was consciously
deferred to "future scope" for exactly that reason.

If asked to help with the pitch narrative, the two-part answer is: (1) conflict-of-interest —
nobody in the payment chain who holds the evidence is incentivised to show it to the merchant,
we're the only party without that conflict; (2) safety as the actual technical differentiator —
every "AI agent" demo does something, ours is built to know when *not* to act, and that's
demonstrated live via the guardrail blocks in §7, not asserted in a slide.

## 9. What's deliberately not built, and why

No auth, login, Docker, CI, tests, settings UI, deployment, multi-tenancy, dark mode, mobile
responsive. Not because they're bad practice — because none of them appear on screen during a
5-minute demo, and every hour spent on them is an hour not spent on the five screens in §7.

No agent framework (LangGraph etc.) — the tool-use loop is hand-rolled (`backend/agents/loop.py`,
~150 lines) specifically because the reasoning-trace event format is the demo, and owning that
format completely matters more than a framework's convenience features.

No deep learning, fine-tuning, or embeddings anywhere load-bearing. LightGBM and IsolationForest
only, trained in under two minutes total, because the ML story only needs to be *real*, not
sophisticated — the demo beat is "we never told the model salary dates exist, it found them,"
which a 20-second LightGBM fit demonstrates just as well as anything heavier would.

## 10. Where to look for more detail

- `CONTRACTS.md` — frozen API shapes. This is downstream of everything above; if something
  here seems to conflict with the contract, the contract wins for implementation purposes and
  the conflict should be raised, not silently resolved either way.
- `.claude/rules/*.md` — per-area conventions that load automatically when working in that area.
- `README.md` — the external-facing version of this document, written for judges/readers outside
  the team.
