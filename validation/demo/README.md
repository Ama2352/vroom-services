# Correlated incident proof

Run `bash validation/demo/correlated-incident-demo.sh` from the repository root after the K3s stack is running. The script is diagnostic-only: it does not scale, restart, delete, or patch workloads. It validates command availability, application deployments, and the latest incident response, then records JSON/Markdown evidence under `validation/reports/`.

The controlled poison-event injection and alert-polling steps remain a live-cluster procedure because they depend on the deployed Redis, Prometheus, Loki, Tempo, Alertmanager, n8n, and agent revisions.
