# Reranker Tournament

Decision: **LOCAL_PASS**; recommendation: **minilm**.

## Why

Offline evidence compares frozen candidates across 40 cases: 20 calibration and 20 held-out, with 20 positive and 20 no-match cases.

## Systems

| System | Top-1 | Recall@3 | MRR | False positive | Forbidden | Exact | Stable | p95 | RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 40.0% | 40.0% | 0.400 | 0/10 (0.0%) | 0 | 1 | true | n/a ms | n/a MB |
| bm25 | 30.0% | 30.0% | 0.300 | 0/10 (0.0%) | 0 | 0 | true | n/a ms | n/a MB |
| minilm | 80.0% | 80.0% | 0.800 | 0/10 (0.0%) | 0 | 0 | true | 330.7 ms | 189.0 MB |
| mixedbread_xsmall | 70.0% | 70.0% | 0.700 | 0/10 (0.0%) | 1 | 0 | true | 610.2 ms | 355.7 MB |
| llm | 10.0% | 10.0% | 0.100 | 1/10 (10.0%) | 0 | 0 | false | 1020.0 ms | n/a MB |

## Results

Operational telemetry: local artifact/estimated-container sizes: minilm 23.0/190.1 MB; mixedbread_xsmall 93.8/260.9 MB; LLM 71 request(s), 3 malformed and 34 provider failure(s), 13683 input + 6046 output tokens, paid-equivalent $0.001168.

- bm25 failed: top-1 accuracy regressed.
- mixedbread_xsmall failed: forbidden candidate accepted.
- llm failed: false-positive rate exceeded baseline.

## Informative failures

- `dns_no_match` — DNS hard negative: expected abstention; baseline returned none with 0 accepted candidate(s)
- `init_crashloop` (`mixedbread_xsmall`) — accepted forbidden key(s): crashloop.

## Decision

LOCAL_PASS selects minilm; only LOCAL_PASS permits a free local rollout candidate.

## Limitations

This is an offline, fixed-fixture experiment; unavailable systems and provider failures are evidence, not production changes.

## Interview explanation

Candidate generation freezes the same BM25 top-eight candidates for every challenger. Semantic decision reranks only those candidates and never expands the retrieval set. Abstention keeps unsupported incidents from becoming advisory diagnoses. The proof gate requires held-out quality, false-positive, stability, latency, and memory evidence. The production boundary remains unchanged because this tournament is an offline evaluator.

## Reproduce

`python -m evaluation.tournament --fixtures incident-diagnosis/agent/evaluation/fixtures/retrieval_cases_v2.json --report-dir incident-diagnosis/agent/evaluation/reports --model-cache incident-diagnosis/agent/evaluation/.models --include-llm --llm-input-usd-per-million 0.05 --llm-output-usd-per-million 0.08 --pricing-source-url https://groq.com/pricing --pricing-retrieved-at 2026-07-31` — [reranker-tournament.json](reranker-tournament.json)
