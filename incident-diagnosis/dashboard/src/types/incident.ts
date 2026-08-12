export type IncidentStatus = 'open' | 'resolved'
export type EvidenceStatus = 'available' | 'no_data' | 'unavailable'
export type TraceStatus = 'correlated' | 'representative' | 'no_trace_id' | 'not_found' | 'unavailable' | 'conflict'
export type ConfidenceLevel = 'high' | 'medium' | 'low' | 'unknown'

export interface DiagnosisConfidence { level: ConfidenceLevel; reasons: string[]; missing_evidence: string[] }
export interface ImpactEvidence { status: EvidenceStatus; window?: string; request_rate: number | null; error_rate_percent: number | null; p95_latency_ms?: number | null; p99_seconds?: number | null; errors?: string[]; triggering_metric?: { name: string; value: number; threshold: number | null; unit?: string; window?: string } }
export interface LogEvidence { status: string; trace_id?: string; operation?: string; message?: string }
export interface TraceHandoff { status: TraceStatus; trace_id?: string; error_service?: string; error_operation?: string; error_message?: string; service_path?: string[]; grafana_url?: string }

export interface ConfigurationDiff {
  status: 'changed' | 'unchanged' | 'unavailable'
  changes: Array<{ path: string; previous?: string | number | null; current?: string | number | null }>
  observed_at?: string
  reason?: string
}

export interface Dependency {
  namespace: string
  name: string
  pods_available: number
  pods_desired: number
  waiting_reason?: string
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
  timestamp?: number
  started_at?: number
  finished_at?: number
  name: string
  duration_ms?: number
  metadata?: Record<string, unknown>
}

export type TimelineEntry = TimelineFiredEntry | TimelineResolvedEntry | TimelineStepEntry

export type PresentationVerdict = 'cause_confirmed' | 'review_required' | 'evaluation_unavailable'
export type PresentationEvidenceState = 'confirmed' | 'context' | 'missing' | 'conflicting'
export type ResponseMode = 'remediation' | 'investigation' | 'knowledge'
export type AuditVerdict = 'passed' | 'rejected' | 'degraded' | 'informational' | 'failed'

export interface PresentationEvidence {
  id: string
  state: PresentationEvidenceState
  kind: 'metric' | 'log' | 'trace' | 'kubernetes' | 'change' | 'dependency' | string
  label: string
  value: string
  detail?: string
  occurred_at?: string | null
  href?: string | null
}

export interface IncidentEvent {
  id: string
  occurred_at?: string | null
  state: PresentationEvidenceState | 'review'
  title: string
  summary: string
  evidence_ids: string[]
}

export interface RecommendedResponse {
  mode: ResponseMode
  summary: string
  rationale?: string
  command?: string | null
  expected_result?: string
}

export interface IncidentPresentation {
  verdict: PresentationVerdict
  headline: string
  summary: string
  confirmed_failure: string
  failure_status?: 'confirmed' | 'unconfirmed' | string
  mechanism_status?: 'confirmed' | 'unconfirmed' | string
  attribution_status?: 'confirmed' | 'unproven' | 'conflicting' | 'unavailable' | string
  causal_basis?: string | null
  evidence_gap?: string | null
  evidence_confidence: ConfidenceLevel
  answer_source: 'knowledge' | 'generated' | 'safe_fallback' | string
  hypothesis?: string | null
  hypothesis_evidence_refs?: string[]
  supporting_evidence: PresentationEvidence[]
  recommended_response: RecommendedResponse
  incident_events: IncidentEvent[]
}

export interface OccurrenceEvidence {
  impact?: ImpactEvidence
  log_evidence?: LogEvidence
  trace_handoff?: TraceHandoff
  configuration_diff?: ConfigurationDiff | null
  dependency?: Dependency | null
  pods_ready?: number
  pods_desired?: number
  waiting_reason?: string
  [key: string]: unknown
}

export interface IncidentOccurrence {
  index: number
  fired_at: number
  presentation: IncidentPresentation
  agent_steps: TimelineStepEntry[]
  evidence: OccurrenceEvidence
  lifecycle_events?: TimelineResolvedEntry[]
}

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
  configuration_diff: ConfigurationDiff | null
  dependency: Dependency | null
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
  presentation?: IncidentPresentation
  retrieval_support?: { mode: string; accepted: boolean; source?: string | null }
  occurrences?: IncidentOccurrence[]
  selected_occurrence?: number
}

export interface IncidentListItem {
  id: string
  alert_name: string
  service: string
  root_cause: string
  timestamp: number
}
