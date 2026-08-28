import { NavLink } from 'react-router-dom'
import { motion } from 'framer-motion'
import { EASE } from '../lib/motion'
import { isMock } from '../api'

const LINKS = [
  { to: '/', label: 'Command Center', end: true },
  { to: '/mandates', label: 'Mandates' },
  { to: '/conversations', label: 'Conversations' },
]

/** A masthead, not a navbar. Wordmark, rule, section list — the way a printed
 *  publication announces itself. */
export default function Nav({ generatedAt }) {
  const stamp = generatedAt
    ? new Date(generatedAt).toLocaleDateString('en-IN', {
        day: 'numeric', month: 'long', year: 'numeric' })
    : ''

  return (
    <header className="max-w-canvas mx-auto px-10">
      <div className="flex items-baseline justify-between pt-9 pb-4">
        <div className="flex items-baseline gap-4">
          <span className="font-display text-[1.75rem] leading-none tracking-[-0.02em]">
            Paygent
          </span>
          <span className="label hidden sm:block">Revenue recovery</span>
        </div>
        <div className="flex items-baseline gap-5">
          {isMock && (
            <span className="label text-ochre border border-ochre-edge px-2 py-1 bg-ochre-bg">
              Fixture data
            </span>
          )}
          <span className="label">{stamp}</span>
        </div>
      </div>

      <motion.div
        className="h-px bg-ink origin-left"
        initial={{ scaleX: 0 }} animate={{ scaleX: 1 }}
        transition={{ duration: 1.1, ease: EASE }}
      />

      <nav className="flex gap-8 pt-3 pb-0">
        {LINKS.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end}
            className={({ isActive }) =>
              `label pb-3 -mb-px border-b-2 transition-colors duration-200 ${
                isActive ? 'border-ink text-ink' : 'border-transparent hover:text-ink-2'
              }`}>
            {l.label}
          </NavLink>
        ))}
      </nav>
      <div className="h-px bg-rule" />
    </header>
  )
}
