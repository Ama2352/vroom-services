// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { test, expect } from 'vitest'
import { IncidentTimeline } from './IncidentTimeline'
import { AgentAudit } from './AgentAudit'
import type { IncidentOccurrence } from '../../types/incident'

const occurrence: IncidentOccurrence = {
  index: 0,
  fired_at: 100,
  evidence: {},
  presentation: {
    verdict: 'cause_confirmed', headline: 'Redis hostname changed', summary: 'The service cannot resolve Redis.', confirmed_failure: 'Redis dial failed', causal_basis: 'A hot-fix changed REDIS_ADDR.', evidence_confidence: 'high', answer_source: 'generated', supporting_evidence: [], recommended_response: { mode: 'remediation', summary: 'Restore the previous address' }, incident_events: [
      { id: 'change:hotfix', state: 'confirmed', title: 'Configuration changed', summary: 'REDIS_ADDR changed to bad-host:6379.', evidence_ids: ['change:hotfix'] },
      { id: 'log:redis', state: 'confirmed', title: 'Redis connection failed', summary: 'DNS lookup failed for bad-host.', evidence_ids: ['log:redis'] },
    ],
  },
  agent_steps: [{ type: 'step', timestamp: 101, name: 'collect_diagnostics', duration_ms: 20, metadata: { parsed: true } }],
}

test('renders chronology without agent internals', () => {
  render(<IncidentTimeline occurrence={occurrence} />)
  expect(screen.getByText('Incident chronology')).toBeInTheDocument()
  expect(screen.getByText('Configuration changed')).toBeInTheDocument()
  expect(screen.queryByText('collect_diagnostics')).not.toBeInTheDocument()
})

test('keeps audit details collapsed until requested', () => {
  render(<AgentAudit occurrence={occurrence} />)
  expect(screen.queryByText('collect_diagnostics')).not.toBeInTheDocument()
})
