# Locked Standard-Flow Result

This document summarizes the locked standard-flow state for the 350-row
benchmark.

## Result

| Item | Value |
|---|---:|
| benchmark rows | 350 |
| strict-success rows | 300 |
| typed residual rows | 50 |
| provider-failure rows | 0 |
| final EC/CG average | 0.896055452484024 |
| final REA average | 0.9062380952380953 |
| original EC/CG average | 0.8274058084772371 |
| original REA average | 0.3385678166307718 |

The strict row rule is:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

The locked standard-flow result is therefore `300/350`, not `350/350`.

## Residual Breakdown

| Failure type | Count |
|---|---:|
| `preflight:no_anchor_regenerate` | 31 |
| `final_metric_gate_failed` | 13 |
| `metric_regression` | 4 |
| `final_judge_failed` | 2 |

## ANS Grounding Audit

Atomic Node Support is used as a grounding guard rather than as the strict
closure metric.

| Graph stage | Nodes | Atomic facts | Supported facts | ANS |
|---|---:|---:|---:|---:|
| raw extraction | 12,340 | 41,703 | 32,665 | 0.783277 |
| second-pass repair | 13,277 | 46,120 | 35,735 | 0.774827 |
| PEARL terminal graph | 8,557 | 32,890 | 26,345 | 0.801003 |

Main factual node ANS for the PEARL terminal graph is `0.8033754732721555`.

## Public Result Files

- `results/FULL_350_SUMMARY.json`
- `results/EC_REA_EVALUATION_SUMMARY_350.json`
- `results/STRICT_ACCEPTED_350.csv`
- `results/TYPED_RESIDUAL_350.csv`
- `results/RESIDUAL_50_CLOSEOUT_QUEUE.csv`
