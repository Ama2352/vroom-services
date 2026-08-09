// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EvidenceGrid } from './EvidenceGrid'
import type { Incident } from '../../types/incident'

const baseIncident: Incident = {
  id: 'incident-1', alert_name: 'GenericAlert', service: 'dispatch-service', status: 'open', timestamp: 1,
  root_cause: 'review required', low_confidence: true, dev_action: 'review', kubectl_hint: 'kubectl get pods',
  pods_available: 1, pods_desired: 1, pods_running: 1, pods_ready: 1,
  restarts: 0, init_restarts: 0, template_diff: null, dependency: null, provenance: null,
  pending_suggestion: null, timeline: [],
}

describe('EvidenceGrid', () => {
  it('does not present an unscoped diagnostic log when canonical log lookup found no match', () => {
    render(<EvidenceGrid incident={{
      ...baseIncident,
      log_error: 'traces export: no healthy Tempo replicas',
      log_evidence: { status: 'no_match' },
    }} />)

    expect(screen.queryByText(/traces export/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Log & Event/)).not.toBeInTheDocument()
  })

  it('renders GitOps and exact service-source provenance as separate facts', () => {
    render(<EvidenceGrid incident={{
      ...baseIncident,
      provenance: {
        service: 'dispatch-service',
        causal_status: { status: 'recent_context', reason_codes: ['no_failure_linkage'], matched_identifiers: [] },
        dual: {
          gitops: { status: 'unavailable', reason: 'no_deployed_configuration_diff' },
          service_source: {
            status: 'found', source_relevance: 'no_relevant_service_files',
            commit: { sha: '5398d9a3', message: 'Merge feature', author: 'Dev', date: '2026-08-09T08:02:53Z', url: 'https://example/5398d9a3' },
            changed_paths: [],
          },
        },
      },
    }} />)

    expect(screen.getByText('Recent context')).toBeInTheDocument()
    expect(screen.getByText('No deployed configuration diff')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '5398d9a3' })).toHaveAttribute('href', 'https://example/5398d9a3')
    expect(screen.getByText('No relevant dispatch-service source files changed in this image revision')).toBeInTheDocument()
    expect(screen.queryByText(/originating commit wasn't found/)).not.toBeInTheDocument()
  })
})
