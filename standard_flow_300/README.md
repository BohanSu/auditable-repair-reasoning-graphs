# Standard Flow 300/350 Code Set

This directory contains the locked standard-flow code set and lightweight audit
results for the 350-row benchmark.

## What This State Means

The locked standard-flow record closes 300 of 350 rows under the strict row rule:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

Rows that do not satisfy the strict gate remain in the denominator and are
recorded as typed residual rows. In this state, the residual count is 50.

## Contents

- `code/`: Python source snapshot for evaluation, residual extraction, repair
  packet construction, ANS checks, merge proposal construction, and figure
  generation.
- `results/`: public result summaries and row-level tables for the locked
  300/350 state.
- `graphs/`: byte-for-byte copies of the 300 accepted terminal DOT graphs,
  organized by model, with a SHA-256 manifest and validation summary.
- `docs/`: scope, metric, and reporting notes for this state.
- `figures/`: lightweight result figures.

## Recommended Reading Order

1. `docs/STANDARD_FLOW_METHOD_DESIGN.md`: what the locked standard-flow method
   does and why the modules are separated.
2. `docs/CURRENT_RESULT.md`: the locked 300/350 result summary.
3. `docs/SCOPE_AND_REPORTING.md`: how to report this directory without
   conflating it with the later review package.
4. `docs/FILE_MAP.md`: what each public result file contains.

## Key Result Files

- `results/FULL_350_SUMMARY.json`: locked standard-flow summary.
- `results/EC_REA_EVALUATION_SUMMARY_350.json`: EC/CG and REA summary.
- `results/STRICT_ACCEPTED_350.csv`: the 300 strict-success rows.
- `results/TYPED_RESIDUAL_350.csv`: the 50 typed residual rows.
- `results/RESIDUAL_50_CLOSEOUT_QUEUE.csv`: the typed queue used to plan
  residual closeout.
- `results/LLM_BASELINE_SUMMARY_350.json`: raw-graph baseline comparison.
- `graphs/manifest.csv`: one row per accepted paper-model graph, including its
  repository path, SHA-256 digest, byte size, and parsed node/edge counts.
- `graphs/summary.json`: graph-set counts and the results of DOT parsing,
  strict structure validation, and source-to-copy hash checks.

## Reporting Boundary

This directory supports the locked standard-flow claim only:

```text
locked standard flow = 300/350 strict rows
```

It does not claim that the locked record is 350/350. The separate 350/350
review package is outside this workshop branch.
