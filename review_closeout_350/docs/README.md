# Current Version Documentation

This directory documents the current-version state for the 350-row benchmark.
The current version is separate from the original-version result.

## Result Boundary

| State | Strict rows | Residual rows | Role |
|---|---:|---:|---|
| original version | 300/350 | 50 | Reportable original-version result |
| intermediate review checkpoint | 327/350 | 23 | Checkpoint before final current-version merge |
| separate current-version package | 350/350 | 0 | Audited current-version package |

The original-version result is not overwritten. The current version keeps
`original_version_overwrite = false`.

## Final Current-Version State

| Metric | Value |
|---|---:|
| strict-success rows | 350 |
| typed residual rows | 0 |
| final EC/CG average | 1.0 |
| final REA average | 1.0 |
| final merge rows | 23 |
| current-version ANS | 0.8085113065326633 |
| original-version ANS floor | 0.8033815290684022 |
| ANS margin | +0.005129777464261132 |

## Documents

- `REVIEW_CLOSEOUT_METHOD_DESIGN.md`: plain method design for the residual
  current version and its relation to the original 300/350 state.
- `DESIGN_DOCUMENT.md`: current-version framework and module design.
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

Every final current-version merge row must satisfy fresh `EC/CG = 1.0`, fresh
`REA = 1.0`, no provider or judge contamination, row-level ANS non-regression,
and inclusion in the controller-filtered merge set. The full current-version state must
also pass the batch ANS guard.
