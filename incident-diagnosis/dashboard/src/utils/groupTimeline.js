import { ScanSearch, Database, Brain, Save } from 'lucide-react'

export const PHASES = [
  { name: 'Collect Evidence', Icon: ScanSearch, steps: ['collect_diagnostics', 'replicaset_diff', 'dependency_chase'] },
  { name: 'Match Knowledge',  Icon: Database,   steps: ['trusted_match_check'] },
  { name: 'Interpret',        Icon: Brain,      steps: ['llm_phase1', 'quality_check', 'llm_refine'] },
  { name: 'Record',           Icon: Save,       steps: ['record_incident'] },
]

function phaseForStep(stepName) {
  return PHASES.find(p => p.steps.includes(stepName))
}

function interpretStatus(steps) {
  const byName = Object.fromEntries(steps.map(s => [s.name, s]))
  const last = byName.llm_refine || byName.llm_phase1
  if (last && last.metadata?.parsed === false) return 'error'
  if (byName.quality_check && byName.quality_check.metadata?.passed === false) return 'warn'
  return 'ok'
}

export function groupTimeline(entries) {
  const items = []
  let currentPhaseName = null
  let currentSteps = []

  function flushPhase() {
    if (!currentPhaseName || currentSteps.length === 0) return
    const phaseDef = PHASES.find(p => p.name === currentPhaseName)
    const durationMs = currentSteps.reduce((sum, s) => sum + (s.duration_ms || 0), 0)
    const status = currentPhaseName === 'Interpret' ? interpretStatus(currentSteps) : 'neutral'
    items.push({ kind: 'phase', name: currentPhaseName, Icon: phaseDef.Icon, steps: currentSteps, durationMs, status })
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
      items.push({ kind: entry.type, entry })
    }
  }
  flushPhase()
  return items
}

export function splitOccurrences(entries) {
  const occurrences = []
  let current = []
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
