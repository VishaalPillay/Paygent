/** A guardrail verdict. A blocking failure is the demo centrepiece — it gets a
 * strong colour on the page, not a discreet red dot. Full literal class names per
 * tone (never `border-${tone}`) so Tailwind's JIT scanner can see them.
 */
const TONE = {
  blocked: { border: 'border-oxblood', text: 'text-oxblood', label: 'Blocked' },
  passed: { border: 'border-forest', text: 'text-forest', label: 'Passed' },
  flagged: { border: 'border-ochre', text: 'text-ochre', label: 'Flagged' },
}

export default function GuardrailBadge({ check }) {
  const key = check.blocking && !check.passed ? 'blocked' : check.passed ? 'passed' : 'flagged'
  const t = TONE[key]

  return (
    <div className={`border-l-2 pl-4 py-2 ${t.border}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`label ${t.text}`}>{t.label}</span>
        <span className="font-mono text-[0.6875rem] text-ink-3">{check.name}</span>
      </div>
      <p className="text-micro text-ink-2 mt-1 leading-relaxed">{check.message}</p>
    </div>
  )
}
