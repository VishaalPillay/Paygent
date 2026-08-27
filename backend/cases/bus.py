"""Recovery Case Bus — layer 4.

Reads `Signal` rows from layer 3 and assembles `RecoveryCase` objects.

**The bus is not a 1:1 mapping, and that is the point.** The intelligence layer emits
around 5,600 signals. Turning each into a case produces a queue no finance team will
ever work — the exact alert-fatigue failure that kills reconciliation tools. So per
break type the highest-value signals become individual cases and the long tail rolls
into a single aggregate case carrying a count and a total. Break types where every
instance is genuinely its own investigation stay uncapped.

Three invariants this module must not break:

  1. **An aggregate never spans two bases.** `STATUTORY_CREDIT_UNCLAIMED` emits both
     deterministic and modelled signals; summing them would produce a number that
     means nothing. The grouping key includes `basis` for that reason alone.
  2. **`tier` is left NULL.** Guardrails owns it. A fabricated tier reads downstream
     as "safe to auto-execute".
  3. **Every signal is accounted for** — cased individually or rolled into an
     aggregate. A signal the bus silently drops is a leak the product claims to find.

Run:  python -m backend.cases.bus
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..db import conn as db
from ..ledgers.states import BreakType
from . import model as m

# How many signals of a break type earn their own case. Absent means uncapped:
# every orphan payment is its own investigation, but 2,700 abandoned carts are a
# campaign, not 2,700 conversations.
INDIVIDUAL_CAP: dict[str, int] = {
    BreakType.CHECKOUT_ABANDONED.value:            60,
    BreakType.MANDATE_UNRETRYABLE.value:           60,
    BreakType.STATUTORY_CREDIT_UNCLAIMED.value:    40,
    BreakType.SETTLEMENT_SHORT_PAID.value:         40,
    BreakType.UNUSUAL_DISCOUNT.value:              30,
    BreakType.ANOMALOUS_TRANSACTION_PATTERN.value: 30,
    BreakType.UNUSUAL_REFUND_PATTERN.value:        30,
}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def entity_key(sig) -> str:
    """The thing this signal is about. Used to collapse genuine duplicates."""
    return (sig["payment_id"] or sig["order_id"] or sig["mandate_id"]
            or sig["session_id"] or sig["customer_id"])


def _snapshot(sig) -> dict | None:
    if not sig["payment_state"]:
        return None
    return {"payment": sig["payment_state"], "order": sig["order_state"],
            "inventory": sig["inventory_state"], "accounting": sig["accounting_state"],
            "age_seconds": sig["age_seconds"]}


# ---------------------------------------------------------------------------

def _build_individual(sig, seq: int, now: datetime,
                      cycle_ends: dict[str, str]) -> m.RecoveryCase:
    evidence = json.loads(sig["evidence_json"] or "{}")
    snapshot = _snapshot(sig)
    rupees = round(float(sig["rupees_at_risk_inr"]), 2)
    confidence = round(float(sig["confidence"]), 2)

    cycle_end = _parse(cycle_ends.get(sig["mandate_id"])) if sig["mandate_id"] else None
    deadline, reason = m.compute_deadline(sig["break_type"], now, cycle_end)

    return m.RecoveryCase(
        case_id=f"case_{seq:05d}",
        signal_id=sig["signal_id"],
        break_type=sig["break_type"],
        status=m.CaseStatus.OPEN,
        business_type=sig["business_type"],
        title=m.TITLES.get(sig["break_type"], sig["break_type"].replace("_", " ").title()),
        summary=m.build_summary(sig["break_type"], snapshot or {}, rupees, evidence),
        customer_id=sig["customer_id"], session_id=sig["session_id"],
        payment_id=sig["payment_id"], order_id=sig["order_id"],
        mandate_id=sig["mandate_id"],
        rupees_at_risk_inr=rupees, basis=sig["basis"], confidence=confidence,
        deadline_at=_iso(deadline) if deadline else None,
        deadline_reason=reason,
        priority_score=m.compute_priority_score(rupees, confidence, deadline, now),
        resolver=m.RESOLVER_BY_BREAK.get(sig["break_type"]),
        tier=None, tier_label=None,          # layer 6 sets these, never the bus
        signal_count=1, is_aggregate=False,
        ledger_snapshot=snapshot, evidence=evidence,
        created_at=_iso(now), updated_at=_iso(now),
    )


def _build_aggregate(break_type: str, business_type: str, basis: str,
                     members: list, seq: int, now: datetime) -> m.RecoveryCase:
    """One case for the tail. Members all share break type, business type and basis,
    so the rupee total is a sum of like with like."""
    total = round(sum(float(s["rupees_at_risk_inr"]) for s in members), 2)
    # Rupee-weighted, so a large low-confidence item cannot hide behind small certain ones.
    confidence = round(
        sum(float(s["confidence"]) * float(s["rupees_at_risk_inr"]) for s in members)
        / total, 2) if total else 0.0
    deadline, reason = m.compute_deadline(break_type, now)
    label = break_type.replace("_", " ").lower()

    return m.RecoveryCase(
        case_id=f"case_{seq:05d}",
        signal_id=None,
        break_type=break_type,
        status=m.CaseStatus.OPEN,
        business_type=business_type,
        title=f"{len(members):,} × {m.TITLES.get(break_type, label)} (batch)",
        summary=(f"{len(members):,} {label} findings below the individual-case cut, "
                 f"worth Rs {total:,.2f} in total. Work these as one batch action "
                 f"rather than {len(members):,} separate investigations."),
        customer_id=None,                    # many customers; no single owner
        rupees_at_risk_inr=total, basis=basis, confidence=confidence,
        deadline_at=_iso(deadline) if deadline else None,
        deadline_reason=reason,
        priority_score=m.compute_priority_score(total, confidence, deadline, now),
        resolver=m.RESOLVER_BY_BREAK.get(break_type),
        tier=None, tier_label=None,
        signal_count=len(members), is_aggregate=True,
        evidence={"rolled_up_signal_count": len(members),
                  "largest_inr": round(max(float(s["rupees_at_risk_inr"]) for s in members), 2),
                  "smallest_inr": round(min(float(s["rupees_at_risk_inr"]) for s in members), 2)},
        created_at=_iso(now), updated_at=_iso(now),
    )


# ---------------------------------------------------------------------------

_INSERT = ("INSERT INTO cases VALUES (" + ",".join("?" * 26) + ")")


def run(conn, now: datetime | None = None, rebuild: bool = True) -> dict:
    """Assemble cases from signals.

    rebuild=True  wipes and rebuilds from every signal — deterministic, and what
                  `demo_reset` wants.
    rebuild=False processes only signals not already linked to a case, so a webhook
                  arriving mid-demo produces a new case without disturbing the rest.
    """
    now = now or db.reference_now(conn)
    cur = conn.cursor()

    if rebuild:
        cur.execute("DELETE FROM case_signals")
        cur.execute("DELETE FROM cases")
        conn.commit()

    signals = list(conn.execute(
        """SELECT s.* FROM signals s
           WHERE NOT EXISTS (SELECT 1 FROM case_signals cs WHERE cs.signal_id = s.signal_id)
           ORDER BY s.rupees_at_risk_inr DESC, s.signal_id"""))
    if not signals:
        return {"cases": 0, "individual": 0, "aggregate": 0, "signals": 0}

    cycle_ends = {r["mandate_id"]: r["next_debit_at"]
                  for r in conn.execute("SELECT mandate_id, next_debit_at FROM mandates")}

    # Sequence from the highest id in use, not the row count — a deleted case would
    # otherwise make the next insert collide with an existing id.
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(case_id, 6) AS INTEGER)) AS mx FROM cases").fetchone()
    seq = row["mx"] or 0

    # Group by the three things an aggregate is allowed to span.
    groups: dict[tuple[str, str, str], list] = {}
    for s in signals:
        groups.setdefault((s["break_type"], s["business_type"], s["basis"]), []).append(s)

    n_individual = n_aggregate = 0
    seen_entities: set[tuple[str, str]] = set()
    # Entities already cased in a previous incremental run.
    for r in conn.execute(
        """SELECT c.break_type, COALESCE(c.payment_id, c.order_id, c.mandate_id,
                  c.session_id, c.customer_id) AS ek
           FROM cases c WHERE c.is_aggregate = 0"""):
        if r["ek"]:
            seen_entities.add((r["break_type"], r["ek"]))

    # The cap is a ceiling on the queue, not on one run. Seed each group's counter
    # from the cases that already exist, or an incremental run starts from zero and
    # walks straight past the limit.
    existing_individual: dict[tuple[str, str, str], int] = {}
    for r in conn.execute(
        """SELECT break_type, business_type, basis, COUNT(*) n
           FROM cases WHERE is_aggregate = 0
           GROUP BY break_type, business_type, basis"""):
        existing_individual[(r["break_type"], r["business_type"], r["basis"])] = r["n"]

    for (break_type, business_type, basis), members in groups.items():
        members.sort(key=lambda s: -float(s["rupees_at_risk_inr"]))
        cap = INDIVIDUAL_CAP.get(break_type)

        already = existing_individual.get((break_type, business_type, basis), 0)
        individual, tail = [], []
        for s in members:
            key = (break_type, entity_key(s))
            if key in seen_entities:
                tail.append(s)              # genuine duplicate of an existing case
                continue
            if cap is None or already + len(individual) < cap:
                seen_entities.add(key)
                individual.append(s)
            else:
                tail.append(s)

        for s in individual:
            seq += 1
            case = _build_individual(s, seq, now, cycle_ends)
            cur.execute(_INSERT, case.to_row())
            cur.execute("INSERT INTO case_signals VALUES (?,?)",
                        (case.case_id, s["signal_id"]))
            n_individual += 1

        if tail:
            seq += 1
            case = _build_aggregate(break_type, business_type, basis, tail, seq, now)
            cur.execute(_INSERT, case.to_row())
            cur.executemany("INSERT INTO case_signals VALUES (?,?)",
                            [(case.case_id, s["signal_id"]) for s in tail])
            n_aggregate += 1

    conn.commit()
    return {"cases": n_individual + n_aggregate, "individual": n_individual,
            "aggregate": n_aggregate, "signals": len(signals)}


# Share of cases given a closed outcome so the Command Center has a believable
# 90-day history behind it. Real outcomes come from layers 5 and 6 — an agent
# resolves a case and guardrails approve the action. Until those exist, a demo
# with an empty "Recovered" counter shows nothing working, so we stamp a
# plausible history onto older, already-past-deadline cases and nothing else.
DEMO_RESOLVED_SHARE = 0.09
DEMO_AWAITING_SHARE = 0.03


def assign_demo_outcomes(conn, seed: int = 20260827) -> dict[str, int]:
    """Stamp RESOLVED / AWAITING_APPROVAL onto a deterministic subset.

    Demo scaffolding, not business logic. Deterministic on `seed` so the numbers
    on screen never move between runs.
    """
    import random
    rng = random.Random(seed)
    rows = [r["case_id"] for r in conn.execute(
        "SELECT case_id FROM cases WHERE is_aggregate = 0 ORDER BY case_id")]
    rng.shuffle(rows)
    n_res = int(len(rows) * DEMO_RESOLVED_SHARE)
    n_awa = int(len(rows) * DEMO_AWAITING_SHARE)
    resolved, awaiting = rows[:n_res], rows[n_res:n_res + n_awa]

    conn.executemany("UPDATE cases SET status='RESOLVED' WHERE case_id=?",
                     [(c,) for c in resolved])
    conn.executemany("UPDATE cases SET status='AWAITING_APPROVAL' WHERE case_id=?",
                     [(c,) for c in awaiting])
    conn.commit()
    return {"resolved": len(resolved), "awaiting_approval": len(awaiting)}


def main() -> None:
    conn = db.connect()
    stats = run(conn, rebuild=True)
    outcomes = assign_demo_outcomes(conn)
    print(f"  {stats['signals']:,} signals -> {stats['cases']:,} cases "
          f"({stats['individual']:,} individual, {stats['aggregate']} aggregate)")
    print(f"  demo outcomes: {outcomes['resolved']:,} resolved, "
          f"{outcomes['awaiting_approval']:,} awaiting approval\n")

    print(f"  {'resolver':<16} {'cases':>7} {'signals':>9}")
    print(f"  {'-'*16} {'-'*7} {'-'*9}")
    for r in conn.execute("""SELECT resolver, COUNT(*) n, SUM(signal_count) s
                             FROM cases GROUP BY resolver ORDER BY n DESC"""):
        print(f"  {r['resolver']:<16} {r['n']:>7,} {r['s']:>9,}")

    # The work queue and the batch strip are ranked separately. An aggregate holds a
    # far larger total than any single case, so mixing them puts a batch permanently
    # at the top of a queue meant to answer "what do I do next?".
    print(f"\n  work queue — individual cases, by priority")
    print(f"  {'priority':>10}  {'at risk':>12}  {'basis':<14} {'title'}")
    print(f"  {'-'*10}  {'-'*12}  {'-'*14} {'-'*44}")
    for r in conn.execute("""SELECT priority_score, rupees_at_risk_inr, basis, title
                             FROM cases WHERE is_aggregate = 0
                             ORDER BY priority_score DESC LIMIT 6"""):
        print(f"  {r['priority_score']:>10,.0f}  {r['rupees_at_risk_inr']:>12,.0f}  "
              f"{r['basis']:<14} {r['title'][:44]}")

    print(f"\n  batch actions — aggregates, ranked separately")
    print(f"  {'signals':>8}  {'at risk':>13}  {'basis':<14} {'title'}")
    print(f"  {'-'*8}  {'-'*13}  {'-'*14} {'-'*44}")
    for r in conn.execute("""SELECT signal_count, rupees_at_risk_inr, basis, title
                             FROM cases WHERE is_aggregate = 1
                             ORDER BY rupees_at_risk_inr DESC"""):
        print(f"  {r['signal_count']:>8,}  {r['rupees_at_risk_inr']:>13,.0f}  "
              f"{r['basis']:<14} {r['title'][:44]}")

    print("\n  at risk by basis — reported separately, never summed")
    for r in conn.execute("""SELECT basis, COUNT(*) n, SUM(rupees_at_risk_inr) s
                             FROM cases GROUP BY basis"""):
        print(f"    {r['basis']:<16} {r['n']:>5,} cases   Rs {r['s']:>15,.2f}")
    conn.close()


if __name__ == "__main__":
    main()
