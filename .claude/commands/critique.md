---
description: Adversarially review recent work for flaws, over-engineering and demo risk
---

Review what we just built. Be adversarial, not agreeable. Structure your answer as:

**1. What is broken.** Actual bugs, wrong logic, unhandled cases that will fire during the
demo. Show the failing input if you can.

**2. What is over-engineered.** Abstractions with one caller, config for values that never
change, error handling for cases that cannot occur. We have 24 hours — name what to delete.

**3. What violates our invariants.** Check against CLAUDE.md: blended deterministic/modelled
numbers, LLM touching a money decision, contract shapes drifting, money not in `_inr` floats.

**4. What will break on stage.** Network dependency, timing assumption, unhandled empty state,
anything that renders blank while a judge is watching.

**5. The one thing I would change.** A single highest-leverage fix, with the reason.

If everything is genuinely fine, say so in one line and stop. Do not manufacture criticism.
