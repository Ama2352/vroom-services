import type { TimelineEntry, TimelineFiredEntry, TimelineResolvedEntry, TimelineStepEntry } from '../types/incident'

export interface Phase {
  name: string
  steps: string[]
}

export const PHASES: Phase[] = [
  { name: 'Collect Evidence', steps: ['collect_diagnostics', 'replicaset_diff', 'dependency_chase', 'provenance_lookup'] },
  { name: 'Match Knowledge',  steps: ['trusted_match_check'] },
  { name: 'Interpret',        steps: ['llm_phase1', 'quality_check', 'llm_refine'] },
  { name: 'Record',           steps: ['record_incident'] },
]

function phaseForStep(stepName: string): Phase | undefined {
  return PHASES.find(p => p.steps.includes(stepName))
}

function computePhaseStatus(steps: TimelineStepEntry[]): 'ok' | 'error' {
  return steps.some(s => s.metadata?.parsed === false) ? 'error' : 'ok'
}

export type PhaseItem = {
  kind: 'phase'
  name: string
  steps: TimelineStepEntry[]
  durationMs: number
  status: 'ok' | 'error'
}
export type FiredItem = { kind: 'fired'; entry: TimelineFiredEntry }
export type ResolvedItem = { kind: 'resolved'; entry: TimelineResolvedEntry }
export type TimelineItem = PhaseItem | FiredItem | ResolvedItem

export function groupTimeline(entries: TimelineEntry[]): TimelineItem[] {
  const items: TimelineItem[] = []
  let currentPhaseName: string | null = null
  let currentSteps: TimelineStepEntry[] = []

  function flushPhase() {
    if (!currentPhaseName || currentSteps.length === 0) return
    const phaseDef = PHASES.find(p => p.name === currentPhaseName)
    // unknown step name with no phase mapping — drop rather than crash (defensive; shouldn't happen with real data)
    if (!phaseDef) { currentPhaseName = null; currentSteps = []; return }
    const durationMs = currentSteps.reduce((sum, s) => sum + (s.duration_ms || 0), 0)
    items.push({ kind: 'phase', name: currentPhaseName, steps: currentSteps, durationMs, status: computePhaseStatus(currentSteps) })
    currentPhaseName = null
    currentSteps = []
  }

  for (const entry of entries) {
    if (entry.type === 'step') {
      const phaseDef = phaseForStep(entry.name)
      const name = phaseDef ? phaseDef.name : entry.name
      if (name !== currentPhaseName) {
        flushPhase()
        currentPhaseName = name
      }
      currentSteps.push(entry)
    } else {
      flushPhase()
      items.push({ kind: entry.type, entry } as FiredItem | ResolvedItem)
    }
  }
  flushPhase()
  return items
}

export function splitOccurrences(entries: TimelineEntry[]): TimelineEntry[][] {
  const occurrences: TimelineEntry[][] = []
  let current: TimelineEntry[] = []
  for (const entry of entries) {
    if (entry.type === 'fired') {
      if (current.length) occurrences.push(current)
      current = [entry]
    } else {
      current.push(entry)
    }
  }
  if (current.length) occurrences.push(current)
  return occurrences
}
