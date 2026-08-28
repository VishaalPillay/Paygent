/** Formatting helpers — the typeset-figure and plain-English pieces every screen
 * shares. Indian digit grouping and the lakh/crore short scale throughout, since
 * every rupee figure in this product is meant to read the way a merchant already
 * reads their own numbers, not the way a Western dashboard would show them.
 */

function shortParts(value) {
  const v = Number(value) || 0
  const abs = Math.abs(v)
  if (abs >= 1e7) return { value: v / 1e7, unit: 'Cr' }
  if (abs >= 1e5) return { value: v / 1e5, unit: 'L' }
  if (abs >= 1e3) return { value: v / 1e3, unit: 'K' }
  return { value: v, unit: '' }
}

function fmt(v, decimals) {
  return v.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

/** Full precision, Indian grouping. `inrExact(1234567.5)` -> "12,34,567.50". */
export function inrExact(value, decimals = 2) {
  return fmt(Number(value) || 0, decimals)
}

/** Lakh/crore short form, numeral and unit together. `inrShort(12400000)` -> "1.2Cr". */
export function inrShort(value) {
  const { value: v, unit } = shortParts(value)
  return `${fmt(v, unit ? 1 : 0)}${unit}`
}

/** Just the numeral half of the short form — for a counting animation where the
 * unit is rendered separately in its own type size (see components/Figure.jsx). */
export function inrValue(value) {
  const { value: v, unit } = shortParts(value)
  return fmt(v, unit ? 1 : 0)
}

/** Just the unit half: "L", "Cr", "K", or "" below one thousand. */
export function inrUnit(value) {
  return shortParts(value).unit
}

/** BreakType enum value -> lowercase, spaced. "ORPHAN_PAYMENT_NO_ORDER" ->
 * "orphan payment no order". Callers apply their own casing via CSS as needed. */
export function humanBreak(breakType) {
  if (!breakType) return ''
  return breakType.toLowerCase().replace(/_/g, ' ')
}

/** How a case's deadline reads right now, relative to `now` (summary.generated_at
 * — the seeded reference instant, not the wall clock, so the label matches
 * whatever moment the rest of the screen is anchored to). */
export function deadlineLabel(deadlineAt, now) {
  if (!deadlineAt) return { overdue: false, urgent: false, text: 'No deadline' }

  const deadline = new Date(deadlineAt).getTime()
  const current = now ? new Date(now).getTime() : Date.now()
  const diffMs = deadline - current
  const overdue = diffMs <= 0
  const absMs = Math.abs(diffMs)
  const hours = absMs / 3_600_000

  let span
  if (hours < 1) span = `${Math.max(1, Math.round(absMs / 60_000))}m`
  else if (hours < 48) span = `${Math.round(hours)}h`
  else span = `${Math.round(hours / 24)}d`

  return {
    overdue,
    urgent: !overdue && hours <= 24,
    text: overdue ? `Overdue by ${span}` : `${span} left`,
  }
}

/** `basis` enum value -> the word this product uses for it everywhere on screen.
 * Never "deterministic"/"modelled" verbatim in front of a reader. */
export const BASIS_LABEL = {
  deterministic: 'Confirmed',
  modelled: 'Estimated',
}
