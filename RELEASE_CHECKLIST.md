# Workshop Branch Release Checklist

This branch contains the locked standard-flow 300/350 code set.

## Included

- `standard_flow_300/code`: 63 Python source files.
- `standard_flow_300/results`: locked summary tables and slim row-level CSVs.
- `standard_flow_300/graphs`: 300 accepted terminal DOT files plus a hash
  manifest and validation summary.
- `standard_flow_300/docs`: public method design, reporting, and metric notes.
- `standard_flow_300/figures`: lightweight benchmark figures.

## Verified Counts

| Evidence | Expected value |
|---|---:|
| `standard_flow_300/results/STRICT_ACCEPTED_350.csv` | 300 rows |
| `standard_flow_300/results/TYPED_RESIDUAL_350.csv` | 50 rows |
| `standard_flow_300/results/FULL_350_SUMMARY.json` strict rows | 300 |
| `standard_flow_300/results/FULL_350_SUMMARY.json` provider failures | 0 |
| `standard_flow_300/graphs/manifest.csv` | 300 unique paper-model rows |
| Parseable and strict-structure-valid DOT files | 300 |
| Source-to-release SHA-256 mismatches | 0 |

## Upload Checks

- No soft links.
- No large run directories.
- No local caches or Python bytecode.
- No private absolute paths in public docs/results.
- No credentials or local configuration files.
- `standard_flow_300/docs/STANDARD_FLOW_METHOD_DESIGN.md` is present and
  describes only the locked 300/350 standard-flow state.
