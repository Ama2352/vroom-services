// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { EvidenceFirstIncident } from './EvidenceFirstIncident'

it('renders raw evidence, units, and an advisory hypothesis without a confirmed cause', () => {
  render(<EvidenceFirstIncident incident={{
    alert_name: 'DLQEventsDetected', service: 'dispatch-service', status: 'open',
    diagnosis: { incident_summary: 'dispatch rejected an event.', diagnosis_cause: null,
      evidence_analysis: {}, hypothesis: 'The event contract may differ.', recommended_action: { kind: 'investigation', summary: 'Compare event contracts.' } },
    raw_evidence: { metrics: { state: 'confirmed', title: 'Alert and metrics', rows: [{ field: 'request rate', value: 0.2, unit: 'req/s' }] },
      logs: { state: 'confirmed', title: 'Structured log', rows: [{ field: 'log_error', value: 'unknown event type' }] } },
    retrieval: { mode: 'nearest', advisory_examples: [{ knowledge_key: 'unsupported_event_contract', example_id: 'x', evidence_template: 'log error' }] },
  }} />)
  expect(screen.getByText('Raw current evidence')).toBeInTheDocument()
  expect(screen.getByText('0.2 req/s')).toBeInTheDocument()
  expect(screen.getByText('Hypothesis — unconfirmed')).toBeInTheDocument()
  expect(screen.queryByText('Diagnosis cause')).not.toBeInTheDocument()
})
