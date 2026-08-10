// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { DiagnosisSummary } from './DiagnosisSummary'
import type { IncidentPresentation } from '../../types/incident'

const base: IncidentPresentation = {
  verdict: 'cause_confirmed', headline: 'Redis failed', summary: 'A runtime value caused the failure.',
  confirmed_failure: 'Redis DNS lookup failed', causal_basis: 'Runtime change matches the log.', evidence_gap: null,
  evidence_confidence: 'high', answer_source: 'generated', supporting_evidence: [],
  recommended_response: { mode: 'remediation', summary: 'Restore Redis' }, incident_events: [],
}

describe('DiagnosisSummary', () => {
  afterEach(cleanup)
  it('shows causal basis for a confirmed cause', () => {
    render(<DiagnosisSummary presentation={base} />)
    expect(screen.getByText('Confirmed failure')).toBeInTheDocument()
    expect(screen.getByText('Causal basis')).toBeInTheDocument()
    expect(screen.queryByText('Evidence gap')).not.toBeInTheDocument()
  })

  it('shows an evidence gap when the cause is unproven', () => {
    render(<DiagnosisSummary presentation={{ ...base, verdict: 'review_required', causal_basis: null, evidence_gap: 'No related change was found.' }} />)
    expect(screen.getByText('Evidence gap')).toBeInTheDocument()
    expect(screen.queryByText('Causal basis')).not.toBeInTheDocument()
  })
})
