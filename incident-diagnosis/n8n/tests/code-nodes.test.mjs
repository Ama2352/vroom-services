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
});
