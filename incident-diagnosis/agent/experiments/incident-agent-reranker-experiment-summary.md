# Incident-Agent Retrieval Experiment Summary

## Objective

Replace the incident agent's token-coverage retrieval decision with a more defensible, precision-first design. The system must handle noisy observability bundles, avoid unsupported diagnoses, and remain practical to run locally without starting the K3s infrastructure.

## Proposed retrieval design

```text
Collected incident evidence
        |
        +--> Exact match: retain as a high-confidence shortcut
        |
        +--> Otherwise, BM25 retrieves the top 8 knowledge candidates
                       |
                       +--> Reranker accepts one supported candidate or abstains
                                      |
                                      +--> Accepted evidence is included in the generation LLM prompt
```

BM25 is not the final diagnosis decision. It is candidate generation: it reduces a large and noisy knowledge base to a bounded candidate set. The reranker then makes the precision-sensitive decision.

## Experiment design

The experiment is offline and does not require K3s. It uses a fixed dataset of 40 incident cases:

- 20 calibration cases, used only to set an acceptance threshold for each challenger.
- 20 held-out cases, used only for final comparison.
- The held-out split contains 10 positive cases and 10 no-match cases, including adversarial negatives that look similar to real incidents but must not produce a diagnosis.

Every challenger receives the identical BM25 top-8 candidate set. This isolates the reranking question: given the same retrieved knowledge, which mechanism selects the correct supported diagnosis and abstains when evidence is insufficient?

The measured gates are:

- Top-1 accuracy: correct diagnosis for positive held-out cases.
- False-positive rate: diagnosing a no-match incident.
- Forbidden acceptance: choosing a deliberately unsafe or incorrect candidate.
- Stability: agreement across repeated decisions.
- Operational evidence: latency, memory, and for the hosted LLM, requests, tokens, and estimated cost.

## Results

| Approach | Held-out top-1 | False positives | Stability | Result |
|---|---:|---:|---|---|
| Current baseline | 40% | 0% | Stable | Safe, but misses many applicable diagnoses. |
| BM25 alone | 30% | 0% | Stable | Useful candidate generator, weak final decision mechanism. |
| MiniLM cross-encoder | **80%** | **0%** | Stable | Selected local reranker. |
| Mixedbread reranker | 70% | 0% | Stable | Rejected: accepted one forbidden candidate. |
| Groq LLM judge | 10% | 10% | Unstable | Rejected from the critical retrieval path. |

MiniLM operational measurements:

- p95 latency: about 331 ms.
- Peak memory: about 189 MB.
- Model artifact: about 23 MB.
- No per-request provider cost or API dependency.

Groq LLM judge operational measurements:

- 71 requests during the complete tournament.
- 34 HTTP 429 rate-limit failures and 3 malformed outputs.
- 13,683 input tokens and 6,046 output tokens.
- Paid-equivalent experiment cost: about USD 0.00117.
- p95 latency: about 1.02 seconds.

## LLM test interpretation

The LLM judge was tested fairly but did not pass the complete solution gate. Its 10% held-out top-1 result is affected by provider rate limits, so it would be inaccurate to claim that the model's semantic reasoning alone is necessarily poor. However, those provider failures are still relevant engineering evidence: the complete LLM-based retrieval solution was inaccurate, unstable, and dependent on an external rate-limited service under the benchmark conditions.

The first live test also showed that the provider could return a semantically valid decision label such as `candidate_1_supported` instead of the strict internal label `accepted`. The parser was updated to normalize that alias only when the referenced candidate exists and is also declared in `selected_keys`. Unknown or inconsistent output remains rejected.

## Recommendation

Use this hybrid retrieval pipeline:

1. Keep exact matching only for explicitly defined, high-confidence signatures.
2. Use BM25 to retrieve a bounded candidate pool.
3. Use the local MiniLM cross-encoder to semantically rerank the candidate pool.
4. Apply a threshold calibrated using labelled cases.
5. Abstain when no candidate passes the threshold.
6. Send only the accepted candidate and its supporting incident evidence to the generation LLM.

This makes the generation LLM explain an evidence-backed retrieval decision instead of asking it to select a diagnosis from an entire noisy observability bundle.

## Interview explanation

> I separated incident diagnosis into retrieval and generation. Exact signatures remain a high-confidence shortcut. For non-exact incidents, BM25 retrieves a small candidate set from the knowledge base, then a local cross-encoder reranks it against the incident evidence. I evaluated candidate generators and rerankers on a fixed held-out dataset containing both true incidents and adversarial no-match cases. MiniLM achieved 80% top-1 accuracy with zero false positives, compared with 30% for BM25 alone and 40% for the previous baseline. I also evaluated an LLM judge, but it was unstable and rate-limited, so I rejected it from the critical retrieval path. The LLM remains in generation, where it produces root-cause analysis grounded only in accepted evidence.

## Evidence

The full tournament report and machine-readable evidence are stored in the isolated service worktree:

- `vroom-services/.worktrees/reranker-tournament/incident-diagnosis/agent/evaluation/reports/reranker-tournament.md`
- `vroom-services/.worktrees/reranker-tournament/incident-diagnosis/agent/evaluation/reports/reranker-tournament.json`

Verification after the LLM comparison: 443 tests passed.

## Code layer and interview scope

You do not need to memorize every function or JSON field. You should understand the responsibility of the main layers:

| File | Responsibility |
|---|---|
| `evaluation/tournament.py` | Orchestrates fixtures, candidate generation, calibration, held-out evaluation, gates, and recommendations. |
| `evaluation/llm_judge.py` | Builds the evidence/candidate prompt, calls Groq, validates JSON output, and records provider/token telemetry. |
| `evaluation/local_reranker.py` | Loads and runs the local MiniLM and Mixedbread reranker models. |
| `evaluation/retrieval.py` | Contains retrieval and BM25 candidate-generation logic. |
| `evaluation/fixtures/retrieval_cases_v2.json` | Fixed 40-case benchmark dataset. |
| `evaluation/reports/reranker-tournament.md` | Human-readable experiment report. |
| `evaluation/reports/reranker-tournament.json` | Machine-readable metrics and traces. |

The important execution flow is:

```text
tournament.py
    +-- exact/current baseline
    +-- BM25 candidate generation
    +-- MiniLM/Mixedbread reranking
    +-- Groq LLM judging
    +-- calibration and held-out evaluation
    +-- report generation
```

The concepts to explain are candidate generation, semantic reranking, threshold calibration, abstention, and held-out evaluation. BM25 narrows the knowledge base to eight candidates; the reranker decides whether one is actually supported. If no candidate passes the calibrated threshold, the system abstains. The generation LLM is separate and receives only accepted evidence.

The tournament is an offline evaluation layer. It proves which retrieval approach is strongest without changing the production incident agent. The Groq key was injected through an environment variable for the experiment and was not stored in source code or reports.
