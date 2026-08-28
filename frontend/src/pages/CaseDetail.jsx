import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { fetchCase, approveAction } from '../api'
import { inrExact, humanBreak, deadlineLabel, BASIS_LABEL } from '../lib/format'
import { rise, stagger } from '../lib/motion'
import TraceStream from '../components/TraceStream'
import GuardrailBadge from '../components/GuardrailBadge'

/** Screen 2. One case, investigated on demand — not one of three rehearsed
 * storefront buttons, whichever of the real open cases a reader actually clicked.
 * Left: the agent's live reasoning trace. Right: the guardrail verdict and
 * whatever action it produced. Same split as Mandate Board and Conversations,
 * on purpose — agent output on one side, deterministic control on the other.
 */
export default function CaseDetail() {
  const { caseId } = useParams()
  const [c, setC] = useState(null)
  const [error, setError] = useState(null)
  const [investigating, setInvestigating] = useState(false)
  const [approving, setApproving] = useState(false)
  const [approveError, setApproveError] = useState(null)

  useEffect(() => {
    setC(null)
    setError(null)
    setInvestigating(false)
    fetchCase(caseId)
      .then((row) => (row ? setC(row) : setError('No case with that id.')))
      .catch((e) => setError(e.message))
  }, [caseId])

  if (error) return <Shell><Message text={error} /></Shell>
  if (!c) return <Shell><Skeleton /></Shell>

  const modelled = c.basis === 'modelled'
  const dl = deadlineLabel(c.deadline_at, c.updated_at)
  const action = c.actions?.[0]
  const canApprove = c.status === 'AWAITING_APPROVAL' && action?.status === 'PROPOSED'
    && !action?.blocked_by?.length

  async function handleApprove() {
    setApproving(true)
    setApproveError(null)
    try {
      const updated = await approveAction(c.case_id, action.action_id)
      setC(updated)
    } catch (e) {
      setApproveError(e.message)
    } finally {
      setApproving(false)
    }
  }

  return (
    <Shell>
      <motion.div variants={stagger(0.07)} initial="hidden" animate="show" className="pt-12">
        <motion.div variants={rise}>
          <Link to="/" className="label text-ink-3 hover:text-ink transition-colors">
            ← Recovery queue
          </Link>
        </motion.div>

        <motion.p variants={rise} className="label text-ink-3 mt-6">
          {c.is_aggregate ? `${c.signal_count.toLocaleString('en-IN')} cases rolled up · ` : ''}
          {humanBreak(c.break_type)} · {c.resolver?.toLowerCase()}
        </motion.p>
        <motion.h1 variants={rise}
          className="font-display text-[2.25rem] md:text-[2.75rem] leading-[1.08]
                     tracking-[-0.02em] mt-3 max-w-[30ch]">
          {c.title}
        </motion.h1>
        <motion.p variants={rise} className="mt-3 text-base text-ink-2 max-w-[58ch] leading-relaxed">
          {c.summary}
        </motion.p>

        <motion.div variants={rise} className="flex flex-wrap items-baseline gap-x-8 gap-y-3 mt-7">
          <div>
            <div className={`figure text-[1.75rem] leading-none ${modelled ? 'text-ochre' : 'text-ink'}`}>
              <span className="text-[0.55em] text-ink-3 font-sans mr-0.5">₹</span>
              {inrExact(c.rupees_at_risk_inr, 2)}
            </div>
            <div className={`label mt-1 ${modelled ? 'text-ochre' : 'text-ink-3'}`}>
              {BASIS_LABEL[c.basis]} at risk
            </div>
          </div>
          <div>
            <div className="figure text-[1.125rem]">{c.status?.replaceAll('_', ' ')}</div>
            <div className="label text-ink-3 mt-1">status</div>
          </div>
          <div>
            <div className={`figure text-[1.125rem] ${dl.overdue ? 'text-oxblood' : dl.urgent ? 'text-ochre' : ''}`}>
              {dl.text}
            </div>
            <div className="label text-ink-3 mt-1">deadline</div>
          </div>
          {c.confidence != null && (
            <div>
              <div className="figure text-[1.125rem]">{Math.round(c.confidence * 100)}%</div>
              <div className="label text-ink-3 mt-1">confidence</div>
            </div>
          )}
        </motion.div>
      </motion.div>

      {c.ledger_snapshot && (
        <motion.div variants={rise} initial="hidden" animate="show"
          className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-rule mt-10 border border-rule">
          {['payment', 'order', 'inventory', 'accounting'].map((k) => (
            <div key={k} className="bg-paper px-4 py-3">
              <div className="label text-ink-3">{k}</div>
              <div className="font-mono text-[0.8125rem] mt-1">{c.ledger_snapshot[k] ?? '—'}</div>
            </div>
          ))}
        </motion.div>
      )}

      <div className="h-px bg-ink mt-10" />

      <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-8 pt-8 pb-20">
        {/* ---- left: the agent's reasoning ---------------------------------- */}
        <section>
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="font-display text-[1.375rem]">Agent reasoning</h2>
            {c.trace_available && !investigating && (
              <button onClick={() => setInvestigating(true)}
                className="label px-4 py-2 border border-ink text-ink hover:bg-paper-deep
                           transition-colors duration-200">
                Investigate live
              </button>
            )}
          </div>
          {c.trace_available ? (
            <TraceStream caseId={investigating ? c.case_id : null}
              onDone={() => fetchCase(c.case_id).then((row) => row && setC(row))} />
          ) : (
            <div className="border border-rule bg-paper-deep p-5">
              <p className="text-micro text-ink-3 leading-relaxed">
                {c.resolver === 'CART'
                  ? 'This case is worked by the cart agent, live in a conversation rather ' +
                    'than an audit trail — see Conversations.'
                  : 'This case is worked by the sequencer — rules and a scoring model ' +
                    'decide the retry plan directly, deliberately without an LLM in the loop.'}
              </p>
            </div>
          )}
        </section>

        {/* ---- right: the deterministic verdict ------------------------------ */}
        <section>
          <h2 className="font-display text-[1.375rem] mb-3">Guardrail verdict</h2>
          {c.guardrail_checks?.length > 0 ? (
            <div className="flex flex-col gap-3">
              {c.guardrail_checks.map((g, i) => <GuardrailBadge key={i} check={g} />)}
            </div>
          ) : (
            <p className="text-micro text-ink-4">
              {c.trace_available
                ? 'Nothing yet — investigate the case to see what it decides.'
                : 'No guardrail has evaluated this case.'}
            </p>
          )}

          {action && (
            <div className="border-t border-rule mt-5 pt-5">
              <div className="label text-ink-3">Proposed action</div>
              <p className="text-[0.875rem] text-ink mt-1.5">
                {action.type}
                {action.amount_inr != null && (
                  <span className="text-ink-3"> · ₹{inrExact(action.amount_inr, 2)}</span>
                )}
              </p>
              <p className="text-micro text-ink-3 mt-1 leading-relaxed">{action.reasoning}</p>
              {canApprove && (
                <button onClick={handleApprove} disabled={approving}
                  className="label px-4 py-2 mt-3 bg-ink text-paper hover:bg-ink-2
                             transition-colors duration-200 disabled:opacity-40">
                  {approving ? 'Approving…' : 'Approve · move the money'}
                </button>
              )}
              {approveError && <p className="text-micro text-oxblood mt-2">⚠ {approveError}</p>}
            </div>
          )}
        </section>
      </div>
    </Shell>
  )
}

const Shell = ({ children }) => (
  <main className="max-w-canvas mx-auto px-10">{children}</main>
)

const Message = ({ text }) => (
  <div className="mt-24 border-l-2 border-oxblood pl-5 py-3">
    <p className="text-base text-oxblood">{text}</p>
  </div>
)

const Skeleton = () => (
  <div className="pt-12 animate-pulse">
    <div className="h-2.5 w-32 bg-rule" />
    <div className="h-10 w-[28rem] max-w-full bg-rule mt-6" />
    <div className="h-4 w-[40rem] max-w-full bg-rule mt-4" />
    <div className="h-px w-full bg-rule mt-10" />
  </div>
)
