# Incident Diagnosis Retrieval Redesign

Date: 2026-08-12

## Goal

Redesign the incident agent around two credible demonstrations:

- A DLQ event-processing failure caused by an unsupported event contract.
- A Redis connectivity failure caused by a workload configuration change.

The design must keep BM25 and MiniLM, improve useful evidence and hypotheses, and remove the old Kubernetes-trigger, free-text-history, and answer-in-retrieval coupling. This is a breaking schema replacement. Redis is ephemeral development storage, so no migration or durable-data compatibility is required.

## Principles

1. Current incident evidence is authoritative.
2. Retrieval finds similar approved cases; it does not prove causality.
3. Human-approved conclusions remain separate from retrieval text.
4. Exact reuse is permitted only for an identical normalized evidence template.
5. Retrieved cases are optional guidance for generation and are never citable incident evidence.
6. Failed validation must reduce certainty without discarding useful evidence analysis.

## Data model

### Knowledge

Knowledge contains the human-approved meaning and response, not retrieval evidence.

```yaml
knowledge_key: unsupported_event_contract
diagnosis_cause: Producer and consumer use incompatible event contracts.
remediation: Align the producer and consumer contract versions before replaying the DLQ.
```

`knowledge_key` is a stable identifier and link. Neither the key, diagnosis cause, nor remediation is indexed by BM25 or MiniLM.

### Approved retrieval example

An approved example is an immutable normalized evidence template copied from a reviewed incident.

```yaml
example_id: example-123
knowledge_key: unsupported_event_contract
exact_reusable: true
evidence:
  alert_name: DLQEventsDetected
  service: dispatch-service
  waiting_reason: ""
  event_reason: ""
  log_error: unknown event type Trip.Requested.v2
  trace_error: dispatch.consume.Trip.Requested.v2
  configuration_diff: []
approval:
  approved_by: operator
  approved_at: 2026-08-12T00:00:00Z
```

Approved examples default to `exact_reusable: true`. A reviewer may unmark an example to make it advisory-only. The review UI shows a small warning when an example has little diagnostic detail, but does not block approval.

The old `history` schema is removed. Approved examples replace history and canonical retrieval records as one consistent type.

### Development bootstrap catalog

The agent seeds a deterministic, varied approved catalog whenever the ephemeral development store has no current-schema corpus. Seeding is idempotent within a running Redis instance and uses a schema-versioned corpus namespace so old development keys cannot be mistaken for current records.

The bootstrap catalog must contain enough neighboring and unrelated cases to exercise retrieval rather than trivially returning the only available answer. It includes at least:

- Unsupported event contract.
- Malformed event payload.
- Event deserialization failure.
- Poison-message/business validation failure.
- Invalid configured dependency address.
- Dependency unavailable or connection refused without a configuration change.
- DNS/service-discovery failure.
- Generic startup CrashLoopBackOff.
- OOMKilled memory-limit failure.
- Image pull failure.
- Missing Secret or ConfigMap.
- Scheduling/resource-capacity failure.

Each knowledge family has at least one approved evidence example. The DLQ and Redis families have multiple wording variants so the tests cover both exact fingerprint reuse and non-identical semantic retrieval. Shared global hints deliberately overlap between related families, allowing the evaluation to verify that MiniLM improves BM25 ordering and that the top three remain distinct knowledge families.

Bootstrap conclusions and remediations are human-authored fixture data. Bootstrap retrieval examples still obey the production schema: immutable normalized evidence, separate knowledge, and separately linked hints.

### Global retrieval hints

Hints are globally reusable approved symptom phrases. Knowledge and hints have a many-to-many relationship.

```yaml
hint:
  hint_id: hint-dns-lookup
  text: DNS lookup failure

knowledge_hint_link:
  knowledge_key: invalid_dependency_address
  hint_id: hint-dns-lookup
```

The LLM may suggest hints from incident evidence. Suggested hints remain pending until a reviewer edits or approves them. Autocomplete searches the global catalog and shows existing knowledge links to encourage reuse. Normalized duplicate hint text is rejected. Hints are retrieval metadata, not evidence.

## Evidence collection and presentation

Collect and retain:

- The triggering alert metric, value, threshold, unit, and window.
- Structured error logs within the incident window.
- Correlated trace error service, operation, and error message.
- Kubernetes workload/container state and warning events.
- Safe workload configuration diff with previous and current values.
- Request rate in requests per second.
- HTTP error rate in percent.
- p95 latency in milliseconds.
- CPU usage in cores and as a percentage of request/limit when available.
- Memory working set in MiB and as a percentage of limit when available.
- Ephemeral-storage usage in MiB and as a percentage of limit when available.
- CPU throttling percentage when available.

Unavailable metrics remain unavailable and are never converted to zero. Resource metrics are displayed as operational context. They enter the retrieval template only when they are the triggering alert evidence.

## Fixed retrieval template

Live incidents and stored examples use the same fixed template:

```yaml
alert_name:
service:
triggering_metric:
waiting_reason:
last_terminated_reason:
event_reason:
event_message:
log_error:
trace_error_service:
trace_error_operation:
trace_error_message:
configuration_diff:
```

The serializer performs mechanical normalization only:

- Fixed field ordering and missing-value representation.
- Whitespace normalization.
- Deterministic sorting and deduplication of repeated values.
- Removal of volatile identifiers such as timestamps, trace IDs, pod-generated suffixes, event IDs, and instantaneous non-triggering metric values.

It performs no causal classification and uses no `normal` or `abnormal` rules. Structured source errors, Kubernetes failure states/events, configuration changes, and the triggering alert fill their corresponding slots. Other collected evidence remains available to the LLM and UI but does not add retrieval noise.

## Retrieval flow

### Exact path

The canonical serialized evidence template is fingerprinted. If it matches exactly one approved example with `exact_reusable: true`, the linked knowledge is reused directly without BM25, MiniLM, or generation.

The exact path builds the incident summary and evidence-group presentation deterministically from current evidence, publishes the linked diagnosis cause and remediation, omits the hypothesis, and records the matched example and knowledge key for audit.

If identical reusable examples point to different knowledge keys, the result is ambiguous and falls through to advisory retrieval.

### Advisory path

When exact lookup does not produce a unique result:

1. Each retrieval document consists only of immutable example evidence plus globally approved hints linked to its knowledge.
2. BM25 ranks example documents using lexical overlap, keeps the highest-ranked example per `knowledge_key`, and selects up to eight distinct knowledge families.
3. MiniLM cross-encodes the live template against those eight selected examples and reranks them semantically.
4. The top three distinct knowledge families are returned as `nearest approved examples`.
5. Their knowledge records are resolved only after ranking.

There is no RRF and no relevance threshold. The existing experiment supports BM25 top-eight candidate generation followed by MiniLM reranking, but its reported quality is not treated as proof of the new evidence-only corpus because the old reranker input included root cause and remediation.

Nearest examples always enter the LLM prompt as optional guidance. If BM25 produces no candidate, generation proceeds from live evidence alone. Retrieval infrastructure failure is reported as `degraded` and also falls back to live-evidence generation.

User-facing retrieval states are:

- `exact`: identical reusable approved example.
- `nearest`: up to three advisory examples.
- `none`: the corpus or candidate set is empty.
- `degraded`: retrieval infrastructure failed.

## LLM generation

For a non-exact incident, one generation call receives:

1. Current evidence, grouped into alert/metrics, logs, traces, Kubernetes, and configuration.
2. Up to three nearest approved cases, each containing its example evidence, linked diagnosis cause, remediation, and knowledge key.
3. Evidence identifiers attached directly to current evidence facts.

The prompt labels retrieved cases as optional historical guidance and states that they are not proof. The LLM may use one, use several compatible insights, or ignore all candidates.

The output contract is:

```yaml
evidence_analysis:
  metrics:
  logs:
  traces:
  kubernetes:
  configuration:
incident_summary:
diagnosis_cause: null
hypothesis:
recommended_action:
used_knowledge_keys: []
evidence_refs: []
hypothesis_evidence_refs: []
```

- `incident_summary` states what current evidence directly shows.
- `diagnosis_cause` contains a confirmed causal explanation or `null`.
- `hypothesis` contains the best evidence-grounded unconfirmed explanation when a cause is not established.
- `recommended_action` is the approved remediation only when supported; otherwise it is the next discriminating investigation.
- `used_knowledge_keys` provides auditability but never counts as evidence.
- Confirmed claims and hypotheses cite only current evidence identifiers.

## Validation and fallback

The hard validator checks JSON structure, required fields, evidence-reference existence, and separation between confirmed and tentative language.

The semantic critic checks whether `incident_summary`, `diagnosis_cause`, `hypothesis`, and `recommended_action` are supported at their stated certainty by current evidence. It does not determine retrieval relevance. It uses the configured model path and is not assumed to be independent merely because it is a second call.

If either validator fails, refine once using the combined issues. If the refined answer still fails:

- Mark the diagnosis review-required.
- Set `diagnosis_cause` to `null`.
- Prevent automatic or confirmed remediation.
- Preserve deterministic collected evidence and evidence-group presentation.
- Preserve a hypothesis only if its own hard grounding checks pass; otherwise label it unavailable rather than leaking a rejected proposal.

## Human approval workflow

When an operator resolves an incident, the system proposes:

- A new or existing `knowledge_key`.
- `diagnosis_cause` and `remediation` for new knowledge.
- The immutable evidence template as a new approved example.
- `exact_reusable: true` by default.
- Suggested global retrieval hints.

The reviewer may edit semantic knowledge, reuse or create hints, and disable exact reuse. Approval atomically stores the knowledge if new, the example, hint links, and audit metadata. Unapproved suggestions never enter retrieval.

## Dashboard design

The incident detail page uses the approved combined prototype as its visual reference: the overall hierarchy and two-column composition of the first prototype, with the explicit raw-evidence cards of the second. The prototype is a design artifact, not production source code.

The page contains:

- A diagnosis hero with `incident_summary`, `diagnosis_cause` when confirmed, and `hypothesis` when the cause is unconfirmed. An absent hypothesis is hidden rather than replaced with filler text.
- A primary left column containing raw current evidence followed by the recommended action.
- A secondary right column containing operational metrics and nearest approved examples.
- A collapsed agent audit section for debugging rather than primary diagnosis content.

The hero status is derived from the validated diagnosis, not merely from alert state: a supported cause is `Cause confirmed`; otherwise it is `Review required`.

### Raw current evidence

Evidence cards show collected source observations, not LLM summaries. Each card uses explicit field/value rows so values remain understandable without relying on position or punctuation. For example:

```text
Kubernetes
available    1 pod
desired      1 pod
restarts     0 pod restarts

Configuration diff
field        REDIS_ADDR
previous     redis.platform.svc.cluster.local:6379
current      bad-host:6379
```

Logs expose the structured field and value. Traces expose participating services, operation, and error. Configuration diffs expose field, previous value, and current value. Long observations may wrap inside their own row; cards do not compress several unrelated values into one sentence.

Every numeric value displays an explicit unit or count context, including `pod`, `pod restarts`, `req/s`, `%`, `ms`, `MiB`, and `events / 5m`. Labels such as `1/1 available` are prohibited because the counted object is unclear.

Raw evidence cards use source-status badges:

- `CONFIRMED`: the source explicitly reports this observation.
- `CONTEXT`: the observation is relevant but does not prove the cause.
- `NOT FOUND`: the collector did not find the expected source or change.

These statuses describe the observation, not causal certainty. Each badge has a single custom hover/focus tooltip containing its explanation. Native duplicate tooltips and empty overlay boxes are not used.

Nearest approved examples are labeled as advisory historical cases and visually separated from current evidence. They must never appear under supporting evidence or use a `CONFIRMED` evidence badge.

### Knowledge and review pages

The old History page is removed. The Knowledge list shows diagnosis cause, remediation, approved-example count, and linked global hints. Knowledge detail allows reviewers to inspect examples, toggle `exact_reusable`, and link or unlink global hints. Immutable example evidence cannot be edited; correcting it requires replacing the example.

The pending-review page shows the copied evidence template, proposed knowledge conclusion, LLM-suggested hints, global hint autocomplete, existing hint links, and a light warning when an exact-reusable example contains little diagnostic detail.

## API contract

The API supports the dashboard approval flow and reproducible development tests without relying on direct Redis edits.

### Knowledge and example management

- `POST /knowledge` creates semantic knowledge.
- `GET /knowledge/{knowledge_key}` returns knowledge, example summaries, and linked hints.
- `PUT /knowledge/{knowledge_key}` updates diagnosis cause and remediation.
- `DELETE /knowledge/{knowledge_key}` deletes knowledge only when no approved examples reference it, unless an explicit cascading development operation is used.
- `POST /knowledge/{knowledge_key}/examples` creates an immutable approved example from a complete normalized evidence template and audit actor.
- `PATCH /examples/{example_id}` may change `exact_reusable` and audit metadata only; it cannot edit evidence.
- `DELETE /examples/{example_id}` removes an erroneous approved example and rebuilds the affected corpus snapshot.

### Global hints

- `POST /hints` creates a normalized global hint and rejects duplicate normalized text.
- `GET /hints?query=...` provides autocomplete and includes current knowledge links.
- `PUT /knowledge/{knowledge_key}/hints` atomically replaces the knowledge-to-hint links with approved hint IDs.

### Atomic pending approval

`POST /pending/{incident_id}/approve` accepts the reviewer actor, new-or-existing knowledge choice, editable semantic conclusion, `exact_reusable`, selected existing hint IDs, and edited new hint texts. The server copies the normalized evidence template from the stored pending incident; the client cannot substitute different incident evidence. Knowledge creation or update, example creation, hint creation/linking, and approval audit are one atomic operation.

### Development corpus administration

The following endpoints exist only when `ENABLE_DEV_ADMIN=true`; otherwise they return `404`:

- `POST /admin/dev/corpus/reset`
- `POST /admin/dev/corpus/seed`
- `POST /admin/dev/corpus/import`
- `GET /admin/dev/corpus/export`

Import accepts explicit knowledge, immutable examples, `exact_reusable` settings, global hints, and links. It is the supported way to inject exact, advisory, ambiguous, and unrelated fixtures for assistant-driven and manual tests. Reset is deliberately destructive but limited to the schema-versioned development corpus. Seed loads the varied bootstrap catalog and remains idempotent.

## Demo expectations

### DLQ event-contract failure

A normal trip request should propagate trace context from `ride-service` to `dispatch-service`. Ride publishes an unsupported contract version; dispatch rejects it and sends it directly to the DLQ. Expected presentation:

- Incident summary: dispatch rejected a named event type and DLQ events increased.
- Evidence: trigger metric, structured log, cross-service error trace, and Kubernetes health context.
- Diagnosis cause: populated only when an identical reusable example or sufficient current evidence supports the contract incompatibility.
- Otherwise hypothesis: producer and consumer may support different contract versions.
- Action: compare supported contract versions before replaying DLQ messages.

The existing direct Redis poison-pill script is insufficient for the final distributed-trace demonstration because it bypasses `ride-service` and does not propagate trace context.

### Redis configuration failure

A workload configuration change sets `REDIS_ADDR` to an invalid endpoint. Runtime logs show DNS or connection failure involving the new value, and the workload becomes unavailable or restarts. Expected presentation:

- Incident summary: ride-service cannot resolve or connect to the configured endpoint.
- Evidence: configuration diff, concrete log error, Kubernetes state, alert trigger, and operational metrics.
- Diagnosis cause: the changed Redis endpoint caused the observed connection failure when current evidence directly connects the new value to the error.
- Action: restore the previous endpoint and verify readiness and Redis connectivity.

## Error handling and observability

- Corpus rebuilds are versioned and atomic; the last valid snapshot may be used when version checks temporarily fail.
- Exact ambiguity is visible and always falls through to advisory retrieval.
- BM25, MiniLM, corpus, and LLM failures are recorded separately.
- Debug output includes the normalized template, fingerprint result, BM25 top eight, MiniLM ordering, selected example IDs, resolved knowledge keys, and validation decisions.
- User-facing output avoids internal labels such as `reranked_history`.

## Testing

Tests cover:

- Deterministic template serialization and fingerprinting.
- Exclusion of volatile/noisy fields.
- Exact reuse, advisory-only examples, and ambiguous exact fingerprints.
- BM25 indexing only example evidence and linked hints.
- MiniLM serialization excluding knowledge key, diagnosis cause, and remediation.
- Top-eight candidate generation, top-three distinct-family collapse, and empty/degraded retrieval.
- Many-to-many global hint links, deduplication, approval, and autocomplete.
- LLM prompt separation between current evidence and nearest cases.
- Validators rejecting knowledge-only citations and unsupported confirmed causes.
- Failed-refine finalization without rejected hypothesis leakage.
- Metric units, p95, CPU, memory, ephemeral storage, and unavailable-data behavior.
- End-to-end DLQ and Redis demo fixtures, plus unrelated/no-knowledge incidents.
- Dashboard rendering of confirmed and review-required diagnoses, omitted optional fields, advisory examples, and raw evidence field/value rows.
- Explicit units for every displayed count and measurement, including long-value wrapping and badge tooltip keyboard focus.
- Knowledge/example/hint CRUD, immutable evidence enforcement, atomic pending approval, duplicate-hint rejection, and exact-reuse toggling.
- Development admin gating, reset/seed/import/export round trips, exact ambiguity fixtures, and deterministic reseeding.

The existing retrieval experiment is retained as historical evidence but must be rerun against the new bootstrap catalog and evidence-only corpus before making quantitative accuracy claims. Its fixtures include exact, paraphrased, neighboring-family, unrelated, sparse, and degraded cases for both demo domains.

## Legacy cleanup and deletion

The implementation is a replacement, not an adapter around the old mechanism. After the new path is covered by tests, remove obsolete code, storage keys, routes, UI, fixtures, and dependencies for:

- Free-text history records, history retrieval, history APIs, and the dashboard History page.
- Canonical Kubernetes-only conclusive matching such as `trigger_waiting_reason`, `conclusive`, and its special-case branch.
- Retrieval documents that embed `root_cause`, `fix_action`, diagnosis cause, remediation, or other answer text.
- Incident-kind routing, evidence-chain tags/rule checks, dependency-chase routing, and causal-graph fields that no longer feed the design.
- Commit/image provenance investigation, image-change presentation, and related GitHub lookup code.
- Incident chronology generation and presentation.
- Duplicate or obsolete metrics collection, including the old p99-only presentation where p95 is now specified.
- Dead serializers, adapters, components, imports, tests, and fixtures that exist only for the removed mechanism.

`incident-diagnosis/agent/presentation.py` and `incident-diagnosis/agent/live_proof.py` receive explicit call-site review: retain and narrow only responsibilities required by the new output/evidence contract, otherwise delete them. The BM25 implementation, MiniLM model artifact, and useful experiment infrastructure remain, but their fixtures and serializers are updated for the evidence-only schema.

Cleanup verification includes repository searches for removed routes and symbols, dead-import/static checks, backend tests, dashboard tests/build, and a final file inventory. User-owned screenshots and unrelated worktree files are never deleted.

## Out of scope

- Commit/image provenance.
- GitHub investigation.
- Dependency-chase routing and causal graphs.
- RRF, score normalization, or calibrated relevance thresholds.
- Automated remediation execution.
- Migration or persistence of existing Redis knowledge/history data.
