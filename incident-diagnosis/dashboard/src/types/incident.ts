export type IncidentStatus = 'open' | 'resolved'

export interface EnvDiffEntry {
  key: string
  old_value: string
  new_value: string
}

export interface TemplateDiff {
  env_changed: boolean
  env_diff: EnvDiffEntry[]
  image_changed: boolean
  old_image?: string
  new_image?: string
  changed_at?: string
}

export interface Dependency {
  namespace: string
  name: string
  pods_available: number
  pods_desired: number
  waiting_reason?: string
}

export type Provenance =
  | { classification: 'hotfix'; changed_at: string }
  | {
      classification: 'gitops-commit'
      commit: { sha: string; author: string; message: string; url: string; diff_snippet: string; date?: string } | null
      pr: { number: number; title: string; url: string } | null
    }

export interface PendingSuggestionRef {
  id: string
  symptom: string
  status: 'pending' | 'approved' | 'rejected'
  proposed_knowledge_key: string
}

export type TimelineFiredEntry = {
  type: 'fired'
  timestamp: number
  evidence_snapshot?: { waiting_reason?: string }
}

export type TimelineResolvedEntry = {
  type: 'resolved'
  timestamp: number
  actor?: string
}

export type TimelineStepEntry = {
  type: 'step'
  timestamp: number
  name: string
  duration_ms?: number
  metadata?: Record<string, unknown>
}

export type TimelineEntry = TimelineFiredEntry | TimelineResolvedEntry | TimelineStepEntry

export interface Incident {
  id: string
  alert_name: string
  service: string
  status: IncidentStatus
  timestamp: number
  root_cause: string
  low_confidence: boolean
  dev_action: string
  kubectl_hint: string
  pods_available: number
  pods_desired: number
  waiting_reason?: string
  last_terminated_reason?: string
  restarts: number
  init_waiting_reason?: string
  init_last_terminated_reason?: string
  init_restarts: number
  log_error?: string
  event_reason?: string
  event_message?: string
  event_object?: string
  template_diff: TemplateDiff | null
  dependency: Dependency | null
  provenance: Provenance | null
  pending_suggestion: PendingSuggestionRef | null
  timeline: TimelineEntry[]
}

export interface IncidentListItem {
  id: string
  alert_name: string
  service: string
  root_cause: string
  timestamp: number
}
