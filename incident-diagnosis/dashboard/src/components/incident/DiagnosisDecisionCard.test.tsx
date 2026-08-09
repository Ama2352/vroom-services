// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DiagnosisDecisionCard } from './DiagnosisDecisionCard'

describe('DiagnosisDecisionCard', () => {
  it('makes a rejected diagnosis visibly review-only and shows its causal chain', () => {
    render(
      <DiagnosisDecisionCard
        decision={{ status: 'rejected_after_refine', published_generated_answer: false }}
        chain={{
          incident_kind: 'dlq',
          trigger_ids: ['metric:dlq_events'],
          primary_ids: ['log:selected', 'trace:abc'],
          causal_context_ids: ['change:gitops-123'],
          contradiction_ids: [],
        }}
      />,
    )

    expect(screen.getByText('Review required')).toBeInTheDocument()
    expect(screen.getByText('Generated remediation was withheld.')).toBeInTheDocument()
    expect(screen.getByText('log:selected')).toBeInTheDocument()
    expect(screen.getByText('change:gitops-123')).toBeInTheDocument()
  })
})
