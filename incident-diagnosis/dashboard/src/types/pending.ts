export type PendingStatus = 'pending' | 'approved' | 'rejected'

export interface PendingListItem {
  id: string
  service: string
  symptom: string
  proposed_knowledge_key: string
  is_new_knowledge_key: boolean
  status: PendingStatus
  decided_by?: string
  decided_at?: number
}

export interface PendingDetail {
  id: string
  service: string
  symptom: string
  context_notes: string
  root_cause: string
  fix_action: string
  proposed_knowledge_key: string
  is_new_knowledge_key: boolean
  status: PendingStatus
}

export interface ApprovePayload {
  actor: string
  mode: 'existing' | 'new'
  knowledge_key: string
  symptom: string
  context_notes: string
  root_cause_pattern: string
  fix_action: string
  conclusive: boolean
}
