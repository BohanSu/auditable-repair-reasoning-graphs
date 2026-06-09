# Metrics and Standard-Flow Workflow

This branch documents the locked standard-flow result only. It does not include
the separate 350/350 review package.

## Row-Level Strict Gate

A row is strict only when all required evaluator gates pass:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

Rows that fail either metric remain counted in the denominator. They are not
removed from the benchmark; they are assigned a typed residual label.

## EC/CG

`EC/CG` is the project evaluator's coverage and grounding score for the graph.
The locked standard-flow result has:

```text
final EC/CG average = 0.896055452484024
strict rows with EC/CG = 1.0 and REA = 1.0 = 300
```

## REA

`REA` is the project evaluator's reasoning-edge accuracy score. The locked
standard-flow result has:

```text
final REA average = 0.9062380952380953
strict rows with EC/CG = 1.0 and REA = 1.0 = 300
```

## ANS Boundary

`ANS` means Atomic Node Support. It is a FActScore-style source-support audit
over atomic factual units extracted from graph nodes. In this branch, ANS is a
grounding audit, not the strict row gate. The strict row decision still requires
`EC/CG = 1.0` and `REA = 1.0`.

The locked PEARL terminal graph has:

```text
all-node ANS = 0.801003
main factual node ANS = 0.8033754732721555
```

## Standard-Flow States

| State | Strict rows | Residual rows | Meaning |
|---|---:|---:|---|
| raw graph baseline | 0/350 | 350 | Raw model graphs under the same strict gate |
| locked standard flow | 300/350 | 50 | Reportable standard-flow record |

The standard flow is:

```text
raw row input
-> raw graph extraction
-> structure repair
-> PEARL semantic repair
-> fresh EC/CG + REA evaluation
-> row accounting
```

## Residual Failure Types

The locked 300/350 record leaves 50 typed residual rows:

| Failure type | Count | Meaning |
|---|---:|---|
| `preflight:no_anchor_regenerate` | 31 | The row lacked a reliable graph anchor for ordinary local repair. |
| `final_metric_gate_failed` | 13 | The terminal graph still failed the strict EC/CG or REA gate. |
| `metric_regression` | 4 | A candidate improved one metric but regressed another required metric. |
| `final_judge_failed` | 2 | The final judge failed despite available graph/evidence material. |

## Reporting Boundary

Use this branch for:

```text
locked standard-flow result: 300/350 strict rows, 50 typed residual rows
```

Do not use this branch to claim that the locked standard-flow record is
350/350.
