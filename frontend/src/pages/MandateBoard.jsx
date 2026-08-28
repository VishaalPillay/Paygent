import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { fetchMandates } from '../api'
import { inrExact, inrShort, humanBreak } from '../lib/format'
import { rise, stagger, EASE } from '../lib/motion'
import CycleGrid from '../components/CycleGrid'

/** Screen 4 — the retry scheduler shown honestly.
 *
 * Two panels: pick a mandate on the left, see what the scheduler decided on the
 * right. The one thing this screen has to prove is that the router refuses to
 * spend a retry on a structurally dead mandate — the calendar staying empty for a
 * REVOKED mandate is the actual demo beat, not decoration around it.
 */
export default function MandateBoard() {
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    fetchMandates().then((d) => { setData(d); setSelected(d.items[0]?.mandate_id ?? null) })
  }, [])

  if (!data) return <main className="max-w-canvas mx-auto px-10 pt-24"><Skeleton /></main>

  const m = data.items.find((i) => i.mandate_id === selected) ?? data.items[0]
  const retryable = m?.router_verdict?.retryable

  return (
    <main className="max-w-canvas mx-auto px-10">
      <div className="pt-12 pb-8">
        <p className="label text-ink-3">Mandate Board</p>
        <h1 className="font-display text-[2.25rem] tracking-[-0.02em] mt-2 max-w-[36ch]">
          Every retry is scarce. NPCI allows {data.max_attempts_per_cycle} per cycle.
        </h1>
        <p className="text-base text-ink-2 mt-2 max-w-[58ch]">
          The router refuses a structurally dead mandate before the model ever scores a
          slot — half of all failures are unretryable, and burning attempts on them is
          the industry default.
        </p>
      </div>
      <div className="h-px bg-ink" />

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.9fr] gap-0 mt-8 mb-20">
        {/* ---- mandate list --------------------------------------------- */}
        <div className="lg:border-r border-rule lg:pr-8">
          <div className="flex items-baseline justify-between mb-3">
            <span className="label">{data.total} mandates</span>
            <span className="label text-ink-4">
              {data.items.filter((i) => !i.router_verdict.retryable).length} blocked
            </span>
          </div>
          <motion.div variants={stagger(0.02)} initial="hidden" animate="show">
            {data.items.map((i) => (
              <MandateRow key={i.mandate_id} item={i}
                active={i.mandate_id === m.mandate_id}
                onClick={() => setSelected(i.mandate_id)} />
            ))}
          </motion.div>
        </div>

        {/* ---- selected mandate ------------------------------------------ */}
        <div className="lg:pl-10 pt-1">
          <motion.div key={m.mandate_id} variants={stagger(0.06)} initial="hidden" animate="show">
            <motion.div variants={rise} className="flex items-start justify-between mb-1">
              <div>
                <h2 className="font-display text-[1.625rem]">{m.customer_name}</h2>
                <p className="text-micro text-ink-3 mt-0.5">
                  Age {m.age} · {m.bank} · {humanBreak(m.state)} ·{' '}
                  <span className="figure">₹{inrExact(m.debit_amount_inr, 0)}</span> per cycle
                  {m.requires_afa && <span className="text-ochre"> · AFA/OTP required</span>}
                </p>
              </div>
              <div className="text-right">
                <div className="figure text-[1.125rem]">
                  {m.attempts_used}/{data.max_attempts_per_cycle}
                </div>
                <div className="label text-ink-4">attempts used</div>
              </div>
            </motion.div>

            {!retryable ? (
              <motion.div variants={rise}
                className="mt-7 border-2 border-oxblood bg-oxblood-bg px-7 py-8">
                <div className="label text-oxblood">0 retries scheduled</div>
                <p className="font-display text-[1.375rem] mt-2 max-w-[38ch]">
                  {m.router_verdict.reason}
                </p>
                <p className="text-micro text-ink-3 mt-3 max-w-[52ch]">
                  Every check the router runs before a single retry is committed:
                </p>
                <ul className="mt-2 space-y-1">
                  {m.router_verdict.checks.map((c) => (
                    <li key={c.name} className="text-micro font-mono flex items-baseline gap-2">
                      <span className={c.passed ? 'text-forest' : 'text-oxblood'}>
                        {c.passed ? '✓' : '✕'}
                      </span>
                      <span className="text-ink-3">{c.name}</span>
                    </li>
                  ))}
                </ul>
              </motion.div>
            ) : (
              <motion.div variants={rise} className="mt-7">
                <CycleGrid mandateKey={m.mandate_id} salaryDay={m.predicted_salary_day}
                  searchDays={data.search_days} naive={m.naive} sequenced={m.sequenced} />
              </motion.div>
            )}

            {/* ---- naive vs sequenced ---------------------------------- */}
            <motion.div variants={rise} className="grid grid-cols-2 mt-9 border-t border-ink pt-5">
              <PlanColumn label="Naive" plan={m.naive} tone="oxblood" />
              <PlanColumn label="Sequenced" plan={m.sequenced} tone="forest" bordered />
            </motion.div>
            {retryable && (
              <motion.p variants={rise} className="text-micro text-ink-3 mt-3">
                Uplift: <span className="figure text-ochre">₹{inrExact(m.uplift_inr, 0)}</span>{' '}
                <span className="label text-ochre">estimated</span> — both plans are model
                output, never a ledger fact.
              </motion.p>
            )}

            {/* ---- the model's own evidence ------------------------------ */}
            {data.model_card.available && (
              <motion.div variants={rise} className="mt-10 pt-6 border-t border-rule">
                <div className="label mb-3">
                  Retry-success model · AUC {data.model_card.auc}
                </div>
                {data.model_card.features.map((f) => (
                  <div key={f.name} className="grid grid-cols-[13rem_1fr_3rem] items-center gap-3 mb-1.5">
                    <span className="text-micro text-ink-2 truncate">{humanBreak(f.name)}</span>
                    <div className="h-[3px] bg-paper-deep">
                      <motion.div className="h-full bg-navy origin-left"
                        initial={{ scaleX: 0 }} animate={{ scaleX: 1 }}
                        transition={{ duration: 0.7, ease: EASE }}
                        style={{ width: `${f.gain_pct}%` }} />
                    </div>
                    <span className="figure text-[0.75rem] text-right">{f.gain_pct}%</span>
                  </div>
                ))}
                <p className="text-micro text-ink-4 mt-2 max-w-[54ch]">
                  The model was never given salary dates. It rediscovered
                  <span className="text-ink-2"> days since predicted salary credit </span>
                  as its dominant signal from outcomes alone.
                </p>
              </motion.div>
            )}
          </motion.div>
        </div>
      </div>
    </main>
  )
}

function MandateRow({ item: i, active, onClick }) {
  const blocked = !i.router_verdict.retryable
  return (
    <motion.button variants={rise} onClick={onClick}
      className={`row-hover w-full text-left grid grid-cols-[3px_1fr_auto] gap-4 items-stretch
        border-t border-rule px-3 py-3.5 -mx-3 ${active ? 'bg-paper-deep' : ''}`}>
      <div className={`w-[3px] ${blocked ? 'bg-oxblood' : 'bg-forest'}`} />
      <div className="min-w-0">
        <div className="text-[0.8125rem] text-ink truncate">{i.customer_name}</div>
        <div className="text-micro text-ink-3">Age {i.age} · {i.bank} · {i.attempts_used}/4 used</div>
      </div>
      <div className="text-right shrink-0">
        <div className="figure text-[0.9375rem]">₹{inrShort(i.debit_amount_inr)}</div>
        <div className={`label ${blocked ? 'text-oxblood' : 'text-forest'}`}>
          {blocked ? 'blocked' : 'retryable'}
        </div>
      </div>
    </motion.button>
  )
}

function PlanColumn({ label, plan, tone, bordered }) {
  return (
    <div className={bordered ? 'pl-8 border-l border-rule' : 'pr-8'}>
      <div className="label mb-2">{label}</div>
      <div className="figure text-[1.375rem]">₹{inrExact(plan.expected_recovery_inr, 0)}</div>
      <div className="text-micro text-ink-3 mt-1">
        {plan.attempts.length} attempt{plan.attempts.length === 1 ? '' : 's'} scheduled
      </div>
    </div>
  )
}

const Skeleton = () => (
  <div className="animate-pulse pt-2">
    <div className="h-2.5 w-40 bg-rule" />
    <div className="h-10 w-[30rem] max-w-full bg-rule mt-5" />
    <div className="h-px w-full bg-rule mt-10" />
    <div className="grid grid-cols-[1fr_1.9fr] gap-10 mt-10">
      <div className="space-y-3">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-14 bg-rule" />)}</div>
      <div className="h-64 bg-rule" />
    </div>
  </div>
)
