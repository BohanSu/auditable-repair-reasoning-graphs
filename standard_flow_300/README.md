# PEARL Original Version 300/350 Code Set

This directory contains the source snapshot and lightweight audit results for
the PEARL original version on the fixed 350-input-record benchmark.

## What This State Means

The original version accepts 300 of 350 input records under the strict rule:

```text
an input record is strictly accepted iff EC/CG = 1.0 and REA = 1.0,
with no provider or judge error
```

Input records that do not satisfy the strict rule remain in the same 350-record
set and are recorded as typed residuals. In this state, the residual count is
50.

## Contents

- `code/`: Python source snapshot for graph evaluation, residual extraction,
  repair packet construction, ANS checks, merge proposal construction, and
  figure generation.
- `results/`: public result summaries and per-record tables for the 300/350
  original-version state.
- `docs/`: scope, metric, and reporting notes for this state.
- `figures/`: lightweight result figures.

## Recommended Reading Order

1. `docs/STANDARD_FLOW_METHOD_DESIGN.md`: method design and module roles.
2. `docs/CURRENT_RESULT.md`: 300/350 result summary and residual breakdown.
3. `docs/SCOPE_AND_REPORTING.md`: how to report this directory without
   conflating it with the current-version package.
4. `docs/FILE_MAP.md`: what each public result file contains.

## Key Result Files

- `results/FULL_350_SUMMARY.json`: original-version summary.
- `results/EC_REA_EVALUATION_SUMMARY_350.json`: EC/CG and REA summary.
- `results/STRICT_ACCEPTED_350.csv`: the 300 strictly accepted input records.
- `results/TYPED_RESIDUAL_350.csv`: the 50 typed residual input records.
- `results/RESIDUAL_50_CLOSEOUT_QUEUE.csv`: the typed queue used to plan
  residual processing.
- `results/LLM_BASELINE_SUMMARY_350.json`: raw-graph baseline comparison.

## Reporting Boundary

Report this directory as:

```text
PEARL original version = 300/350 strictly accepted input records
```

It does not claim 350/350. The current-version 350/350 evidence is kept in
`../review_closeout_350/`.
