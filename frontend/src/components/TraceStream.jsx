import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import GuardrailBadge from './GuardrailBadge'

const EVENT_NAMES = ['thinking', 'tool_call', 'tool_result', 'guardrail', 'conclusion', 'done', 'error']

/** The reasoning trace, live, for one case. Opens an SSE connection and renders
 * each event as it arrives — never buffers and dumps them, so a reader watches
 * the agent think rather than seeing a wall of text appear all at once. Pacing
 * (~850ms between events) is the server's job; this just renders what arrives.
 */
export default function TraceStream({ caseId }) {
  const [events, setEvents] = useState([])
  const [status, setStatus] = useState('idle') // idle | connecting | live | done
  const esRef = useRef(null)

  useEffect(() => {
    esRef.current?.close()
    if (!caseId) { setStatus('idle'); setEvents([]); return }

    setEvents([])
    setStatus('connecting')
    const es = new EventSource(`/api/cases/${caseId}/stream`)
    esRef.current = es

    EVENT_NAMES.forEach((name) => {
      es.addEventListener(name, (e) => {
        const data = JSON.parse(e.data)
        setEvents((prev) => [...prev, data])
        setStatus(name === 'done' ? 'done' : 'live')
        if (name === 'done') es.close()
      })
    })
    es.onerror = () => { setStatus((s) => (s === 'done' ? s : 'done')); es.close() }

    return () => es.close()
  }, [caseId])

  return (
    <div className="border border-rule bg-paper-deep min-h-[16rem] max-h-[30rem] overflow-y-auto p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="label">Agent audit</span>
        {status === 'live' && (
          <span className="flex items-center gap-2 label text-oxblood">
            <span className="w-1.5 h-1.5 rounded-full bg-oxblood animate-breathe" /> live
          </span>
        )}
        {status === 'connecting' && <span className="label text-ink-4">connecting…</span>}
      </div>

      {status === 'idle' && (
        <p className="text-micro text-ink-4">Trigger a scenario to watch the agent investigate.</p>
      )}

      <AnimatePresence initial={false}>
        {events.map((e, i) => (
          <motion.div key={`${e.seq ?? i}`} initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.3 }} className="py-1">
            <EventLine e={e} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}

function EventLine({ e }) {
  switch (e.event) {
    case 'thinking':
      return <p className="text-[0.8125rem] text-ink-2 italic leading-relaxed">{e.text}</p>
    case 'tool_call':
      return (
        <p className="font-mono text-[0.75rem] text-navy">
          → {e.tool}({Object.values(e.args || {}).map(String).join(', ')})
        </p>
      )
    case 'tool_result':
      return (
        <p className={`font-mono text-[0.75rem] pl-4 ${e.ok ? 'text-ink-3' : 'text-oxblood'}`}>
          ← {e.summary}
        </p>
      )
    case 'guardrail':
      return <div className="mt-1"><GuardrailBadge check={e} /></div>
    case 'conclusion':
      return (
        <div className="mt-2 border-t border-rule pt-2">
          <p className="text-[0.8125rem] text-ink leading-relaxed">{e.text}</p>
          {e.recommended_action && (
            <p className="label text-ink-3 mt-1.5">
              {e.recommended_action.type} · {e.recommended_action.status}
              {e.recommended_action.blocked_by?.length > 0 ? ' · blocked' : ''}
            </p>
          )}
        </div>
      )
    case 'error':
      return <p className="text-[0.75rem] text-oxblood">⚠ {e.message}</p>
    case 'done': {
      const tone = e.status === 'BLOCKED' ? 'text-oxblood'
        : e.status === 'RESOLVED' ? 'text-forest' : 'text-ochre'
      return <p className={`label mt-2 ${tone}`}>{e.status} · {e.duration_ms}ms</p>
    }
    default:
      return null
  }
}
