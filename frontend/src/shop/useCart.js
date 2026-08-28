import { useCallback, useEffect, useRef, useState } from 'react'

const KEY = 'paygent.cart'
const ABANDON_AFTER_MS = 4000   // demo pacing; a real window is 20-30 minutes

/** Cart state, plus the abandonment signal.
 *
 * The interesting part is not the cart — it is what happens when the customer
 * leaves with one. A held cart that is never paid for is waterfall gate B1, the
 * single largest leak in the whole model, and it is invisible to the merchant
 * because nothing in their systems records a sale that did not happen.
 *
 * So we record it here: when the tab is hidden or closed with items still in the
 * cart, the session is marked abandoned and reported. `sendBeacon` is used on
 * unload because a normal fetch is cancelled the moment the page goes away.
 */
export function useCart() {
  const [items, setItems] = useState(() => {
    try { return JSON.parse(localStorage.getItem(KEY))?.items ?? [] } catch { return [] }
  })
  const [abandoned, setAbandoned] = useState(false)
  const timer = useRef(null)
  const sessionId = useRef(
    (() => {
      try {
        const s = JSON.parse(localStorage.getItem(KEY))?.sessionId
        if (s) return s
      } catch { /* fresh session */ }
      return `ses_live_${Math.random().toString(36).slice(2, 10)}`
    })(),
  )
  // Whether *this* session_id has already been reported abandoned. The backend
  // treats a repeat POST for the same session_id as idempotent (one real
  // abandonment must produce exactly one signal, since both the visibility timer
  // and the tab-close handler can each call report() for the same leave) — so
  // reusing the same id for a second, separate abandonment silently no-ops and
  // no new conversation ever opens. `add()` below rotates the id once this is
  // true, so coming back and leaving again reports as its own signal.
  const reported = useRef(
    (() => {
      try { return !!JSON.parse(localStorage.getItem(KEY))?.reported } catch { return false }
    })(),
  )

  useEffect(() => {
    localStorage.setItem(
      KEY, JSON.stringify({ items, sessionId: sessionId.current, reported: reported.current }))
  }, [items])

  const total = items.reduce((s, i) => s + i.price_inr * i.qty, 0)
  const count = items.reduce((s, i) => s + i.qty, 0)

  const report = useCallback((beacon) => {
    if (!items.length) return
    const body = JSON.stringify({
      session_id: sessionId.current,
      cart_value_inr: total,
      item_count: count,
      items: items.map((i) => ({ sku: i.sku, qty: i.qty })),
      abandoned_at: new Date().toISOString(),
    })
    try {
      if (beacon && navigator.sendBeacon) {
        navigator.sendBeacon('/api/carts/abandoned', new Blob([body], { type: 'application/json' }))
      } else {
        fetch('/api/carts/abandoned', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
        }).catch(() => {})
      }
    } catch { /* the shop must never break because reporting failed */ }
    // Kept locally too, so the recovery screen can pick it up with the backend down.
    localStorage.setItem('paygent.abandoned', body)

    reported.current = true
    localStorage.setItem(
      KEY, JSON.stringify({ items, sessionId: sessionId.current, reported: true }))
  }, [items, total, count])

  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden && items.length) {
        timer.current = setTimeout(() => { setAbandoned(true); report(false) }, ABANDON_AFTER_MS)
      } else {
        clearTimeout(timer.current)
      }
    }
    const onUnload = () => { if (items.length) report(true) }
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', onUnload)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', onUnload)
      clearTimeout(timer.current)
    }
  }, [items, report])

  const add = (p, qty = 1) => {
    if (reported.current) {
      // Engaging again after a previous abandonment was already reported — a new
      // episode, so it gets its own identity and can report as its own signal if
      // they leave again instead of colliding with the last session_id's
      // idempotency check on the backend.
      sessionId.current = `ses_live_${Math.random().toString(36).slice(2, 10)}`
      reported.current = false
      setAbandoned(false)
    }
    setItems((cur) => {
      const i = cur.findIndex((x) => x.sku === p.sku)
      if (i === -1) return [...cur, { ...p, qty }]
      const next = [...cur]; next[i] = { ...next[i], qty: next[i].qty + qty }
      return next
    })
  }
  const setQty = (sku, qty) => setItems((cur) =>
    qty <= 0 ? cur.filter((x) => x.sku !== sku)
             : cur.map((x) => (x.sku === sku ? { ...x, qty } : x)))
  const clear = () => { setItems([]); setAbandoned(false) }

  return { items, count, total, add, setQty, clear, abandoned, sessionId: sessionId.current }
}
