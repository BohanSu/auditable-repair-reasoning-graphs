# PEARL Current Version 350/350 Code Set

This directory contains the code snapshot and lightweight audit evidence for the
350-row current-version state.

## What This State Means

The current version starts from the original 300/350 state, reaches an
intermediate 327/350 checkpoint, and verifies the final 23-row merge set. The
result is a 350/350 current-version state, with original-version overwrite disabled.

```text
original version: 300/350
intermediate review checkpoint: 327/350
final merge set: 23 rows
current version: 350/350
original-version overwrite: false
```

## Contents

- `code/`: current-version repair and audit Python source snapshot.
- `results/`: controller queue, strict merge candidates, merge audit,
  current-version accounting state, and ANS guard.
- `docs/`: public design document, validation summary, reproduction notes, and
  source-boundary notes.
- `figures/`: final vector overview.

## Recommended Reading Order

1. `docs/REVIEW_CLOSEOUT_METHOD_DESIGN.md`: how the current-version residual repair works and
   why it is separate from the original-version result.
2. `docs/DESIGN_DOCUMENT.md`: detailed current-version framework and acceptance
   contract.
3. `docs/VALIDATION_SUMMARY.md`: validation results for the 350/350 current-version state.
4. `docs/REPRODUCTION_COMMANDS.md`: lightweight audit commands.
5. `docs/SOURCE_POINTERS.md`: included/excluded artifact boundary.

## Key Result Files

- `results/REVIEW_FULL_350_SUMMARY.json`: current-version 350-row summary.
- `results/REVIEW_FULL_350_ACCOUNTING.csv`: public row-level current-version state.
- `results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv`: verification table for the
  final merge candidates.
- `results/STRICT_MERGE_REVIEW_AUDIT.csv`: row-level merge audit.
- `results/REVIEW_BATCH_ANS_GUARD.json`: batch ANS guard for the current-version state.
- `results/STANDARD_FLOW_350_CLOSURE_TRACE.csv`: compact state transition trace.

## Reporting Boundary

Use this directory as current-version evidence. Do not use it to state that the
original-version result has been overwritten. The original-version result remains
300/350; this directory verifies a 350/350 current-version state.
