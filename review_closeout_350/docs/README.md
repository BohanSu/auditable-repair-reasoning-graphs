# Review Closeout Documentation

This directory documents the review closeout state for the 350-row benchmark.
The closeout is separate from the locked standard-flow record.

## Result Boundary

| State | Strict rows | Residual rows | Role |
|---|---:|---:|---|
| locked standard flow | 300/350 | 50 | Reportable standard-flow record |
| intermediate review checkpoint | 327/350 | 23 | Checkpoint before final review merge |
| separate review package | 350/350 | 0 | Audited closeout package |

The locked standard-flow record is not overwritten. The review closeout keeps
`locked_record_write = false`.

## Final Review State

| Metric | Value |
|---|---:|
| strict-success rows | 350 |
| typed residual rows | 0 |
| final EC/CG average | 1.0 |
| final REA average | 1.0 |
| final merge rows | 23 |
| review ANS | 0.8085113065326633 |
| locked standard-flow ANS floor | 0.8033815290684022 |
| ANS margin | +0.005129777464261132 |

## Documents

- `REVIEW_CLOSEOUT_METHOD_DESIGN.md`: plain method design for the residual
  review closeout and its relation to the locked 300/350 standard-flow record.
- `DESIGN_DOCUMENT.md`: closeout framework and module design.
- `VALIDATION_SUMMARY.md`: validation results and acceptance contract.
- `REPRODUCTION_COMMANDS.md`: commands for checking the lightweight release and
  rerunning scripts when external artifacts are available.
- `SOURCE_POINTERS.md`: external artifact boundary and release policy.

## Key Result Files

- `results/REVIEW_FULL_350_SUMMARY.json`
- `results/REVIEW_FULL_350_ACCOUNTING.csv`
- `results/AFTER327_CONTROLLER_QUEUE.csv`
- `results/STRICT_MERGE_CANDIDATES.csv`
- `results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv`
- `results/STRICT_MERGE_REVIEW_AUDIT.csv`
- `results/REVIEW_BATCH_ANS_GUARD.json`

## Acceptance Contract

Every final review merge row must satisfy fresh `EC/CG = 1.0`, fresh
`REA = 1.0`, no provider or judge contamination, row-level ANS non-regression,
and inclusion in the controller-filtered merge set. The full review state must
also pass the batch ANS guard.
