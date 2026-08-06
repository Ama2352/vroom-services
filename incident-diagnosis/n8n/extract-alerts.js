const body = $input.first().json.body ?? $input.first().json;
const finiteNumber = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};
return (body.alerts ?? []).map((alert) => {
  const labels = alert.labels ?? {};
  const annotations = alert.annotations ?? {};
  const pod = labels.pod ?? '';
  const podService = pod.replace(/-[a-f0-9]{6,}-[a-z0-9]{5}$/, '');
  return { json: {
    fingerprint: alert.fingerprint ?? '',
    starts_at: alert.startsAt ?? '',
    alert_name: labels.alertname ?? body.groupLabels?.alertname ?? 'UnknownAlert',
    incident_kind: labels.incident_kind ?? ({ DLQEventsDetected: 'dlq', KubePodCrashLooping: 'crashloop' }[labels.alertname] ?? ''),
    service: labels.service || labels.job || podService || 'unknown',
    namespace: labels.namespace ?? 'unknown',
    pod,
    severity: labels.severity ?? 'warning',
    metric_value: finiteNumber(annotations.metric_value),
    threshold: finiteNumber(annotations.threshold ?? labels.threshold),
    status: alert.status ?? body.status ?? 'firing',
  }};
});
