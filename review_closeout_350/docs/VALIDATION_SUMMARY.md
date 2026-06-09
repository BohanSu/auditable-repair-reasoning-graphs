# Validation Summary

This file summarizes the checks that support the separate review package.

## Review Result

| Metric | Value |
|---|---:|
| accounted rows | 350 |
| strict-success rows | 350 |
| typed residual rows | 0 |
| final EC/CG average | 1.0 |
| final REA average | 1.0 |
| final merge rows | 23 |
| locked-record write | false |

The review package is separate from the locked standard-flow result. The locked
standard-flow result remains `300/350`.

## Controller State

| Metric | Value |
|---|---:|
| checkpoint strict rows | 327 |
| checkpoint residual rows | 23 |
| controller rows | 23 |
| rows ready for review merge | 23 |
| provider calls in final controller step | false |

Failure types in the final 23-row controller queue:

| Failure type | Count |
|---|---:|
| `preflight:no_anchor_regenerate` | 13 |
| `final_metric_gate_failed` | 5 |
| `metric_regression` | 4 |
| `final_judge_failed` | 1 |

## Merge-Candidate Checks

All 23 final merge candidates were verified for:

- fresh `EC/CG = 1.0`;
- fresh `REA = 1.0`;
- no fresh judge/provider error;
- row-level ANS guard pass;
- mergeable-with-ANS-guard flag pass;
- fresh-evaluation hash verification pass.

The public evidence is:

- `results/STRICT_MERGE_CANDIDATES.csv`
- `results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv`
- `results/STRICT_MERGE_REVIEW_AUDIT.csv`

## Batch ANS Guard

| Metric | Value |
|---|---:|
| locked standard-flow ANS floor | 0.8033815290684022 |
| review 350-row ANS | 0.8085113065326633 |
| ANS margin | +0.005129777464261132 |
| candidate rows replaced | 23 |
| provider calls | false |
| guard passed | true |

The batch guard shows that the 350/350 review package does not reach strict
metrics by sacrificing source support relative to the locked 300-row floor.

## Interpretation

The review closeout passes three nested checks:

1. Each final candidate row passes fresh EC/CG and REA.
2. Each final candidate row passes row-level ANS non-regression.
3. The full 350-row review state passes batch ANS against the locked
   standard-flow floor.

All three checks are required for the review closeout claim.
