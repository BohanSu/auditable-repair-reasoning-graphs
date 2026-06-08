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

## Layout

```text
standard_flow_300/
  code/       Python source snapshot for the standard-flow and residual-audit path
  results/    GitHub-safe result summaries and row-level tables
  docs/       Scope, metric, and reporting notes
  figures/    Lightweight benchmark figures
```

## Key Evidence

- `standard_flow_300/results/FULL_350_SUMMARY.json`
- `standard_flow_300/results/EC_REA_EVALUATION_SUMMARY_350.json`
- `standard_flow_300/results/STRICT_ACCEPTED_350.csv`
- `standard_flow_300/results/TYPED_RESIDUAL_350.csv`
- `standard_flow_300/results/RESIDUAL_50_CLOSEOUT_QUEUE.csv`

## Environment

Install the lightweight Python dependencies with:

```bash
python -m pip install -r requirements.txt
```

Some scripts can call external model providers and require credentials supplied
through environment variables outside the repository.
