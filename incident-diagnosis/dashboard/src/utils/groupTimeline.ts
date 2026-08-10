import type { TimelineEntry, TimelineFiredEntry, TimelineResolvedEntry, TimelineStepEntry } from '../types/incident'

export interface Phase {
  name: string
  steps: string[]
}

export const PHASES: Phase[] = [
  { name: 'Collect Evidence', steps: ['collect_diagnostics', 'configuration_diff', 'dependency_chase'] },
  { name: 'Match Knowledge',  steps: ['trusted_match_check'] },
  { name: 'Evaluate Diagnosis', steps: [
    'exact_conclusive', 'llm_phase1', 'hard_validation', 'semantic_critic',
    'llm_refine', 'hard_validation_refine', 'semantic_critic_refine',
  ] },
  { name: 'Record',           steps: ['record_incident'] },
]

function phaseForStep(stepName: string): Phase | undefined {
  return PHASES.find(p => p.steps.includes(stepName))
}

function computePhaseStatus(steps: TimelineStepEntry[]): 'ok' | 'error' {
  return steps.some(s => s.metadata?.parsed === false || s.metadata?.passed === false) ? 'error' : 'ok'
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

export type AuditPhase = {
  name: string
  steps: TimelineStepEntry[]
  durationMs: number
  verdict: 'passed' | 'rejected' | 'degraded' | 'informational' | 'failed'
}

const AUDIT_PHASES: Record<string, string> = {
  trusted_match_check: 'Knowledge retrieval',
  retrieval: 'Knowledge retrieval',
  exact_conclusive: 'Generate',
  llm_phase1: 'Generate',
  quality_check: 'Generate',
  hard_validation: 'Validate',
  semantic_critic: 'Validate',
  llm_refine: 'Refine',
  hard_validation_refine: 'Refine',
  semantic_critic_refine: 'Refine',
  record_incident: 'Finalize',
}

function auditPhaseForStep(name: string): string {
  return AUDIT_PHASES[name] || 'Other'
}

function auditVerdict(steps: TimelineStepEntry[]): AuditPhase['verdict'] {
  if (steps.some(step => step.metadata?.status === 'unavailable' || step.metadata?.status === 'degraded')) return 'degraded'
  if (steps.some(step => step.metadata?.passed === false && (step.name.includes('critic') || step.name.includes('validation')))) return 'rejected'
  if (steps.some(step => step.metadata?.parsed === false || step.metadata?.error)) return 'failed'
  if (steps.some(step => step.name === 'trusted_match_check' && step.metadata?.trusted_match === false)) return 'informational'
  return 'passed'
}

export function groupAgentAudit(entries: TimelineEntry[]): AuditPhase[] {
  const phases: AuditPhase[] = []
  let currentName: string | null = null
  let currentSteps: TimelineStepEntry[] = []

  function flush() {
    if (!currentName || currentSteps.length === 0) return
    phases.push({
      name: currentName,
      steps: currentSteps,
      durationMs: currentSteps.reduce((sum, step) => sum + (step.duration_ms || 0), 0),
      verdict: auditVerdict(currentSteps),
    })
    currentName = null
    currentSteps = []
  }

  for (const entry of entries) {
    if (entry.type !== 'step') continue
    const name = auditPhaseForStep(entry.name)
    if (name !== currentName) {
      flush()
      currentName = name
    }
    currentSteps.push(entry)
  }
  flush()
  return phases
}

export function groupTimeline(entries: TimelineEntry[]): TimelineItem[] {
  const items: TimelineItem[] = []
  let currentPhaseName: string | null = null
  let currentSteps: TimelineStepEntry[] = []

  function flushPhase() {
    if (!currentPhaseName || currentSteps.length === 0) return
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
