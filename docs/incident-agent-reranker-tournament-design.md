# Incident Agent Retrieval Reranker Tournament

**Date:** 2026-07-31

**Status:** Approved direction; implementation requires a separate plan

**Scope:** Offline retrieval experiment only

## Summary

The previous proof showed that BM25 improved held-out top-1 retrieval accuracy from 0.500 to
0.875, but the selected BM25-only design accepted one forbidden result in a two-case held-out
no-match subset. Production retrieval therefore remains unchanged.

The next experiment will answer a broader question: which precision-oriented retrieval
architecture best fits the incident agent under a free-runtime and 500 MB process-memory
constraint? One runner will compare the current token-coverage baseline, BM25 alone, BM25 plus
two free local cross-encoder rerankers, and BM25 plus a separate retrieval-stage LLM judge. The
existing diagnosis-generation, grounding, and self-refinement pipeline will not run and will not
be modified.

The experiment produces a detailed machine-readable artifact and a concise presentation report.
No challenger reaches production unless it passes the frozen held-out gate.

## Prior evidence and problem statement

The first offline proof established two facts:

1. BM25 is a materially better candidate ranker than the current token-coverage mechanism on the
   small frozen corpus.
2. A raw BM25 score floor is not a sufficient semantic acceptance mechanism. In the failed DNS
   hard negative, `CrashLoopBackOff` produced enough lexical evidence to accept generic crash-loop
   knowledge even though that knowledge did not explain the DNS error.

The 0.500 held-out false-positive rate was one false acceptance among only two held-out no-match
cases. It is a valid proof failure, but not a reliable estimate of production prevalence. The next
dataset must contain substantially more hard negatives before comparing acceptance mechanisms.

## Goals

- Preserve the strongest unique conclusive exact match.
- Use the same BM25 top-eight candidate set for every second-stage challenger.
- Compare two free local specialized rerankers with a retrieval-stage LLM judge.
- Measure ranking quality, abstention quality, decision stability, latency, and memory.
- Keep all ordinary development and evaluation independent of K3s, Prometheus, Loki, GitOps, and
  a real Redis server.
- Produce a concise, interview-presentable report explaining the evidence and the go/no-go
  decision.
- Keep the existing CV bullet unchanged.

## Non-goals

- Do not change production `memory.py`, `app.py`, or `interpreter.py` during the experiment.
- Do not inject challenger output into the generation prompt.
- Do not run the existing diagnosis-generation LLM, grounding checks, or self-refinement.
- Do not fine-tune a reranker from the current human-entered history.
- Do not require a paid API, GPU, live cluster, or model server.
- Do not claim that a sigmoid reranker score is a calibrated probability.
- Do not create a production rollout task merely because one system ranks positives well.

## Compared systems

System A reproduces current production retrieval exactly, including its current exact gate. Systems
B through E share the corrected unique-conclusive exact gate, approved candidate corpus, and
identical BM25 top-eight candidates. This preserves an honest production baseline without
confounding the second-stage challenger comparison.

| ID | System | First stage | Second-stage decision |
|---|---|---|---|
| A | Current baseline | Existing exact gate and token coverage | Existing threshold |
| B | BM25 only | Exact gate and BM25 top eight | Calibrated raw BM25 floor |
| C | BM25 + MiniLM | Exact gate and BM25 top eight | Quantized local MiniLM cross-encoder |
| D | BM25 + Mixedbread | Exact gate and BM25 top eight | Quantized local Mixedbread cross-encoder |
| E | BM25 + retrieval LLM | Exact gate and BM25 top eight | Separate LLM relevance judge with abstention |

### Local model candidates

`cross-encoder/ms-marco-MiniLM-L6-v2` is the smallest candidate. It is an English, Apache-2.0
cross-encoder with approximately 22 million parameters. Its official repository provides a
23.2 MB quantized ONNX artifact.

`mixedbread-ai/mxbai-rerank-xsmall-v1` is the quality-oriented local candidate. It is an English,
Apache-2.0 cross-encoder with 70.8 million parameters. Its official repository provides an
87.2 MB quantized ONNX artifact.

Both models run through ONNX Runtime on CPU. The experiment must pin model revisions and verify
artifact checksums. PyTorch is not part of the intended production runtime. Larger BGE, Jina, and
generative rerankers are excluded because of the 500 MB ceiling, non-commercial model licensing,
or excessive local cost.

### Retrieval LLM comparator

The LLM comparator uses the agent's existing Groq integration and introduces no new provider. Its
pinned default is `llama-3.1-8b-instant`, the smaller model already present in the agent's current
configuration, while diagnosis generation keeps its existing priority list unchanged. Before
calibration begins, the runner performs one preflight, records the provider and exact model
identifier, and then disables fallback. If the pinned model is unavailable, the LLM challenger is
reported as unavailable instead of silently substituting another model.

The comparator is an experimental quality ceiling, not automatically a deployable free service.
The final report must distinguish zero current spend from a guaranteed zero-cost production
contract.

## Architecture and boundaries

```text
Sanitized incident fixture
          |
          v
Unique conclusive exact gate ----------------------> exact result
          |
          | no unique exact result
          v
BM25 candidate generation -> fixed top-eight candidate set
          |
          +--> raw BM25 acceptance
          +--> MiniLM reranking and rejection
          +--> Mixedbread reranking and rejection
          +--> retrieval LLM evaluation and abstention
          |
          v
Metrics, resource measurements, traces, and reports
```

The experiment has five independent components:

1. **Fixture loader:** validates immutable labeled cases and split membership.
2. **Candidate generator:** reproduces the production exact path for system A, and runs the shared
   challenger exact gate plus BM25 top-eight retrieval once per case for systems B through E.
3. **Decision adapters:** expose a common interface for BM25, local cross-encoders, and the LLM
   judge.
4. **Evaluator:** calibrates only on the calibration split, freezes configuration, and opens the
   held-out split once.
5. **Reporter:** writes full JSON evidence and a concise Markdown decision report.

No adapter may call diagnosis generation or mutate Redis, knowledge, history, or fixture labels.

## Evidence and candidate contracts

The incident query is serialized as stable field-labelled text. Empty fields are omitted, field
order is fixed, and values are treated as data rather than instructions.

```text
alert_name: PodUnavailable
waiting_reason: CrashLoopBackOff
log_error: lookup redis: no such host
event_reason: BackOff
dependency_name: redis
dependency_state: unhealthy
```

Each candidate is also field-labelled:

```text
knowledge_key: crashloop
trigger: CrashLoopBackOff
known_symptom: application repeatedly exits during startup
root_cause_pattern: application exits during startup
approved_history_context: ...
```

Only approved knowledge and history linked to existing approved knowledge may become candidates.
Candidate IDs, knowledge keys, source IDs, BM25 scores, reranker scores, and rejection reasons are
preserved in the trace. Large raw bundles are not passed to rerankers; only the normalized evidence
contract is used.

## Decision adapters

### BM25-only adapter

The BM25 adapter retains the previous raw-score calibration behavior so it remains a reproducible
comparison. Unique conclusive exact results bypass the score floor. Non-exact candidates below the
calibrated floor are rejected.

### Local cross-encoder adapters

Each local adapter scores every `(incident evidence, candidate document)` pair. It then:

1. sorts by reranker score, using BM25 score, knowledge key, and source ID as deterministic
   tie-breakers;
2. collapses duplicate sources to one result per knowledge key;
3. rejects candidates below its calibration-only score floor;
4. returns at most three accepted candidates.

The floor is model-specific. It is selected from observed calibration scores and is not described
as confidence or probability.

### Retrieval LLM adapter

The LLM receives the normalized incident and all BM25 top-eight candidates in one request. Logs,
events, and candidate text are delimited as untrusted data. The prompt requires schema-valid JSON
and permits rejecting every candidate.

For each candidate the response contains:

- candidate key;
- `supported` boolean;
- ordinal relevance grade from 0 to 3;
- supporting evidence field names;
- conflicting or unexplained evidence field names;
- concise reason.

The response also contains `selected_keys` and a decision of `accepted` or
`no_supported_candidate`. Application code validates candidate IDs, removes unsupported entries,
and uses BM25 score only as a deterministic tie-breaker between equal LLM grades. A malformed,
timed-out, inconsistent, or unknown-key response is a failed retrieval decision, not an invitation
to silently accept the BM25 result.

## Dataset design

The existing 20 cases remain as regression fixtures. Before any challenger is run, the corpus is
expanded to at least 40 human-reviewed cases:

- at least 20 positive exact or advisory cases;
- at least 20 no-match or hard-negative cases;
- at least 10 held-out no-match or hard-negative cases;
- every bootstrap knowledge pattern represented by a positive case;
- Kubernetes, application-log, dependency, GitOps, event, metrics, sparse, and ambiguous cases;
- hard negatives that share generic Kubernetes states with an unrelated specific error;
- duplicate histories and multiple sources linked to one knowledge key.

Cases use sanitized captured evidence when available and carefully reviewed synthetic evidence
otherwise. Each fixture contains expected mode, accepted keys, forbidden keys, split, and a concise
rationale. Split membership and labels are committed before model scoring. Calibration cases may
be inspected and used to select floors or revise the LLM prompt. Held-out labels must not be used
to tune a model, threshold, prompt, serialization, or candidate count.

## Calibration and selection

### Shared first stage

BM25 query projection, document projection, and top-eight count are frozen using calibration data
before any final held-out comparison. The evaluator reports BM25 recall@8 as a candidate-generation
ceiling. A reranker is not blamed when the expected key was absent from its input candidates.

### Numeric adapters

For BM25, MiniLM, and Mixedbread, the evaluator tries observed calibration scores as possible
floors. It chooses among calibration-passing floors by this strict ordering:

1. zero forbidden acceptances and zero exact failures;
2. lowest no-match false-positive rate;
3. highest top-1 accuracy;
4. highest recall@3;
5. highest floor as the conservative tie-breaker.

### LLM adapter

The LLM prompt and parser may be revised using calibration cases only. The final prompt, model ID,
provider, temperature zero configuration, parser version, and prompt hash are frozen before the
held-out run. All prompt revisions remain in version control so failed calibration attempts are
not hidden.

## Evaluation protocol

Deterministic systems run once per case after calibration. The LLM system runs each held-out case
three times with identical input and temperature zero. The report includes majority metrics and
worst-run metrics. Any disagreement in accept-versus-abstain or selected top key is a stability
failure for that case.

Quality metrics:

- BM25 recall@8;
- top-1 accuracy;
- recall@3;
- mean reciprocal rank;
- no-match accuracy and false-positive rate;
- forbidden acceptances;
- exact-gate failures;
- abstention accuracy;
- per-case regressions;
- LLM agreement and malformed-response rates.

Operational metrics:

- model artifact and resulting container sizes;
- cold-load latency;
- p50 and p95 retrieval latency;
- incident-agent process peak resident memory with each local model loaded;
- LLM request count, input/output tokens, p50/p95 latency, and provider failures;
- current observed spend and a separately labelled theoretical paid cost.

## Frozen held-out gate

A challenger passes only if all applicable conditions hold:

- zero forbidden acceptances;
- zero exact-gate failures;
- held-out no-match false-positive rate no greater than the current baseline;
- top-1 accuracy no lower than the current baseline;
- recall@3 no lower than the current baseline;
- deterministic local ranking across repeated checks;
- for the LLM, 100% agreement on accept/abstain and selected top key across the three held-out
  repetitions;
- for a local reranker, total incident-agent process peak RSS no greater than 500 MB;
- local reranking p95 no greater than 1,000 ms on the documented development laptop.

Among passing systems, select by lowest false-positive rate, highest top-1 accuracy, highest
recall@3, lowest operational cost, lowest p95 latency, and lowest peak memory, in that order. Prefer
a local model when all quality metrics tie.

Possible outcomes are explicit:

- **Local PASS:** a local model passes and may receive a separately approved production plan.
- **LLM-only PASS:** the LLM establishes a quality ceiling, but production remains unchanged until
  the user explicitly accepts its external availability and cost risks.
- **Multiple PASS:** the ordered policy selects one recommendation; non-selected results remain in
  the report.
- **FAIL:** production remains unchanged and the report identifies candidate-generation,
  relevance-decision, stability, or resource failure.

The held-out gate is not reopened to rescue a failed system. Any material redesign requires a new
fixture version and another experiment.

## Reports and presentation artifact

The runner writes:

1. `reranker-tournament.json` — complete configuration, environment, metrics, resource data,
   prompts, hashes, per-run outcomes, and per-case traces;
2. `reranker-tournament.md` — concise report intended for review and interview presentation.

The Markdown report is capped at roughly 1,200 words and contains:

- one-sentence result and go/no-go decision;
- why the experiment was run;
- five-system comparison table;
- dataset and frozen-split summary;
- quality, latency, memory, and stability table;
- the DNS hard-negative trace and at most one other informative failure;
- selected architecture or explicit reason for no rollout;
- limitations, including sample size and external free-tier risk;
- a five-sentence interview explanation;
- exact reproduction command and links to the full JSON artifact.

The concise report must not hide a failed gate behind an aggregate score. It states the number of
no-match cases so percentages cannot be misread, and distinguishes one false positive out of two
from an estimated production rate.

## Error handling

- Missing model artifact or checksum mismatch: mark that challenger unavailable and continue the
  remaining experiment; overall selection cannot choose it.
- Local load or inference failure: record the exception class and case; never fall back to BM25 for
  that challenger's score.
- LLM preflight failure: mark the LLM challenger unavailable without affecting local evaluation.
- LLM timeout, rate limit, malformed JSON, unknown candidate key, or schema violation: record a
  failed decision for that repetition; do not switch providers or models.
- Empty BM25 candidate set: return `none` for every second-stage adapter without invoking a model.
- Fixture schema or split violation: fail the whole experiment before scoring.
- Report write failure: fail the command; a benchmark without artifacts is not accepted.

## Testing strategy

Fast unit tests cover fixture validation, stable serialization, candidate identity, exact bypass,
duplicate collapse, numeric floor selection, deterministic tie-breaking, LLM schema validation,
unknown-key rejection, repetition aggregation, pass/fail gates, and report rendering.

Contract tests use fake local scores and recorded LLM responses, require no network, and prove that
all adapters receive identical BM25 candidates. Slow model smoke tests load each pinned ONNX model
on CPU and verify stable ordering on a small fixture subset. The real LLM experiment is an explicit
network test and is never part of the default unit suite.

The full tournament runs without K3s. A live Alertmanager-to-Slack test is out of scope until a
challenger passes and a separate production design is approved.

## References

- Sentence Transformers documents the standard retrieve-and-rerank pattern in which an efficient
  first stage retrieves candidates and a cross-encoder reranks the small result set:
  <https://www.sbert.net/examples/cross_encoder/applications/README.html>
- MiniLM model and official quantized ONNX artifacts:
  <https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2/tree/main/onnx>
- Mixedbread xsmall model card and official quantized ONNX artifacts:
  <https://huggingface.co/mixedbread-ai/mxbai-rerank-xsmall-v1>
- RankGPT demonstrates generative LLMs as passage rerankers:
  <https://aclanthology.org/2023.emnlp-main.923/>
- Corrective RAG motivates evaluating retrieval quality before generation:
  <https://arxiv.org/abs/2401.15884>

## Interview explanation

The intended concise explanation is:

> The first BM25 proof improved known-incident ranking but exposed that a lexical score was not a
> confidence estimate: one DNS hard negative was incorrectly accepted as a generic crash loop. I
> therefore built a frozen retrieval tournament rather than immediately modifying production. The
> same BM25 top-eight candidates were evaluated by a raw threshold, two free CPU cross-encoders,
> and a separate retrieval-stage LLM with abstention. I compared accuracy and no-match precision
> alongside memory, latency, stability, and cost, while leaving diagnosis generation unchanged.
> Only a challenger satisfying every held-out precision and resource gate could proceed to a
> production design.

## Success criteria

- The experiment is reproducible without the cluster.
- Every reranker receives the same BM25 candidates.
- Held-out labels cannot influence threshold, prompt, model, or serialization choices.
- The result reports both quality and operational cost.
- The concise report is understandable without reading implementation code.
- Production retrieval and generation remain unchanged unless a separate approved rollout follows
  a passing result.
