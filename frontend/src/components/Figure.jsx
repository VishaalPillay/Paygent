import { useEffect, useRef, useState } from 'react'
import { useInView } from 'framer-motion'
import { inrValue, inrUnit } from '../lib/format'

/** A rupee figure that counts up when it scrolls into view.
 *
 * Typeset rather than rendered: the numeral is serif and tabular, the unit (L/Cr)
 * is set smaller and lighter beside it, and the rupee sign is lighter still. That
 * hierarchy inside a single number is most of what separates a typeset figure from
 * a dashboard stat.
 */
export default function Figure({
  value, size = 'stat', basis, duration = 1100, className = '', prefix = true,
}) {
  const ref = useRef(null)
  const inView = useInView(ref, { once: true, margin: '-40px' })
  const [shown, setShown] = useState(0)

  useEffect(() => {
    if (!inView) return
    const target = Number(value) || 0
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setShown(target); return
    }
    let raf, start
    const tick = (t) => {
      start ??= t
      const p = Math.min((t - start) / duration, 1)
      // Same curve as the motion language, so numbers settle when the page does.
      const eased = 1 - Math.pow(1 - p, 4)
      setShown(target * eased)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [inView, value, duration])

  const unit = inrUnit(value)
  const tone = basis === 'modelled' ? 'text-ochre' : ''
  const sizes = {
    hero: 'text-hero', stat: 'text-stat',
    sm: 'text-[1.5rem] leading-none', xs: 'text-[1.125rem] leading-none',
  }

  return (
    <span ref={ref} className={`figure inline-flex items-baseline gap-[0.15em] ${sizes[size]} ${tone} ${className}`}>
      {prefix && <span className="text-[0.52em] text-ink-3 font-sans font-normal translate-y-[-0.06em]">₹</span>}
      <span>{inrValue(shown)}</span>
      {unit && <span className="text-[0.4em] font-sans font-medium tracking-wide text-ink-3 ml-[0.1em]">{unit}</span>}
    </span>
  )
}
