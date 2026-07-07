export interface KnowledgeListItem {
  key: string
  root_cause_pattern: string
  conclusive: boolean
  history_count: number
}

export interface KnowledgeHistoryEntry {
  id: string
  service: string
  symptom: string
}

export interface KnowledgeDetail {
  knowledge: {
    root_cause_pattern: string
    fix_action: string
    conclusive: boolean
  }
  history: KnowledgeHistoryEntry[]
}
