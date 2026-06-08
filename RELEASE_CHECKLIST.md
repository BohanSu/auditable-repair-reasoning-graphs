# Release Checklist

This checklist records the GitHub-readiness checks for this package.

## Package Scope

- Release root: `github_ready/pearl_300_350_code_release_20260608`
- Locked standard-flow code set: `standard_flow_300`
- Review closeout code set: `review_closeout_350`
- Full run directories are intentionally excluded.
- No soft links are used.

## Result Boundary

| State | Evidence | Verified value |
|---|---|---:|
| locked strict rows | `standard_flow_300/results/STRICT_ACCEPTED_350.csv` | 300 |
| locked typed residual rows | `standard_flow_300/results/TYPED_RESIDUAL_350.csv` | 50 |
| review accounting rows | `review_closeout_350/results/REVIEW_FULL_350_ACCOUNTING.csv` | 350 |
| review non-strict rows | `review_closeout_350/results/REVIEW_FULL_350_ACCOUNTING.csv` | 0 |
| final merge verification rows | `review_closeout_350/results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv` | 23 |
| review locked-record write | `review_closeout_350/results/REVIEW_FULL_350_SUMMARY.json` | false |
| review batch ANS guard | `review_closeout_350/results/REVIEW_BATCH_ANS_GUARD.json` | passed |

## Content Checks

- Package size: about 3.9 MB.
- File count: 171 files before empty-directory cleanup.
- Python source files: 63 in `standard_flow_300/code`, 63 in `review_closeout_350/code`.
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
  github_ready/pearl_300_350_code_release_20260608/standard_flow_300/code \
  github_ready/pearl_300_350_code_release_20260608/review_closeout_350/code
```

The syntax check passed. `PYTHONPYCACHEPREFIX` was used so bytecode was not
written into the release package.

## Upload Notes

Before uploading, create a Git repository at the release root or copy this
directory into the target repository. Keep credentials, external run artifacts,
local caches, and provider logs outside the repository.
