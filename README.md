# Auditable Repair for Scientific Reasoning Graph Extraction

This is a GitHub-ready code and audit release for the PEARL 350-row benchmark.
It keeps the source-code snapshots and lightweight result evidence needed to
explain two distinct states:

- `standard_flow_300/`: the locked standard-flow result. It contains 300 strict
  successes out of 350 rows, 50 typed residual rows, and zero provider-failure
  rows in the locked evaluation record.
- `review_closeout_350/`: the review closeout result. It starts from the locked
  300/350 state, uses an intermediate 327/350 checkpoint, verifies a final
  23-row merge set, and reaches a review-only 350/350 state with locked-record
  write disabled.

These two directories must not be treated as the same claim. The first is the
locked standard-flow record; the second is a separately audited closeout state.

## Layout

```text
standard_flow_300/
  code/       Standard-flow and residual-audit Python source snapshot
  results/    GitHub-safe result summaries and row-level tables
  docs/       Public scope, metric, and reporting notes
  figures/    Lightweight figures for the 350-row benchmark

review_closeout_350/
  code/       Closeout Python source snapshot
  results/    Controller, merge, review-accounting, and ANS-guard evidence
  docs/       Public design, validation, and reproduction notes
  figures/    Final vector framework overview
```

The full run directories, provider logs, local caches, and machine-specific
paths are intentionally excluded. The release contains real files only; no soft
links are used.

## Result Boundary

| State | Strict rows | Residual rows | Role |
|---|---:|---:|---|
| locked standard flow | 300/350 | 50 | Reportable standard-flow record |
| intermediate review checkpoint | 327/350 | 23 | Checkpoint before final closeout merge |
| review closeout | 350/350 | 0 | Audited closeout state, locked-record write disabled |

The strict row rule is:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

`EC/CG` and `REA` are the evaluator metrics used by the project for graph-level
coverage/grounding and reasoning-edge validity. `ANS` is a FActScore-style
atomic node support audit used as a grounding guard; it is not a replacement for
the strict EC/CG and REA gate.

## Main Entry Points

- Read `METRICS_AND_WORKFLOW.md` first for the metric definitions and stage
  logic.
- Use `standard_flow_300/results/FULL_350_SUMMARY.json` for the locked 300/350
  summary.
- Use `review_closeout_350/results/REVIEW_FULL_350_SUMMARY.json` for the
  review-only 350/350 summary.
- Use `review_closeout_350/results/STANDARD_FLOW_350_CLOSURE_TRACE.csv` to see
  how the 300/350, 327/350, and 350/350 states relate.

## Environment

The scripts are Python 3 scripts and use standard-library utilities plus common
scientific Python packages. Some evaluator scripts can call external model
providers; credentials must be configured outside this repository.

Install the lightweight dependencies with:

```bash
python -m pip install -r requirements.txt
```

## Release Policy

This release is prepared for GitHub upload. It excludes large generated
artifacts, transient logs, Python bytecode, private local paths, credentials,
and raw provider outputs.
