# Incident Agent Causal Correlation and Proof Design

**Date:** 2026-08-06
**Status:** Approved design
**Scope:** Incident diagnosis correctness, a controlled live proof, public project documentation, and local interview preparation

## Summary

Complete the incident-agent story around a real causal chain rather than an isolated alert or
synthetic Redis message. A controlled deployment regression will introduce an incompatible event
contract between `ride-service` and `dispatch-service`. The agent must correlate the GitOps change,
Prometheus DLQ impact, one canonical structured Loki error, and the exact agreeing Tempo trace into
a correct diagnosis and remediation. Kubernetes state, dependency health, and other collected facts
remain available, but an alert-kind policy assigns each item a causal role instead of presenting all
evidence to the LLM with equal weight.

The existing approved-memory design remains intact: a unique human-approved conclusive match uses
the exact fast path; otherwise BM25 generates candidates and MiniLM may accept one as advisory
context. Free-text generation is guarded by deterministic validation and a separate semantic critic.
One refinement is allowed, after which both gates run again. A second failure returns an honest
low-confidence result.

After the agent passes offline and live verification, the public README and incident-agent documents
will be rewritten as professional engineering documentation. Interview coaching and CV preparation
remain local-only and uncommitted.

## Problem

The deployed agent already contains the BM25 plus MiniLM retrieval refactor, grounding checks,
self-refinement, evidence collection, deterministic confidence, and trace handoff. The offline
reranker tournament selected MiniLM with 80% held-out top-1 accuracy, 80% recall@3, zero false
positives on ten held-out no-match cases, zero forbidden acceptances, 330.7 ms p95 latency, and
189 MB peak process RSS.

The latest live DLQ result nevertheless exposed a downstream diagnosis defect. The triggering
`DLQEventsDetected` incident contained a structured unknown-event error and a correlated Tempo
error span, but the root cause selected an unrelated readiness timeout. General diagnostics and
structured correlation can also select different Loki records, allowing the displayed log, trace
handoff, prompt, and confidence calculation to describe different occurrences. The current quality
gate can accept an answer that overlaps with any evidence without proving that the answer explains
the triggering metric.

The current public documentation compounds the problem: the detailed workflow still describes the
removed token-coverage production design, existing screenshots predate the final retrieval and
correlation work, and no committed live correlation report proves the complete causal story.

## Goals

- Produce one coherent alert-relative evidence chain from change to operational impact.
- Use a single canonical structured log for the prompt, dashboard, saved incident, and Tempo lookup.
- Make alert-kind evidence priority explicit, declarative, testable, and observable.
- Correlate a deployment or configuration change to the affected request path without treating every
  recent change as causal.
- Preserve all collected evidence for audit and dashboard use while limiting what can become primary
  causal evidence.
- Preserve the unique exact-conclusive knowledge fast path.
- Preserve BM25 candidate generation and MiniLM advisory acceptance without redesigning retrieval.
- Add deterministic hard checks, a separate semantic critic, one targeted refinement, and mandatory
  re-evaluation.
- Prove the design with frozen offline fixtures and a controlled GitOps event-contract regression.
- Commit sanitized machine-readable and concise human-readable proof artifacts.
- Refactor public documentation around the verified production mechanism and results.
- Create local-only interview preparation derived from the committed evidence.

## Non-goals

- Autonomous remediation or mutation by the incident agent or kubectl executor.
- Replacing BM25, MiniLM, Redis memory, Alertmanager, n8n, Slack, or the dashboard.
- Treating every alert as a metrics/logs/traces incident; Kubernetes state can be primary for pod and
  container failures.
- Requiring an interviewer to provision K3s, download the reranker, or rerun hosted-LLM evaluation.
- Claiming that temporal proximity alone proves a deployment caused an incident.
- Publishing interview questions, CV coaching, or personal achievement language in the GitHub repo.
- Directly injecting an unknown Redis event as the canonical end-to-end proof.

## Architecture

### Evidence flow

```text
Alertmanager alert + fingerprint + startsAt
                    |
                    v
          normalize incident kind
                    |
                    v
        collect and normalize evidence
     metrics / Loki / Tempo / K8s / change
                    |
                    v
       apply alert-kind evidence policy
                    |
                    v
          build bounded causal chain
                    |
          +---------+----------+
          |                    |
          v                    v
 approved memory         generation path
 exact or advisory       draft -> gates -> refine
          \                    /
           v                  v
        incident record / Slack / dashboard
```

Collection and causal selection are separate responsibilities. Existing collectors may continue to
fetch the full normalized bundle. The correlation layer assigns roles and constructs the prompt.
Unselected information remains stored and visible instead of being discarded.

### Alert-kind policy registry

Prometheus alerts should carry a stable `incident_kind` label where practical. Existing alert names
remain a compatibility fallback. A registry maps the normalized kind to an `EvidencePolicy` rather
than spreading priority decisions across collectors and prompt code.

Representative policy:

```python
POLICIES = {
    "dlq": EvidencePolicy(
        trigger=("impact.triggering_metric",),
        primary=("log_evidence", "trace_handoff"),
        causal_context=("template_diff", "provenance"),
        consequence=("dlq_state",),
        secondary=("k8s_state", "k8s_event"),
        required=("impact.triggering_metric", "log_evidence"),
    ),
    "crashloop": EvidencePolicy(
        trigger=("k8s_state",),
        primary=("k8s_state", "log_evidence"),
        causal_context=("template_diff", "provenance"),
        secondary=("impact", "trace_handoff"),
        required=("k8s_state",),
    ),
}
```

Every correlated item receives a stable identifier, role, availability status, and selection reason.
Unknown kinds use a conservative generic policy and may produce a low-confidence result.

### Canonical log and exact trace

The alert-relative structured Loki selection becomes the only primary log record. It retains the
event ID, timestamp, namespace, service, operation, message, trace ID, and span ID. The legacy
human-readable `log_error` is derived from this record rather than selected independently.

Tempo is queried only with the selected log's valid trace ID. A trace is `correlated` only when:

- its trace ID exactly equals the selected log trace ID;
- its error span agrees with the log service, operation, or diagnostic message; and
- it is inside the alert-relative incident window.

A mismatch is retained as conflicting evidence and cannot raise confidence or support a causal
claim.

### Change and provenance correlation

A recent image or configuration change receives one of four statuses:

```text
causal_candidate | recent_context | conflicting | unavailable
```

A change is a causal candidate only when it precedes the incident within a bounded window, affects a
service present in the selected log/trace/dependency path, plausibly explains the observed failure,
and is not contradicted by evidence showing the failure predates it. Provenance distinguishes a
matching GitOps commit from live/GitOps drift or an out-of-band hotfix. The LLM cannot promote
`recent_context` to the root cause.

### Retrieval modes

The production retrieval behavior remains:

1. `EXACT_CONCLUSIVE`: one unique approved conclusive knowledge entry exactly matches a bounded
   canonical signal. BM25 and MiniLM are skipped. When required current evidence is present and not
   contradictory, the backend assembles the diagnosis from the approved pattern and fix, attaches
   current resource identity and evidence, and runs deterministic validation. The semantic critic is
   skipped for this complete, unambiguous fast path.
2. `RERANKED_ADVISORY`: BM25 produces up to eight approved-memory candidates and MiniLM may accept
   the top result above its calibrated floor. The result is labelled hypothesis-only and enters the
   full generation and evaluation path.
3. `NONE` or `DEGRADED`: the agent diagnoses from live evidence or abstains. A reranker failure never
   silently accepts the BM25 top result.

Multiple exact matches, missing support, or contradictory evidence fall through to the full path.

### Generation, evaluation, and refinement

The backend constructs an ordered `EvidenceChain` before generation. The LLM receives the trigger,
primary failure, causal candidate, operational consequence, secondary context, and optional advisory
memory as separately labelled sections.

The generation schema retains the public fields and adds internal evidence references:

```json
{
  "root_cause": "...",
  "dev_action": "...",
  "kubectl_hint": "...",
  "evidence_refs": ["metric:dlq_events", "log:...", "trace:...", "change:..."]
}
```

The deterministic validator checks schema, exact evidence identifiers, required primary references,
trace equality, timestamps, service/namespace identity, provenance status, placeholder-free commands,
and limited lexical constraints such as preserving a distinctive error signature. It does not attempt
to judge causal meaning through token overlap alone.

A separate low-temperature semantic critic receives the evidence chain and draft. It returns strict
JSON containing `pass` or `fail` plus coded issues. It evaluates whether the draft explains the
triggering signal, respects evidence roles, connects the change to the failure, avoids unsupported
claims, and proposes remediation for the stated cause.

Acceptance requires both gates. On failure, the generator receives the original chain, draft, and
combined issues for one targeted refinement. Both gates run again. A second failure, critic outage,
or invalid critic output returns a safe low-confidence result rather than a confident draft. Every
stage and verdict is recorded in the incident timeline.

## Controlled Live Scenario

### Healthy baseline

`ride-service` emits the supported `Trip.Requested` contract, `dispatch-service` consumes it, the DLQ
metric is zero, and a normal ride request completes its expected path.

### Failure introduction

The healthy `vroom-dev` baseline sets `ride-service` to `EVENT_CONTRACT_VERSION=v1`, which emits
`Trip.Requested`. The controlled GitOps change sets that one environment value to `v2`, causing
`ride-service` to emit `Trip.Requested.v2` while `dispatch-service` continues to support only
`Trip.Requested`. A normal application request then creates the incompatible event. The recovery
commit restores `EVENT_CONTRACT_VERSION=v1`. No direct Redis poison-message injection is used as the
canonical proof.

The expected chain is:

```text
GitOps producer update
-> ride-service emits Trip.Requested.v2
-> dispatch.consume rejects the unsupported type
-> the event moves to the DLQ
-> the DLQ metric crosses its threshold
-> Alertmanager and n8n invoke the agent
-> Loki and Tempo identify the same failing request
-> the agent attributes the regression to the compatible change and recommends rollback or a
   compatible consumer deployment
```

### Deployment ownership and pause gates

For every revision requiring deployment:

1. The agent implements, verifies, commits, and pushes the change.
2. The agent reports the exact commit SHA and expected deployed revision, then stops.
3. The user watches GitLab CI, approves/applies Kargo promotion, waits for completion, and notifies the
   agent.
4. Only after that notification does the agent verify the live image/configuration and continue.

This applies to the agent fix, controlled regression, and recovery. Recovery is prepared before the
failure revision is promoted. The test runs only in `vroom-dev`. Unexpected broader damage ends the
test and moves to recovery rather than extending evidence collection.

## Testing and Proof

### Automated coverage

Add tests for policy selection, generic fallback, canonical log selection, exact trace correlation,
evidence roles, provenance statuses, the conclusive fast path, ambiguous/contradictory knowledge,
deterministic validation, semantic critic parsing and failure, one refinement plus full re-evaluation,
safe fallback, and unchanged BM25/MiniLM behavior.

### Offline diagnosis evaluation

Freeze human-labelled cases before real-model runs. Cases include the GitOps event-contract
regression, a conflicting readiness event, a Redis hostname hotfix, exact OOM knowledge, accepted
MiniLM advisory memory, trace mismatch, unrelated deployment, sparse evidence, and critic/refinement
failure. Expected causes, required evidence, forbidden claims, and remediation requirements are not
editable after evaluation begins.

Commit `diagnosis-correlation.json` and `diagnosis-correlation.md` reporting trigger-explanation
accuracy, primary-evidence usage, trace consistency, provenance attribution, unsupported claims,
remediation relevance, refinement recovery, and abstention correctness. The critic verdict is
recorded, but scoring uses the frozen human-authored expectations rather than self-grading.

### Live proof artifact

The read-only proof harness records sanitized alert labels, fingerprint and start time, metric value
and threshold, canonical Loki event and trace ID, agreeing Tempo span, change and GitOps commit, final
diagnosis and action, confidence, evidence references, both gate results, and recovery confirmation.
It emits machine-readable JSON and concise Markdown suitable for commit.

Completion requires the same service, alert, revision, timestamp window, and trace ID to agree across
the saved artifact, Slack, dashboard, and Tempo screenshot.

## Public Documentation

The main README will provide a concise professional overview: operational problem, end-to-end flow,
evidence correlation, BM25/MiniLM retrieval, measured results, one current screenshot, and links.

Create:

- `docs/incident-agent.md`: architecture, collectors, policy registry, canonical correlation,
  provenance, retrieval modes, generation controls, feedback loop, safety, failure handling, and the
  verified regression walkthrough.
- `docs/incident-agent-evaluation.md`: BM25-only failure, MiniLM tournament, frozen datasets, decision
  boundaries, diagnosis evaluation, live proof, limitations, and links to committed reports.

The obsolete `docs/incident-agent-workflow.md` becomes a compatibility pointer after links migrate.
Public prose remains professional project documentation; it contains no interview coaching or CV
language.

Add concise Mermaid diagrams for the end-to-end causal flow, retrieval modes, and generation control.
Recapture current Slack, dashboard, Tempo, provenance, confidence, timeline, and knowledge-review
screens after the successful live test. Screenshots must be sanitized and internally consistent.

## Local Interview Preparation

Create or update an ignored local document outside the public `vroom-services` documentation. It may
contain the two-minute explanation, likely questions, deep follow-ups, trade-offs, failure lessons,
benchmark interpretation, live-scenario walkthrough, CV wording, supporting evidence, and limitations.
It must not be committed or linked by public documents.

## Error Handling

- Missing required evidence: return a specific low-confidence diagnosis naming what is unavailable.
- Loki unavailable or no scoped structured log: do not claim log/trace correlation.
- Invalid or absent trace ID: skip exact Tempo lookup and lower confidence.
- Trace disagreement: retain the conflict, prohibit causal use, and lower confidence.
- Provenance unavailable: diagnose runtime failure without attributing the introducing change.
- Recent unrelated change: label context-only and prohibit causal promotion.
- Critic unavailable or malformed: do not accept an unreviewed non-conclusive draft as high confidence.
- Exact knowledge ambiguity or contradiction: use the full pipeline rather than guessing.
- Retrieval failure: continue evidence-only and expose degraded status.
- Live scenario failure outside intended scope: stop and promote the prepared recovery.

## Acceptance Criteria

- Existing retrieval tests and the committed MiniLM tournament remain passing and unchanged in meaning.
- A single canonical structured log drives prompt text, saved evidence, and exact Tempo lookup.
- Alert-kind policies assign roles deterministically and expose selection reasons.
- The exact-conclusive path remains fast, human-approved, unique, and contradiction-aware.
- Non-conclusive outputs pass deterministic validation and an independent semantic critic.
- Refined outputs are re-evaluated by both gates; a second failure cannot become confident.
- Frozen offline cases pass their approved diagnosis and abstention gates.
- The live GitOps contract regression produces the expected DLQ metric, structured log, exact agreeing
  trace, causal provenance, correct diagnosis, relevant remediation, and high deterministic confidence.
- An unrelated readiness event does not become the DLQ root cause.
- The recovery revision restores the healthy baseline.
- Sanitized JSON/Markdown proof and consistent screenshots are committed.
- Public README and mechanism/evaluation documents describe only the current implementation.
- Local interview preparation remains uncommitted.
- Every push pauses until the user confirms GitLab CI, Kargo promotion, and rollout completion.

## Approval

Approved conversationally by the user on 2026-08-06. Implementation requires a separate detailed
plan and remains subject to repository and Herdr approval gates.
