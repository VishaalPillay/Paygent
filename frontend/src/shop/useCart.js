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

  useEffect(() => {
    localStorage.setItem(KEY, JSON.stringify({ items, sessionId: sessionId.current }))
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

  const add = (p, qty = 1) => setItems((cur) => {
    const i = cur.findIndex((x) => x.sku === p.sku)
    if (i === -1) return [...cur, { ...p, qty }]
    const next = [...cur]; next[i] = { ...next[i], qty: next[i].qty + qty }
    return next
  })
  const setQty = (sku, qty) => setItems((cur) =>
    qty <= 0 ? cur.filter((x) => x.sku !== sku)
             : cur.map((x) => (x.sku === sku ? { ...x, qty } : x)))
  const clear = () => { setItems([]); setAbandoned(false) }

  return { items, count, total, add, setQty, clear, abandoned, sessionId: sessionId.current }
}
