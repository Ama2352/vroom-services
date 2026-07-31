# Reranker Tournament

Decision: **LOCAL_PASS**; recommendation: **minilm**.

## Why

Offline evidence compares frozen candidates across 40 cases: 20 calibration and 20 held-out, with 20 positive and 20 no-match cases.

## Systems

| System | Top-1 | Recall@3 | MRR | False positive | Forbidden | Exact | Stable | p95 | RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 40.0% | 40.0% | 0.400 | 0/10 (0.0%) | 0 | 1 | true | n/a ms | n/a MB |
| bm25 | 30.0% | 30.0% | 0.300 | 0/10 (0.0%) | 0 | 0 | true | n/a ms | n/a MB |
| minilm | 80.0% | 80.0% | 0.800 | 0/10 (0.0%) | 0 | 0 | true | 461.7 ms | 187.5 MB |
| mixedbread_xsmall | 0.0% | 0.0% | 0.000 | 0/10 (0.0%) | 0 | 0 | true | 893.4 ms | 350.8 MB |
| llm | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | n/a | n/a |

## Results

Operational telemetry: local artifact/estimated-container sizes: minilm 23.0/190.1 MB; mixedbread_xsmall 93.8/260.9 MB; LLM 0 request(s), 0 malformed and 0 provider failure(s), 0 input + 0 output tokens, paid-equivalent $n/a.

- MiniLM passed with 8/10 held-out top-1 cases, 0/10 false positives, 461.7 ms p95 latency, and 187.5 MB peak RSS.
- Mixedbread failed: no calibration score floor passed, and held-out top-1 and recall@3 regressed to 0/10.
- The LLM comparator was unavailable because neither `GROQ_KEY` nor `GROQ_API_KEY` was present; no provider request was attempted. The official Groq snapshot retrieved from <https://groq.com/pricing> on 2026-07-31 was $0.05/M input tokens and $0.08/M output tokens for `llama-3.1-8b-instant`.

## Informative failures

- `dns_no_match` — DNS hard negative: expected abstention; baseline returned none with 0 accepted candidate(s)
- `ambiguous_conclusive` — did not return an expected diagnosis key.

## Decision

LOCAL_PASS selects minilm; only LOCAL_PASS permits a free local rollout candidate.

## Limitations

This is an offline, fixed-fixture experiment. The unavailable LLM is not quality evidence; production remains unchanged pending a separate design for the selected free local reranker.

## Interview explanation

Candidate generation freezes the same BM25 top-eight candidates for every challenger. Semantic decision reranks only those candidates and never expands the retrieval set. Abstention keeps unsupported incidents from becoming advisory diagnoses. The proof gate requires held-out quality, false-positive, stability, latency, and memory evidence. The production boundary remains unchanged because this tournament is an offline evaluator.

## Reproduce

`python -m evaluation.tournament --fixtures evaluation/fixtures/retrieval_cases_v2.json --report-dir evaluation/reports --model-cache evaluation/.models --include-llm --llm-input-usd-per-million 0.05 --llm-output-usd-per-million 0.08` — [reranker-tournament.json](reranker-tournament.json)
