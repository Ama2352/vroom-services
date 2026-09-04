# Retrieval Evaluation Redesign

## Goal

Produce a small but defensible offline evaluation of the clean incident retrieval pipeline without presenting curated or synthetic fixtures as real-world performance.

## Benchmarks

`historical_retrieval_cases.json` remains the fixed 40-case archive-derived regression benchmark. It measures retrieval against the historical project corpus and must be described as a small offline benchmark.

`semantic_disambiguation_cases.json` contains at least 16 cases. At least 8 are archive-derived; up to 8 are synthetic stress cases. Synthetic cases are allowed only when the archive lacks a suitable ambiguity and must never be described as historical incidents.

## Semantic Case Contract

Every semantic case includes:

- `expected_keys`: the supported family or families;
- `competing_keys`: plausible, keyword-sharing alternatives;
- `shared_keywords`: words that make lexical ambiguity realistic;
- `decisive_fields`: evidence fields that distinguish the expected family;
- `provenance`: `archive_derived` or `synthetic_stress`;
- `rationale`: a concise explanation of the ambiguity.

A semantic case is valid only when BM25 surfaces an expected family and at least one declared competitor. Cases with only a disguised exact keyword are rejected.

## Evidence Coverage

Report field-population percentages for `service`, `triggering_metric`, `log_error`, trace fields, and `configuration_diff`. Also report coverage counts for Kubernetes-heavy, metrics-plus-logs, logs-plus-traces, configuration-related, sparse/no-match, and conflicting-evidence cases. Do not populate fields artificially; diversity is a dataset-level property.

## Metrics and Decision

For each benchmark report exact-template correctness, advisory Top-1, MRR, Recall@3, false positives, correct abstentions, forbidden acceptances, and degraded results. Break semantic metrics down by provenance.

The reranking trace is `BM25 candidates -> raw reranker ordering/scores -> score floor -> accepted candidates`. A reranker may be selected only when it has no material held-out quality regression, explains any trade-off, passes retrieval safety, and meets the stated latency and memory gates. This evaluation does not measure the LLM critic; its gate is named `retrieval safety`.
