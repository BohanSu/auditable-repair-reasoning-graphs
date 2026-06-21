# Auditable Repair for Scientific Reasoning Graph Extraction

This is a GitHub-ready code and audit release for the PEARL 350-row benchmark.
It keeps one shared Python source snapshot and two separate audited result
states:

- `standard_flow_300/`: the original-version result. It contains 300 strict
  successes out of 350 rows, 50 typed residual rows, and zero provider-failure
  rows in the original-version evaluation record.
- `review_closeout_350/`: the current-version result. It starts from the original
  300/350 state, uses an intermediate 327/350 checkpoint, verifies a final
  23-row merge set, and reaches a 350/350 current-version state with
  original-version overwrite disabled.

These two directories must not be treated as the same claim. The first is the
original-version result; the second is a separately audited current-version state.

## Layout

```text
code_snapshot/
  Shared Python source snapshot used by both audited states

standard_flow_300/
  results/    GitHub-safe result summaries and row-level tables
  docs/       Public scope, metric, and reporting notes
  figures/    Lightweight figures for the 350-row benchmark

review_closeout_350/
  results/    Controller, merge, current-version accounting, and ANS-guard evidence
  docs/       Public architecture, validation, and reproduction notes
  figures/    Final vector framework overview
```

The two old `code/` copies were identical. They have been collapsed into
`code_snapshot/` so the release has one canonical source snapshot instead of two
parallel copies. The full run directories, provider logs, local caches, and
machine-specific paths are intentionally excluded. The release contains real
files only; no soft links are used.

## Result Boundary

| State | Strict rows | Residual rows | Role |
|---|---:|---:|---|
| original version | 300/350 | 50 | Reportable original-version result |
| intermediate current-version checkpoint | 327/350 | 23 | Checkpoint before final current-version merge |
| current version | 350/350 | 0 | Audited current-version state, original-version overwrite disabled |

The strict row rule is:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

`EC/CG` and `REA` are the evaluator metrics used by the project for graph-level
coverage/grounding and reasoning-edge validity. `ANS` is a FActScore-style
atomic node support audit used as a grounding guard; it is not a replacement for
the strict EC/CG and REA gate.

## Method Architecture

The package has one evaluation contract but two distinct processing paths.

Original version:

```text
raw graph extraction
-> structure repair
-> PEARL semantic repair
-> fresh EC/CG + REA evaluation
-> strict accounting
-> 300/350
```

Current version:

```text
50 typed residual rows
-> failure-typed controller
-> targeted repair lanes
-> candidate preflight
-> fresh EC/CG + REA evaluation
-> row-level ANS guard
-> strict merge review
-> batch ANS guard
-> 350/350
```

The intermediate `327/350` checkpoint belongs to the current-version path. It
explains why the final merge set contains 23 rows; it is not a third benchmark
claim.

## Main Entry Points

- Read `METRICS_AND_WORKFLOW.md` first for the metric definitions and stage
  logic.
- Read `code_snapshot/README.md` for the shared source inventory and script
  grouping.
- Read `standard_flow_300/docs/STANDARD_FLOW_METHOD_DESIGN.md` for the original-version method: raw graph extraction, structure repair, PEARL semantic
  repair, fresh EC/CG+REA evaluation, and accounting.
- Read `review_closeout_350/docs/METHOD_ARCHITECTURE.md` for the
  current-version residual repair method: controller routing, targeted repair
  lanes, fresh evaluation, merge review, and ANS guard.
- Use `standard_flow_300/results/FULL_350_SUMMARY.json` for the original 300/350 summary.
- Use `review_closeout_350/results/REVIEW_FULL_350_SUMMARY.json` for the
  350/350 current-version summary.
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
