import { motion } from 'framer-motion'
import Figure from './Figure'
import { rise, stagger } from '../lib/motion'

/** The three headline counters.
 *
 * The third is deliberately two figures inside one frame, divided by a rule and
 * never totalled. Confirmed money and modelled money are different kinds of claim,
 * and a merged number would be the one thing on this screen a sharp reader could
 * fault. The footnote says so out loud rather than leaving it implied.
 */
export default function RecoveredCounter({ counters, caseCounts }) {
  const c = counters || {}
  const n = caseCounts || {}

  return (
    <motion.section variants={stagger(0.1)} initial="hidden" animate="show"
      className="grid grid-cols-1 md:grid-cols-[1fr_1fr_1.4fr]">
      <motion.div variants={rise} className="pr-8 py-7">
        <div className="label">Recovered</div>
        <div className="mt-3"><Figure value={c.recovered_inr} /></div>
        <div className="mt-2 text-micro text-ink-3">
          {(n.resolved ?? 0).toLocaleString('en-IN')} cases closed
        </div>
      </motion.div>

      <motion.div variants={rise} className="px-8 py-7 md:border-l border-rule">
        <div className="label">Awaiting approval</div>
        <div className="mt-3"><Figure value={c.awaiting_approval_inr} /></div>
        <div className="mt-2 text-micro text-ink-3">
          {(n.awaiting_approval ?? 0).toLocaleString('en-IN')} need a human decision
        </div>
      </motion.div>

      <motion.div variants={rise} className="pl-8 py-7 md:border-l border-rule">
        <div className="label">At risk</div>
        <div className="mt-3 grid grid-cols-2 gap-6">
          <div>
            <div className="text-micro text-ink-2 mb-1.5 flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 bg-ink inline-block" /> Confirmed
            </div>
            <Figure value={c.deterministic_at_risk_inr} size="sm" />
          </div>
          <div className="pl-6 border-l border-rule">
            <div className="text-micro text-ink-2 mb-1.5 flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 inline-block hatch border border-ochre-edge" /> Estimated
            </div>
            <Figure value={c.modelled_at_risk_inr} size="sm" basis="modelled" />
          </div>
        </div>
        <div className="mt-3 text-[0.6875rem] leading-snug text-ink-4 max-w-[30rem]">
          Confirmed figures are read from ledger records. Estimated figures are model
          output. They are never added together.
        </div>
      </motion.div>
    </motion.section>
  )
}
