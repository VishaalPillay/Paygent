# Onboarding Claude Code — do this once, both of you

## 1. Copy the files in

From the repo root:

```
paygent/
├── CLAUDE.md                      committed
├── CLAUDE.local.md                gitignored, personal
└── .claude/
    ├── settings.json              committed
    ├── settings.local.json        gitignored, personal
    ├── rules/                     committed
    │   ├── ledgers.md
    │   ├── guardrails.md
    │   ├── agents.md
    │   ├── sequencer.md
    │   └── frontend.md
    └── commands/                  committed
        ├── critique.md
        ├── contract-check.md
        ├── demo-check.md
        ├── handoff.md
        └── whats-left.md
```

```bash
cp CLAUDE.local.md.template CLAUDE.local.md
# then fill it in for yourself
```

## 2. Gitignore the personal files

Append to `.gitignore`:

```
CLAUDE.local.md
.claude/settings.local.json
```

## 3. Verify it actually loaded

Start Claude Code **from the repo root**, then:

```
/context
```

Under **Memory files** you should see `CLAUDE.md` and your `CLAUDE.local.md`.
If a file is not listed, Claude cannot see it. Use `/memory` to open and fix.

Path-scoped rules will not show at launch. That is correct — they load when Claude
opens a file matching their `paths:` pattern. Open `backend/ledgers/matrix.py`, run
`/context` again, and `ledgers.md` should now appear.

## 4. Confirm the commands registered

```
/critique
/contract-check
/demo-check
/handoff
/whats-left
```

They should autocomplete after typing `/`.

## 5. Always launch from the repo root

CLAUDE.md loads from your working directory and every directory above it. Launching
Claude Code from inside `backend/` means the root CLAUDE.md still loads, but launching
from outside the repo means nothing loads.

---

# How to actually use this over 24 hours

## The loop that works

1. **Plan before code.** For anything non-trivial, ask for the approach first and read it.
   Catching a wrong plan costs seconds. Catching wrong code costs an hour.
2. **Build.**
3. **Run `/critique`.** Every two hours minimum. It is calibrated to be adversarial.
4. **Run `/handoff` before any break or merge.** Paste it to your teammate.

## Run these at the sync points (hours 5, 10, 16, 21)

- `/contract-check` — catches the drift that silently breaks integration
- `/whats-left` — forces an honest cut decision while cutting is still cheap

## Feed corrections back

When you correct Claude on the same thing twice, that correction belongs in a file:

- Applies everywhere → add to `CLAUDE.md`
- Applies to one area → add to the matching `.claude/rules/*.md`
- Personal to you → `CLAUDE.local.md`

Claude also writes its own notes automatically. Run `/memory` to see what it saved and
delete anything wrong. Auto memory is per-repository and stays on your machine.

## When Claude ignores an instruction

1. `/context` — did the file actually load?
2. Is the instruction specific enough to verify? "Use rupee floats with an `_inr` suffix"
   works. "Handle money properly" does not.
3. Are two files contradicting each other? Claude picks arbitrarily when they do.

## Keep CLAUDE.md under 200 lines

Longer files consume context and get followed less reliably. If it grows, move the detail
into a path-scoped rule. That is the entire point of `.claude/rules/`.
