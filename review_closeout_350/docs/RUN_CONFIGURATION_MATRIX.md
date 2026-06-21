# Current Version Run Configuration Matrix

This file records the run-control settings that can still be recovered from the
GitHub-ready `350/350` closeout package.

It separates three cases:

- `artifact-locked`: fixed by the public closeout evidence itself;
- `exported default`: exposed by the shipped script, but the lightweight
  package does not prove that no local override was used in the historical run;
- `not recoverable`: not retained in the public package.

## Artifact-Locked Settings

| Item | Value | Status |
|---|---|---|
| start state | original version `300/350`, `50` typed residual rows | artifact-locked |
| internal checkpoint | `327/350`, `23` rows left in the final controller queue | artifact-locked |
| final current-version state | `350/350`, `0` typed residual rows | artifact-locked |
| merge count | `23` final merge rows | artifact-locked |
| strict gate | `final_CG = 1.0`, `final_REA = 1.0`, no provider or judge error | artifact-locked |
| row guard | every merged row passes row-level ANS non-regression | artifact-locked |
| batch guard floor | original-version ANS floor `0.8033815290684022` | artifact-locked |
| batch guard result | current-version ANS `0.8085113065326633`, margin `+0.005129777464261132`, guard passed | artifact-locked |
| overwrite policy | `original_version_overwrite = false` | artifact-locked |

## Script Matrix

| Script | Role | Provider calls | Recoverable settings |
|---|---|---|---|
| `build_after327_failure_typed_controller_queue.py` | Provider-free builder for the final `23`-row controller queue. | no | path-only CLI: `--residual-csv`, `--summary-json`, `--attempt-root`, `--runs-root`, `--ans-guard-root`, `--strict-candidate-root`, `--ans-eval-root`, `--out-root`. No loop-count or threshold flag is exposed at the CLI layer. |
| `run_residual_50_closure_engine.py` | Main closeout controller and launcher for lane-specific next actions. | yes | exported defaults: dry-run by default (`--execute false`, `--execute-raw-regeneration false`, `--execute-source-regeneration-escalation false`), `--timeout 240`, `--max-tokens 5000`, `--gpt-retries 2`, `--gpt-retry-sleep 8.0`, `--gpt-transport` default from `GPT55_CURATION_TRANSPORT` or `curl`, `--gpt-stream` default from `GPT55_CURATION_STREAM`, `--max-prune-rounds 3`, `--judge-error-retries 2`, `--judge-error-retry-sleep 5.0`, `--eval-raw-http-models o3`, `--candidate-frontier` default path enabled unless `--no-frontier-guard` is set, `--inner-workers 6`, `--min-anchor-correct-ratio 0.0`, `--max-repair-candidate-ratio 1.0`, `--min-raw-cg 0.0`, `--escalation-generation-model gpt-5.5`, `--escalation-generation-timeout 600.0`, `--escalation-generation-retries 2`, `--escalation-generation-retry-sleep 12.0`. |
| `run_evidence_bound_graph_spec_regeneration.py` | Provider-backed regeneration runner for rows that need a new source-grounded graph spec. | yes | exported defaults: `--model gpt-5.5`, `--execute false`, `--materialize-existing true`, `--copy-staging true`, `--timeout 600.0`, `--max-tokens 5000`, `--retries 1`, `--retry-sleep 8.0`, `--transport` default from `GPT55_EBR_TRANSPORT` or `curl`, `--stream false`. |
| `run_evidence_bound_micro_edit_patch.py` | Provider-backed micro-edit runner for narrow local graph fixes. | yes | exported defaults: `--model gpt-5.5`, `--execute false`, `--materialize-existing true`, `--copy-staging true`, `--timeout 600.0`, `--max-tokens 1200`, `--retries 1`, `--retry-sleep 8.0`, `--transport curl`. |
| `run_evidence_bound_local_window_patch.py` | Provider-free local patch materializer for a precomputed patch and allowed target set. | no | required inputs: `--source-graph-spec`, `--source-packet`, `--source-stage-dir`, `--patch`, `--allowed-targets`, `--label`, `--paper-spec`; exported defaults: `--model constraint-local-v2`, `--copy-staging true`. |
| `build_strict_merge_candidates_from_fresh_eval.py` | Converts fresh `1.0 / 1.0` evaluations into strict-merge candidates. | no | required `--fresh-eval-results`; defaults for `--accounting`, `--queue`, `--out-root`. The hard filter is inside the script: keep only rows with `strict_gate_passed = true`, `judge_provider_error = false`, `CG >= 1.0`, `REA >= 1.0`. |
| `build_controller_filtered_strict_merge_candidates.py` | Filters the merge set through the final controller queue. | no | path-only CLI: `--controller-queue`, `--out-root`. No numeric threshold flag is exposed; the filter follows the controller and candidate artifacts. |
| `build_ans_claims_for_strict_merge_candidates.py` | Builds the ANS claim packets for final merge candidates. | no | exported defaults: `--window 1`, `--root-window None`, `--traversal-depth 2`, `--max-evidence-sentences 10`, `--max-evidence-chars 1800`, `--include-viewpoints true`, `--graph-ordered-evidence true`. |
| `evaluate_ans_factscore_style_350.py` | ANS/FActScore-style evaluator for row-level or batch guard stages. | yes | exported defaults: `--model gpt-5.5`, `--temperature 0.0`, `--max-tokens 6000`, `--timeout 120`, `--retries 3`, `--retry-sleep 2.0`, `--batch-size 8`, `--workers 1`, `--top-k 4`, `--max-evidence-chars 1800`, `--max-facts-per-node 6`, `--combined-judge false`. Default excluded models: `gpt_5_4`, `gpt_5_5`. |
| `build_strict_merge_ans_guard_report.py` | Provider-free row-level ANS guard reporter. | no | path-only CLI: `--merge-candidates`, `--ans-summary`, `--out-root`. |
| `build_strict_merge_proposal_350.py` | Provider-free builder for the proposed `350`-row accounting package. | no | path-only CLI: `--accounting`, `--provenance`, `--merge-candidates`, `--out-root`. The script writes a proposal package only and does not rewrite canonical accounting. |
| `build_proposal_batch_ans_guard.py` | Provider-free batch ANS guard for a proposed accounting package. | no | required `--proposal-root`; exported defaults: `--canonical-accounting`, `--base-ans-node-results`, `--stage pearl_terminal_graph`, `--out-root`. |

## What The Public Package Does Not Recover

- the exact historical shell-command sequence used to produce the final `23`
  merged rows;
- any local override of provider endpoint, HTTP transport, or stream mode;
- per-row retry history or queue ordering inside the historical closeout run;
- temporary execution directories that were intentionally excluded from the
  GitHub-ready package.

Use this file as the configuration boundary for the public `350/350` claim. If
exact shell-command provenance is needed, the missing external run directories
must be restored.
