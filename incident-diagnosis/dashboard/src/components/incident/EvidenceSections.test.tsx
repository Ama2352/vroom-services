// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { EvidenceSections } from './EvidenceSections'

describe('EvidenceSections', () => {
  afterEach(cleanup)

  it('does not render full panels for empty sources', () => {
    render(<EvidenceSections evidence={{ trace_handoff: { status: 'no_trace_id' } }} />)
    expect(screen.queryByText('Dependencies')).not.toBeInTheDocument()
    expect(screen.getByText(/Trace unavailable/)).toBeInTheDocument()
  })

  it('keeps meaningful runtime state for an application incident', () => {
    render(<EvidenceSections evidence={{ pods_ready: 1, pods_desired: 1, waiting_reason: '', log_evidence: { status: 'found', message: 'event rejected' } }} />)
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getByText('event rejected')).toBeInTheDocument()
  })

  it('renders a correlated trace id as a Grafana link', () => {
    render(<EvidenceSections evidence={{ trace_handoff: { status: 'correlated', trace_id: 'abc123', grafana_url: 'http://grafana.local/explore?trace=abc123' } }} />)
    expect(screen.getByRole('link', { name: /abc123/ })).toHaveAttribute('href', 'http://grafana.local/explore?trace=abc123')
  })
})
