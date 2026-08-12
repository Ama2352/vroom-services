# Incident Diagnosis Retrieval Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old history-, Kubernetes-trigger-, and causal-routing mechanism with evidence-template retrieval, grounded diagnosis, approval APIs, and an operator UI that reliably demonstrates DLQ contract failure and Redis configuration failure.

**Architecture:** One normalized evidence contract feeds immutable approved examples, exact fingerprints, BM25 top-eight generation, MiniLM top-three reranking, LLM diagnosis, validation, persistence, and UI presentation. Semantic knowledge and global hints are stored separately from evidence. The replacement is introduced from the data boundary outward, then old history, chronology, dependency chase, provenance, and obsolete presentation paths are deleted.

**Tech Stack:** Python 3, Flask, Redis/fakeredis, Prometheus/Loki/Tempo, rank-bm25, ONNX MiniLM cross-encoder, pytest, React 19, TypeScript 6, Vite 8, Vitest, Testing Library, Tailwind CSS.

## Global Constraints

- Redis development data is ephemeral; replace schemas directly and provide no migration compatibility.
- Keep BM25 candidate generation at eight distinct knowledge families, followed by MiniLM reranking to three distinct families.
- Do not use an RRF layer, score normalization, or a calibrated relevance threshold.
- Exact reuse requires one unique identical normalized template whose example has `exact_reusable: true`.
- Knowledge keys, diagnosis causes, and remediations never enter BM25 or MiniLM serialization.
- Global approved hints may enter retrieval serialization; LLM-suggested hints remain pending until human approval.
- Retrieved examples are advisory and cannot be cited as current incident evidence.
- Missing measurements remain missing and are never converted to zero.
- The generation path uses a hard validator, semantic critic, and at most one refinement.
- Failed refinement clears the confirmed cause and confirmed remediation while preserving collected evidence and any independently hard-grounded hypothesis.
- Every displayed number includes its unit or count context; the primary latency is p95 in milliseconds.
- Preserve `incident-diagnosis/agent/DLQ.png`, `oom.png`, and `redis-addr.png` and all unrelated user files.

---

### Task 1: Define and serialize the normalized evidence contract

**Files:**
- Modify: `incident-diagnosis/agent/evidence_projection.py`
- Create: `incident-diagnosis/agent/tests/test_evidence_template.py`
- Modify: `incident-diagnosis/agent/tests/test_evidence_projection.py`

**Interfaces:**
- Produces: `EvidenceTemplate`, `normalize_evidence(alert, facts, log, trace, configuration) -> EvidenceTemplate`, `EvidenceTemplate.serialize() -> str`, `EvidenceTemplate.fingerprint() -> str`, `EvidenceTemplate.to_prompt_groups() -> dict`, and `EvidenceTemplate.to_gate_context() -> dict`.
- Consumes: normalized alert, runtime facts, structured log, trace, and workload configuration diff dictionaries.

- [ ] **Step 1: Write failing contract tests**

```python
def test_template_is_fixed_order_and_omits_volatile_values():
    template = normalize_evidence(
        alert={"alert_name": "DLQEventsDetected", "service": "dispatch-service"},
        facts={"waiting_reason": "", "event_reason": ""},
        log={"status": "found", "message": "unknown event type Trip.Requested.v2", "trace_id": "volatile"},
        trace={"status": "correlated", "error_service": "dispatch-service", "error_operation": "dispatch.consume"},
        configuration={"status": "unchanged", "changes": []},
    )
    assert template.serialize().splitlines() == [
        "alert_name: DLQEventsDetected",
        "service: dispatch-service",
        "triggering_metric:",
        "waiting_reason:",
        "last_terminated_reason:",
        "event_reason:",
        "event_message:",
        "log_error: unknown event type Trip.Requested.v2",
        "trace_error_service: dispatch-service",
        "trace_error_operation: dispatch.consume",
        "trace_error_message:",
        "configuration_diff:",
    ]
    assert "volatile" not in template.serialize()

def test_fingerprint_changes_for_material_config_change_only():
    assert redis_bad_host.fingerprint() != redis_good_host.fingerprint()
    assert with_different_trace_id.fingerprint() == redis_bad_host.fingerprint()
```

- [ ] **Step 2: Run the focused tests and confirm the missing API failure**

Run: `python -m pytest incident-diagnosis/agent/tests/test_evidence_template.py -q`

Expected: FAIL because `EvidenceTemplate` and `normalize_evidence` do not exist.

- [ ] **Step 3: Replace the loose projection with one typed template**

```python
TEMPLATE_FIELDS = (
    "alert_name", "service", "triggering_metric", "waiting_reason",
    "last_terminated_reason", "event_reason", "event_message", "log_error",
    "trace_error_service", "trace_error_operation", "trace_error_message",
    "configuration_diff",
)

@dataclass(frozen=True)
class EvidenceTemplate:
    values: tuple[tuple[str, str], ...]
    evidence: tuple[dict[str, Any], ...]

    def serialize(self) -> str:
        return "\n".join(f"{key}: {value}" for key, value in self.values)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.serialize().encode("utf-8")).hexdigest()
```

Normalization is mechanical: collapse whitespace; sort/deduplicate repeated messages and changes; remove trace IDs, timestamps, generated pod suffixes, event IDs, and non-triggering instantaneous metrics. Preserve raw grouped evidence separately with stable IDs such as `alert:trigger`, `log:selected`, `trace:selected`, `k8s:state`, and `config:workload`.

- [ ] **Step 4: Run all projection tests**

Run: `python -m pytest incident-diagnosis/agent/tests/test_evidence_template.py incident-diagnosis/agent/tests/test_evidence_projection.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the contract**

```bash
git add incident-diagnosis/agent/evidence_projection.py incident-diagnosis/agent/tests/test_evidence_template.py incident-diagnosis/agent/tests/test_evidence_projection.py
git commit -m "refactor: define normalized incident evidence template"
```

### Task 2: Replace Redis history with knowledge, examples, and global hints

**Files:**
- Modify: `incident-diagnosis/agent/memory.py`
- Modify: `incident-diagnosis/agent/seed.py`
- Modify: `incident-diagnosis/agent/tests/test_memory.py`
- Modify: `incident-diagnosis/agent/tests/test_seed.py`
- Create: `incident-diagnosis/agent/tests/fixtures/bootstrap_corpus.json`

**Interfaces:**
- Produces: `store/get/list/update/delete_knowledge`, `store/get/list/update/delete_example`, `store/get/search_hint`, `replace_knowledge_hints`, `approve_pending`, `reset_dev_corpus`, `export_corpus`, `import_corpus`, and `seed_if_empty`.
- Consumes: `EvidenceTemplate.serialize()` and `.fingerprint()` from Task 1.

- [ ] **Step 1: Replace history tests with schema and atomicity tests**

```python
def test_example_evidence_is_immutable(fake_rdb):
    example_id = store_example(fake_rdb, approved_example("unsupported_event_contract"))
    with pytest.raises(ImmutableEvidenceError):
        update_example(fake_rdb, example_id, {"evidence": {"log_error": "changed"}})

def test_approve_pending_is_atomic(fake_rdb):
    result = approve_pending(fake_rdb, pending_id, actor="operator", knowledge=knowledge,
                             exact_reusable=True, hint_ids=(), new_hints=("unknown event type",))
    assert get_example(fake_rdb, result.example_id)["knowledge_key"] == knowledge["knowledge_key"]
    assert search_hints(fake_rdb, "unknown event")
```

Also test normalized hint deduplication, many-to-many links, corpus version increments, delete protection, export/import round trips, and schema-versioned reset.

- [ ] **Step 2: Run storage tests and confirm old-schema assumptions fail**

Run: `python -m pytest incident-diagnosis/agent/tests/test_memory.py incident-diagnosis/agent/tests/test_seed.py -q`

Expected: FAIL on missing example/hint operations and old history assertions.

- [ ] **Step 3: Implement schema-versioned Redis records and bootstrap fixtures**

```python
SCHEMA_VERSION = "v2"
KNOWLEDGE_INDEX = f"diagnosis:{SCHEMA_VERSION}:knowledge:index"
EXAMPLE_INDEX = f"diagnosis:{SCHEMA_VERSION}:example:index"
HINT_INDEX = f"diagnosis:{SCHEMA_VERSION}:hint:index"
CORPUS_VERSION_KEY = f"diagnosis:{SCHEMA_VERSION}:corpus:version"

def update_example(rdb, example_id: str, fields: dict) -> bool:
    forbidden = {"evidence", "fingerprint", "knowledge_key"}.intersection(fields)
    if forbidden:
        raise ImmutableEvidenceError(",".join(sorted(forbidden)))
    # only exact_reusable and approval metadata are persisted
```

Use a Redis transaction for pending approval. Seed the approved fixture families specified by the design, including multiple DLQ and Redis variants, neighboring event-processing and connection failures, OOM, CrashLoop, image-pull, missing configuration, and scheduling cases. Hints are linked globally rather than copied into knowledge.

- [ ] **Step 4: Run storage and bootstrap tests**

Run: `python -m pytest incident-diagnosis/agent/tests/test_memory.py incident-diagnosis/agent/tests/test_seed.py -q`

Expected: PASS, with no `history:*` key created.

- [ ] **Step 5: Commit the new persistence model**

```bash
git add incident-diagnosis/agent/memory.py incident-diagnosis/agent/seed.py incident-diagnosis/agent/tests/test_memory.py incident-diagnosis/agent/tests/test_seed.py incident-diagnosis/agent/tests/fixtures/bootstrap_corpus.json
git commit -m "refactor: replace diagnosis history with approved examples"
```

### Task 3: Rebuild exact, BM25, and MiniLM retrieval over evidence only

**Files:**
- Modify: `incident-diagnosis/agent/retrieval/models.py`
- Modify: `incident-diagnosis/agent/retrieval/corpus.py`
- Modify: `incident-diagnosis/agent/retrieval/bm25.py`
- Modify: `incident-diagnosis/agent/retrieval/reranker.py`
- Modify: `incident-diagnosis/agent/retrieval/service.py`
- Delete: `incident-diagnosis/agent/retrieval/signals.py`
- Modify: `incident-diagnosis/agent/tests/test_retrieval_models_signals.py`
- Modify: `incident-diagnosis/agent/tests/test_retrieval_corpus.py`
- Modify: `incident-diagnosis/agent/tests/test_retrieval_bm25.py`
- Modify: `incident-diagnosis/agent/tests/test_retrieval_reranker.py`
- Modify: `incident-diagnosis/agent/tests/test_retrieval_service.py`

**Interfaces:**
- Produces: `RetrievalDocument(example_id, knowledge_key, fingerprint, exact_reusable, evidence_text, hint_texts)`, `RetrievalCandidate`, `RetrievalResult(mode, candidates, exact_ambiguous, degraded_reason)`, and `RetrievalService.retrieve(template)`.
- Consumes: v2 memory records from Task 2 and `EvidenceTemplate` from Task 1.

- [ ] **Step 1: Write retrieval behavior tests**

```python
def test_unique_identical_reusable_example_is_exact(service):
    result = service.retrieve(live_template)
    assert result.mode is RetrievalMode.EXACT
    assert [c.knowledge_key for c in result.candidates] == ["unsupported_event_contract"]

def test_ambiguous_fingerprint_falls_through_to_nearest(service):
    result = service.retrieve(template_with_two_linked_causes)
    assert result.mode is RetrievalMode.NEAREST
    assert result.exact_ambiguous is True

def test_candidate_serialization_excludes_answers(candidate):
    text = serialize_candidate(candidate)
    assert "diagnosis cause" not in text
    assert "remediation" not in text
```

Assert BM25 keeps the best example per knowledge key and returns at most eight families; MiniLM returns at most three families; advisory retrieval has no acceptance-floor rejection; empty corpus yields `none`; failures yield `degraded`.

- [ ] **Step 2: Run retrieval tests and confirm failures**

Run: `python -m pytest incident-diagnosis/agent/tests/test_retrieval_*.py -q`

Expected: FAIL because the old models include root cause/fix action and use canonical Kubernetes signals plus an acceptance floor.

- [ ] **Step 3: Implement evidence-only retrieval**

```python
class RetrievalMode(str, Enum):
    EXACT = "exact"
    NEAREST = "nearest"
    NONE = "none"
    DEGRADED = "degraded"

@dataclass(frozen=True)
class RetrievalResult:
    mode: RetrievalMode
    candidates: tuple[RetrievalCandidate, ...] = ()
    exact_ambiguous: bool = False
    degraded_reason: str | None = None
```

Build `exact_by_fingerprint`; resolve exact only for one reusable example/knowledge key; otherwise BM25 the live serialized template, collapse by family, keep eight, rerank all eight with MiniLM, and return the first three. Serialize only `evidence_text` plus approved `hint_texts`. Remove the tie-margin logic and `ACCEPTANCE_FLOOR` so MiniLM ordering is direct and auditable.

- [ ] **Step 4: Run focused retrieval tests including the pinned model serialization tests**

Run: `python -m pytest incident-diagnosis/agent/tests/test_retrieval_models_signals.py incident-diagnosis/agent/tests/test_retrieval_corpus.py incident-diagnosis/agent/tests/test_retrieval_bm25.py incident-diagnosis/agent/tests/test_retrieval_reranker.py incident-diagnosis/agent/tests/test_retrieval_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the retrieval replacement**

```bash
git add incident-diagnosis/agent/retrieval incident-diagnosis/agent/tests/test_retrieval_models_signals.py incident-diagnosis/agent/tests/test_retrieval_corpus.py incident-diagnosis/agent/tests/test_retrieval_bm25.py incident-diagnosis/agent/tests/test_retrieval_reranker.py incident-diagnosis/agent/tests/test_retrieval_service.py
git commit -m "refactor: retrieve approved cases from evidence templates"
```

### Task 4: Collect meaningful metrics and bounded raw evidence

**Files:**
- Modify: `incident-diagnosis/agent/collector.py`
- Modify: `incident-diagnosis/agent/alerting.py`
- Modify: `incident-diagnosis/agent/diagnostics.py`
- Modify: `incident-diagnosis/agent/correlation.py`
- Modify: `incident-diagnosis/agent/tests/test_collector.py`
- Modify: `incident-diagnosis/agent/tests/test_alerting.py`
- Modify: `incident-diagnosis/agent/tests/test_correlation.py`
- Create: `incident-diagnosis/agent/tests/test_diagnostics.py`

**Interfaces:**
- Produces: `collect_operational_metrics(service, namespace, window, alert)`, `collect_runtime_evidence(service, namespace)`, `collect_configuration_diff(service, namespace)`, and `correlate_trace(log_evidence, start_epoch_s, end_epoch_s)` with raw status and units.
- Removes: `collect_bundle`, `_prom` zero fallback, and `resolve_dependency`/dependency chase.

- [ ] **Step 1: Add failing missing-data, metric-unit, trace, and diff tests**

```python
def test_metrics_keep_missing_values_and_define_units(mock_prometheus):
    metrics = collect_operational_metrics("dispatch-service", "vroom-dev", "5m", alert)
    assert metrics["cpu_usage"]["value"] is None
    assert metrics["cpu_usage"]["status"] == "no_data"
    assert metrics["request_rate"]["unit"] == "req/s"
    assert metrics["p95_latency"]["unit"] == "ms"

def test_configuration_diff_excludes_image_and_keeps_env_and_resources():
    assert diff["changes"] == [{
        "path": "containers.ride-service.env.REDIS_ADDR",
        "previous": "redis.platform.svc.cluster.local:6379",
        "current": "bad-host:6379",
    }]
```

Cover CPU cores/percent, memory MiB/limit percent, ephemeral storage MiB/limit percent, CPU throttling percent, request rate, HTTP error percent, p95, trigger value/threshold/unit/window, main/init container states, warning events, cross-service trace path, and unavailable collector status.

- [ ] **Step 2: Run collection tests and confirm failures**

Run: `python -m pytest incident-diagnosis/agent/tests/test_alerting.py incident-diagnosis/agent/tests/test_collector.py incident-diagnosis/agent/tests/test_correlation.py incident-diagnosis/agent/tests/test_diagnostics.py -q`

Expected: FAIL on p99, zero-valued missing data, absent resource metrics, and dependency behavior.

- [ ] **Step 3: Implement typed observations and remove noisy collectors**

```python
def observation(value, unit: str, status: str, *, window: str | None = None) -> dict:
    return {"value": value, "unit": unit, "status": status, "window": window}

metrics = {
    "request_rate": observation(rps, "req/s", rps_status, window=window),
    "http_error_rate": observation(error_pct, "%", error_status, window=window),
    "p95_latency": observation(p95_ms, "ms", p95_status, window=window),
    "memory_working_set": observation(memory_mib, "MiB", memory_status),
}
```

Use PromQL scoped to namespace/service. Normalize `metric_unit` and `metric_window` from Alertmanager annotations without alert-name classification. Keep configuration diff as its own collector layer, comparing only `env` and `resources` between active and verified predecessor workload revisions. Trace output includes `service_path`, `error_service`, `error_operation`, and `error_message` when Tempo contains them.

- [ ] **Step 4: Run collection tests**

Run: `python -m pytest incident-diagnosis/agent/tests/test_alerting.py incident-diagnosis/agent/tests/test_collector.py incident-diagnosis/agent/tests/test_correlation.py incident-diagnosis/agent/tests/test_diagnostics.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the evidence collection changes**

```bash
git add incident-diagnosis/agent/alerting.py incident-diagnosis/agent/collector.py incident-diagnosis/agent/diagnostics.py incident-diagnosis/agent/correlation.py incident-diagnosis/agent/tests/test_alerting.py incident-diagnosis/agent/tests/test_collector.py incident-diagnosis/agent/tests/test_correlation.py incident-diagnosis/agent/tests/test_diagnostics.py
git commit -m "refactor: collect bounded incident evidence with units"
```

### Task 5: Generate connected diagnosis reasoning and validate certainty

**Files:**
- Modify: `incident-diagnosis/agent/interpreter.py`
- Modify: `incident-diagnosis/agent/validation.py`
- Modify: `incident-diagnosis/agent/critic.py`
- Modify: `incident-diagnosis/agent/finalization.py`
- Modify: `incident-diagnosis/agent/tests/test_validation.py`
- Modify: `incident-diagnosis/agent/tests/test_critic.py`
- Modify: `incident-diagnosis/agent/tests/test_finalization.py`
- Create: `incident-diagnosis/agent/tests/test_interpreter_contract.py`

**Interfaces:**
- Produces: `generate_diagnosis(template, current_evidence, retrieval, models, groq_key, openrouter_key, llm=None) -> DiagnosisDraft` with `evidence_analysis`, `incident_summary`, nullable `diagnosis_cause`, `hypothesis`, `recommended_action`, `used_knowledge_keys`, and current-evidence references; also `suggest_retrieval_hints(template, existing_hints, llm) -> tuple[str, ...]` for pending human review.
- Consumes: up to three resolved candidate knowledge records from Task 3; exact results bypass generation.

- [ ] **Step 1: Write failing prompt/output/fallback tests**

```python
def test_prompt_separates_current_evidence_from_advisory_cases():
    prompt = build_generation_prompt(current, nearest)
    assert "CURRENT EVIDENCE — citable" in prompt
    assert "NEAREST APPROVED EXAMPLES — advisory, not evidence" in prompt
    assert all(case.knowledge_key in prompt for case in nearest)

def test_failed_refine_keeps_only_hard_grounded_hypothesis():
    result = finalize_diagnosis(rejected, gate_context, "vroom-dev", "dispatch-service")
    assert result["diagnosis_cause"] is None
    assert result["recommended_action"]["kind"] == "investigation"
    assert result["hypothesis"] == "The producer and consumer may use different contract versions."
```

Also test that candidate-only facts cannot support cause or hypothesis, a critic outage is review-required rather than accepted, tentative words are legal only in `hypothesis`, and refine runs once.

Test hint suggestion separately: it receives the normalized incident template plus existing global hint text, returns short symptom phrases, reuses normalized existing hints where possible, and never writes them to Redis.

- [ ] **Step 2: Run diagnosis tests and confirm the old contract fails**

Run: `python -m pytest incident-diagnosis/agent/tests/test_interpreter_contract.py incident-diagnosis/agent/tests/test_validation.py incident-diagnosis/agent/tests/test_critic.py incident-diagnosis/agent/tests/test_finalization.py -q`

Expected: FAIL because the old output is `root_cause`/`dev_action`/`kubectl_hint` and the fallback discards evidence analysis.

- [ ] **Step 3: Implement the new generation and guard contracts**

```python
REQUIRED_KEYS = {
    "evidence_analysis", "incident_summary", "diagnosis_cause", "hypothesis",
    "recommended_action", "used_knowledge_keys", "evidence_refs",
    "hypothesis_evidence_refs",
}

def validate_diagnosis(draft: dict, context: dict) -> GateResult:
    # validate shape, known current-evidence refs, confirmed/tentative separation,
    # candidate key existence, and remediation certainty
```

The semantic critic receives the bounded current evidence, candidate guidance, and draft. It checks support at the stated certainty but does not decide retrieval relevance. Refinement receives the combined hard and semantic issues. Finalization deterministically retains raw evidence analysis; rejected cause/remediation are cleared; hypothesis survives only when its references exist and its tokens are grounded in those referenced current facts.

`suggest_retrieval_hints` is called only when creating a pending review after operator resolution. It has strict JSON parsing and returns unapproved text for reviewer editing; failure yields an empty suggestion list without affecting the incident diagnosis.

- [ ] **Step 4: Run diagnosis contract tests**

Run: `python -m pytest incident-diagnosis/agent/tests/test_interpreter_contract.py incident-diagnosis/agent/tests/test_validation.py incident-diagnosis/agent/tests/test_critic.py incident-diagnosis/agent/tests/test_finalization.py -q`

Expected: PASS.

- [ ] **Step 5: Commit generation and validation**

```bash
git add incident-diagnosis/agent/interpreter.py incident-diagnosis/agent/validation.py incident-diagnosis/agent/critic.py incident-diagnosis/agent/finalization.py incident-diagnosis/agent/tests/test_interpreter_contract.py incident-diagnosis/agent/tests/test_validation.py incident-diagnosis/agent/tests/test_critic.py incident-diagnosis/agent/tests/test_finalization.py
git commit -m "refactor: generate grounded incident diagnosis with one refine"
```

### Task 6: Integrate the investigation flow and expose approval/admin APIs

**Files:**
- Modify: `incident-diagnosis/agent/app.py`
- Modify: `incident-diagnosis/agent/presentation.py`
- Create: `incident-diagnosis/agent/tests/test_app_api.py`
- Create: `incident-diagnosis/agent/tests/test_app_investigate.py`
- Delete if present: `incident-diagnosis/agent/live_proof.py`

**Interfaces:**
- Produces: the API routes specified in the design, incident occurrence payloads with raw evidence/metrics/retrieval state, and deterministic exact-path presentation.
- Consumes: Tasks 1–5 interfaces.

- [ ] **Step 1: Add failing Flask API and orchestration tests**

```python
def test_dev_admin_is_hidden_without_flag(client):
    assert client.post("/admin/dev/corpus/reset").status_code == 404

def test_pending_approval_copies_server_side_template(client, pending_incident):
    response = client.post(f"/pending/{pending_incident}/approve", json=approval_payload)
    assert response.status_code == 200
    assert response.json["example"]["evidence"] == stored_pending_template

def test_non_exact_investigation_passes_three_candidates_to_generator(client, spies):
    response = client.post("/investigate", json=dlq_alert)
    assert len(spies.generator_candidates) == 3
```

Cover CRUD, exact toggle only, immutable example rejection, hint autocomplete/linking, atomic approval, delete conflict, admin import/export/reset/seed, `none`/`degraded`, exact bypass, no `incident_kind` requirement, and pending-review creation on incident resolution.

- [ ] **Step 2: Run the API tests and confirm failures**

Run: `python -m pytest incident-diagnosis/agent/tests/test_app_api.py incident-diagnosis/agent/tests/test_app_investigate.py -q`

Expected: FAIL on old history routes and old investigate orchestration.

- [ ] **Step 3: Rewrite application orchestration and presentation**

```python
@app.post("/admin/dev/corpus/reset")
def reset_corpus_route():
    if os.getenv("ENABLE_DEV_ADMIN", "false").lower() != "true":
        abort(404)
    reset_dev_corpus(rdb)
    retrieval_service.invalidate()
    return jsonify({"reset": True})
```

In `/investigate`: normalize the alert; collect metrics/logs/traces/Kubernetes/configuration; build one template; retrieve; resolve candidate knowledge after ranking; run deterministic exact or LLM generation; validate/finalize; build raw-evidence presentation; store occurrence; return debug fields only when requested. Debug output includes normalized template, fingerprint result/ambiguity, BM25 top eight, MiniLM top three, selected example and knowledge IDs, corpus/stale state, LLM phase results, and validation decisions.

On `POST /incidents/{id}/resolve`, store the operator actor, create one pending review from the selected occurrence's server-side template and diagnosis, and call the bounded hint suggester. Remove dependency chase, causal chain, incident-kind routing, `collect_bundle`, confidence rewriting of diagnosis text, chronology construction, and asynchronous free-text history reflection.

Keep `presentation.py` only as the pure raw-evidence/API view builder. Delete it instead if the same pure transformation fits cleanly in a focused `incident_view.py`; do not keep compatibility wrappers. Confirm `live_proof.py` remains absent.

- [ ] **Step 4: Run app and all non-model agent tests**

Run: `python -m pytest incident-diagnosis/agent/tests -q -m "not model"`

Expected: PASS.

- [ ] **Step 5: Commit the integrated backend**

```bash
git add incident-diagnosis/agent/app.py incident-diagnosis/agent/presentation.py incident-diagnosis/agent/tests/test_app_api.py incident-diagnosis/agent/tests/test_app_investigate.py
git commit -m "feat: expose evidence-based diagnosis and approval APIs"
```

### Task 7: Replace dashboard contracts and build the approved incident view

**Files:**
- Modify: `incident-diagnosis/dashboard/src/types/incident.ts`
- Modify: `incident-diagnosis/dashboard/src/pages/IncidentDetailPage.tsx`
- Create: `incident-diagnosis/dashboard/src/components/incident/DiagnosisHero.tsx`
- Create: `incident-diagnosis/dashboard/src/components/incident/RawEvidenceGrid.tsx`
- Create: `incident-diagnosis/dashboard/src/components/incident/OperationalMetrics.tsx`
- Create: `incident-diagnosis/dashboard/src/components/incident/NearestExamples.tsx`
- Create: `incident-diagnosis/dashboard/src/components/ui/EvidenceBadge.tsx`
- Create: `incident-diagnosis/dashboard/src/components/incident/RawEvidenceGrid.test.tsx`
- Create: `incident-diagnosis/dashboard/src/pages/IncidentDetailPage.test.tsx`
- Modify: `incident-diagnosis/dashboard/src/index.css`

**Interfaces:**
- Produces: typed v2 incident API contract and the combined v1-layout/v2-evidence-card UI.
- Consumes: Task 6 occurrence payload.

- [ ] **Step 1: Write failing component tests for semantics and units**

```tsx
it('names every counted object and measurement unit', () => {
  render(<RawEvidenceGrid evidence={dlqEvidence} />)
  expect(screen.getByText('1 pod')).toBeInTheDocument()
  expect(screen.getByText('0 pod restarts')).toBeInTheDocument()
  expect(screen.queryByText('1/1 available')).not.toBeInTheDocument()
})

it('puts tooltip content in one accessible popup', async () => {
  render(<EvidenceBadge state="confirmed" />)
  await userEvent.hover(screen.getByText('CONFIRMED'))
  expect(screen.getByRole('tooltip')).toHaveTextContent('source explicitly reports')
})
```

Test wrapped field/value rows, absent hypothesis hiding, `Cause confirmed` versus `Review required`, advisory labeling, unavailable metrics, p95 ms, and collapsed audit.

- [ ] **Step 2: Run dashboard tests and confirm failures**

Run: `npm test -- RawEvidenceGrid IncidentDetailPage`

Working directory: `incident-diagnosis/dashboard`

Expected: FAIL because the v2 components and types do not exist.

- [ ] **Step 3: Implement the approved view and remove old incident cards from the page**

```ts
export type ObservationState = 'confirmed' | 'context' | 'not_found'
export interface RawEvidenceCard {
  id: string
  state: ObservationState
  title: string
  rows: Array<{ label: string; value: string; href?: string }>
}
```

Use the diagnosis hero, two-column layout, raw current evidence and recommendation on the left, operational metrics and nearest approved examples on the right, and collapsed audit below. Tooltips use `role="tooltip"`, keyboard focus, and one CSS box; omit native `title`. Cards render one field/value per row.

- [ ] **Step 4: Run tests, typecheck, and build**

Run: `npm test && npm run typecheck && npm run build`

Working directory: `incident-diagnosis/dashboard`

Expected: all commands PASS.

- [ ] **Step 5: Commit the incident dashboard view**

```bash
git add incident-diagnosis/dashboard/src
git commit -m "feat: present raw incident evidence with explicit units"
```

### Task 8: Rebuild knowledge review UI and remove History

**Files:**
- Modify: `incident-diagnosis/dashboard/src/types/knowledge.ts`
- Modify: `incident-diagnosis/dashboard/src/types/pending.ts`
- Modify: `incident-diagnosis/dashboard/src/pages/KnowledgePage.tsx`
- Modify: `incident-diagnosis/dashboard/src/pages/KnowledgeCreatePage.tsx`
- Modify: `incident-diagnosis/dashboard/src/pages/KnowledgeDetailPage.tsx`
- Modify: `incident-diagnosis/dashboard/src/pages/PendingDetailPage.tsx`
- Modify: `incident-diagnosis/dashboard/src/App.tsx`
- Modify: `incident-diagnosis/dashboard/src/components/layout/Sidebar.tsx`
- Delete: `incident-diagnosis/dashboard/src/pages/HistoryPage.tsx`
- Create: `incident-diagnosis/dashboard/src/pages/PendingDetailPage.test.tsx`
- Create: `incident-diagnosis/dashboard/src/pages/KnowledgeDetailPage.test.tsx`

**Interfaces:**
- Produces: UI for semantic knowledge, immutable examples, exact-reuse toggle, global hint autocomplete/linking, and atomic pending approval.
- Consumes: Task 6 management endpoints.

- [ ] **Step 1: Write failing approval and knowledge tests**

```tsx
it('approves the copied template with editable semantic fields and global hints', async () => {
  render(<PendingDetailPage />)
  expect(await screen.findByText('Copied incident evidence')).toBeInTheDocument()
  expect(screen.getByLabelText('Exact reusable')).toBeChecked()
  await userEvent.type(screen.getByLabelText('Retrieval hints'), 'unknown event')
  expect(await screen.findByText('unknown event type')).toBeInTheDocument()
})
```

Test example immutability, exact toggle, hint reuse/link display, sparse-template warning, example replacement/delete, and absence of History navigation/route.

- [ ] **Step 2: Run focused UI tests and confirm failures**

Run: `npm test -- PendingDetailPage KnowledgeDetailPage`

Working directory: `incident-diagnosis/dashboard`

Expected: FAIL on old history and conclusive fields.

- [ ] **Step 3: Implement v2 review and knowledge pages**

```ts
export interface ApprovedExample {
  example_id: string
  exact_reusable: boolean
  fingerprint: string
  evidence: Record<string, string>
  approval: { approved_by: string; approved_at: string }
}
```

Knowledge list shows cause, remediation, example count, and hints. Detail shows immutable example templates and lets the operator toggle exact reuse or delete/replace an example. Pending review submits knowledge mode/key/cause/remediation, exact reuse, selected hint IDs, and new hint text; it never submits replacement evidence.

- [ ] **Step 4: Run all dashboard checks**

Run: `npm test && npm run typecheck && npm run build`

Working directory: `incident-diagnosis/dashboard`

Expected: PASS with no `/history` route or navigation item.

- [ ] **Step 5: Commit review UI and History deletion**

```bash
git add incident-diagnosis/dashboard/src
git commit -m "refactor: manage approved diagnosis examples and hints"
```

### Task 9: Make the DLQ demo produce real cross-service evidence

**Files:**
- Modify: `../vroom-infra/inject-poison-pill.sh`
- Modify: `validation/demo/inject-poison-pill.sh`
- Modify: `../vroom-gitops/platform/observability/prometheus/prometheus-values.yaml`
- Modify: `incident-diagnosis/n8n/extract-alerts.js`
- Modify: `incident-diagnosis/n8n/tests/code-nodes.test.mjs`
- Create: `incident-diagnosis/agent/tests/fixtures/dlq_investigation.json`
- Create: `incident-diagnosis/agent/tests/fixtures/redis_config_investigation.json`

**Interfaces:**
- Produces: a ride-service-originated unsupported-contract request with propagated trace context, dispatch rejection/DLQ evidence, and Alertmanager input that needs no `incident_kind`.
- Consumes: existing ride/dispatch demo endpoints and Task 6 `/investigate` alert contract.

- [ ] **Step 1: Add failing n8n and fixture contract tests**

```javascript
test('extracts DLQ alert without incident_kind', () => {
  const result = runExtract(alertmanagerPayload)
  assert.equal(result[0].json.alert_name, 'DLQEventsDetected')
  assert.equal(result[0].json.service, 'dispatch-service')
  assert.equal('incident_kind' in result[0].json, false)
  assert.equal(result[0].json.metric_unit, 'events')
  assert.equal(result[0].json.metric_window, '5m')
})
```

Assert the DLQ fixture contains ride-service then dispatch-service in the trace path, a structured unsupported-event log, and an `events / 5m` trigger. Assert the Redis fixture contains the exact changed endpoint in both configuration diff and runtime error.

- [ ] **Step 2: Run n8n and backend fixture tests and confirm failures**

Run: `node --test incident-diagnosis/n8n/tests/code-nodes.test.mjs`

Run: `python -m pytest incident-diagnosis/agent/tests/test_app_investigate.py -q`

Expected: at least the new distributed-trace and no-incident-kind assertions FAIL.

- [ ] **Step 3: Update the demo scripts and alert extraction**

The poison-pill scripts temporarily set ride-service's existing `EVENT_CONTRACT_VERSION=v2`, wait for rollout, and call the normal ride trip API three times with a stable demo correlation header per request. Ride publishes `Trip.Requested.v2` with W3C trace context; dispatch records the consumer span, structured rejection, and direct permanent-failure DLQ increment. The cleanup trap restores `EVENT_CONTRACT_VERSION=v1` and waits for rollout. The script waits for observable evidence and then prints the Alertmanager webhook command. It does not write directly to Redis or pre-arm retry counters.

Add generic `metric_unit` and `metric_window` annotations to Prometheus alert rules, including `events` and `5m` for `DLQEventsDetected`. Keep n8n extraction limited to fingerprint, start time, alert name, service, namespace, pod, severity, status, metric value, threshold, metric unit, and metric window. Do not restore `incident_kind` or a DLQ-specific workflow branch.

- [ ] **Step 4: Run fixture and n8n tests**

Run: `node --test incident-diagnosis/n8n/tests/code-nodes.test.mjs`

Run: `python -m pytest incident-diagnosis/agent/tests/test_app_investigate.py -q`

Expected: PASS.

- [ ] **Step 5: Commit demo integration**

Commit the service-repository changes:

```bash
git add validation/demo/inject-poison-pill.sh incident-diagnosis/n8n incident-diagnosis/agent/tests/fixtures/dlq_investigation.json incident-diagnosis/agent/tests/fixtures/redis_config_investigation.json
git commit -m "test: make DLQ demo produce cross-service evidence"
```

Commit the two sibling-repository changes independently:

```bash
git -C ../vroom-infra add inject-poison-pill.sh
git -C ../vroom-infra commit -m "test: inject DLQ failure through ride service"
git -C ../vroom-gitops add platform/observability/prometheus/prometheus-values.yaml
git -C ../vroom-gitops commit -m "feat: annotate alert metric units and windows"
```

### Task 10: Refresh retrieval experiments against the v2 corpus

**Files:**
- Modify: `incident-diagnosis/agent/evaluation/serialization.py`
- Modify: `incident-diagnosis/agent/evaluation/benchmark.py`
- Modify: `incident-diagnosis/agent/evaluation/fixtures/retrieval_cases_v2.json`
- Modify: `incident-diagnosis/agent/tests/fixtures/retrieval_cases_v2.json`
- Modify: `incident-diagnosis/agent/tests/test_evaluation_fixtures_v2.py`
- Modify: `incident-diagnosis/agent/tests/test_evaluation_candidates.py`
- Modify: `incident-diagnosis/agent/tests/test_retrieval_benchmark.py`
- Regenerate: `incident-diagnosis/agent/experiments/reports/bm25-proof.json`
- Regenerate: `incident-diagnosis/agent/experiments/reports/bm25-proof.md`
- Regenerate: `incident-diagnosis/agent/experiments/reports/reranker-tournament.json`
- Regenerate: `incident-diagnosis/agent/experiments/reports/reranker-tournament.md`

**Interfaces:**
- Produces: evidence-only evaluation fixtures and reports; preserves the pinned MiniLM artifact/manifest.
- Consumes: Task 3 retrieval contract and Task 2 bootstrap families.

- [ ] **Step 1: Change fixture tests to reject answer leakage**

```python
def test_candidate_fixture_contains_only_evidence_and_hints(v2_cases):
    for candidate in v2_cases.candidates:
        assert "root_cause" not in candidate
        assert "remediation" not in candidate
        assert candidate["evidence"]["alert_name"]
```

Include exact, paraphrased, neighboring-family, unrelated, sparse, ambiguous, empty, and degraded DLQ/Redis cases.

- [ ] **Step 2: Run evaluation fixture tests and confirm old schema failure**

Run: `python -m pytest incident-diagnosis/agent/tests/test_evaluation_fixtures_v2.py incident-diagnosis/agent/tests/test_evaluation_candidates.py incident-diagnosis/agent/tests/test_retrieval_benchmark.py -q`

Expected: FAIL because old fixtures serialize answers/history.

- [ ] **Step 3: Rewrite evaluation serialization and fixtures**

Use the production `EvidenceTemplate.serialize()` for query and candidate evidence. Append only linked approved hint text to candidates. Measure BM25 top-eight family recall and MiniLM top-three family ordering; report exact ambiguity separately and make no calibrated accuracy claim. Give `evaluation.benchmark` the same explicit `--fixtures` and `--report-dir` CLI contract as the tournament so report regeneration never depends on hard-coded old paths.

- [ ] **Step 4: Run tests and regenerate reports using the repository evaluation commands**

Run: `python -m pytest incident-diagnosis/agent/tests/test_evaluation_fixtures_v2.py incident-diagnosis/agent/tests/test_evaluation_candidates.py incident-diagnosis/agent/tests/test_retrieval_benchmark.py -q`

Run: `python -m evaluation.benchmark --fixtures evaluation/fixtures/retrieval_cases_v2.json --report-dir experiments/reports`

Run: `python -m evaluation.tournament --fixtures evaluation/fixtures/retrieval_cases_v2.json --report-dir experiments/reports`

Working directory for the two report commands: `incident-diagnosis/agent`

Expected: tests PASS and both Markdown/JSON reports identify the v2 evidence-only corpus.

- [ ] **Step 5: Commit fixtures and reproducible reports**

```bash
git add incident-diagnosis/agent/evaluation incident-diagnosis/agent/tests/fixtures/retrieval_cases_v2.json incident-diagnosis/agent/tests/test_evaluation_fixtures_v2.py incident-diagnosis/agent/tests/test_evaluation_candidates.py incident-diagnosis/agent/tests/test_retrieval_benchmark.py incident-diagnosis/agent/experiments/reports
git commit -m "test: evaluate evidence-only diagnosis retrieval"
```

### Task 11: Delete the remaining old mechanism and verify the replacement

**Files:**
- Delete or narrow after call-site audit: `incident-diagnosis/agent/confidence.py`
- Delete or narrow after call-site audit: `incident-diagnosis/agent/presentation.py`
- Delete obsolete tests and fixtures under: `incident-diagnosis/agent/tests/`
- Delete obsolete incident components: `incident-diagnosis/dashboard/src/components/incident/ConfidenceCard.tsx`, `DiagnosisDecisionCard.tsx`, `DiagnosisSummary.tsx`, `EvidenceSections.tsx`, `ImmediateFixCard.tsx`, `ImpactCard.tsx`, `KnowledgeSuggestionCard.tsx`, `RootCauseCard.tsx`, `SupportingEvidence.tsx`, `Timeline.tsx`, `TimelinePhaseGroup.tsx`, `TraceHandoffCard.tsx`
- Delete obsolete matching/evaluation files only when no v2 caller remains.

**Interfaces:**
- Produces: a smaller source tree with no compatibility code for removed schemas/mechanisms.
- Consumes: all preceding tasks.

- [ ] **Step 1: Record the forbidden-symbol cleanup gate**

Run:

```powershell
rg -n "history:index|/history|trigger_waiting_reason|exact_conclusive|reranked_advisory|ACCEPTANCE_FLOOR|incident_kind|dependency_chase|causal_chain|commit provenance|image change|incident chronology|p99_seconds|root_cause_pattern|fix_action" incident-diagnosis/agent incident-diagnosis/dashboard/src
```

Expected before cleanup: matches remain. Classify each match as v2 fixture terminology that must be renamed, obsolete production code to delete, or unrelated text. `live_proof.py` must not exist.

- [ ] **Step 2: Delete dead files and remove dead imports/routes/types/tests**

Remove a file only after `rg` shows no v2 call site. Keep `presentation.py` only if Task 6 made it the single pure view builder; otherwise remove it. Retain BM25, MiniLM artifact files, and useful v2 experiment infrastructure. Do not delete screenshots or `.superpowers` local prototypes.

- [ ] **Step 3: Run the forbidden-symbol gate again**

Run the Step 1 command.

Expected: no production matches. Any retained historical experiment report match is explicitly labeled as historical and is outside runtime imports.

- [ ] **Step 4: Run the complete verification suite**

Run: `python -m pytest incident-diagnosis/agent/tests -q -m "not model"`

Run: `python -m pytest incident-diagnosis/kubectl-executor/tests -q`

Run: `node --test incident-diagnosis/n8n/tests/code-nodes.test.mjs`

Run: `npm test && npm run typecheck && npm run build`

Working directory for the npm command: `incident-diagnosis/dashboard`

Run: `git diff --check`

Expected: all commands PASS and the dashboard production build succeeds.

- [ ] **Step 5: Inspect the final file inventory and commit cleanup**

Run: `rg --files incident-diagnosis/agent incident-diagnosis/dashboard/src | Sort-Object`

Confirm every remaining file has a v2 caller or test purpose, then commit:

```bash
git add -A incident-diagnosis/agent incident-diagnosis/dashboard/src
git commit -m "chore: remove legacy incident diagnosis mechanism"
```

### Task 12: Exercise both demonstrations through the deployed workflow

**Files:**
- Modify only if verification exposes a defect: files already owned by Tasks 4, 6, 7, or 9.
- Record results: `incident-diagnosis/agent/experiments/demo-acceptance.md`

**Interfaces:**
- Produces: reproducible operator acceptance evidence for both business stories.
- Consumes: the deployed v2 agent, n8n workflow, observability stack, dashboard, and dev corpus seed endpoint.

- [ ] **Step 1: Reset and seed the ephemeral development corpus**

From inside the VM, call `POST /admin/dev/corpus/reset` followed by `POST /admin/dev/corpus/seed`, with `ENABLE_DEV_ADMIN=true`. Export the corpus and confirm DLQ, Redis, neighboring, and unrelated knowledge families exist.

- [ ] **Step 2: Run the Redis configuration demonstration**

Change `REDIS_ADDR` to `bad-host:6379`, wait for the workload revision and runtime failure, and trigger the real service alert. Confirm the dashboard shows the field/previous/current diff, runtime connection or DNS error, Kubernetes state, metrics with units, and either a validated cause or a grounded hypothesis plus discriminating next action.

- [ ] **Step 3: Restore Redis and run the distributed DLQ demonstration**

Run the updated poison-pill script through ride-service. Confirm three attempts produce a ride-to-dispatch trace, dispatch structured unsupported-contract rejection, DLQ increase in `events / 5m`, healthy Kubernetes context, and a diagnosis/hypothesis that names the contract mismatch without claiming unrelated metric problems.

- [ ] **Step 4: Record exact and nearest behavior**

Approve one occurrence with `exact_reusable: true`, repeat it, and confirm exact reuse skips BM25/MiniLM/LLM. Demote the example to advisory-only, repeat with a wording variation, and confirm debug audit shows BM25 top eight, MiniLM top three, and generation using candidates as advisory only.

- [ ] **Step 5: Write acceptance results and commit**

Record commands, observed evidence, retrieval mode, validation result, dashboard behavior, and any deviation in `demo-acceptance.md`, without credentials or bearer tokens.

```bash
git add incident-diagnosis/agent/experiments/demo-acceptance.md
git commit -m "docs: record incident diagnosis demo acceptance"
```
