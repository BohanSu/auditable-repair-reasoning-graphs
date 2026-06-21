# Release Checklist

This checklist records the GitHub-readiness checks for this package.

## Package Scope

- Release root: `deliverables/github_ready/pearl_300_350_code_release_20260608`
- Shared code snapshot: `code_snapshot/`
- Original-version result state: `standard_flow_300/`
- Current-version result state: `review_closeout_350/`
- Duplicate `code/` copies were removed; both states now point to the same
  exported source snapshot.
- Full run directories are intentionally excluded.
- No soft links are used.

## Result Boundary

| State | Evidence | Verified value |
|---|---|---:|
| original-version strict rows | `standard_flow_300/results/STRICT_ACCEPTED_350.csv` | 300 |
| original-version typed residual rows | `standard_flow_300/results/TYPED_RESIDUAL_350.csv` | 50 |
| current-version accounting rows | `review_closeout_350/results/REVIEW_FULL_350_ACCOUNTING.csv` | 350 |
| current-version non-strict rows | `review_closeout_350/results/REVIEW_FULL_350_ACCOUNTING.csv` | 0 |
| final merge verification rows | `review_closeout_350/results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv` | 23 |
| current-version overwrites original version | `review_closeout_350/results/REVIEW_FULL_350_SUMMARY.json` | false |
| current-version batch ANS guard | `review_closeout_350/results/REVIEW_BATCH_ANS_GUARD.json` | passed |

## Content Checks

- Package size: about 2.2 MB.
- File count: 110 files.
- Python source files: 63 in `code_snapshot/`.
- Canonical method-design docs are present for both audited states:
  `standard_flow_300/docs/STANDARD_FLOW_METHOD_DESIGN.md` and
  `review_closeout_350/docs/METHOD_ARCHITECTURE.md`.
- No files larger than 10 MB.
- No `.pyc`, `.DS_Store`, `.log`, `.tmp`, `.bak`, or editor backup files in the release directory.
- No symlinks in the release directory.
- Public docs/results do not contain private absolute paths.
- Public docs/results do not use old internal review-file names.

## Syntax Check

The code snapshots were syntax-checked with:

```bash
PYTHONPYCACHEPREFIX="$(mktemp -d)" \
python3 -m compileall -q \
  deliverables/github_ready/pearl_300_350_code_release_20260608/code_snapshot
```

The syntax check passed. `PYTHONPYCACHEPREFIX` was used so bytecode was not
written into the release package.

## Upload Notes

Before uploading, create a Git repository at the release root or copy this
directory into the target repository. Keep credentials, external run artifacts,
local caches, and provider logs outside the repository.
