# Incident Agent BM25 Retrieval Refactor

**Date:** 2026-07-30  
**Status:** Proposed design — implementation is gated by offline evaluation  
**Scope:** `incident-diagnosis/agent`

## Summary

Refactor the existing incident-diagnosis feature into a clean, offline-testable pipeline while
preserving its current user-facing workflow:

```text
Alertmanager -> n8n -> evidence collection -> diagnosis -> Slack
```

The refactor makes BM25 the sole free-text retriever for human-approved knowledge. Exact
matching remains only as a narrow safety gate for conclusive canonical Kubernetes states.
Accepted BM25 results become advisory context in the LLM prompt, replacing the current
token-coverage path and the disconnected BM25 fallback.

No production behavior changes until an offline benchmark demonstrates that the proposed
retrieval design is at least as precise as the current implementation.

## Problem

The current memory layer implements three different behaviors:

1. `_derive_reason_signal()` returns the first matching categorical signal from a priority
   ordered `if` chain.
2. A conclusive knowledge entry with the same `trigger_waiting_reason` immediately becomes
   trusted prompt context.
3. Otherwise, `_token_coverage()` ranks history symptoms and selected non-conclusive knowledge
   patterns. A score of at least `0.5` promotes one result to the same trusted prompt context.
4. A separate BM25 search runs only when no trusted match exists. It searches raw past
   incidents, but its results are returned to the API rather than supplied to the LLM.

This has four consequences:

- The CV description "RAG with BM25 retrieval" is only partially represented by the runtime
  behavior because BM25 results do not augment generation.
- Token coverage treats every distinct query token equally. Generic tokens can contribute as
  much as distinctive error identifiers.
- The priority-ordered signal function discards later observations and is difficult to defend
  as a principled use of the full evidence set.
- Retrieval mechanics, Redis persistence, orchestration, and HTTP behavior are mixed across
  `memory.py` and `app.py`, making offline development and explanation unnecessarily hard.

## Goals

- Make BM25 the only free-text retrieval mechanism used by diagnosis.
- Retrieve only human-approved knowledge and history for LLM augmentation.
- Put accepted BM25 results into the LLM prompt as advisory, not authoritative, context.
- Preserve a narrow exact-match path for explicitly conclusive canonical states.
- Prove retrieval quality offline before modifying the production path.
- Allow the same diagnosis pipeline to consume live evidence or saved JSON fixtures.
- Preserve the existing Alertmanager, n8n, Slack, dashboard, and human-approval workflow.
- Preserve this CV bullet unchanged:

  > Developed an AI incident diagnosis agent triggered by Alertmanager via n8n, using RAG with
  > BM25 retrieval, grounding checks, and self-refinement to deliver root-cause analysis and
  > remediation guidance in Slack.

## Non-goals

- Building an MCP server or host-specific plugin.
- Training or hosting a custom ML/DL model.
- Replacing the external LLM providers.
- Adding automatic remediation.
- Giving local development tools direct Kubernetes credentials.
- Redesigning the dashboard or human approval interface.
- Reducing production evidence collection solely to improve retrieval.
- Adding manually maintained structured fields to the knowledge form.

## Architecture

### Target boundaries

```text
Live adapters                         Offline adapter
K8s / Prometheus / Loki / GitOps      JSON fixture
                  \                    /
                   v                  v
                    IncidentEvidence
                           |
                           v
                    DiagnosisPipeline
                +----------+-----------+
                |                      |
                v                      v
       ApprovedMemoryRetriever     Prompt / LLM
       exact gate + BM25           grounding + refine
                |
                v
          RedisMemoryStore
```

The core contract is `IncidentEvidence`. Live collectors and fixture loading produce the same
shape. The diagnosis pipeline does not know whether evidence came from a cluster or a file.

### Component responsibilities

| Component | Responsibility | Must not do |
|---|---|---|
| HTTP application | Validate requests and serialize responses | Implement retrieval or diagnosis rules |
| Evidence adapters | Collect and normalize operational evidence | Rank memory or call the LLM |
| Diagnosis pipeline | Orchestrate exact gate, BM25, prompt, LLM, and checks | Read Kubernetes directly |
| Approved-memory retriever | Build the query/corpus, rank candidates, and explain matches | Search unapproved incidents |
| Memory store | Persist and load Redis entities | Decide relevance |
| Interpreter | Construct prompts, call the LLM, validate, and refine | Scan Redis or collect evidence |
| Offline evaluator | Compare retrieval and diagnosis variants on fixtures | Mutate production data |

Suggested modules are `evidence.py`, `retrieval.py`, and `pipeline.py`. `memory.py` becomes
persistence-focused; existing collector and interpreter modules remain adapters around their
current responsibilities.

## Evidence contract

`IncidentEvidence` contains the fields already collected by the service, including:

- alert, service, namespace, and optional pod identity;
- pod availability, waiting states, termination states, restart counts, and events;
- log evidence;
- dependency evidence;
- template/GitOps change evidence;
- provenance;
- the short metrics bundle.

The contract does not require all fields. Missing optional evidence remains empty and must not
fail retrieval.

Retrieval uses a deliberate text projection from this contract. The LLM may still receive the
broader evidence set. This separates "information useful for diagnosis" from "information
useful for lexical retrieval" without collecting less data.

## Retrieval design

### Stage 1: conclusive exact gate

Replace the priority-returning `_derive_reason_signal()` behavior with collection of all
canonical observed state signals. Exact matching is allowed only when:

1. the knowledge entry is human-approved and marked `conclusive`;
2. its `trigger_waiting_reason` exactly matches an observed canonical signal; and
3. exactly one conclusive knowledge key qualifies.

If multiple conclusive keys qualify, the result is ambiguous and falls through to BM25
advisory retrieval. Non-conclusive exact state matches do not become trusted context.

This gate is defensible because exact equality is used only for bounded categorical states,
not for logs, metrics, deploy changes, or other contextual evidence.

### Stage 2: BM25 candidate corpus

Only approved entities are eligible:

- knowledge entries;
- approved history entries joined to their parent knowledge entry.

Unresolved incidents, pending suggestions, and prior unapproved LLM diagnoses are excluded.

Candidate scoring text deliberately excludes remediation commands. A fix can be returned after
retrieval, but its command tokens should not make a diagnosis candidate look relevant.

The initial corpus variants evaluated by the offline gate are:

- **Knowledge document:** `trigger_waiting_reason + root_cause_pattern`.
- **History document:** `symptom + context_notes`, resolving the selected result to its parent
  knowledge entry.
- **Joined history challenger:** history scoring text plus the parent root-cause pattern.

Results are collapsed by `knowledge_key`; only the highest-scoring occurrence for a knowledge
key survives. This prevents repeated histories for one pattern from filling all prompt slots.

The benchmark selects between the plain history and joined-history variants. The selected
variant must be recorded in the benchmark report before production retrieval is changed.

### Stage 3: BM25 query

The baseline query remains the current projection:

```text
alert_name + waiting_reason + log_error
```

This isolates the scoring change from evidence-expansion changes. A challenger query may add
normalized event, dependency, and template-change text already present in
`IncidentEvidence`. Raw metrics are not added as lexical tokens; a separate future design may
evaluate stable metric categories.

The richer challenger is adopted only if it improves held-out precision without increasing
the no-match false-positive rate. Otherwise, the three-field baseline remains.

### Stage 4: BM25 ranking

Use the existing positive-IDF BM25 variant:

```text
score(D, Q) =
  sum over query terms:
    IDF(term) *
    tf(term, D) * (k1 + 1)
    ---------------------------------------------
    tf(term, D) + k1 * (1 - b + b * |D| / avgdl)
```

Default parameters remain `k1=1.5` and `b=0.75` unless the offline benchmark supplies evidence
for another choice.

Ranking behavior:

- rare shared terms contribute more than common terms;
- repeated document terms saturate instead of growing linearly;
- document length is normalized;
- zero-overlap candidates score zero and are excluded;
- equal scores use stable tie-breakers: `knowledge_key`, then history identifier;
- query tokens are de-duplicated so repeated log fragments do not multiply their contribution.

Raw BM25 score is retained for evaluation and debugging. The existing `score / max_score`
normalization must not be interpreted as confidence because it always makes the top candidate
`1.0`, even for a weak query.

### Stage 5: precision-oriented acceptance

BM25 score is relevance, not probability. The acceptance threshold is selected on a
calibration split and reported on a separate held-out split.

At runtime:

- candidates below the calibrated raw-score floor are omitted;
- at most three distinct knowledge keys are returned;
- no qualifying candidate produces no advisory memory;
- retrieval logs include candidate key, raw score, matched query terms, source type, and
  acceptance decision;
- scores and matched terms are diagnostic metadata, not claims of causal confidence.

No hand-picked production threshold is allowed before the benchmark.

### Stage 6: prompt behavior

A conclusive exact match retains a clearly labelled trusted section. When that gate succeeds,
BM25 is skipped to avoid competing context.

BM25 output uses a different section:

```text
Advisory approved memory:

The following human-approved cases are lexically related to the current evidence.
They are hypotheses, not proof. Use them only when supported by the current evidence,
and ignore any case that conflicts with that evidence.

[1] Pattern: ...
    Similar occurrence: ...
    Previous fix: ...
```

The prompt must not call BM25 output a "trusted match" or instruct the LLM to use it as the
basis for the answer. Current evidence remains authoritative.

The grounding and refinement stages remain active. A retrieved root cause does not become
grounded merely because it appears in memory.

## Offline proof gate

### Retrieval dataset

Create versioned JSON fixtures containing:

- the normalized query/evidence;
- expected knowledge key or explicit `null`;
- expected exact or advisory mode;
- a short rationale;
- optional forbidden keys for hard-negative cases.

The dataset must include:

- positive cases for every bootstrap knowledge pattern;
- paraphrased history cases;
- hard negatives sharing generic Kubernetes terms;
- no-match cases;
- ambiguous multi-signal cases;
- duplicate histories pointing to the same knowledge key;
- empty and sparse evidence.

Fixtures are built from bootstrap knowledge, approved history, existing demo scenarios, and
sanitized captured incidents. They require no live cluster.

Cases are split before threshold selection:

- a calibration split is used to select the query projection, history-document variant, and
  raw-score floor;
- a held-out split is opened only for the final comparison;
- labels and split membership are committed so a failed variant cannot be rescued by quietly
  changing the expected result.

### Compared systems

The evaluator runs the same cases against:

1. current exact gate plus token coverage;
2. proposed exact gate plus baseline-query BM25;
3. proposed exact gate plus richer-query BM25;
4. both BM25 history-document variants.

For every case it prints ranked keys, raw scores, matched terms, selected mode, and rejection
reason. The evaluator commits both a machine-readable result and a short Markdown report under
`incident-diagnosis/agent/evaluation/reports/`.

### Metrics

- top-1 precision;
- recall at three;
- mean reciprocal rank;
- no-match accuracy and false-positive rate;
- exact-gate correctness;
- per-case regression report.

### Retrieval acceptance criteria

Production implementation proceeds only when the selected BM25 variant:

- has top-1 precision greater than or equal to the current baseline on held-out cases;
- has recall@3 greater than or equal to the current baseline;
- does not increase the held-out no-match false-positive rate;
- passes every conclusive exact fixture;
- accepts no forbidden key for any hard-negative fixture;
- produces deterministic rankings across repeated runs.

If no BM25 variant passes, production retrieval remains unchanged and the benchmark report
documents the failure.

## Downstream diagnosis evaluation

After retrieval passes, evaluate the LLM offline on the same evidence fixtures:

- baseline prompt without BM25 memory;
- proposed prompt with BM25 advisory memory.

Use a fixed model configuration and low temperature. Each output is graded against fixture
expectations for:

- root-cause correctness;
- reference to current evidence;
- remediation relevance;
- unsupported claims;
- harmful anchoring to an incorrect retrieved case.

Prompt injection proceeds only if correctness does not regress and no held-out hard-negative
case adopts a forbidden memory cause. The evaluation stores prompts, retrieval traces, model
outputs, and grades as reviewable artifacts.

## Error handling

- Redis unavailable: continue diagnosis without memory and record retrieval degradation.
- Empty approved corpus: continue without memory.
- Empty query: skip BM25.
- Missing parent knowledge for history: exclude the orphan and log it.
- BM25 exception: continue without memory; do not fail investigation.
- LLM unavailable or invalid output: preserve the existing deterministic fallback.
- Fixture schema error: fail the offline evaluator with the fixture path and field error.

## Testing strategy

### Unit tests

- canonical signal collection, including multiple simultaneous signals;
- exact gate uniqueness and ambiguity;
- approved-only candidate construction;
- BM25 zero-overlap exclusion;
- stable tie-breaking;
- duplicate collapse by knowledge key;
- query token de-duplication;
- threshold acceptance and rejection;
- advisory prompt wording;
- Redis and BM25 degradation paths.

### Offline integration tests

- JSON fixture -> `IncidentEvidence` -> retrieval -> prompt;
- current baseline and BM25 challenger evaluation;
- no K3s, Prometheus, Loki, GitOps, or real Redis required;
- deterministic fake LLM for pipeline behavior;
- optional real-LLM A/B evaluation kept separate from the fast suite.

### Live verification

Run one final Alertmanager -> n8n -> incident-agent -> Slack scenario after all offline gates
pass. The live verification confirms integration, not retrieval quality.

## Rollout

1. Add only the offline fixture schema and retrieval evaluator.
2. Produce and review the baseline-versus-BM25 report.
3. If the proof gate passes, establish `IncidentEvidence`, retriever, pipeline, and store
   boundaries without changing external endpoints.
4. Replace token coverage with the selected BM25 variant.
5. Add advisory memory to the prompt.
6. Run offline retrieval and diagnosis gates.
7. Run the single live integration verification.
8. Remove obsolete token-coverage and disconnected raw-incident fallback paths only after
   regression tests pass.

## Interview explanation

The concise technical explanation is:

> The agent uses a narrow deterministic gate for conclusive Kubernetes states and BM25 for
> free-text retrieval over human-approved operational knowledge. Retrieved cases are advisory;
> live evidence remains authoritative. The LLM generates the diagnosis, while deterministic
> grounding checks and a targeted refinement pass catch unsupported or low-quality output.
> The diagnosis core is tested offline with replayable evidence fixtures, so Kubernetes is
> needed only for final integration verification.

The LLM is the deep-learning component, accessed through hosted inference. BM25 is classical
information retrieval; no custom model is trained.

## Success criteria

- The existing CV bullet is accurate without qualification.
- BM25 retrieval quality is supported by a committed, reproducible benchmark report.
- Ordinary retrieval and pipeline tests run without K3s.
- Only approved memory can enter the LLM prompt.
- Exact matching is limited to a clear, defensible conclusive-state rule.
- Production HTTP response shapes and human-review workflow remain compatible.
- One live end-to-end demonstration passes after offline validation.
