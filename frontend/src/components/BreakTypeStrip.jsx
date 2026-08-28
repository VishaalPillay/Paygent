import { motion } from 'framer-motion'
import { inrShort, humanBreak } from '../lib/format'
import { rise, stagger, EASE } from '../lib/motion'

/** Grouped by detection engine, not just break type.
 *
 * Every break type comes from exactly one of three engines: the Consistency
 * Matrix (ledger facts), Anomaly Detection (rules + statistics), or ML Scorers
 * (predictions). Grouping this way is what makes each engine visible on screen
 * instead of blending into one flat list.
 */

const ENGINES = [
  { key: 'consistency_matrix', label: 'Consistency Matrix',
    blurb: 'Compares the four ledgers. Anything outside a legal combination is a fact, not a guess.' },
  { key: 'anomaly', label: 'Anomaly Detection',
    blurb: 'Rules and statistics over normal-looking transactions that do not add up.' },
  { key: 'ml_scorer', label: 'ML Scorers',
    blurb: 'Predicts what has not happened yet — always labelled Estimated.' },
]

function Row({ r, max }) {
  const modelled = r.basis === 'modelled'
  return (
    <motion.div variants={rise}
      className="border-t border-rule py-3 grid grid-cols-[1fr_auto] gap-4 items-baseline">
      <div className="min-w-0">
        <div className="flex items-baseline gap-2.5">
          <span className="text-[0.8125rem] text-ink capitalize truncate">
            {humanBreak(r.break_type)}
          </span>
          <span className="label text-ink-4 shrink-0">{r.case_count.toLocaleString('en-IN')}</span>
        </div>
        <div className="mt-1.5 h-[3px] bg-paper-deep w-full">
          <motion.div
            className={`h-full origin-left ${modelled ? '' : 'bg-oxblood'}`}
            style={{
              width: `${Math.max((r.rupees_at_risk_inr / max) * 100, 1.5)}%`,
              ...(modelled ? { backgroundImage:
                'repeating-linear-gradient(-45deg, #F6EAE7 0 2px, #8C2B22 2px 4px)' } : {}),
            }}
            initial={{ scaleX: 0 }} whileInView={{ scaleX: 1 }}
            viewport={{ once: true }} transition={{ duration: 0.8, ease: EASE }}
          />
        </div>
      </div>
      <span className={`figure text-[0.9375rem] ${modelled ? 'text-ochre' : 'text-ink-2'}`}>
        ₹{inrShort(r.rupees_at_risk_inr)}
      </span>
    </motion.div>
  )
}

export default function BreakTypeStrip({ rows }) {
  if (!rows?.length) return null
  const max = Math.max(...rows.map((r) => r.rupees_at_risk_inr))

  return (
    <section className="pt-11 pb-2">
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="font-display text-[1.75rem] tracking-[-0.02em]">Detection engines</h2>
        <span className="label">{rows.length} distinct failures detected</span>
      </div>
      <p className="text-micro text-ink-3 max-w-[60ch] mb-8">
        Three engines, three kinds of evidence — grouped so each stays visible
        rather than blending into one list.
      </p>

      {ENGINES.map((eng) => {
        const group = rows.filter((r) => r.engine === eng.key)
        if (!group.length) return null
        return (
          <div key={eng.key} className="mb-9">
            <div className="flex items-baseline gap-3 mb-0.5">
              <h3 className="text-[0.9375rem] text-ink">{eng.label}</h3>
              <span className="label text-ink-4">{group.length}</span>
            </div>
            <p className="text-micro text-ink-3 mb-3">{eng.blurb}</p>
            <motion.div variants={stagger(0.035)} initial="hidden" whileInView="show"
              viewport={{ once: true, margin: '-40px' }}
              className="grid grid-cols-1 lg:grid-cols-2 gap-x-12">
              {group.map((r) => (
                <Row key={`${r.break_type}-${r.basis}`} r={r} max={max} />
              ))}
            </motion.div>
          </div>
        )
      })}
      <div className="border-t border-rule" />
    </section>
  )
}
