import { Link } from 'react-router-dom'
import type { PendingSuggestionRef } from '../../types/incident'
import { Card, CardTitle } from '../ui/Card'
import StatusBadge from '../ui/Badge'
import { buttonClasses } from '../ui/Button'

export function KnowledgeSuggestionCard({ suggestion }: { suggestion: PendingSuggestionRef }) {
  return (
    <Card>
      <CardTitle className="text-ink-soft">Knowledge Suggestion</CardTitle>
      <p className="mb-2 text-[11px] text-ink-faint">
        Suggested to save this symptom pattern to the Knowledge Base for faster future recognition — approval required.
      </p>
      <p className="mb-2.5 rounded-md border border-border bg-canvas px-2.5 py-2 text-[12.5px] text-ink-soft">
        "{suggestion.symptom}"
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={suggestion.status} />
        {suggestion.status === 'approved' && (
          <code className="rounded-md border border-border bg-canvas px-2 py-0.5 text-xs text-ink-soft">
            {suggestion.proposed_knowledge_key}
          </code>
        )}
        {suggestion.status === 'pending' && (
          <Link to={`/pending/${suggestion.id}`} className={buttonClasses('primary', 'ml-auto')}>
            Review &amp; Decide
          </Link>
        )}
      </div>
    </Card>
  )
}
