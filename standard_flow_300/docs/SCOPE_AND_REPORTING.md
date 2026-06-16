# Scope and Reporting

This file defines how to report the original-version state.

## Scope

The benchmark contains 350 rows, organized as five model groups with 70 rows
each. Every row remains in the denominator.

## Strict Row Rule

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

`EC/CG` is the evaluator's coverage and grounding metric. `REA` is the
reasoning-edge accuracy metric. A row must pass both metrics to be counted as
strict.

## Reportable Statement

```text
On the 350-row benchmark, the PEARL original version accounts for all rows
with zero provider-failure rows and reaches 300 strict successes. The remaining
50 rows are typed residuals. Final EC/CG improves from 0.8274 to 0.8961, and
final REA improves from 0.3386 to 0.9062.
```

## Do Not Conflate States

The original-version result is:

```text
300/350 strict rows
```

The current-version state is separate:

```text
350/350 strict rows, original-version overwrite disabled
```

Use `review_closeout_350` only when discussing the current-version evidence.

## Primary Evidence

- `results/FULL_350_SUMMARY.json`
- `results/EC_REA_EVALUATION_SUMMARY_350.json`
- `results/STRICT_ACCEPTED_350.csv`
- `results/TYPED_RESIDUAL_350.csv`
- `results/RESIDUAL_50_CLOSEOUT_QUEUE.csv`

## ANS Reporting

Atomic Node Support is a grounding audit inspired by FActScore-style atomic
fact verification. It is used to check whether graph nodes are source-supported.
It is not the strict closure metric.

The PEARL original-version terminal graph has all-node ANS `0.801003` and main factual
node ANS `0.8033754732721555`.
