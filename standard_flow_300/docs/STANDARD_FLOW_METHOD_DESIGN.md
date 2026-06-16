# Original Version Method Design

This document explains the method implemented by the `standard_flow_300`
package. The package name is historical; the documented state is the PEARL
original version on 350 input records, with 300 strictly accepted records and
50 typed residuals.

## Scope

The package evaluates one fixed 350-input-record benchmark. Each input record
contains paper evidence, one raw model graph response, model/paper/run identity
fields, and source evidence coordinates. Every input record remains in the
denominator.

The original-version output is:

| Item | Value |
|---|---:|
| input records | 350 |
| strictly accepted input records | 300 |
| typed residual input records | 50 |
| provider-failure records after recovery | 0 |
| final EC/CG average | 0.896055452484024 |
| final REA average | 0.9062380952380953 |

## Strict Acceptance Rule

An input record is accepted only after evaluation:

```text
strictly accepted iff EC/CG = 1.0 and REA = 1.0,
with no provider or judge error
```

Input records that fail either metric remain in the 350-record denominator and
are written as typed residuals.

## Method Pipeline

The original version separates graph construction, semantic repair, evaluation,
and result recording.

```text
raw input record
-> raw graph extraction
-> structure repair
-> PEARL semantic repair
-> EC/CG + REA evaluation
-> result recording
```

### 1. Raw Graph Extraction

The raw model response is treated as input material. The pipeline reads its
initial nodes, edges, and claimed conclusion, but does not treat the response as
an accepted graph.

Evidence file:

- `results/LLM_BASELINE_SUMMARY_350.json`

Baseline result:

| Item | Value |
|---|---:|
| baseline strict accepted | 0/350 |
| original EC/CG average | 0.8274058084772371 |
| original REA average | 0.3385678166307718 |

### 2. Structure Repair

Structure repair turns a raw graph-like response into a schema-checkable graph:
explicit nodes, directed edges, normalized labels, and a root candidate.

This stage is necessary because the evaluator needs a well-formed graph object.
It is not a semantic success claim. A schema-checkable graph can still fail
coverage or reasoning-edge accuracy.

### 3. PEARL Semantic Repair

PEARL semantic repair is local to one input record and constrained by evidence.
It uses evaluator failure signals to decide what can be preserved and what
needs local repair.

The core repair logic has five roles:

| Role | Purpose |
|---|---|
| Vote/filter materialization | Keep majority-supported reasoning units as anchors. |
| Rejected-unit repair | Repair missing, rejected, or invalid local reasoning steps. |
| Semantic root construction | Build or repair the final semantic root node. |
| Consistency trim | Remove unsupported or inconsistent graph units before re-evaluation. |
| Result recording | Write strict accepted or typed residual state for every input record. |

`NROOT` is the final semantic root node id used by PEARL. It is synthesized
after reliable terminal conclusions are retained or repaired, and its text must
state the paper-level research aim or claim supported by those conclusions. It
is not an evaluation metric or a formal aggregation label.

The repair output is still only a candidate graph. It is counted as strict only
after EC/CG and REA both equal 1.0.

### 4. Evaluation

Each candidate graph is evaluated under the same EC/CG and REA rules used for
the raw baseline. Provider or judge execution failures are not counted as
semantic success; they must be recovered and evaluated cleanly.

Evidence files:

- `results/EC_REA_EVALUATION_SUMMARY_350.json`
- `results/FULL_350_SUMMARY.json`

### 5. Result Recording

Result recording writes every input record into one of two states:

| State | Meaning | Evidence |
|---|---|---|
| strict accepted | EC/CG and REA both equal 1.0. | `results/STRICT_ACCEPTED_350.csv` |
| typed residual | At least one strict metric fails, or the input record has an unrecovered evaluator failure. | `results/TYPED_RESIDUAL_350.csv` |

The current original-version state has no unrecovered provider failures and 50
typed residual input records.

## Residual Taxonomy

The 50 residual input records are visible and typed:

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

This directory supports only the original-version claim:

```text
PEARL original version: 300/350 strictly accepted input records,
50 typed residual input records
```

The separate `../review_closeout_350/` package documents the current-version
350/350 state. It does not overwrite this 300/350 original-version result.

## What This Directory Does Not Claim

This directory does not claim:

- the original version is 350/350;
- ANS replaces EC/CG and REA;
- residual input records were removed from the denominator;
- a schema-valid graph is automatically semantically correct;
- the current-version package is part of the original-version result.
