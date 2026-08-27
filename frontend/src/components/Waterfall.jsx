import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { inrShort } from '../lib/format'
import { EASE } from '../lib/motion'

/* Hand-built SVG rather than a chart library.
 *
 * A waterfall is not a Recharts primitive — you fake it with a stacked bar and a
 * transparent base segment, and then every visual decision fights the library's
 * defaults. Drawing it directly costs about a hundred lines and buys exact control
 * of the one thing that matters: modelled buckets must be visibly hatched, not
 * merely a different colour.
 *
 * Two things worth knowing if you edit this:
 *   - Text is animated on OPACITY ONLY. Framer Motion treats `y` on an SVG node as
 *     a transform, which stacks with the `y` attribute and silently doubles the
 *     offset. Position stays a static attribute.
 *   - TOP_PAD exists so the tallest bar's value label has somewhere to live. The
 *     first loss bar starts at the very top of the plot, so without it the label
 *     renders outside the viewBox.
 */

const W = 1200
const PLOT_H = 290
const TOP_PAD = 46
const BOTTOM_PAD = 58
const PAD_X = 8
const GAP = 14
const MIN_BAR = 5          // a bucket worth 1% of the total is still worth seeing

// Short names for the axis. Deriving these from the stage string gave two bars
// called "Realised", because every gate's destination is the next gate's origin.
const SHORT = {
  B1: 'Abandoned', B2: 'Declined', B3: 'No order',
  B4: 'Fees & holds', B5: 'Reversals', B6: 'Unclaimed GST',
}

export default function Waterfall({ data }) {
  const ref = useRef(null)
  const inView = useInView(ref, { once: true, margin: '-80px' })
  if (!data?.buckets?.length) return null

  const { gross_intended_inr: gross, realised_inr: realised, buckets } = data
  const cols = [
    { key: 'start', label: 'Checkout intent', value: gross, kind: 'total' },
    ...buckets.map((b) => ({
      key: b.id, id: b.id, label: SHORT[b.id] ?? b.id, value: b.leaked_inr,
      kind: 'loss', basis: b.basis, entering: b.entering_inr, detail: b.detail,
    })),
    { key: 'end', label: 'Realised', value: realised, kind: 'total', tone: 'good' },
  ]

  const colW = (W - PAD_X * 2 - GAP * (cols.length - 1)) / cols.length
  const scale = PLOT_H / gross
  const baseline = TOP_PAD + PLOT_H
  const xAt = (i) => PAD_X + i * (colW + GAP)

  const geom = cols.map((c, i) => {
    const h = Math.max(c.value * scale, MIN_BAR)
    const y = c.kind === 'total'
      ? baseline - h
      : TOP_PAD + (gross - c.entering) * scale
    return { ...c, x: xAt(i), y, h }
  })

  return (
    <div ref={ref} className="w-full">
      <svg viewBox={`0 0 ${W} ${TOP_PAD + PLOT_H + BOTTOM_PAD}`}
           className="w-full h-auto" role="img"
           aria-label="Revenue waterfall across six sequential leak gates">
        <defs>
          <pattern id="wf-hatch" width="7" height="7" patternTransform="rotate(-45)"
                   patternUnits="userSpaceOnUse">
            <rect width="7" height="7" fill="#F6EAE7" />
            <line x1="0" y1="0" x2="0" y2="7" stroke="#8C2B22" strokeWidth="3" opacity="0.62" />
          </pattern>
        </defs>

        <line x1={0} x2={W} y1={TOP_PAD + PLOT_H / 2} y2={TOP_PAD + PLOT_H / 2}
              stroke="#E2DCCC" strokeWidth="1" strokeDasharray="2 6" />
        <line x1={0} x2={W} y1={baseline} y2={baseline} stroke="#CFC7B2" strokeWidth="1" />

        {geom.map((c, i) => {
          const isLoss = c.kind === 'loss'
          const modelled = c.basis === 'modelled'
          const fill = !isLoss
            ? (c.tone === 'good' ? '#2B5540' : '#17140E')
            : modelled ? 'url(#wf-hatch)' : '#8C2B22'
          const delay = 0.15 + i * 0.1
          const prev = geom[i - 1]

          return (
            <g key={c.key}>
              {i > 0 && (
                <motion.line
                  x1={prev.x} x2={c.x + colW} y1={c.y} y2={c.y}
                  stroke="#CFC7B2" strokeWidth="1" strokeDasharray="2 4"
                  initial={{ opacity: 0 }} animate={inView ? { opacity: 1 } : {}}
                  transition={{ delay: delay + 0.1, duration: 0.5 }}
                />
              )}
              <motion.rect
                x={c.x} y={c.y} width={colW} fill={fill}
                stroke={modelled ? '#8C2B22' : 'none'} strokeWidth={modelled ? 1 : 0}
                initial={{ height: 0, opacity: 0 }}
                animate={inView ? { height: c.h, opacity: 1 } : {}}
                transition={{ delay, duration: 0.75, ease: EASE }}
              />
              {/* value — opacity only, position is a static attribute */}
              <motion.text
                x={c.x + colW / 2} y={c.y - 12} textAnchor="middle"
                className="figure" fontSize="16"
                fill={isLoss ? '#8C2B22' : c.tone === 'good' ? '#2B5540' : '#17140E'}
                initial={{ opacity: 0 }} animate={inView ? { opacity: 1 } : {}}
                transition={{ delay: delay + 0.32, duration: 0.5 }}
              >
                {isLoss ? '−' : ''}₹{inrShort(c.value)}
              </motion.text>
              <motion.text
                x={c.x + colW / 2} y={baseline + 22} textAnchor="middle"
                fontSize="12.5" fill="#4A4437" fontFamily="IBM Plex Sans"
                initial={{ opacity: 0 }} animate={inView ? { opacity: 1 } : {}}
                transition={{ delay: delay + 0.38, duration: 0.5 }}
              >
                {c.label}
              </motion.text>
              {c.id && (
                <motion.text
                  x={c.x + colW / 2} y={baseline + 40} textAnchor="middle"
                  fontSize="10" fill="#B5AC98" fontFamily="IBM Plex Mono"
                  letterSpacing="0.08em"
                  initial={{ opacity: 0 }} animate={inView ? { opacity: 1 } : {}}
                  transition={{ delay: delay + 0.42, duration: 0.5 }}
                >
                  {c.id}
                </motion.text>
              )}
            </g>
          )
        })}
      </svg>

      <div className="flex items-center gap-7 pt-2">
        <span className="flex items-center gap-2 label text-ink-2">
          <span className="w-5 h-3.5 bg-oxblood inline-block" />
          Confirmed from records
        </span>
        <span className="flex items-center gap-2 label text-ink-2">
          <span className="w-5 h-3.5 inline-block border border-oxblood"
                style={{ backgroundImage:
                  'repeating-linear-gradient(-45deg, #F6EAE7 0 2px, #8C2B22 2px 4px)' }} />
          Estimated by model
        </span>
      </div>
    </div>
  )
}
