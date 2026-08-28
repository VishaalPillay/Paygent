import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import FanIllustration from '../components/FanIllustration'
import TraceStream from '../components/TraceStream'
import MiniCart from './MiniCart'
import { useCart } from './useCart'
import { inrExact } from '../lib/format'
import { rise, stagger, EASE } from '../lib/motion'

/** Paygent — the merchant's shop.
 *
 * A separate site on a separate port, because a customer has to be able to close
 * it. Nothing here mentions Paygent: this is the merchant's own storefront, and
 * every signal it produces is a side effect of an ordinary purchase attempt.
 */

// Two products, deliberately different markup: the cart agent's discount ceiling
// comes from agents/cart.py::PRODUCT_MARGINS, keyed by this sku — a commodity item
// and a premium variant land on visibly different max discounts (10% vs 25%) when
// negotiated in Conversations, not because the agent decided that, but because the
// policy engine looked up a different margin floor for each product.
const PRODUCTS = [
  {
    sku: 'SKU-0417',
    brand: 'Atomberg',
    name: 'Renesa Ceiling Fan',
    variant: '1200 mm · BLDC · Matte Black',
    price_inr: 4299.0,
    mrp_inr: 5990.0,
  },
  {
    sku: 'SKU-0623',
    brand: 'Atomberg',
    name: 'Renesa+ Smart Ceiling Fan',
    variant: '1200 mm · BLDC · Wi-Fi + Remote',
    price_inr: 6999.0,
    mrp_inr: 9990.0,
  },
]

const SCENARIOS = [
  {
    id: 'orphan_payment',
    label: 'Payment succeeded, order not confirmed',
    detail: 'Money moves, the merchant’s order system never hears about it.',
  },
  {
    id: 'failed_but_confirmed',
    label: 'Payment failed, order confirmed',
    detail: 'The reverse — nobody wrote a rule for this. Watch what happens.',
  },
  {
    id: 'duplicate_payment',
    label: 'Charged twice for one checkout',
    detail: 'The gateway retries and captures a second time on the same cart.',
  },
]

export default function ShopApp() {
  const cart = useCart()
  const [cartOpen, setCartOpen] = useState(false)
  const [selectedSku, setSelectedSku] = useState(PRODUCTS[0].sku)
  const [qty, setQty] = useState(1)
  const [added, setAdded] = useState(false)
  const [placing, setPlacing] = useState(false)
  const [log, setLog] = useState([])
  const [activeCaseId, setActiveCaseId] = useState(null)
  const [runningScenario, setRunningScenario] = useState(null)

  const product = PRODUCTS.find((p) => p.sku === selectedSku)
  const offPct = Math.round((1 - product.price_inr / product.mrp_inr) * 100)

  const push = (text, tone) =>
    setLog((l) => [...l, {
      id: `${Date.now()}-${l.length}`, tone, text,
      t: new Date().toLocaleTimeString('en-IN', { hour12: false }),
    }])

  function addToCart() {
    cart.add(product, qty)
    setAdded(true)
    setCartOpen(true)
    setTimeout(() => setAdded(false), 1800)
  }

  async function checkout() {
    setCartOpen(false)
    setPlacing(true)
    await new Promise((r) => setTimeout(r, 500))
    cart.clear()
    setPlacing(false)
  }

  async function runScenario(scenarioId) {
    setRunningScenario(scenarioId)
    setActiveCaseId(null)
    setLog([])
    const amount = cart.total || product.price_inr * qty

    try {
      const res = await fetch('/api/demo/scenario', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario: scenarioId,
          session_id: cart.sessionId,
          cart_value_inr: amount,
          item_count: cart.count || qty,
        }),
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const body = await res.json()
      body.events.forEach((e) => push(e.text, e.tone === 'neutral' ? undefined : e.tone))
      if (body.case) {
        push(`case ${body.case.case_id} → watch the agent audit on the right`, 'good')
        setActiveCaseId(body.case.case_id)
      }
    } catch (err) {
      push(`could not reach the backend — ${err.message}`, 'bad')
    } finally {
      setRunningScenario(null)
    }
  }

  return (
    <div className="min-h-screen">
      {/* ---- shop masthead ------------------------------------------------ */}
      <header className="max-w-[76rem] mx-auto px-10">
        <div className="flex items-baseline justify-between pt-8 pb-4">
          <div className="flex items-baseline gap-4">
            <span className="font-display text-[1.75rem] leading-none tracking-[-0.02em]">Paygent</span>
            <span className="label hidden sm:block">Fans &amp; air</span>
          </div>
          <div className="flex items-center gap-8">
            <span className="label hidden md:block">Ceiling</span>
            <span className="label hidden md:block">Pedestal</span>
            <span className="label hidden md:block">Exhaust</span>
            <MiniCart open={cartOpen} setOpen={setCartOpen} items={cart.items}
              count={cart.count} total={cart.total} setQty={cart.setQty}
              onCheckout={checkout} />
          </div>
        </div>
        <motion.div className="h-px bg-ink origin-left"
          initial={{ scaleX: 0 }} animate={{ scaleX: 1 }}
          transition={{ duration: 1, ease: EASE }} />
      </header>

      {/* ---- the customer left with a full cart --------------------------- */}
      <AnimatePresence>
        {cart.abandoned && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.4, ease: EASE }}
            className="bg-ochre-bg border-b border-ochre-edge overflow-hidden">
            <div className="max-w-[76rem] mx-auto px-10 py-3 flex items-center gap-3">
              <span className="w-1.5 h-1.5 rounded-full bg-ochre animate-breathe shrink-0" />
              <p className="text-micro text-ink-2">
                Your cart is saved — we've held ₹{inrExact(cart.total)} of stock for you.
                <span className="text-ink-3"> Session {cart.sessionId} reported as abandoned.</span>
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <main className="max-w-[76rem] mx-auto px-10">
        <motion.div variants={stagger(0.08)} initial="hidden" animate="show"
          className="grid grid-cols-1 lg:grid-cols-[1.15fr_1fr] gap-16 pt-12 pb-16">

          <motion.div variants={rise}>
            <div className="border border-rule bg-paper-raised p-10">
              <FanIllustration spinning={placing} />
            </div>
          </motion.div>

          <motion.div variants={rise} className="pt-2">
            <div className="flex gap-2 mb-5">
              {PRODUCTS.map((p) => (
                <button key={p.sku} onClick={() => setSelectedSku(p.sku)}
                  className={`label px-3 py-1.5 border transition-colors duration-200 ${
                    p.sku === selectedSku
                      ? 'border-ink text-ink bg-paper-deep'
                      : 'border-rule text-ink-3 hover:text-ink-2'}`}>
                  {p.name}
                </button>
              ))}
            </div>

            <div className="label text-ink-3">{product.brand}</div>
            <h1 className="font-display text-[2.5rem] leading-[1.08] tracking-[-0.02em] mt-2">
              {product.name}
            </h1>
            <p className="text-base text-ink-2 mt-2">{product.variant}</p>

            <div className="flex items-baseline gap-4 mt-7">
              <span className="figure text-[2.25rem] leading-none">
                <span className="text-[0.5em] text-ink-3 font-sans mr-1">₹</span>
                {inrExact(product.price_inr, 0)}
              </span>
              <span className="figure text-ink-4 line-through text-[1.125rem]">
                ₹{inrExact(product.mrp_inr, 0)}
              </span>
              <span className="label text-forest">{offPct}% off</span>
            </div>
            <p className="text-micro text-ink-3 mt-1.5">Inclusive of 18% GST · Free delivery</p>

            <div className="rule mt-8 pt-8">
              <div className="label mb-2">Quantity</div>
              <div className="flex items-stretch border border-rule-strong w-fit">
                <button onClick={() => setQty((q) => Math.max(1, q - 1))}
                  className="w-10 h-10 text-ink-2 hover:bg-paper-deep transition-colors">−</button>
                <div className="w-12 h-10 flex items-center justify-center figure border-x border-rule-strong">
                  {qty}
                </div>
                <button onClick={() => setQty((q) => Math.min(5, q + 1))}
                  className="w-10 h-10 text-ink-2 hover:bg-paper-deep transition-colors">+</button>
              </div>
            </div>

            <div className="mt-8 flex flex-col gap-3">
              <button onClick={addToCart}
                className={`h-12 border label transition-colors duration-200
                  ${added ? 'border-forest text-forest bg-forest-bg'
                          : 'border-ink text-ink hover:bg-paper-deep'}`}>
                {added ? 'Added to cart' : 'Add to cart'}
              </button>
              <button onClick={checkout} disabled={placing || !cart.count}
                className="h-12 bg-ink text-paper label hover:bg-ink-2 transition-colors
                           duration-200 disabled:opacity-40 disabled:cursor-not-allowed">
                {placing ? 'Placing order…'
                  : cart.count ? `Pay with UPI · ₹${inrExact(cart.total)}`
                  : 'Add an item to pay'}
              </button>
              <p className="text-micro text-ink-4 text-center">
                Razorpay test mode · no real money moves
              </p>
            </div>
          </motion.div>
        </motion.div>

        {/* ---- instrumentation, kept at the bottom and clearly not the shop -- */}
        <section className="border-t-2 border-ink pt-7 pb-20">
          <h2 className="font-display text-[1.375rem] tracking-[-0.02em]">Reconciliation demo</h2>
          <p className="text-micro text-ink-3 mt-2 max-w-[60ch]">
            Not part of the shop. Each button writes a real payment into the ledgers below and
            asks the same engine that runs the seeded data to classify it — nothing here is
            staged.
          </p>

          <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.3fr] gap-14 mt-7">
            <div>
              <div className="flex flex-col gap-3">
                {SCENARIOS.map((s) => (
                  <button key={s.id} onClick={() => runScenario(s.id)}
                    disabled={runningScenario !== null}
                    className={`text-left border p-4 transition-colors duration-200
                      disabled:opacity-40 disabled:cursor-not-allowed
                      ${runningScenario === s.id
                        ? 'border-oxblood bg-oxblood-bg'
                        : 'border-rule-strong hover:bg-paper-deep'}`}>
                    <div className="text-[0.875rem] text-ink">
                      {runningScenario === s.id ? 'Running…' : s.label}
                    </div>
                    <div className="text-micro text-ink-3 mt-1">{s.detail}</div>
                  </button>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-5">
              <div>
                <span className="label">Event log</span>
                <div className="border border-rule bg-paper-deep min-h-[8rem] p-5 mt-2
                                font-mono text-[0.75rem]">
                  {log.length === 0 && <p className="text-ink-4">Trigger a scenario above…</p>}
                  <AnimatePresence initial={false}>
                    {log.map((l) => (
                      <motion.div key={l.id} initial={{ opacity: 0, x: -6 }}
                        animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.35, ease: EASE }}
                        className={`flex gap-4 py-[3px] ${
                          l.tone === 'bad' ? 'text-oxblood' : l.tone === 'warn' ? 'text-ochre'
                          : l.tone === 'good' ? 'text-forest' : 'text-ink-2'}`}>
                        <span className="text-ink-4 shrink-0">{l.t}</span>
                        <span>{l.text}</span>
                      </motion.div>
                    ))}
                  </AnimatePresence>
                </div>
              </div>

              <TraceStream caseId={activeCaseId} />
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}
