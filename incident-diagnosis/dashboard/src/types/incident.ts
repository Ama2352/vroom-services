export type IncidentStatus = 'open' | 'resolved'
export type EvidenceStatus = 'available' | 'no_data' | 'unavailable'
export type TraceStatus = 'correlated' | 'representative' | 'no_trace_id' | 'not_found' | 'unavailable' | 'conflict'
export type ConfidenceLevel = 'high' | 'medium' | 'low' | 'unknown'

export interface DiagnosisConfidence { level: ConfidenceLevel; reasons: string[]; missing_evidence: string[] }
export interface ImpactEvidence { status: EvidenceStatus; window?: string; request_rate: number | null; error_rate_percent: number | null; p99_seconds: number | null; errors?: string[]; triggering_metric?: { name: string; value: number; threshold: number | null } }
export interface LogEvidence { status: string; trace_id?: string; operation?: string; message?: string }
export interface TraceHandoff { status: TraceStatus; trace_id?: string; error_service?: string; error_operation?: string; error_message?: string; service_path?: string[]; grafana_url?: string }

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

export interface CommitSummary {
  sha: string
  author?: string
  message?: string
  url?: string
  date?: string
}

export interface ProvenanceSource {
  status: 'found' | 'unavailable'
  reason?: string
  classification?: string
  commit?: CommitSummary
  changed_at?: string
  changed_paths?: string[]
  source_relevance?: 'relevant_files_found' | 'no_relevant_service_files'
}

export interface DualProvenance {
  service: string
  causal_status: {
    status: 'causal_candidate' | 'recent_context' | 'conflicting' | 'unavailable'
    reason_codes: string[]
    matched_identifiers: string[]
  }
  dual: { gitops: ProvenanceSource; service_source: ProvenanceSource }
  classification?: string
  changed_at?: string
}

export type Provenance =
  | DualProvenance
  | { classification: 'hotfix'; target?: 'dependency'; dependency_name?: string; diff?: string; drift?: Array<{ key: string; correct: string; wrong: string }>; changed_at?: string }
  | {
      classification: 'gitops-commit'
      target?: 'dependency'
      dependency_name?: string
      diff?: string
      drift?: Array<{ key: string; correct: string; wrong: string }>
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
  pods_running: number
  pods_ready: number
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
  impact?: ImpactEvidence
  log_evidence?: LogEvidence
  trace_handoff?: TraceHandoff
  diagnosis_confidence?: DiagnosisConfidence
  diagnosis_decision?: { status: string; published_generated_answer: boolean } | null
  causal_chain_summary?: {
    incident_kind: string
    trigger_ids: string[]
    primary_ids: string[]
    causal_context_ids: string[]
    contradiction_ids: string[]
  } | null
}

export interface IncidentListItem {
  id: string
  alert_name: string
  service: string
  root_cause: string
  timestamp: number
}
