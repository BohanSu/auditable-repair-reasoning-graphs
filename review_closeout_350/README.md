# Review Closeout 350/350 Code Set

This directory contains the code snapshot and lightweight audit evidence for the
350-row review closeout state.

## What This State Means

The closeout starts from the locked 300/350 standard-flow record, reaches an
intermediate 327/350 checkpoint, and verifies the final 23-row merge set. The
result is a separate 350/350 review package, with locked-record write disabled.

```text
locked standard flow: 300/350
intermediate review checkpoint: 327/350
final merge set: 23 rows
separate review package: 350/350
locked-record write: false
```

## Contents

- `code/`: closeout Python source snapshot.
- `results/`: controller queue, strict merge candidates, merge audit,
  review-accounting state, and ANS guard.
- `docs/`: public design document, validation summary, reproduction notes, and
  source-boundary notes.
- `figures/`: final vector overview.

## Recommended Reading Order

1. `docs/REVIEW_CLOSEOUT_METHOD_DESIGN.md`: how the residual closeout works and
   why it is separate from the locked standard-flow record.
2. `docs/DESIGN_DOCUMENT.md`: detailed closeout framework and acceptance
   contract.
3. `docs/VALIDATION_SUMMARY.md`: validation results for the 350/350 review
   package.
4. `docs/REPRODUCTION_COMMANDS.md`: lightweight audit commands.
5. `docs/SOURCE_POINTERS.md`: included/excluded artifact boundary.

## Key Result Files

- `results/REVIEW_FULL_350_SUMMARY.json`: review 350-row closeout summary.
- `results/REVIEW_FULL_350_ACCOUNTING.csv`: public row-level review state.
- `results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv`: verification table for the
  final merge candidates.
- `results/STRICT_MERGE_REVIEW_AUDIT.csv`: row-level merge audit.
- `results/REVIEW_BATCH_ANS_GUARD.json`: batch ANS guard for the review state.
- `results/STANDARD_FLOW_350_CLOSURE_TRACE.csv`: compact state transition trace.

## Reporting Boundary

Use this directory as review closeout evidence. Do not use it to state that the
locked standard-flow record has been overwritten. The locked result remains
300/350; this directory verifies a separate 350/350 review package.
