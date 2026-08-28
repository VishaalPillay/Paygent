import { useState } from 'react'
import { Routes, Route } from 'react-router-dom'
import { MotionConfig } from 'framer-motion'
import Nav from './components/Nav'
import CommandCenter from './pages/CommandCenter'
import CaseDetail from './pages/CaseDetail'
import Conversations from './pages/Conversations'
import MandateBoard from './pages/MandateBoard'

export default function App() {
  const [generatedAt, setGeneratedAt] = useState(null)

  return (
    // Framer Motion is JavaScript, so the prefers-reduced-motion rule in index.css
    // does not reach it. `reducedMotion="user"` makes it honour the OS setting and
    // drop transform animations for anyone who has asked for less movement.
    <MotionConfig reducedMotion="user">
      <div className="min-h-screen">
        <Nav generatedAt={generatedAt} />
        <Routes>
          <Route path="/" element={
            <CommandCenter onLoaded={(s) => setGeneratedAt(s.generated_at)} />} />
          <Route path="/conversations" element={<Conversations />} />
          <Route path="/mandates" element={<MandateBoard />} />
          <Route path="/cases/:caseId" element={<CaseDetail />} />
          <Route path="*" element={<NotBuilt />} />
        </Routes>
      </div>
    </MotionConfig>
  )
}

/** The four screens still to come render an honest placeholder rather than a blank
 *  route. Nothing in this product is allowed to show an empty screen. */
const NotBuilt = () => (
  <main className="max-w-canvas mx-auto px-10 pt-24">
    <p className="label text-ink-3">Not built yet</p>
    <h1 className="font-display text-[2.5rem] mt-4 tracking-[-0.02em]">
      This screen is next.
    </h1>
    <p className="text-base text-ink-2 mt-4 max-w-[46ch]">
      Command Center is live. Case Detail, Mandate Board, Conversations and the
      Storefront follow in that order.
    </p>
  </main>
)
