import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { inrExact } from '../lib/format'
import { rise } from '../lib/motion'

const POLL_MS = 2000

/** Cart Agent — the second proof of the product's central pattern (AI proposes,
 * something deterministic decides), lived out as a real conversation rather than
 * a guardrail block. Polls for a real abandoned cart; when one shows up, the
 * agent opens the conversation on its own — cart recovery is an outbound nudge,
 * it doesn't wait to be spoken to.
 *
 * Left: the conversation. Right: the policy engine's verdict on every rung the
 * agent has asked for. Same split as Case Detail and Mandate Board, on purpose.
 */
export default function CartAgentPanel() {
  const [cart, setCart] = useState(null)
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [offers, setOffers] = useState([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)
  // Persisted, not just a ref — a page refresh mid-demo must not re-open a
  // conversation the agent already started (and burn another LLM call doing it).
  const seenCaseId = useRef(sessionStorage.getItem('paygent.cartAgent.seenCaseId'))
  const scrollRef = useRef(null)

  // A page refresh restores `seenCaseId` from storage but not React state — fetch
  // (never re-open) whatever the backend already has for it. An empty message on
  // an existing conversation is a no-op read, not a turn, so this costs no LLM call.
  useEffect(() => {
    const restored = seenCaseId.current
    if (!restored) return
    const cnv = `cnv_${restored}`
    setConversationId(cnv)
    send(cnv, '', null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Poll for a newly abandoned cart. A fresh case_id — not just "a cart exists"
  // — is what triggers the agent opening a new conversation, so refreshing the
  // page or a cart that was already handled doesn't restart it.
  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const res = await fetch('/api/carts/abandoned/latest')
        if (!res.ok) return
        const latest = await res.json()
        if (cancelled || !latest || latest.case_id === seenCaseId.current) {
          if (latest) setCart(latest) // known cart, but skip re-opening it
          return
        }
        seenCaseId.current = latest.case_id
        sessionStorage.setItem('paygent.cartAgent.seenCaseId', latest.case_id)
        setCart(latest)
        await openConversation(latest)
      } catch { /* the panel must never crash the dashboard because polling failed */ }
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  async function openConversation(latest) {
    const cnv = `cnv_${latest.case_id}`
    setConversationId(cnv)
    setMessages([])
    setOffers([])
    setError(null)
    await send(cnv, '', latest)
  }

  async function send(cnv, message, cartInfo) {
    setSending(true)
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          conversation_id: cnv,
          message,
          session_id: cartInfo?.session_id,
          cart_value_inr: cartInfo?.cart_value_inr,
          sku: cartInfo?.sku,
        }),
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const body = await res.json()
      setMessages(body.messages)
      if (body.offer) setOffers((o) => [...o, body.offer])
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  function submit(e) {
    e.preventDefault()
    if (!draft.trim() || !conversationId || sending) return
    send(conversationId, draft.trim(), cart)
    setDraft('')
  }

  return (
    <motion.section variants={rise} className="pt-12 pb-16">
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="font-display text-[1.75rem] tracking-[-0.02em]">Cart Agent</h2>
        <span className="label text-ink-3">{cart ? `session ${cart.session_id}` : 'waiting for a cart'}</span>
      </div>
      <p className="text-micro text-ink-3 max-w-[58ch] mb-6">
        Close the storefront tab with something in the cart and this wakes on its own. The
        agent may request a discount; the policy engine on the right decides what it
        actually gets.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-8">
        {/* ---- left: the conversation ---------------------------------- */}
        <div className="border border-rule bg-paper-raised flex flex-col h-[26rem]">
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-5 flex flex-col gap-3">
            {messages.length === 0 && (
              <p className="text-micro text-ink-4 m-auto">
                {cart ? 'Connecting…' : 'No abandoned cart yet — leave one on the storefront.'}
              </p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`max-w-[80%] ${m.role === 'customer' ? 'self-end text-right' : 'self-start'}`}>
                <div className={`px-3.5 py-2 text-[0.8125rem] leading-relaxed ${
                  m.role === 'customer'
                    ? 'bg-ink text-paper'
                    : 'bg-paper-deep border-l-2 border-navy text-ink'}`}>
                  {m.text}
                </div>
                <div className="text-micro text-ink-4 mt-1">
                  {new Date(m.at).toLocaleTimeString('en-IN', { hour12: false })}
                </div>
              </div>
            ))}
            {sending && <p className="label text-ink-4 self-start">agent is typing…</p>}
          </div>
          <form onSubmit={submit} className="border-t border-rule flex">
            <input value={draft} onChange={(e) => setDraft(e.target.value)}
              disabled={!conversationId || sending}
              placeholder={conversationId ? 'Type as the customer…' : 'Waiting for a cart…'}
              className="flex-1 px-4 py-3 text-[0.8125rem] bg-transparent outline-none
                         placeholder:text-ink-4 disabled:cursor-not-allowed" />
            <button type="submit" disabled={!conversationId || sending || !draft.trim()}
              className="px-5 label text-ink-2 hover:text-ink disabled:opacity-30
                         disabled:cursor-not-allowed border-l border-rule">
              Send
            </button>
          </form>
        </div>

        {/* ---- right: the policy engine's verdicts ---------------------- */}
        <div className="border border-rule bg-paper-deep p-5 h-[26rem] overflow-y-auto">
          <span className="label">Offer policy</span>
          {cart && (
            <p className="text-micro text-ink-3 mt-2">
              Cart value <span className="figure text-ink">₹{inrExact(cart.cart_value_inr, 0)}</span>
            </p>
          )}
          {error && <p className="text-micro text-oxblood mt-3">⚠ {error}</p>}
          {offers.length === 0 && !error && (
            <p className="text-micro text-ink-4 mt-4">
              No rung requested yet — ask for a discount in the chat to see the policy decide.
            </p>
          )}
          <div className="flex flex-col gap-3 mt-4">
            {offers.map((o, i) => (
              <div key={i} className={`border-l-2 pl-4 py-2 ${o.granted ? 'border-forest' : 'border-oxblood'}`}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`label ${o.granted ? 'text-forest' : 'text-oxblood'}`}>
                    {o.granted ? 'Granted' : 'Denied'}
                  </span>
                  <span className="font-mono text-[0.6875rem] text-ink-3">
                    requested {o.requested_rung}
                  </span>
                </div>
                <p className="text-micro text-ink-2 mt-1 leading-relaxed">{o.reason}</p>
                <div className="flex gap-4 mt-1.5 text-micro text-ink-3">
                  <span>granted: <span className="font-mono">{o.granted_rung}</span></span>
                  {o.discount_inr > 0 && <span>−₹{inrExact(o.discount_inr, 0)}</span>}
                  {o.shipping_waived_inr > 0 && <span>free shipping</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </motion.section>
  )
}
