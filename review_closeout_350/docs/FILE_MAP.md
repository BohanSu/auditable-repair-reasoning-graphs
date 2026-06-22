# Current Version File Map

This is the public file map for the GitHub-ready `review_closeout_350`
directory. It describes the lightweight acceptance package only.

| Path | Purpose |
|---|---|
| `README.md` | Overview of the `350/350` current-version acceptance package. |
| `../../code_snapshot/` | Shared Python source snapshot used by both audited states. |
| `figures/framework_v2.svg` | Visual summary of the current-version closeout pipeline. |
| `results/REVIEW_FULL_350_SUMMARY.json` | Final current-version summary. |
| `results/REVIEW_FULL_350_ACCOUNTING.csv` | Row-level current-version accounting table. |
| `results/STANDARD_FLOW_350_CLOSURE_TRACE.csv` | Trace linking the `300/350`, `327/350`, and `350/350` states. |
| `results/AFTER327_CONTROLLER_QUEUE.csv` | Final 23-row controller queue. |
| `results/AFTER327_CONTROLLER_SUMMARY.json` | Controller counts and queue summary. |
| `results/STRICT_MERGE_CANDIDATES.csv` | Final strict-merge candidate table before merge review. |
| `results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv` | Fresh-eval and artifact verification for the final merge candidates. |
| `results/STRICT_MERGE_REVIEW_AUDIT.csv` | Row-level strict merge audit. |
| `results/STRICT_MERGE_REVIEW_AUDIT.json` | Structured merge-audit record. |
| `results/REVIEW_BATCH_ANS_GUARD.json` | Batch ANS guard for the `350/350` current-version state. |
| `results/REVIEW_BATCH_ANS_GUARD.csv` | Flat batch ANS guard table. |
| `results/PACKAGE_MANIFEST.json` | Lightweight manifest describing the public acceptance package. |
| `docs/METHOD_ARCHITECTURE.md` | Canonical current-version method architecture. |
| `docs/ACCEPTED_RUN_SETTINGS.md` | Locked claim settings and recoverable closeout defaults. |
| `docs/RUN_CONFIGURATION_MATRIX.md` | Script-by-script configuration inventory for the closeout package. |
| `docs/PARAMETER_REFERENCE.md` | Exhaustive CLI parameter reference for the released `350/350` pipeline scripts. |
| `docs/VALIDATION_SUMMARY.md` | Final acceptance checks and validated counts. |
| `docs/FILE_MAP.md` | This public file inventory. |
| `docs/REPRODUCTION_COMMANDS.md` | Lightweight audit commands for the public package. |
| `docs/SOURCE_POINTERS.md` | Included and excluded artifact boundary. |

The full per-paper graphs, evaluator directories, provider logs, and local
caches are intentionally outside this GitHub-ready package.
