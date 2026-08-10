// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SupportingEvidence } from './SupportingEvidence'
import type { IncidentPresentation } from '../../types/incident'

const presentation: IncidentPresentation = {
  verdict: 'review_required', headline: 'DLQ failure', summary: '', confirmed_failure: 'Unknown event',
  causal_basis: null, evidence_gap: 'No related change', evidence_confidence: 'high', answer_source: 'safe_fallback',
  supporting_evidence: [{ id: 'log:selected', state: 'confirmed', kind: 'log', label: 'Structured log', value: 'unknown event' },
    { id: 'change:provenance', state: 'context', kind: 'change', label: 'Recent change', value: 'unrelated rollout' }],
  recommended_response: { mode: 'investigation', summary: 'Inspect logs' }, incident_events: [],
}

describe('SupportingEvidence', () => {
  afterEach(cleanup)
  it('explains contextual evidence inline', () => {
    render(<SupportingEvidence presentation={presentation} />)
    expect(screen.getAllByText('Context only').length).toBeGreaterThan(0)
    expect(screen.getByText(/without a demonstrated causal connection/)).toBeInTheDocument()
  })
})
