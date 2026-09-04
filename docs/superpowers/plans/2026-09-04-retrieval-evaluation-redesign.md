# Retrieval Evaluation Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a transparent historical and semantic retrieval evaluation for the clean incident agent.

**Architecture:** Keep the 40-case historical regression fixture fixed. Expand the semantic fixture with provenance and ambiguity metadata, validate the BM25 candidate precondition, and report benchmark, coverage, trace, and operational evidence separately in Colab.

**Tech Stack:** Python 3, pytest, JSON fixtures, matplotlib, ONNX rerankers.

**Spec:** `docs/superpowers/specs/2026-09-04-retrieval-evaluation-redesign.md`

## Global Constraints

- Do not modify `incident-diagnosis/agent/` runtime behavior.
- Never label synthetic stress cases as historical incidents.
- Preserve the 40 historical cases and their labels.
- Keep all reranker candidates bounded by the production BM25 top-8 path.

### Task 1: Make semantic fixture provenance and ambiguity explicit

**Files:**
- Modify: `incident-diagnosis/evaluation/benchmark.py`
- Modify: `incident-diagnosis/evaluation/fixtures/semantic_disambiguation_cases.json`
- Test: `incident-diagnosis/evaluation/tests/test_benchmark.py`

- [ ] Add failing tests requiring at least 16 semantic cases, at least 8 `archive_derived` cases, valid provenance, non-empty competitors, shared keywords, decisive fields, and rationale.
- [ ] Run `python -m pytest tests/test_benchmark.py -q` and confirm the fixture fails the new contract.
- [ ] Extend `RetrievalCase` and `load_cases()` with `shared_keywords`, `decisive_fields`, `provenance`, and `rationale`.
- [ ] Expand the fixture to 16 cases with the approved 8/8 maximum split and explicit metadata.
- [ ] Run `python -m pytest tests/test_benchmark.py -q` and confirm it passes.
- [ ] Commit with `git commit -m "feat: expand semantic retrieval fixtures"`.

### Task 2: Add realistic dataset-level evidence diversity

**Files:**
- Modify: `incident-diagnosis/evaluation/fixtures/historical_retrieval_cases.json` or retain the current file under that meaningful name
- Modify: `incident-diagnosis/evaluation/fixtures/semantic_disambiguation_cases.json`
- Modify: `incident-diagnosis/evaluation/benchmark.py`
- Test: `incident-diagnosis/evaluation/tests/test_benchmark.py`

- [ ] Add failing tests for the six evidence categories: Kubernetes-heavy, metrics-plus-logs, logs-plus-traces, configuration-related, sparse/no-match, and conflicting evidence.
- [ ] Implement `evidence_category_coverage(cases)` using populated normalized-template fields; do not fabricate fields merely to pass a count.
- [ ] Add archive-derived cases first, then only documented synthetic stress cases for missing categories.
- [ ] Run `python -m pytest tests/test_benchmark.py -q` and inspect the coverage output.
- [ ] Commit with `git commit -m "feat: cover diverse incident evidence"`.

### Task 3: Validate semantic ambiguity before scoring rerankers

**Files:**
- Modify: `incident-diagnosis/evaluation/benchmark.py`
- Test: `incident-diagnosis/evaluation/tests/test_benchmark.py`

- [ ] Add a failing test that every semantic case’s BM25 candidates include an expected key and a declared competing key, and that at least one declared shared keyword appears in both the query and competitor candidate evidence.
- [ ] Implement a `validate_semantic_cases()` helper that raises a case-specific error when either condition is absent.
- [ ] Call the helper from the notebook before model loading.
- [ ] Run `python -m pytest tests/test_benchmark.py -q` and confirm all semantic cases satisfy the precondition.
- [ ] Commit with `git commit -m "test: validate semantic retrieval ambiguity"`.

### Task 4: Report benchmark and provenance metrics clearly

**Files:**
- Modify: `incident-diagnosis/evaluation/model_selection_colab.ipynb`
- Modify: `incident-diagnosis/evaluation/benchmark.py`
- Test: `incident-diagnosis/evaluation/tests/test_benchmark.py`

- [ ] Add failing notebook-source tests for separate historical/semantic charts, provenance breakdown, schema field coverage, evidence-category coverage, and retrieval-safety wording.
- [ ] Print exact, Top-1, MRR, Recall@3, false positives, abstentions, forbidden acceptances, and degraded counts for each benchmark.
- [ ] Print semantic results separately for `archive_derived` and `synthetic_stress` cases.
- [ ] Keep the per-case trace as BM25 candidates, raw reranker candidates, score floor, and accepted candidates.
- [ ] Run `python -m pytest tests/test_benchmark.py -q` and validate notebook JSON with `python -m json.tool model_selection_colab.ipynb > $null`.
- [ ] Commit with `git commit -m "feat: report retrieval evaluation provenance"`.

### Task 5: Apply the defensible model-selection gate

**Files:**
- Modify: `incident-diagnosis/evaluation/benchmark.py`
- Modify: `incident-diagnosis/evaluation/model_selection_colab.ipynb`
- Test: `incident-diagnosis/evaluation/tests/test_benchmark.py`

- [ ] Add failing tests for a material-regression policy that accepts an explicitly documented small trade-off but rejects unsafe or substantial regressions.
- [ ] Implement a structured gate result containing quality deltas, retrieval-safety result, p95 latency, peak RSS, and explanatory reasons.
- [ ] Make the notebook report `selected`, `inconclusive`, or `rejected` with reasons; never call BM25 a winner by default.
- [ ] Run `python -m pytest -q` in `incident-diagnosis/evaluation` and `incident-diagnosis/agent`.
- [ ] Commit with `git commit -m "feat: explain retrieval model selection gates"`.
