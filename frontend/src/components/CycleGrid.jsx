import { motion } from 'framer-motion'
import { EASE } from '../lib/motion'

/** The 30-day billing cycle, with the solver's 14-day search window drawn inside it.
 *
 * The days past the search window are not dead space — they are the point: NPCI
 * gives a 30-day cycle, but an attempt scheduled past day 14 is wasted (the model's
 * confidence in a slot that far out is not worth trusting), so the scheduler never
 * looks there. Making that boundary visible is more honest than hiding it.
 */
export default function CycleGrid({ salaryDay, searchDays, naive, sequenced, mandateKey }) {
  const cells = Array.from({ length: 30 }, (_, i) => i + 1)
  const naiveDay = naive?.attempts?.[0]
    ? new Date(naive.attempts[0].slot_at).getDate() : null
  const seqByDay = new Map(
    (sequenced?.attempts ?? []).map((a) => [new Date(a.slot_at).getDate(), a]))

  return (
    <div>
      <div className="grid grid-cols-10 gap-1.5">
        {cells.map((day) => {
          const inWindow = day <= searchDays
          const isSalary = day === salaryDay
          const seq = seqByDay.get(day)
          const isNaive = day === naiveDay
          return (
            <motion.div key={`${mandateKey}-${day}`}
              initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: day * 0.012, duration: 0.3, ease: EASE }}
              className={`relative h-14 border flex flex-col items-center justify-center
                ${inWindow ? 'border-rule bg-paper-raised' : 'border-rule bg-paper-deep opacity-50'}
                ${isSalary ? 'border-forest' : ''}`}>
              <span className="text-[0.625rem] text-ink-4 absolute top-1 left-1.5">{day}</span>
              {isSalary && (
                <span className="text-[0.5rem] text-forest absolute bottom-1 tracking-tight">
                  salary
                </span>
              )}
              {seq && (
                <motion.span
                  initial={{ scale: 0 }} animate={{ scale: 1 }}
                  transition={{ delay: 0.4 + day * 0.012, type: 'spring', stiffness: 300, damping: 16 }}
                  title={`${Math.round(seq.predicted_success * 100)}% predicted`}
                  className="w-2.5 h-2.5 rounded-full bg-forest" />
              )}
              {isNaive && (
                <motion.span
                  initial={{ scale: 0, rotate: -20 }} animate={{ scale: 1, rotate: 0 }}
                  transition={{ delay: 0.25, type: 'spring', stiffness: 300, damping: 16 }}
                  title="naive: blind retry on the 1st of next cycle, no slot selection"
                  className={`text-oxblood font-bold leading-none text-[0.95rem] select-none ${seq ? 'ml-3' : ''}`}>
                  ✕
                </motion.span>
              )}
            </motion.div>
          )
        })}
      </div>
      <div className="flex items-center gap-x-6 gap-y-2 pt-4 flex-wrap max-w-[46rem]">
        <Legend swatch={<span className="text-oxblood font-bold text-[0.8rem] leading-none inline-block">✕</span>} label="Naive slot (1st of next cycle)" />
        <Legend swatch={<span className="w-2.5 h-2.5 rounded-full bg-forest inline-block" />} label="Sequenced slot" />
        <Legend swatch={<span className="w-3 h-3 border border-forest inline-block" />} label="Predicted salary day" />
        <Legend swatch={<span className="w-3 h-3 bg-paper-deep opacity-50 inline-block" />} label={`Beyond ${searchDays}-day search window`} />
      </div>
    </div>
  )
}

const Legend = ({ swatch, label }) => (
  <span className="flex items-center gap-2 label text-ink-3">{swatch}{label}</span>
)
