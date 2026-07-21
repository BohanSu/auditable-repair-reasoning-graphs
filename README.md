# Auditable Repair for Scientific Reasoning Graph Extraction

This branch contains the locked standard-flow workshop release for the PEARL
350-row benchmark.

## Branch Scope

This branch is intentionally limited to the locked standard-flow result:

```text
locked standard flow: 300/350 strict rows
typed residual rows: 50
provider-failure rows: 0
```

The strict row rule is:

```text
strict row iff EC/CG = 1.0 and REA = 1.0, with no provider or judge error
```

Rows that fail either metric remain in the 350-row denominator and are written
as typed residuals.

## Layout

```text
standard_flow_300/
  code/       Python source snapshot for the standard-flow and residual-audit path
  results/    GitHub-safe result summaries and row-level tables
  graphs/     The 300 accepted terminal DOT graphs, hash manifest, and audit summary
  docs/       Method design, reporting boundary, and result notes
  figures/    Lightweight benchmark figures
```

## Recommended Reading Order

1. `standard_flow_300/docs/STANDARD_FLOW_METHOD_DESIGN.md`: explains the
   standard-flow method modules and why the locked result is 300/350.
2. `standard_flow_300/docs/CURRENT_RESULT.md`: summarizes the locked result and
   residual taxonomy.
3. `standard_flow_300/docs/SCOPE_AND_REPORTING.md`: gives safe reporting
   language and metric boundaries.
4. `standard_flow_300/docs/FILE_MAP.md`: lists the public release files.

## Key Evidence

- `standard_flow_300/results/FULL_350_SUMMARY.json`
- `standard_flow_300/results/EC_REA_EVALUATION_SUMMARY_350.json`
- `standard_flow_300/results/STRICT_ACCEPTED_350.csv`
- `standard_flow_300/results/TYPED_RESIDUAL_350.csv`
- `standard_flow_300/results/RESIDUAL_50_CLOSEOUT_QUEUE.csv`
- `standard_flow_300/graphs/manifest.csv`
- `standard_flow_300/graphs/summary.json`

## Reporting Boundary

This branch does not contain the separate 350/350 review package. Report this
branch only as:

```text
locked standard-flow result: 300/350 strict rows, 50 typed residual rows
```

## Environment

Install the lightweight Python dependencies with:

```bash
python -m pip install -r requirements.txt
```

Some scripts can call external model providers and require credentials supplied
through environment variables outside the repository.
