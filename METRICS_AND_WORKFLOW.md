# Metrics and Workflow

This release separates the original-version result from the later current version
state. The separation is necessary because the original-version result and the
current version answer different audit questions.

## Row-Level Strict Gate

A row is strict only when all required evaluator gates pass:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

Rows that fail either metric remain counted in the denominator. They are not
removed from the benchmark; they are assigned a typed residual label so the
next repair step can be audited.

## EC/CG

`EC/CG` is the project evaluator's coverage and grounding score for the graph.
The score measures whether the final graph covers the required scientific
entities or content units under the evaluator's matching protocol. A score of
`1.0` means the row satisfies the coverage/grounding gate used by this benchmark.

The original-version result has:

```text
final EC/CG average = 0.896055452484024
strict rows with EC/CG = 1.0 and REA = 1.0 = 300
```

## REA

`REA` is the project evaluator's reasoning-edge accuracy score. It measures
whether the graph's reasoning steps are accepted by the evaluator. A score of
`1.0` means every evaluated reasoning step required for the row passes.

The original-version result has:

```text
final REA average = 0.9062380952380953
strict rows with EC/CG = 1.0 and REA = 1.0 = 300
```

## ANS

`ANS` means Atomic Node Support. It is a FActScore-style support audit over
atomic factual units extracted from graph nodes. It is used as a grounding guard
to prevent a repair from reaching EC/CG and REA by adding unsupported graph
content or by deleting difficult but required content.

ANS is a guard, not the strict closure metric. The strict row decision still
requires `EC/CG = 1.0` and `REA = 1.0`.

The current version uses two ANS checks:

- row-level ANS non-regression for each final merge candidate;
- batch-level ANS comparison between the original 300-row support floor and the
  current-version 350-row state.

The final current-version batch guard has:

```text
original-version ANS floor = 0.8033815290684022
current-version 350-row ANS = 0.8085113065326633
margin = +0.005129777464261132
```

## Workflow States

The release uses three explicit states:

| State | Strict rows | Residual rows | Meaning |
|---|---:|---:|---|
| original version | 300/350 | 50 | The reportable original-version result |
| intermediate current-version checkpoint | 327/350 | 23 | A checkpoint after part of the current-version residual repair had been verified |
| separate current-version package | 350/350 | 0 | Audited current-version package, with original-version overwrite disabled |

The final 23 rows are not silently blended into the original-version result.
They are represented in a separate current-version package with controller,
fresh-evaluation, merge-audit, and ANS-guard evidence.

## Residual Failure Types

The original 300/350 result leaves 50 residual rows:

| Failure type | Count | Meaning |
|---|---:|---|
| `preflight:no_anchor_regenerate` | 31 | The row lacked a reliable graph anchor for ordinary local repair |
| `final_metric_gate_failed` | 13 | The terminal graph still failed the strict EC/CG or REA gate |
| `metric_regression` | 4 | A candidate improved one metric but regressed another required metric |
| `final_judge_failed` | 2 | The final judge failed despite available graph/evidence material |

The current-version controller routes these failures into narrower repair lanes before
any final merge is considered.

## Closeout Acceptance Contract

A final current-version merge candidate must satisfy:

- fresh `EC/CG = 1.0`;
- fresh `REA = 1.0`;
- no provider or judge contamination;
- local preflight checks for candidate material;
- row-level ANS non-regression;
- inclusion in the controller-filtered merge set;
- current-version batch ANS at or above the original-version support floor;
- original-version overwrite remains disabled.

This is why the current version can be audited without changing the original-version result.
