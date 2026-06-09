# Standard-Flow Method Design

This document explains the method implemented by the `standard_flow_300`
package. It describes the locked standard flow only. It does not describe the
later residual review package as part of the locked record.

## Scope

The package evaluates one fixed 350-row benchmark. Each row contains paper
evidence, one raw model graph response, and row identity fields. The standard
flow keeps every row in the denominator.

The locked standard-flow output is:

| Item | Value |
|---|---:|
| accounted rows | 350 |
| strict-success rows | 300 |
| typed residual rows | 50 |
| provider-failure rows after recovery | 0 |
| final EC/CG average | 0.896055452484024 |
| final REA average | 0.9062380952380953 |

## Strict Row Rule

A row is accepted by the standard flow only after fresh evaluation:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

Rows that fail either metric remain in the 350-row denominator and are written
as typed residuals.

## Method Pipeline

The standard flow separates graph construction, semantic repair, fresh
evaluation, and accounting.

```text
raw row input
-> raw graph extraction
-> structure repair
-> PEARL semantic repair
-> fresh EC/CG + REA evaluation
-> row accounting
```

### 1. Raw Graph Extraction

The raw model response is treated as input material. The flow reads its initial
nodes, edges, and claimed conclusion, but does not treat the response as an
accepted graph.

Evidence file:

- `results/LLM_BASELINE_SUMMARY_350.json`

Baseline result:

| Item | Value |
|---|---:|
| raw strict rows | 0/350 |
| original EC/CG average | 0.8274058084772371 |
| original REA average | 0.3385678166307718 |

### 2. Structure Repair

Structure repair turns a raw graph-like response into a schema-checkable graph:
explicit nodes, directed edges, normalized labels, and a root candidate.

This stage is necessary because the evaluator needs a well-formed graph object.
It is not a semantic success claim. A schema-checkable graph can still fail
coverage or reasoning-edge accuracy.

### 3. PEARL Semantic Repair

PEARL semantic repair is row-local and evidence-bound. It uses evaluator
failure signals to decide what can be preserved and what needs local repair.

The core repair logic has four roles:

| Role | Purpose |
|---|---|
| Vote/filter materialization | Keep majority-supported reasoning units as anchors. |
| Rejected-unit repair | Repair missing, rejected, or invalid local reasoning steps. |
| Semantic root construction | Build or repair the final conclusion node. |
| Consistency trim | Remove unsupported or inconsistent graph units before re-evaluation. |

The repair output is still only a candidate graph. It is not counted as strict
until the fresh EC/CG and REA gate passes.

### 4. Fresh Evaluation

Each candidate graph is re-evaluated under the same EC/CG and REA rules used
for the raw baseline. Provider or judge execution failures are not counted as
semantic success; they must be recovered and re-evaluated.

Evidence files:

- `results/EC_REA_EVALUATION_SUMMARY_350.json`
- `results/FULL_350_SUMMARY.json`

### 5. Row Accounting

Accounting writes every row into one of two standard-flow states:

| State | Meaning | Evidence |
|---|---|---|
| strict success | Fresh EC/CG and REA both equal 1.0. | `results/STRICT_ACCEPTED_350.csv` |
| typed residual | At least one strict gate fails, or the row has an unrecovered evaluator failure. | `results/TYPED_RESIDUAL_350.csv` |

The current locked state has no unrecovered provider failures and 50 typed
residual rows.

## Residual Taxonomy

The 50 residual rows are visible and typed:

| Failure type | Count | Interpretation |
|---|---:|---|
| `preflight:no_anchor_regenerate` | 31 | No reliable majority-supported starting graph for ordinary local repair. |
| `final_metric_gate_failed` | 13 | Candidate graph still fails EC/CG or REA after repair. |
| `metric_regression` | 4 | A candidate helps one metric but regresses another required metric. |
| `final_judge_failed` | 2 | Final judging did not produce a valid strict result. |

Evidence files:

- `results/RESIDUAL_FAILURE_TYPE_COUNTS_350.csv`
- `results/RESIDUAL_50_CLOSEOUT_QUEUE.csv`
- `results/RESIDUAL_50_CLOSEOUT_SUMMARY.json`

## Reporting Boundary

This directory supports only the locked standard-flow claim:

```text
locked standard flow: 300/350 strict rows, 50 typed residual rows
```

The separate `../review_closeout_350/` package documents a later residual
review closeout. That package can be audited as a separate 350/350 review state,
but it does not overwrite this locked 300/350 standard-flow record.

## What This Directory Does Not Claim

This directory does not claim:

- the locked standard-flow record is 350/350;
- ANS replaces EC/CG and REA as the strict row gate;
- residual rows were removed from the denominator;
- a schema-valid graph is automatically semantically correct;
- the separate review package is part of the locked standard-flow record.
