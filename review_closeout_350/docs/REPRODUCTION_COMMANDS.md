# Reproduction and Audit Commands

This GitHub-ready package contains lightweight public artifacts. The commands
below audit the included files. Full regeneration requires the external graph,
evidence, and evaluator artifacts expected by the source scripts.

Run commands from the release root:

```bash
cd github_ready/pearl_300_350_code_release_20260608
```

## Check Locked Standard-Flow Summary

```bash
python - <<'PY'
import json
from pathlib import Path

p = Path("standard_flow_300/results/FULL_350_SUMMARY.json")
d = json.loads(p.read_text())
print({
    "rows": d["requested_rows"],
    "strict_success_rows": d["strict_success_rows"],
    "typed_residual_rows": d["typed_residual_rows"],
    "provider_failures": d["current_api_provider_failures"],
    "final_CG_avg": d["final_CG_avg"],
    "final_REA_avg": d["final_REA_avg"],
})
PY
```

Expected:

```text
strict_success_rows = 300
typed_residual_rows = 50
provider_failures = 0
```

## Check Review Closeout Summary

```bash
python - <<'PY'
import json
from pathlib import Path

p = Path("review_closeout_350/results/REVIEW_FULL_350_SUMMARY.json")
d = json.loads(p.read_text())
print({
    "accounted_rows": d["accounted_rows"],
    "strict_success_rows": d["strict_success_rows"],
    "typed_residual_rows": d["typed_residual_rows"],
    "final_CG_avg": d["final_CG_avg"],
    "final_REA_avg": d["final_REA_avg"],
    "merged_row_count": d["merged_row_count"],
    "locked_record_write": d["locked_record_write"],
})
PY
```

Expected:

```text
strict_success_rows = 350
typed_residual_rows = 0
merged_row_count = 23
locked_record_write = false
```

## Check Row-Level Review Accounting

```bash
python - <<'PY'
import csv
from pathlib import Path

p = Path("review_closeout_350/results/REVIEW_FULL_350_ACCOUNTING.csv")
rows = list(csv.DictReader(p.open()))
print({
    "rows": len(rows),
    "non_strict_rows": sum(r["strict_gate_passed"].lower() != "true" for r in rows),
    "non_1_CG": sum(abs(float(r["final_CG"]) - 1.0) > 1e-12 for r in rows),
    "non_1_REA": sum(abs(float(r["final_REA"]) - 1.0) > 1e-12 for r in rows),
})
PY
```

Expected:

```text
rows = 350
non_strict_rows = 0
non_1_CG = 0
non_1_REA = 0
```

## Check Final Merge Candidates

```bash
python - <<'PY'
import csv
from pathlib import Path

p = Path("review_closeout_350/results/STRICT_MERGE_CANDIDATE_VERIFICATION.csv")
rows = list(csv.DictReader(p.open()))
print({
    "rows": len(rows),
    "all_graph_exists": all(r["graph_exists"] == "True" for r in rows),
    "all_eval_dir_exists": all(r["eval_dir_exists"] == "True" for r in rows),
    "all_fresh_eval_exists": all(r["fresh_eval_exists"] == "True" for r in rows),
    "all_hash_matches": all(r["fresh_eval_hash_matches"] == "True" for r in rows),
    "all_mergeable_verified": all(r["mergeable_verified"] == "True" for r in rows),
})
PY
```

Expected:

```text
rows = 23
all_hash_matches = true
all_mergeable_verified = true
```

## Check Batch ANS Guard

```bash
python - <<'PY'
import json
from pathlib import Path

p = Path("review_closeout_350/results/REVIEW_BATCH_ANS_GUARD.json")
d = json.loads(p.read_text())
print({
    "locked_floor": d["locked_standard_flow_ans_floor"]["ans"],
    "review_ans": d["review_state_with_candidate_ans_replacements"]["ans"],
    "margin": d["review_batch_ans_guard_margin"],
    "passed": d["review_batch_ans_guard_passed"],
})
PY
```

Expected:

```text
passed = true
margin = 0.005129777464261132
```

## Provider-Calling Scripts

Some scripts in `code/` can call external providers. They require credentials in
environment variables outside the repository. Provider or quota errors should be
treated as execution failures, not as semantic graph failures, and should not be
merged into review results.
