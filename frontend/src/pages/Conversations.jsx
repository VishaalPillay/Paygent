import { motion } from 'framer-motion'
import { rise, stagger } from '../lib/motion'
import CartAgentPanel from '../components/CartAgentPanel'

/** Screen 4. Agent negotiation on the left, policy denial on the right — the same
 * split as Case Detail and Mandate Board, this time lived out as a real
 * conversation instead of a guardrail block. Cart Agent is currently the only
 * conversation this screen hosts.
 */
export default function Conversations() {
  return (
    <main className="max-w-canvas mx-auto px-10">
      <motion.div variants={stagger(0.09)} initial="hidden" animate="show" className="pt-12">
        <motion.p variants={rise} className="label text-ink-3">
          Live negotiation · policy-gated
        </motion.p>
        <motion.h1 variants={rise}
          className="font-display text-[2.75rem] md:text-[3.5rem] leading-[1.05]
                     tracking-[-0.025em] mt-4 max-w-[22ch]">
          What the agent asked for, and what it actually got
        </motion.h1>
      </motion.div>

      <div className="h-px bg-ink mt-11" />

      <CartAgentPanel />
    </main>
  )
}
