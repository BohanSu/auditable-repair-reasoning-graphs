# Review Closeout Design

This document describes the review closeout framework for the PEARL 350-row
benchmark. The closeout is designed to be auditable: every row remains in the
denominator, every repair candidate is routed by failure type, and the final
review state is checked by both strict graph metrics and Atomic Node Support.

## Objective

The locked standard flow reaches `300/350` strict rows. The remaining 50 rows
are typed residuals rather than dropped cases. The closeout layer asks a
separate question: can the residual rows be repaired and verified under the same
strict row rule, while preserving source support?

The final review state reaches:

| Item | Value |
|---|---:|
| review strict rows | 350/350 |
| typed residual rows | 0 |
| final EC/CG average | 1.0 |
| final REA average | 1.0 |
| final merge rows | 23 |
| locked-record write | false |
| review ANS | 0.8085113065326633 |
| locked standard-flow ANS floor | 0.8033815290684022 |

The locked standard-flow record remains `300/350`. The review closeout is a
separate audited state.

## Why a Closeout Layer Was Needed

The standard flow handles most rows with a single high-throughput path:

```text
raw graph extraction
-> second-pass structure repair
-> PEARL semantic repair
-> fresh EC/CG and REA evaluation
-> strict row gate
```

The final residual rows did not fail for one uniform reason. The locked 300/350
state contains no-anchor failures, metric-gate failures, metric regressions, and
judge failures. Repeating the same repair loop would mix these causes together.

The closeout layer therefore separates:

- failure typing;
- targeted repair lane selection;
- local candidate preflight;
- fresh strict-metric evaluation;
- row-level ANS non-regression;
- controller-filtered merge review;
- batch ANS guard.

## Framework

The final workflow is:

```text
locked standard-flow residuals
-> failure-typed controller
-> targeted repair lanes
-> candidate preflight
-> fresh EC/CG = 1.0 and REA = 1.0 evaluation
-> row-level ANS guard
-> controller-filtered strict merge set
-> review accounting state
-> review batch ANS guard
-> 350/350 review closeout
```

The accompanying vector figure is `figures/framework_v2.svg`.

## Failure-Typed Controller

The controller routes each residual row by the observed failure type and current
candidate evidence. Its role is to avoid treating all failures as another graph
generation problem.

At the final review checkpoint, the controller has:

| Item | Value |
|---|---:|
| current strict rows | 327 |
| current residual rows | 23 |
| controller rows | 23 |
| rows ready for review merge | 23 |

The final 23-row controller queue contains:

| Failure type | Count |
|---|---:|
| `preflight:no_anchor_regenerate` | 13 |
| `final_metric_gate_failed` | 5 |
| `metric_regression` | 4 |
| `final_judge_failed` | 1 |

## Repair Lanes

The closeout uses multiple narrow lanes rather than one general extra module.

`compact source-seed rebuild` is used when the row lacks a reliable graph
anchor. It builds a short source-grounded graph with a semantic root and direct
source leaves, avoiding unsupported intermediate commitments.

`source-leaf bridge repair` is used when the graph has useful structure but
misses a directly supported source bridge. It preserves stable nodes and adds or
replaces only the narrow source-supported unit.

`ANS-delta or minimal-root edit` is used when a candidate is near strict closure
but one graph commitment causes metric instability or source-support regression.

`provider and judge isolation` prevents provider failures from being counted as
semantic failures. Provider-contaminated fresh evaluations cannot enter the
merge set.

## Candidate Preflight

Before a candidate is sent to fresh evaluation, local preflight checks ensure
that the candidate material is coherent enough to evaluate. The checks cover
candidate graph availability, candidate/evaluator consistency, coverage of
required entities, high-risk premise support, and absence of provider
contamination.

Preflight is not an acceptance gate by itself. It reduces invalid evaluator
calls and prevents obviously unsupported candidates from reaching the expensive
fresh-evaluation step.

## Fresh Evaluation

A closeout candidate must pass:

```text
fresh EC/CG = 1.0
fresh REA = 1.0
fresh judge/provider error = false
```

Fresh evaluation failures remain failures. Provider errors are treated as an
execution state and must be rerun after the provider condition is resolved.

## Row-Level ANS Guard

Fresh `1.0/1.0` is necessary but not sufficient. The candidate also has to pass
a row-level Atomic Node Support guard. This prevents a candidate from reaching
strict metrics by dropping difficult content or adding unsupported claims.

The guard compares the candidate's main factual ANS against the row floor and
requires no provider or rate error in the ANS evaluation.

## Controller-Filtered Merge Set

The merge set is built only from candidates that the controller marks as ready
and that pass all strict and ANS checks. The public table
`results/STRICT_MERGE_CANDIDATES.csv` keeps the candidate label, source failure
type, fresh metrics, row ANS values, guard margin, and mergeability flags.

The final merge set contains 23 rows.

## Review Accounting State

The review accounting state combines the locked standard-flow rows and the
controller-filtered final merge set into a separate 350-row review state. It
does not overwrite the locked standard-flow record.

The public row table is `results/REVIEW_FULL_350_ACCOUNTING.csv`, and the
summary is `results/REVIEW_FULL_350_SUMMARY.json`.

## Batch ANS Guard

The batch guard compares the review 350-row ANS against the locked standard-flow
ANS floor:

| Item | Value |
|---|---:|
| locked standard-flow ANS floor | 0.8033815290684022 |
| review 350-row ANS | 0.8085113065326633 |
| margin | +0.005129777464261132 |
| guard passed | true |

This check is needed because row-level non-regression alone does not prove that
the full 350-row review state preserves source support.

## Acceptance Contract

A final review merge candidate must satisfy all of the following:

- fresh `EC/CG = 1.0`;
- fresh `REA = 1.0`;
- no provider or judge contamination;
- local preflight checks pass;
- row-level ANS non-regression passes;
- inclusion in the controller-filtered merge set;
- fresh-evaluation hash verification passes;
- locked-record write remains disabled.

The full review state must also pass the batch ANS guard.

## Evidence Files

- `results/REVIEW_FULL_350_SUMMARY.json`
- `results/REVIEW_FULL_350_ACCOUNTING.csv`
- `results/AFTER327_CONTROLLER_SUMMARY.json`
- `results/AFTER327_CONTROLLER_QUEUE.csv`
- `results/STRICT_MERGE_CANDIDATES.csv`
- `results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv`
- `results/STRICT_MERGE_REVIEW_AUDIT.csv`
- `results/REVIEW_BATCH_ANS_GUARD.json`
- `results/STANDARD_FLOW_350_CLOSURE_TRACE.csv`

These files are lightweight public artifacts. Full per-paper graphs and
evaluator directories are intentionally outside the GitHub-ready release.
