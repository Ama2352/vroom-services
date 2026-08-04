const d = $input.first().json;
const confidence = d.diagnosis_confidence ?? { level: 'unknown', reasons: [], missing_evidence: [] };
const trace = d.trace_handoff ?? { status: 'unavailable' };
const blocks = [
  { type: 'header', text: { type: 'plain_text', text: `${d.alert_name ?? 'Incident'} — ${d.service ?? 'unknown'}` } },
  { type: 'section', text: { type: 'mrkdwn', text: `*Diagnosis*\n${d.root_cause ?? 'Unavailable'}` } },
  { type: 'section', text: { type: 'mrkdwn', text: `*Confidence: ${String(confidence.level).toUpperCase()}*\n${confidence.reasons.join('\n') || confidence.missing_evidence.join('\n')}` } },
  { type: 'section', text: { type: 'mrkdwn', text: `*Suggested action*\n${d.dev_action ?? 'Investigate manually.'}\n\`${d.kubectl_hint ?? ''}\`` } },
];
if (trace.status === 'correlated' && /^https?:\/\//.test(trace.grafana_url ?? '')) {
  blocks.push({ type: 'section', text: { type: 'mrkdwn', text: `*Correlated trace*\n${trace.error_service ?? ''} → ${trace.error_operation ?? ''}` }, accessory: { type: 'button', text: { type: 'plain_text', text: 'Open trace' }, url: trace.grafana_url } });
}
return [{ json: { channel: '#vroom-monitoring', text: `${d.alert_name ?? 'Incident'} on ${d.service ?? 'unknown'}`, blocks } }];
