// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { RecommendedResponse } from './RecommendedResponse'

describe('RecommendedResponse', () => {
  afterEach(cleanup)
  it('renders the command below the response text', () => {
    const command = 'kubectl set env deployment/ride-service -n vroom-dev REDIS_ADDR=redis.platform.svc.cluster.local:6379'
    render(<RecommendedResponse response={{ mode: 'remediation', summary: 'Restore Redis', rationale: 'The change is causal.', command, expected_result: 'Pod becomes ready.' }} />)
    expect(screen.getByText(command)).toBeInTheDocument()
    expect(screen.getByText('Expected verification:')).toBeInTheDocument()
  })

  it('does not render a placeholder command', () => {
    render(<RecommendedResponse response={{ mode: 'investigation', summary: 'Inspect the service', command: 'kubectl logs <service-pod-name>' }} />)
    expect(screen.queryByText('kubectl logs <service-pod-name>')).not.toBeInTheDocument()
    expect(screen.getByText(/No executable command/)).toBeInTheDocument()
  })
})
