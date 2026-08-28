import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { inrExact, deadlineLabel, humanBreak, BASIS_LABEL } from '../lib/format'
import { rise } from '../lib/motion'

const MotionLink = motion(Link)

/** One row in the recovery queue.
 *
 * Laid out as a ledger line rather than a card: a hairline above, an urgency mark
 * in the margin, and the figure hard right so a column of them scans vertically.
 * Hover darkens the paper; it does not lift, because nothing here is floating.
 */
export default function CaseCard({ c, now, index = 0 }) {
  const dl = deadlineLabel(c.deadline_at, now)
  const modelled = c.basis === 'modelled'
  const mark = dl.overdue ? 'bg-oxblood' : dl.urgent ? 'bg-ochre' : 'bg-rule-strong'

  return (
    <MotionLink to={`/cases/${c.case_id}`} variants={rise} custom={index}
      className="row-hover group grid grid-cols-[3px_1fr_auto] gap-5 items-stretch
                 border-t border-rule px-4 py-5 -mx-4 cursor-pointer">
      {/* items-stretch, not items-start — the mark is a margin rule running the
          full height of the entry, the way a printed ledger flags a line. */}
      <div className={`w-[3px] ${mark} transition-colors duration-200`} />

      <div className="min-w-0">
        <h3 className="font-display text-[1.0625rem] leading-snug text-ink
                       group-hover:text-navy transition-colors duration-200">
          {c.title}
        </h3>
        <p className="mt-1 text-micro text-ink-2 leading-relaxed max-w-[46rem]">
          {c.summary}
        </p>
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="label text-ink-2">{c.resolver?.toLowerCase()}</span>
          <span className="w-1 h-1 bg-rule-strong rounded-full" />
          <span className="font-mono text-[0.6875rem] text-ink-3">
            {humanBreak(c.break_type)}
          </span>
          {/* Many rows share a title — the queue clusters by design, because it is
              ordered by money. The identifier is what makes two of them distinct. */}
          <span className="w-1 h-1 bg-rule-strong rounded-full" />
          <span className="font-mono text-[0.6875rem] text-ink-4">
            {c.payment_id || c.mandate_id || c.session_id || c.case_id}
          </span>
          {c.is_aggregate && (
            <>
              <span className="w-1 h-1 bg-rule-strong rounded-full" />
              <span className="label text-navy">
                {c.signal_count.toLocaleString('en-IN')} rolled up
              </span>
            </>
          )}
        </div>
      </div>

      <div className="text-right shrink-0 pl-4">
        <div className={`figure text-[1.375rem] leading-none ${modelled ? 'text-ochre' : 'text-ink'}`}>
          <span className="text-[0.55em] text-ink-3 font-sans mr-0.5">₹</span>
          {inrExact(c.rupees_at_risk_inr, c.rupees_at_risk_inr >= 1e5 ? 0 : 2)}
        </div>
        <div className={`label mt-1.5 ${modelled ? 'text-ochre' : 'text-ink-3'}`}>
          {BASIS_LABEL[c.basis]}
        </div>
        <div className={`text-[0.6875rem] mt-2 font-mono ${
          dl.overdue ? 'text-oxblood font-medium' : dl.urgent ? 'text-ochre' : 'text-ink-4'}`}>
          {dl.text}
        </div>
      </div>
    </MotionLink>
  )
}
