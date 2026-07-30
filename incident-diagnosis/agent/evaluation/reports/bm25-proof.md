# BM25 Retrieval Proof

**Status:** FAIL
**Infrastructure required:** none
**LLM calls:** none
**Selected variant:** rich_joined
**Calibrated raw-score floor:** 3.725
**Selection rationale:** Lowest calibration false-positive rate, then highest top-1 accuracy, highest recall@3, and lexicographic variant name.

## Calibration metrics

| System | Top-1 | Recall@3 | False-positive | Forbidden | Exact failures |
|---|---:|---:|---:|---:|---:|
| baseline | 0.625 | 0.625 | 0.000 | 0 | 0 |
| rich_joined | 0.875 | 1.000 | 0.000 | 0 | 0 |

## Held Out metrics

| System | Top-1 | Recall@3 | False-positive | Forbidden | Exact failures |
|---|---:|---:|---:|---:|---:|
| baseline | 0.500 | 0.500 | 0.000 | 0 | 1 |
| rich_joined | 0.875 | 0.875 | 0.500 | 1 | 0 |

## Challenger calibration outcomes

- **baseline_plain**: calibrated=true, threshold=12.793, false_positive=0.000, top1=0.625, recall_at_3=0.625, exact_failures=0
- **baseline_joined**: calibrated=true, threshold=3.725, false_positive=0.000, top1=0.750, recall_at_3=0.875, exact_failures=0
- **rich_plain**: calibrated=true, threshold=4.422, false_positive=0.000, top1=0.750, recall_at_3=0.750, exact_failures=0
- **rich_joined**: calibrated=true, threshold=3.725, false_positive=0.000, top1=0.875, recall_at_3=1.000, exact_failures=0

## Per-case traces

### oom_exact

- Split: calibration
- Expected mode: exact
- Expected keys: oom
- Selected mode: exact
- Exact ambiguity: false
- Rationale: OOMKilled is an approved conclusive canonical state.
- Ranked:
  - oom | raw_score=1.000 | matched_terms=none | source=knowledge:oom

### init_oom_exact

- Split: calibration
- Expected mode: exact
- Expected keys: init_oom
- Selected mode: exact
- Exact ambiguity: false
- Rationale: An init-container OOMKilled termination is conclusive.
- Ranked:
  - init_oom | raw_score=1.000 | matched_terms=none | source=knowledge:init_oom

### config_error_exact

- Split: calibration
- Expected mode: exact
- Expected keys: config_error
- Selected mode: exact
- Exact ambiguity: false
- Rationale: CreateContainerConfigError with a missing Secret is conclusive.
- Ranked:
  - config_error | raw_score=1.000 | matched_terms=none | source=knowledge:config_error

### image_pull_advisory

- Split: calibration
- Expected mode: advisory
- Expected keys: image_pull
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: Image pull evidence should retrieve image guidance, not OOM guidance.
- Ranked:
  - image_pull | raw_score=4.637 | matched_terms=imagepullbackoff | source=knowledge:image_pull

### failed_scheduling_advisory

- Split: calibration
- Expected mode: advisory
- Expected keys: failed_scheduling
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: Scheduling evidence should retrieve scheduling guidance.
- Ranked:
  - failed_scheduling | raw_score=4.768 | matched_terms=failedscheduling | source=knowledge:failed_scheduling

### outbox_not_draining

- Split: calibration
- Expected mode: advisory
- Expected keys: outbox_not_draining
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: Pending outbox events should retrieve the outbox backlog pattern.
- Ranked:
  - outbox_not_draining | raw_score=13.912 | matched_terms=events, no, outbox, pending, published, rows | source=history:fixture-history-0008

### driver_geo_empty

- Split: calibration
- Expected mode: advisory
- Expected keys: driver_matching_returns_0_results
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: An empty driver geosearch should retrieve the matching-zero-results pattern.
- Ranked:
  - driver_matching_returns_0_results | raw_score=12.621 | matched_terms=available, drivers, empty, geosearch, returns | source=history:fixture-history-0003

### generic_crashloop

- Split: calibration
- Expected mode: advisory
- Expected keys: crashloop
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: A generic startup crash should retrieve crashloop guidance without claiming OOM.
- Ranked:
  - init_crashloop | raw_score=4.050 | matched_terms=crashloopbackoff | source=knowledge:init_crashloop
  - crashloop | raw_score=3.725 | matched_terms=crashloopbackoff | source=knowledge:crashloop

### tls_no_match

- Split: calibration
- Expected mode: none
- Expected keys: none
- Selected mode: none
- Exact ambiguity: false
- Rationale: The catalog has no approved TLS certificate diagnosis.
- Ranked: none

### disk_no_match

- Split: calibration
- Expected mode: none
- Expected keys: none
- Selected mode: none
- Exact ambiguity: false
- Rationale: The catalog has no approved disk-pressure diagnosis.
- Ranked: none

### init_crashloop

- Split: held_out
- Expected mode: advisory
- Expected keys: init_crashloop
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: Init-container crash evidence should retrieve the init-specific pattern.
- Ranked:
  - init_crashloop | raw_score=8.100 | matched_terms=crashloopbackoff, init | source=knowledge:init_crashloop
  - init_oom | raw_score=3.827 | matched_terms=init | source=knowledge:init_oom
  - crashloop | raw_score=3.725 | matched_terms=crashloopbackoff | source=knowledge:crashloop

### scaled_to_zero

- Split: held_out
- Expected mode: advisory
- Expected keys: deployment_scaled_to_zero, zero_replica
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: Zero desired and available replicas admits either approved zero-replica key.
- Ranked:
  - deployment_scaled_to_zero | raw_score=8.343 | matched_terms=0, no, rps, traffic | source=history:fixture-history-0002

### xautoclaim_backlog

- Split: held_out
- Expected mode: advisory
- Expected keys: xautoclaim_pel_backlog
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: Unacknowledged pending entries should retrieve the XAUTOCLAIM backlog pattern.
- Ranked:
  - xautoclaim_pel_backlog | raw_score=9.672 | matched_terms=acknowledged, messages, not, xautoclaim | source=history:fixture-history-0010

### trip_timeout_storm

- Split: held_out
- Expected mode: advisory
- Expected keys: trip_offer_timeout_storm
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: Immediate post-request cancellations should retrieve the offer-timeout pattern.
- Ranked:
  - trip_offer_timeout_storm | raw_score=17.609 | matched_terms=after, cancelled, deadline, immediately, offer, requested, trips | source=history:fixture-history-0009

### argocd_outofsync

- Split: held_out
- Expected mode: advisory
- Expected keys: argocd_app_stuck_outofsync
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: Persistent Argo CD drift should retrieve the Argo CD pattern.
- Ranked:
  - argocd_app_stuck_outofsync | raw_score=9.503 | matched_terms=infrastructure, outofsync, perpetually, vroom | source=history:fixture-history-0001

### kargo_analysis_failed

- Split: held_out
- Expected mode: advisory
- Expected keys: kargo_verification_failing
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: A failed promotion analysis should retrieve the Kargo verification pattern.
- Ranked:
  - kargo_verification_failing | raw_score=14.699 | matched_terms=analysisrun, blocked, checks, failed, prometheus, promotion | source=history:fixture-history-0005

### high_error_rate

- Split: held_out
- Expected mode: advisory
- Expected keys: high_error_rate_on_ride_service
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: Elevated ride-service 5xx traffic should retrieve the high-error-rate pattern.
- Ranked:
  - high_error_rate_on_ride_service | raw_score=17.598 | matched_terms=1, 5xx, above, http, requests, status, total | source=history:fixture-history-0004

### dns_no_match

- Split: held_out
- Expected mode: none
- Expected keys: none
- Selected mode: advisory
- Exact ambiguity: false
- Rationale: DNS failure has no approved catalog diagnosis despite the generic wait state.
- Ranked:
  - init_crashloop | raw_score=4.050 | matched_terms=crashloopbackoff | source=knowledge:init_crashloop
  - crashloop | raw_score=3.725 | matched_terms=crashloopbackoff | source=knowledge:crashloop

### sparse_no_match

- Split: held_out
- Expected mode: none
- Expected keys: none
- Selected mode: none
- Exact ambiguity: false
- Rationale: An alert name without supporting facts must not produce an approved diagnosis.
- Ranked: none

### ambiguous_conclusive

- Split: held_out
- Expected mode: advisory
- Expected keys: init_oom, config_error
- Selected mode: none
- Exact ambiguity: true
- Rationale: Two competing conclusive states require advisory handling.
- Ranked: none
