# Current Version Method Design

This document explains the method implemented by the `review_closeout_350`
package. It describes a current-version package built after the original-version result. It does not redefine the original-version result.

## Starting Point

The current version starts from the original-version state:

| State | Strict rows | Residual rows |
|---|---:|---:|
| original version | 300/350 | 50 |

The 50 residual rows are not dropped. They are the input to a separate residual
review process.

## Objective

The current version asks a narrower question than the original version:

> Can the remaining residual rows be repaired and verified under the same
> EC/CG + REA strict gate, while preserving row identity and source support?

The final output is a separate current-version package:

| State | Strict rows | Residual rows | Boundary |
|---|---:|---:|---|
| current version | 350/350 | 0 | original-version result unchanged |

## Why This Is Separate From the Original Version

The original version is a high-throughput path that produces the original 300/350 result. The current-version residual repair is a targeted review process for rows that did
not pass that path.

The distinction matters because the current-version residual repair uses additional evidence
guards, merge review, and source-support checks. It can support a separate
350/350 current-version package, but it should not be described as overwriting the original-version result.

## Inputs

The current version uses:

- typed residual rows from the original version;
- residual failure labels;
- candidate repair material;
- fresh EC/CG and REA evaluation results;
- row-level and batch-level ANS support audits;
- merge-audit tables.

Primary input evidence:

- `results/STANDARD_FLOW_350_CLOSURE_TRACE.csv`
- `results/AFTER327_CONTROLLER_QUEUE.csv`
- `results/AFTER327_CONTROLLER_SUMMARY.json`

## Closeout Pipeline

The current-version pipeline is:

```text
typed residual rows
-> failure-typed controller
-> targeted residual repair lanes
-> candidate preflight
-> fresh EC/CG + REA evaluation
-> row-level ANS guard
-> strict merge review
-> separate 350-row current-version accounting
-> batch ANS guard
```

Each stage has a different role. No single stage is enough by itself.

## Failure-Typed Controller

The controller prevents all residuals from being treated as one generic retry
bucket. It routes rows based on the residual label and available evidence.

The original-version residual split is:

| Failure type | Count |
|---|---:|
| `preflight:no_anchor_regenerate` | 31 |
| `final_metric_gate_failed` | 13 |
| `metric_regression` | 4 |
| `final_judge_failed` | 2 |

Before the final merge, the controller reaches an intermediate checkpoint:

| Checkpoint item | Value |
|---|---:|
| strict rows | 327 |
| remaining residual rows | 23 |
| final merge candidates | 23 |

This checkpoint is not a new dataset and not a final original-version result. It
only explains why the final review queue contains 23 rows.

## Targeted Repair Lanes

The current version uses targeted lanes because the residual failure modes differ.

| Lane | Used when | Design intent |
|---|---|---|
| compact source-seed rebuild | No reliable anchor exists. | Build a small source-grounded graph rather than free-rewriting the old graph. |
| source-leaf bridge repair | Useful structure exists but a source-supported bridge is missing. | Preserve stable units and repair the narrow missing support. |
| minimal root or ANS-delta edit | A row is near strict closure but has a local instability. | Change the smallest responsible unit while preserving support. |
| provider/judge isolation | Evaluation state is contaminated by execution failure. | Rerun after provider state is resolved; do not count execution failure as semantic success. |

The repair lanes produce candidates. They do not decide acceptance.

## Fresh Strict Evaluation

A review candidate must pass the same strict row rule:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

Fresh evaluation evidence:

- `results/STRICT_MERGE_CANDIDATES.csv`
- `results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv`

## Source-Support Guard

Fresh EC/CG and REA are necessary but not sufficient for the current-version package.
The current version also checks Atomic Node Support so that a candidate cannot reach
strict graph metrics by deleting difficult content or adding unsupported claims.

ANS has two roles:

- row-level non-regression for final merge candidates;
- batch-level guard for the full 350-row current-version package.

Batch ANS evidence:

- `results/REVIEW_BATCH_ANS_GUARD.json`
- `results/REVIEW_BATCH_ANS_GUARD.csv`

The final batch guard is:

| Item | Value |
|---|---:|
| original-version ANS floor | 0.8033815290684022 |
| current-version 350-row ANS | 0.8085113065326633 |
| margin | +0.005129777464261132 |
| guard passed | true |

ANS is a source-support guard, not a replacement for EC/CG and REA.

## Merge Review and Accounting

Only controller-approved, fresh-evaluated, ANS-guarded candidates enter the
merge review. The merge review checks that candidate rows can replace residual
rows in the current-version state without changing row identity or corrupting
the accounting state.

Evidence files:

- `results/STRICT_MERGE_REVIEW_AUDIT.csv`
- `results/STRICT_MERGE_REVIEW_AUDIT.json`
- `results/REVIEW_FULL_350_ACCOUNTING.csv`
- `results/REVIEW_FULL_350_SUMMARY.json`

Final current-version state:

| Item | Value |
|---|---:|
| accounted rows | 350 |
| strict-success rows | 350 |
| typed residual rows | 0 |
| final EC/CG average | 1.0 |
| final REA average | 1.0 |
| merged row count | 23 |
| original-version overwrite | false |

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

The original-version result remains:

```text
original version = 300/350 strict rows, 50 typed residual rows
```

## What This Directory Does Not Claim

This directory does not claim:

- the original-version result was overwritten;
- the 327 checkpoint is a separate benchmark result;
- ANS replaces EC/CG and REA;
- provider failures can be treated as semantic failures or semantic successes;
- residual rows were removed from the denominator.
