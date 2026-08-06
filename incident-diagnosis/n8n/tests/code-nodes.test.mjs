import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

test('extract-alerts expands every Alertmanager alert independently', async () => {
  const source = await readFile(new URL('../extract-alerts.js', import.meta.url), 'utf8');
  const fn = new Function('$input', source);
  const result = fn({ first: () => ({ json: { alerts: [
    { fingerprint: 'fp-a', startsAt: '2026-08-04T10:15:00Z', labels: { alertname: 'HighErrorRate', service: 'ride-service', namespace: 'vroom-dev' }, annotations: { metric_value: '8.2', threshold: '5' } },
    { fingerprint: 'fp-b', startsAt: '2026-08-04T10:16:00Z', labels: { alertname: 'DLQEventsDetected', job: 'dispatch-service', namespace: 'vroom-dev' }, annotations: { metric_value: '1' } },
  ] } }) });
  assert.equal(result.length, 2);
  assert.equal(result[0].json.fingerprint, 'fp-a');
  assert.equal(result[0].json.metric_value, 8.2);
  assert.equal(result[1].json.fingerprint, 'fp-b');
  assert.equal(result[1].json.service, 'dispatch-service');
  assert.equal(result[1].json.starts_at, '2026-08-04T10:16:00Z');
  assert.equal(result[1].json.incident_kind, 'dlq');
});

test('build-slack-message preserves suggestions and correlated trace link', async () => {
  const source = await readFile(new URL('../build-slack-message.js', import.meta.url), 'utf8');
  const fn = new Function('$input', source);
  const result = fn({ first: () => ({ json: {
    alert_name: 'DLQEventsDetected', service: 'dispatch-service', root_cause: 'Unknown event entered the DLQ',
    dev_action: 'Inspect the producer payload', kubectl_hint: 'kubectl logs deploy/dispatch-service',
    diagnosis_confidence: { level: 'high', reasons: ['exact trace ID'], missing_evidence: [] },
    trace_handoff: { status: 'correlated', error_service: 'dispatch-service', error_operation: 'dispatch.consume.UNKNOWN', grafana_url: 'http://grafana/explore?trace=abc' },
  } }) });
  const json = result[0].json;
  const text = JSON.stringify(json.blocks);
  assert.match(text, /high/i);
  assert.match(text, /Inspect the producer payload/);
  assert.match(text, /dispatch.consume.UNKNOWN/);
  assert.match(text, /http:\/\/grafana\/explore/);
});
