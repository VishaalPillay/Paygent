import { motion } from 'framer-motion'
import { inrShort, humanBreak } from '../lib/format'
import { rise, stagger, EASE } from '../lib/motion'

/** The leak taxonomy.
 *
 * The queue below is ordered by money, so it clusters on whichever break type
 * carries the biggest tickets — which is honest, but it hides how many distinct
 * failures the engine actually separates. This strip is where that breadth lives:
 * every break type the four-ledger matrix can name, with what each is costing.
 *
 * Bars are proportional within each basis and never across them, because a
 * confirmed rupee and a modelled rupee are not the same length of anything.
 */
export default function BreakTypeStrip({ rows }) {
  if (!rows?.length) return null
  const max = Math.max(...rows.map((r) => r.rupees_at_risk_inr))

  return (
    <section className="pt-11 pb-2">
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="font-display text-[1.75rem] tracking-[-0.02em]">Leak taxonomy</h2>
        <span className="label">{rows.length} distinct failures detected</span>
      </div>
      <p className="text-micro text-ink-3 max-w-[56ch] mb-6">
        The engine enumerates every legal combination of the four ledgers and treats
        everything else as a break. These are what it found.
      </p>

      <motion.div variants={stagger(0.035)} initial="hidden" whileInView="show"
        viewport={{ once: true, margin: '-60px' }}
        className="grid grid-cols-1 lg:grid-cols-2 gap-x-12">
        {rows.map((r) => {
          const modelled = r.basis === 'modelled'
          return (
            <motion.div key={`${r.break_type}-${r.basis}`} variants={rise}
              className="group border-t border-rule py-3 grid grid-cols-[1fr_auto] gap-4 items-baseline">
              <div className="min-w-0">
                <div className="flex items-baseline gap-2.5">
                  <span className="text-[0.8125rem] text-ink capitalize truncate">
                    {humanBreak(r.break_type)}
                  </span>
                  <span className="label text-ink-4 shrink-0">
                    {r.case_count.toLocaleString('en-IN')}
                  </span>
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
                    viewport={{ once: true }}
                    transition={{ duration: 0.8, ease: EASE }}
                  />
                </div>
              </div>
              <div className="text-right shrink-0">
                <span className={`figure text-[0.9375rem] ${modelled ? 'text-ochre' : 'text-ink-2'}`}>
                  ₹{inrShort(r.rupees_at_risk_inr)}
                </span>
              </div>
            </motion.div>
          )
        })}
      </motion.div>
      <div className="border-t border-rule" />
    </section>
  )
}
