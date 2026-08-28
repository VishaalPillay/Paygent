/** Shared Framer Motion primitives. One easing curve and one stagger pattern,
 * reused everywhere rather than restated per component — see index.css's
 * `.animate-rule` for the same curve applied to a plain CSS transition, so the
 * hand-rolled and Framer-driven animations settle at the same rate.
 */

export const EASE = [0.2, 0.7, 0.2, 1]

/** A child that rises into place. Used with `variants={rise}` on an element whose
 * parent supplies `initial="hidden"` and `animate`/`whileInView="show"` — Framer
 * Motion propagates the variant name down, so children need nothing else. */
export const rise = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.6, ease: EASE } },
}

/** A container that staggers its `rise` children. `step` is the delay between
 * each child, `delay` is the delay before the first one starts. */
export function stagger(step = 0.08, delay = 0) {
  return {
    hidden: {},
    show: { transition: { staggerChildren: step, delayChildren: delay } },
  }
}
