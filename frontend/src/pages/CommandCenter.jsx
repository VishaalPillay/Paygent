import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { fetchSummary, fetchCases } from '../api'
import { inrShort } from '../lib/format'
import { rise, stagger, EASE } from '../lib/motion'
import Waterfall from '../components/Waterfall'
import RecoveredCounter from '../components/RecoveredCounter'
import CaseCard from '../components/CaseCard'
import BreakTypeStrip from '../components/BreakTypeStrip'

/** Screen 1.
 *
 * Its only job is to make an invisible problem visible, so it contains no AI
 * feature at all. Every figure here is read from ledger records or declared as
 * model output. The waterfall is the argument; the queue is the consequence.
 */
export default function CommandCenter({ onLoaded }) {
  const [summary, setSummary] = useState(null)
  const [cases, setCases] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    Promise.all([fetchSummary(), fetchCases()])
      .then(([s, c]) => { setSummary(s); setCases(c); onLoaded?.(s) })
      .catch((e) => setError(e.message))
  }, [onLoaded])

  if (error) return <Shell><Message text={`Could not load data — ${error}`} tone="bad" /></Shell>
  if (!summary) return <Shell><Skeleton /></Shell>

  const w = summary.waterfall
  const leaked = w.gross_intended_inr - w.realised_inr
  const pct = ((leaked / w.gross_intended_inr) * 100).toFixed(0)
  const queue = cases?.items ?? []
  const aggregates = cases?.aggregates ?? []

  return (
    <Shell>
      {/* ---- the argument -------------------------------------------------- */}
      <motion.div variants={stagger(0.09)} initial="hidden" animate="show" className="pt-12">
        <motion.p variants={rise} className="label text-ink-3">
          Ninety days · all channels
        </motion.p>
        <motion.h1 variants={rise}
          className="font-display text-[2.75rem] md:text-[3.5rem] leading-[1.05]
                     tracking-[-0.025em] mt-4 max-w-[22ch]">
          Where <span className="text-ink">₹{inrShort(w.gross_intended_inr)}</span> became{' '}
          <span className="text-forest">₹{inrShort(w.realised_inr)}</span>
        </motion.h1>
        <motion.p variants={rise}
          className="mt-5 text-base text-ink-2 max-w-[52ch] leading-relaxed">
          {pct}% of checkout intent never reached the bank. It leaked across six
          sequential gates, each sitting inside a different company's system — and no
          merchant has visibility across more than one of them.
        </motion.p>
      </motion.div>

      <motion.div className="h-px bg-rule mt-11 origin-left"
        initial={{ scaleX: 0 }} animate={{ scaleX: 1 }}
        transition={{ duration: 1, ease: EASE, delay: 0.35 }} />

      {/* ---- the waterfall ------------------------------------------------- */}
      <section className="pt-10 pb-4">
        <Waterfall data={w} />
      </section>

      <div className="h-px bg-ink mt-6" />

      {/* ---- the counters -------------------------------------------------- */}
      <RecoveredCounter counters={summary.counters} caseCounts={summary.case_counts} />

      <div className="h-px bg-ink" />

      {/* ---- what kinds of leak, and how much each costs -------------------- */}
      <BreakTypeStrip rows={summary.by_break_type} />

      {/* ---- the queue ----------------------------------------------------- */}
      <section className="pt-12 pb-6">
        <div className="flex items-baseline justify-between mb-1">
          <h2 className="font-display text-[1.75rem] tracking-[-0.02em]">Recovery queue</h2>
          <span className="label">
            {(cases?.total ?? 0).toLocaleString('en-IN')} open · sorted by rupees × urgency
          </span>
        </div>
        <p className="text-micro text-ink-3 max-w-[54ch] mb-2">
          Not a log. Ordered by what it costs to leave alone, so the top of this list is
          always the next thing worth doing.
        </p>

        <motion.div variants={stagger(0.045, 0.1)} initial="hidden" animate="show" className="mt-6">
          {queue.slice(0, 6).map((c, i) => (
            <CaseCard key={c.case_id} c={c} index={i} now={summary.generated_at} />
          ))}
          <div className="border-t border-rule" />
        </motion.div>
      </section>

    </Shell>
  )
}

const Shell = ({ children }) => (
  <main className="max-w-canvas mx-auto px-10">{children}</main>
)

const Message = ({ text, tone }) => (
  <div className={`mt-24 border-l-2 pl-5 py-3 ${
    tone === 'bad' ? 'border-oxblood text-oxblood' : 'border-rule text-ink-3'}`}>
    <p className="text-base">{text}</p>
  </div>
)

/** A loading state that looks like the page being typeset, rather than a spinner.
 *  Empty and loading states matter more than polish here — a blank screen during a
 *  live demo reads as broken even when nothing is wrong. */
const Skeleton = () => (
  <div className="pt-12 animate-pulse">
    <div className="h-2.5 w-40 bg-rule" />
    <div className="h-12 w-[34rem] max-w-full bg-rule mt-6" />
    <div className="h-12 w-[24rem] max-w-full bg-rule mt-3" />
    <div className="h-px w-full bg-rule mt-12" />
    <div className="flex items-end gap-4 mt-12 h-[300px]">
      {[100, 62, 48, 44, 34, 26, 24, 26].map((h, i) => (
        <div key={i} className="flex-1 bg-rule" style={{ height: `${h}%` }} />
      ))}
    </div>
  </div>
)
