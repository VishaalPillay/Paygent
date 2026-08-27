import { motion, AnimatePresence } from 'framer-motion'
import { inrExact } from '../lib/format'
import { EASE } from '../lib/motion'
import FanIllustration from '../components/FanIllustration'

/** Cart button plus the panel it opens. Ordinary shop furniture, on purpose —
 *  nothing here should hint that anything is being measured. */
export default function MiniCart({ open, setOpen, items, count, total, setQty, onCheckout }) {
  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)}
        className="flex items-center gap-2.5 label py-2 px-3 -mr-3 hover:text-ink transition-colors">
        Cart
        <span className={`min-w-[1.35rem] h-[1.35rem] px-1 inline-flex items-center justify-center
          figure text-[0.75rem] transition-colors duration-200
          ${count ? 'bg-ink text-paper' : 'border border-rule-strong text-ink-4'}`}>
          {count}
        </span>
      </button>

      <AnimatePresence>
        {open && (
          <>
            <motion.div className="fixed inset-0 z-10" onClick={() => setOpen(false)}
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
            <motion.div
              initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.28, ease: EASE }}
              className="absolute right-0 top-[calc(100%+0.75rem)] z-20 w-[23rem]
                         bg-paper-raised border border-rule-strong p-6">
              <div className="label pb-3 border-b border-rule">Your cart</div>

              {items.length === 0 ? (
                <p className="text-micro text-ink-3 py-6">Nothing in your cart yet.</p>
              ) : (
                <>
                  {items.map((it) => (
                    <div key={it.sku} className="flex gap-4 py-4 border-b border-rule">
                      <div className="w-12 h-12 border border-rule shrink-0 flex items-center
                                      justify-center bg-paper">
                        <div className="w-9 h-9 opacity-70"><FanIllustration /></div>
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-[0.8125rem] text-ink truncate">{it.name}</div>
                        <div className="text-micro text-ink-3 mt-0.5">{it.variant}</div>
                        <div className="flex items-center gap-3 mt-2">
                          <div className="flex items-stretch border border-rule">
                            <button onClick={() => setQty(it.sku, it.qty - 1)}
                              className="w-6 h-6 text-micro text-ink-2 hover:bg-paper-deep">−</button>
                            <span className="w-7 h-6 flex items-center justify-center figure text-[0.75rem]">
                              {it.qty}
                            </span>
                            <button onClick={() => setQty(it.sku, it.qty + 1)}
                              className="w-6 h-6 text-micro text-ink-2 hover:bg-paper-deep">+</button>
                          </div>
                          <span className="figure text-[0.8125rem] text-ink-2">
                            ₹{inrExact(it.price_inr * it.qty, 0)}
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                  <div className="flex items-baseline justify-between pt-4">
                    <span className="label">Total</span>
                    <span className="figure text-[1.25rem]">₹{inrExact(total)}</span>
                  </div>
                  <button onClick={onCheckout}
                    className="w-full h-11 bg-ink text-paper label mt-4 hover:bg-ink-2
                               transition-colors duration-200">
                    Checkout
                  </button>
                </>
              )}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  )
}
