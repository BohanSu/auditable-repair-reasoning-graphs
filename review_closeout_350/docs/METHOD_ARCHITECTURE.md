# Current Version Method Architecture

This document explains the `350/350` current-version package in
`review_closeout_350/`. It starts from the locked original-version result and
adds a separate residual closeout path. The original-version result is not
rewritten.

## State Boundary

| State | Strict rows | Residual rows | Meaning |
|---|---:|---:|---|
| original version | 300/350 | 50 | Locked original-version result |
| intermediate current-version checkpoint | 327/350 | 23 | Internal checkpoint before the final merge review |
| current version | 350/350 | 0 | Audited closeout state with `original_version_overwrite = false` |

The `327/350` checkpoint is not a separate benchmark claim. It only explains
why the final audited merge set has 23 rows.

## Overall Architecture

The strict row rule does not change:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

What changes is the path used for the remaining 50 residual rows.

Original version:

```text
raw graph extraction
-> structure repair
-> PEARL semantic repair
-> fresh EC/CG + REA evaluation
-> strict accounting
-> 300/350
```

Current version:

```text
50 typed residual rows
-> failure-typed controller
-> targeted repair lanes
-> candidate preflight
-> fresh EC/CG + REA evaluation
-> row-level ANS guard
-> strict merge review
-> 350-row accounting
-> batch ANS guard
-> 350/350
```

The figure `figures/framework_v2.svg` is the visual summary of this closeout
path.

## Why a Separate Closeout Layer Exists

The remaining 50 rows do not fail for one uniform reason.

| Failure type | Count |
|---|---:|
| `preflight:no_anchor_regenerate` | 31 |
| `final_metric_gate_failed` | 13 |
| `metric_regression` | 4 |
| `final_judge_failed` | 2 |

If these rows were treated as one generic retry bucket, the audit trail would
mix together graph-anchor failures, coverage failures, metric tradeoffs, and
execution failures. The closeout layer keeps them separated and makes every
later acceptance decision explainable.

## Stage 1: Failure-Typed Controller

The controller reads the residual queue and routes each row to the next
evidence-bound action. Its job is to decide what kind of repair is allowed
before any new graph is merged.

Public evidence:

- `results/AFTER327_CONTROLLER_QUEUE.csv`
- `results/AFTER327_CONTROLLER_SUMMARY.json`
- `results/STANDARD_FLOW_350_CLOSURE_TRACE.csv`

Final controller state:

| Item | Value |
|---|---:|
| checkpoint strict rows | 327 |
| checkpoint residual rows | 23 |
| controller rows | 23 |
| rows ready for final review | 23 |

## Stage 2: Targeted Repair Lanes

The controller routes rows into narrow lanes. Each lane is designed for one
failure pattern.

| Lane | Used when | Repair rule |
|---|---|---|
| compact source-seed rebuild | no reliable anchor exists | Rebuild a small source-grounded graph instead of free-rewriting the old graph. |
| source-leaf bridge repair | useful graph structure exists but a source-backed bridge is missing | Preserve the stable structure and patch only the missing supported unit. |
| minimal root or ANS-delta edit | the row is close to closure but one commitment causes instability | Change the smallest responsible graph unit and keep the rest fixed. |
| provider or judge isolation | a clean semantic decision was blocked by execution state | Rerun after execution issues are cleared; do not count them as semantic success. |

The repair lanes produce candidates. They do not decide acceptance.

## Stage 3: Candidate Preflight and Fresh Evaluation

Every candidate is checked locally before fresh evaluation. Preflight makes sure
the candidate graph, evidence references, and evaluation payload are coherent
enough to send through the strict gate.

A candidate can only move forward if fresh evaluation still gives:

```text
EC/CG = 1.0
REA = 1.0
judge/provider error = false
```

Public evidence:

- `results/STRICT_MERGE_CANDIDATES.csv`
- `results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv`

## Stage 4: Row-Level ANS Guard

Fresh `1.0 / 1.0` is necessary, but it is not enough. A candidate also has to
show that it did not gain strict-metric closure by deleting difficult content or
adding unsupported claims.

The current-version package therefore keeps a row-level Atomic Node Support
guard for every final merge candidate.

## Stage 5: Strict Merge Review and Final Accounting

Only controller-approved, fresh-evaluated, and ANS-safe candidates enter the
strict merge review. The merge review checks row identity, mergeability,
artifact integrity, and final accounting consistency before a row is counted in
the current-version state.

Public evidence:

- `results/STRICT_MERGE_REVIEW_AUDIT.csv`
- `results/STRICT_MERGE_REVIEW_AUDIT.json`
- `results/REVIEW_FULL_350_ACCOUNTING.csv`
- `results/REVIEW_FULL_350_SUMMARY.json`

Final accounting state:

| Item | Value |
|---|---:|
| accounted rows | 350 |
| strict-success rows | 350 |
| typed residual rows | 0 |
| final EC/CG average | 1.0 |
| final REA average | 1.0 |
| merged row count | 23 |
| original-version overwrite | false |

## Stage 6: Batch ANS Guard

The batch guard checks the full 350-row closeout state against the original
300-row support floor.

| Item | Value |
|---|---:|
| original-version ANS floor | 0.8033815290684022 |
| current-version 350-row ANS | 0.8085113065326633 |
| margin | +0.005129777464261132 |
| guard passed | true |

This is the final check that the `350/350` state did not trade away source
support at the package level.

## Script Anchors

The shared source snapshot for both audited states is `../../code_snapshot/`. The
main script groups for the current-version path are:

| Stage | Main scripts | Role |
|---|---|---|
| original-version graph and evaluator path | `run_vote_guided_semantic_root_batch.py`, `curate_graph_from_vote_results_gpt55.py`, `evaluator.py` | Produces and evaluates the original-version PEARL graphs under the strict gate. |
| residual package prep | `build_residual_50_closeout_package.py`, `build_residual_closeout_v2_plan.py`, `extract_residual_50_candidate_frontier.py` | Turns the 50 residual rows into an auditable repair queue. |
| controller and diagnostics | `run_residual_50_closure_engine.py`, `build_after327_failure_typed_controller_queue.py`, `build_after327_*diagnostic.py` | Routes residual rows and explains why rows stay open or move forward. |
| lane materialization | `materialize_*.py`, `run_evidence_bound_*.py`, `build_evidence_bound_*packets.py`, `repair_evidence_bound_fresh_eval_provider_errors.py` | Builds lane-specific repair candidates and fresh-eval packets. |
| merge and guards | `build_strict_merge_candidates_from_fresh_eval.py`, `build_controller_filtered_strict_merge_candidates.py`, `build_strict_merge_proposal_350.py`, `build_strict_merge_candidate_package.py`, `build_ans_claims_for_strict_merge_candidates.py`, `evaluate_ans_factscore_style_350.py`, `build_strict_merge_ans_guard_report.py`, `build_proposal_batch_ans_guard.py` | Verifies the final 23-row merge set and checks both row-level and batch-level ANS. |

## Reporting Boundary

Report this directory as:

```text
current version: 350/350 strict rows, 0 residual rows,
with the original-version result unchanged
```

Do not report it as:

```text
original version = 350/350
```
