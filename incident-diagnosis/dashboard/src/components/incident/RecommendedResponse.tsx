import { ClipboardCheck } from 'lucide-react'
import type { RecommendedResponse as Response } from '../../types/incident'
import { Card } from '../ui/Card'
import { CodeBlock } from '../ui/CodeBlock'

const PLACEHOLDER = /<[^>]+>/

export function RecommendedResponse({ response }: { response: Response }) {
  const command = response.command && !PLACEHOLDER.test(response.command) ? response.command : null
  return (
    <Card className={response.mode === 'remediation' ? 'border-healthy bg-healthy-soft' : 'border-root-cause bg-root-cause-soft'}>
      <div className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-wide text-accent">
        <ClipboardCheck size={14} aria-hidden="true" /> Recommended response
      </div>
      <h3 className="mt-2 text-sm font-bold text-ink">{response.summary}</h3>
      {response.rationale && <p className="mt-1 text-xs text-ink-soft">{response.rationale}</p>}
      {command && <div className="mt-3"><CodeBlock>{command}</CodeBlock></div>}
      {!command && response.mode !== 'knowledge' && <p className="mt-3 rounded-md border border-border bg-canvas px-3 py-2 text-xs text-ink-faint">No executable command is published until the diagnosis is sufficiently supported.</p>}
      {response.expected_result && <p className="mt-2 text-[11px] text-ink-soft"><strong className="text-ink">Expected verification:</strong> {response.expected_result}</p>}
    </Card>
  )
}
