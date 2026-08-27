import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import FanIllustration from '../components/FanIllustration'
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

const PRODUCT = {
  sku: 'SKU-0417',
  brand: 'Atomberg',
  name: 'Renesa Ceiling Fan',
  variant: '1200 mm · BLDC · Matte Black',
  price_inr: 4299.0,
  mrp_inr: 5990.0,
}

export default function ShopApp() {
  const cart = useCart()
  const [cartOpen, setCartOpen] = useState(false)
  const [qty, setQty] = useState(1)
  const [added, setAdded] = useState(false)
  const [paying, setPaying] = useState(false)
  const [log, setLog] = useState([])
  const [dropWebhook, setDropWebhook] = useState(true)

  const push = (text, tone) =>
    setLog((l) => [...l, {
      id: `${Date.now()}-${l.length}`, tone, text,
      t: new Date().toLocaleTimeString('en-IN', { hour12: false }),
    }])

  function addToCart() {
    cart.add(PRODUCT, qty)
    setAdded(true)
    setCartOpen(true)
    setTimeout(() => setAdded(false), 1800)
  }

  async function checkout() {
    setCartOpen(false)
    setPaying(true)
    setLog([])
    const wait = (ms) => new Promise((r) => setTimeout(r, ms))
    const amount = cart.total

    push(`order created · ${cart.count} item(s) · ₹${inrExact(amount)}`)
    await wait(700); push('UPI collect request sent to customer')
    await wait(900); push('payment captured at gateway · pay_live_0001', 'good')
    await wait(600); push('webhook received · payment.captured')
    await wait(400); push('signature verified')
    await wait(500)

    if (dropWebhook) {
      push('DEMO_DROP_WEBHOOK on — acknowledged, NOT applied', 'warn')
      await wait(1100); push('ledgers disagree · payment CAPTURED, order MISSING', 'bad')
      await wait(600); push(`case opened · ORPHAN_PAYMENT_NO_ORDER · ₹${inrExact(amount)}`, 'bad')
    } else {
      push('order confirmed, inventory reserved, revenue booked', 'good')
      await wait(700); push('four ledgers agree · no case opened', 'good')
      cart.clear()
    }
    setPaying(false)
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
              <FanIllustration spinning={paying} />
            </div>
          </motion.div>

          <motion.div variants={rise} className="pt-2">
            <div className="label text-ink-3">{PRODUCT.brand}</div>
            <h1 className="font-display text-[2.5rem] leading-[1.08] tracking-[-0.02em] mt-2">
              {PRODUCT.name}
            </h1>
            <p className="text-base text-ink-2 mt-2">{PRODUCT.variant}</p>

            <div className="flex items-baseline gap-4 mt-7">
              <span className="figure text-[2.25rem] leading-none">
                <span className="text-[0.5em] text-ink-3 font-sans mr-1">₹</span>
                {inrExact(PRODUCT.price_inr, 0)}
              </span>
              <span className="figure text-ink-4 line-through text-[1.125rem]">
                ₹{inrExact(PRODUCT.mrp_inr, 0)}
              </span>
              <span className="label text-forest">28% off</span>
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
              <button onClick={checkout} disabled={paying || !cart.count}
                className="h-12 bg-ink text-paper label hover:bg-ink-2 transition-colors
                           duration-200 disabled:opacity-40 disabled:cursor-not-allowed">
                {paying ? 'Processing…'
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
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.3fr] gap-14">
            <div>
              <h2 className="font-display text-[1.375rem] tracking-[-0.02em]">Demo controls</h2>
              <p className="text-micro text-ink-3 mt-2 max-w-[42ch]">
                Not part of the shop. Close this tab with something in the cart and the
                abandonment is reported the moment you leave.
              </p>
              <label className="flex items-start gap-4 mt-6 cursor-pointer">
                <button onClick={() => setDropWebhook((v) => !v)}
                  className={`mt-0.5 w-11 h-6 shrink-0 border transition-colors duration-200
                    ${dropWebhook ? 'bg-oxblood border-oxblood' : 'bg-paper-deep border-rule-strong'}`}>
                  <motion.span className="block w-4 h-4 bg-paper m-[3px]"
                    animate={{ x: dropWebhook ? 20 : 0 }}
                    transition={{ duration: 0.25, ease: EASE }} />
                </button>
                <span>
                  <span className="text-[0.875rem] text-ink">Drop the payment webhook</span>
                  <span className="block text-micro text-ink-3 mt-1 max-w-[40ch]">
                    The confirmation is verified and acknowledged, then discarded. The payment
                    stays captured with no order — what a lost webhook does in production.
                  </span>
                </span>
              </label>
            </div>

            <div>
              <div className="flex items-baseline justify-between mb-3">
                <span className="label">Event log</span>
                {paying && (
                  <span className="flex items-center gap-2 label text-oxblood">
                    <span className="w-1.5 h-1.5 rounded-full bg-oxblood animate-breathe" /> live
                  </span>
                )}
              </div>
              <div className="border border-rule bg-paper-deep min-h-[12rem] p-5 font-mono text-[0.75rem]">
                {log.length === 0 && <p className="text-ink-4">Waiting for a payment…</p>}
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
          </div>
        </section>
      </main>
    </div>
  )
}
