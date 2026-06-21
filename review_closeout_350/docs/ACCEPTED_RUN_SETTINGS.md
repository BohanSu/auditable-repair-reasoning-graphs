# Current Version Accepted Run Settings

This file records the settings that matter for the public `350/350`
current-version claim.

## Locked Claim Settings

| Setting | Accepted value | How it is fixed |
|---|---|---|
| start boundary | original version `300/350`, `50` typed residual rows | locked by the public package |
| internal checkpoint | `327/350`, `23` residual rows remaining | locked by controller and closure-trace artifacts |
| final current-version result | `350/350`, `0` typed residual rows | locked by summary and accounting artifacts |
| final merge rows | `23` | locked by strict-merge verification artifacts |
| overwrite policy | `original_version_overwrite = false` | locked by current-version summary |
| strict gate | `final_CG = 1.0`, `final_REA = 1.0`, no provider or judge error | locked by merge verification and final accounting |
| row-level ANS policy | every final merge row must pass row-level ANS non-regression | locked by the strict merge ANS guard artifacts |
| batch ANS floor | `0.8033815290684022` | locked by `REVIEW_BATCH_ANS_GUARD.json` |
| batch ANS result | `0.8085113065326633`, margin `+0.005129777464261132`, guard passed | locked by `REVIEW_BATCH_ANS_GUARD.json` |

## Locked Final Queue Breakdown

| Failure type in final `23`-row controller queue | Count |
|---|---:|
| `preflight:no_anchor_regenerate` | 13 |
| `final_metric_gate_failed` | 5 |
| `metric_regression` | 4 |
| `final_judge_failed` | 1 |

## Exported Closeout Defaults Still Recoverable

The lightweight package does not preserve one monolithic historical shell
command for the full closeout. What it does preserve is the default behavior of
the shipped closeout scripts.

### Main closeout controller

| Parameter | Exported default |
|---|---|
| `--execute` | `false` |
| `--skip-local-repair` | `false` |
| `--execute-raw-regeneration` | `false` |
| `--execute-source-regeneration-escalation` | `false` |
| `--timeout` | `240` |
| `--max-tokens` | `5000` |
| `--gpt-retries` | `2` |
| `--gpt-retry-sleep` | `8.0` |
| `--max-prune-rounds` | `3` |
| `--judge-error-retries` | `2` |
| `--judge-error-retry-sleep` | `5.0` |
| `--eval-raw-http-models` | `o3` |
| `--inner-workers` | `6` |
| `--min-anchor-correct-ratio` | `0.0` |
| `--max-repair-candidate-ratio` | `1.0` |
| `--min-raw-cg` | `0.0` |
| `--escalation-generation-model` | `gpt-5.5` |
| `--escalation-generation-timeout` | `600.0` |
| `--escalation-generation-retries` | `2` |
| `--escalation-generation-retry-sleep` | `12.0` |
| frontier guard posture | enabled unless `--no-frontier-guard` is passed |

### Provider-backed lane runners

| Script | Exported defaults |
|---|---|
| `run_evidence_bound_graph_spec_regeneration.py` | `model=gpt-5.5`, `execute=false`, `materialize_existing=true`, `copy_staging=true`, `timeout=600.0`, `max_tokens=5000`, `retries=1`, `retry_sleep=8.0`, `stream=false` |
| `run_evidence_bound_micro_edit_patch.py` | `model=gpt-5.5`, `execute=false`, `materialize_existing=true`, `copy_staging=true`, `timeout=600.0`, `max_tokens=1200`, `retries=1`, `retry_sleep=8.0`, `transport=curl` |
| `run_evidence_bound_local_window_patch.py` | provider-free; requires explicit source graph, packet, patch, and allowed targets; exported defaults `model=constraint-local-v2`, `copy_staging=true` |

### Final ANS evaluation defaults

| Parameter | Exported default |
|---|---|
| `--model` | `gpt-5.5` |
| `--temperature` | `0.0` |
| `--max-tokens` | `6000` |
| `--timeout` | `120` |
| `--retries` | `3` |
| `--retry-sleep` | `2.0` |
| `--batch-size` | `8` |
| `--workers` | `1` |
| `--top-k` | `4` |
| `--max-evidence-chars` | `1800` |
| `--max-facts-per-node` | `6` |
| `--combined-judge` | `false` |

## What Is Not Locked By The Public Package

- the exact historical closeout shell-command chain;
- any local override of provider endpoint, HTTP transport, or stream mode;
- exact retry history for individual residual rows;
- temporary queue directories and local caches that were excluded from the
  GitHub-ready package.

For the public current-version claim, the locked settings are the state
boundary, the `23`-row merge count, the strict gate, the row-level ANS rule,
the batch ANS floor, and the final overwrite policy. Everything else should be
treated as exported default behavior unless the missing external run directory
is restored.
