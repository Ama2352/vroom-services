import type { Incident } from '../../types/incident'
import { Card, CardTitle } from '../ui/Card'
import { CodeBlock } from '../ui/CodeBlock'

export function ImmediateFixCard({ incident }: { incident: Incident }) {
  return (
    <Card>
      <CardTitle className="text-ink-soft">Immediate Fix</CardTitle>
      <p className="mb-2 text-[12.5px] text-ink-soft">{incident.dev_action}</p>
      <CodeBlock>{incident.kubectl_hint}</CodeBlock>
    </Card>
  )
}
