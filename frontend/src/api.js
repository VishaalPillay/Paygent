/** Data access.
 *
 * VITE_USE_MOCK=true serves fixtures exported straight from the seeded database by
 * `python -m scripts.export_mocks`, so the mock path carries real figures rather
 * than invented ones. That path has to keep working all night: it is the fallback
 * if the backend dies on stage.
 */

import summaryMock from './mock/summary.json'
import casesMock from './mock/cases.json'
import mandatesMock from './mock/mandates.json'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

async function get(path, fallback) {
  if (USE_MOCK) return fallback
  try {
    const res = await fetch(path)
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return await res.json()
  } catch (err) {
    // A dead backend degrades to fixtures rather than to an empty screen.
    console.warn(`[api] ${path} failed, serving fixture:`, err.message)
    return fallback
  }
}

export const fetchSummary = () => get('/api/summary', summaryMock)
export const fetchCases = () => get('/api/cases?limit=60', casesMock)
export const fetchMandates = () => get('/api/mandates', mandatesMock)
export const isMock = USE_MOCK

// Not list-fixture-backed like the three above — mock mode falls back to
// whatever's already in casesMock rather than a separate single-case fixture,
// since Case Detail's whole point is opening a real, specific case.
export const fetchCase = (caseId) =>
  get(`/api/cases/${caseId}`, casesMock.items.find((c) => c.case_id === caseId) ?? null)

export async function approveAction(caseId, actionId) {
  if (USE_MOCK) throw new Error('Approvals need the live backend, not fixture data.')
  const res = await fetch(`/api/cases/${caseId}/actions/${actionId}/approve`, { method: 'POST' })
  const body = await res.json()
  if (!res.ok) throw new Error(body?.error?.message || `${res.status} ${res.statusText}`)
  return body
}
