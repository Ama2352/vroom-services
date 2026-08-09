import { describe, expect, it } from 'vitest'
import { groupTimeline } from './groupTimeline'
import type { TimelineEntry } from '../types/incident'

describe('groupTimeline', () => {
  it('keeps routing and both evaluation gates as visible ordered phases', () => {
    const entries: TimelineEntry[] = [
      { type: 'step', name: 'collect_diagnostics', timestamp: 1, duration_ms: 20 },
      { type: 'step', name: 'routing', timestamp: 2, duration_ms: 10 },
      { type: 'step', name: 'evidence_chain', timestamp: 3, duration_ms: 10 },
      { type: 'step', name: 'trusted_match_check', timestamp: 4, duration_ms: 15 },
      { type: 'step', name: 'llm_phase1', timestamp: 5, duration_ms: 50 },
      { type: 'step', name: 'hard_validation', timestamp: 6, duration_ms: 7 },
      { type: 'step', name: 'semantic_critic', timestamp: 7, duration_ms: 80 },
      { type: 'step', name: 'llm_refine', timestamp: 8, duration_ms: 60 },
      { type: 'step', name: 'hard_validation_refine', timestamp: 9, duration_ms: 8 },
      { type: 'step', name: 'semantic_critic_refine', timestamp: 10, duration_ms: 90 },
      { type: 'step', name: 'record_incident', timestamp: 11, duration_ms: 5 },
    ]

    const phases = groupTimeline(entries).filter(item => item.kind === 'phase')

    expect(phases.map(phase => phase.name)).toEqual([
      'Collect Evidence', 'Correlate Evidence', 'Match Knowledge', 'Evaluate Diagnosis', 'Record',
    ])
    expect(phases[3].durationMs).toBe(295)
  })
})
