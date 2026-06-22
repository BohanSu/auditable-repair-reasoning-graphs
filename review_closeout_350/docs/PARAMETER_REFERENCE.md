# Current Version Parameter Reference

This file is the exhaustive parameter reference for the public `350/350`
current-version package.

It covers the scripts that participate directly in the released closeout path:

- residual controller and lane dispatch;
- provider-backed regeneration and micro-edit lanes;
- strict-merge candidate construction;
- row-level and batch-level ANS evaluation and guards;
- proposal accounting for the final `23`-row merge set.

Unless noted otherwise, the values below are the defaults exported by the
released `../../code_snapshot/`. They should be read as shipped defaults, not as
proof that no local override was used in the historical run.

## Claim-Locked Settings

These settings are fixed by the public artifacts themselves, not by a CLI flag:

| Item | Value |
|---|---|
| original-version start state | `300/350`, `50` typed residual rows |
| intermediate checkpoint | `327/350`, `23` rows left in the final controller queue |
| final current-version state | `350/350`, `0` typed residual rows |
| final merge rows | `23` |
| strict gate | `final_CG = 1.0`, `final_REA = 1.0`, no provider or judge error |
| row-level guard | merged rows must pass ANS non-regression |
| batch ANS floor | `0.8033815290684022` |
| final batch ANS | `0.8085113065326633` |
| batch ANS margin | `+0.005129777464261132` |
| overwrite policy | `original_version_overwrite = false` |

## 1. `run_residual_50_closure_engine.py`

Main closeout controller. It reads the residual queue, chooses lane-specific
next actions, and can optionally execute local repair, raw regeneration, or
escalation generation.

| Parameter | Default | Meaning |
|---|---|---|
| `--queue` | `DEFAULT_QUEUE` | Input residual queue CSV. |
| `--report-root` | empty | Output directory. Empty means timestamped default root. |
| `--lanes` | `[]` | Restrict execution to selected lanes. |
| `--paper-specs` | `[]` | Restrict execution to selected paper specs. |
| `--paper-specs-file` | empty | File listing paper specs. |
| `--limit` | `0` | Process all selected rows when `0`; otherwise stop at the given row count. |
| `--execute` | `false` | Run executable lanes. Default behavior is dry-run planning. |
| `--skip-local-repair` | `false` | Skip local executable lanes even when `--execute` is on. |
| `--execute-raw-regeneration` | `false` | Execute the raw-regeneration delegation queue. |
| `--execute-source-regeneration-escalation` | `false` | Execute the escalation-generation queue. |
| `--env-file` | `PROJECT_ROOT/.env` | Environment file loaded before provider-backed steps. |
| `--provider-profile` | `reports/pearl_runs/06_runtime_state/provider_preflight_stage1_selected_20260524.json` | Provider/runtime profile consumed by the closeout controller. |
| `--timeout` | `240` | Per-row PEARL batch timeout for local lane execution. |
| `--max-tokens` | `5000` | GPT token budget for local PEARL curation calls. |
| `--gpt-retries` | `2` | Retry count for GPT-based curation inside local execution. |
| `--gpt-retry-sleep` | `8.0` | Sleep between GPT retry attempts. |
| `--gpt-transport` | env `GPT55_CURATION_TRANSPORT` or `curl` | HTTP transport for GPT-based curation. |
| `--gpt-stream` | env-driven boolean | Stream GPT responses unless disabled by environment. |
| `--max-prune-rounds` | `3` | Maximum local prune rounds before giving up on a row. |
| `--judge-error-retries` | `2` | Retry count for judge parse/error failures. |
| `--judge-error-retry-sleep` | `5.0` | Sleep between judge-error retries. |
| `--eval-raw-http-models` | `o3` | Judge model keys routed through raw HTTP. |
| `--candidate-graph-overrides` | empty | JSON mapping/list of `paper_spec -> existing .dot` candidate overrides. |
| `--candidate-frontier` | `DEFAULT_CANDIDATE_FRONTIER` | Audited frontier CSV injected as a coverage guard. |
| `--no-frontier-guard` | `false` | Disable frontier injection even if the CSV exists. |
| `--inner-workers` | `6` | Internal worker count for local lane execution. |
| `--min-anchor-correct-ratio` | `0.0` | Minimum raw anchor-correct ratio required before local repair. |
| `--max-repair-candidate-ratio` | `1.0` | Maximum rejected/total ratio allowed for local repair. |
| `--min-raw-cg` | `0.0` | Minimum raw CG required before local repair. |
| `--escalation-generation-model` | `gpt-5.5` | Provider model used for escalation generation. |
| `--escalation-fallback-generation-models` | empty | Comma-separated fallback escalation models. |
| `--escalation-generation-timeout` | `600.0` | Timeout for escalation-generation requests. |
| `--escalation-generation-retries` | `2` | Retry count for escalation-generation requests. |
| `--escalation-generation-retry-sleep` | `12.0` | Sleep between escalation-generation retries. |

## 2. `run_evidence_bound_graph_spec_regeneration.py`

Provider-backed regeneration runner for rows that need a new source-grounded
graph spec.

| Parameter | Default | Meaning |
|---|---|---|
| `--packet-index` | `DEFAULT_PACKET_INDEX` | Regeneration packet index JSON/CSV. |
| `--attempts-root` | `DEFAULT_ATTEMPTS_ROOT` | Root directory for generation attempts. |
| `--staging-root` | `DEFAULT_STAGING_ROOT` | Root directory for strict-gate staging outputs. |
| `--lanes` | `[]` | Restrict to selected packet lanes. |
| `--paper-specs` | `[]` | Restrict to selected paper specs. |
| `--paper-specs-file` | empty | File listing paper specs. |
| `--limit` | `0` | Process all selected packets when `0`; otherwise stop at the given count. |
| `--model` | `gpt-5.5` | Provider model for regeneration. |
| `--execute` | `false` | Execute provider calls. Default behavior is provider-free planning. |
| `--materialize-existing` | `true` | Materialize already available results when possible. |
| `--copy-staging` | `true` | Copy final outputs into staging directories. |
| `--env-file` | `PROJECT_ROOT/.env` | Environment file for provider credentials. |
| `--base-url` | empty | Provider base URL override. |
| `--api-key-env` | `OPENAI_API_KEY` | Environment variable used for API key lookup. |
| `--timeout` | `600.0` | Request timeout. |
| `--max-tokens` | `5000` | Token budget per regeneration call. |
| `--retries` | `1` | Retry count for provider calls. |
| `--retry-sleep` | `8.0` | Sleep between provider retries. |
| `--transport` | env `GPT55_EBR_TRANSPORT` or `curl` | HTTP transport layer. |
| `--stream` | `false` | Stream provider responses. |

## 3. `run_evidence_bound_micro_edit_patch.py`

Provider-backed micro-edit runner for narrow local graph fixes.

| Parameter | Default | Meaning |
|---|---|---|
| `--packet-index` | `DEFAULT_PACKET_INDEX` | Micro-edit packet index. |
| `--attempts-root` | `DEFAULT_ATTEMPTS_ROOT` | Root directory for edit attempts. |
| `--staging-root` | `DEFAULT_STAGING_ROOT` | Root directory for staged candidate outputs. |
| `--limit` | `0` | Process all packets when `0`; otherwise stop at the given count. |
| `--model` | `gpt-5.5` | Provider model for micro-edit generation. |
| `--execute` | `false` | Execute provider calls. Default behavior is provider-free planning. |
| `--materialize-existing` | `true` | Materialize already available outputs when possible. |
| `--copy-staging` | `true` | Copy staged outputs into final staging locations. |
| `--env-file` | `PROJECT_ROOT/.env` | Environment file for provider credentials. |
| `--base-url` | empty | Provider base URL override. |
| `--api-key-env` | `OPENAI_API_KEY` | Environment variable used for API key lookup. |
| `--timeout` | `600.0` | Request timeout. |
| `--max-tokens` | `1200` | Token budget per micro-edit call. |
| `--retries` | `1` | Retry count for provider calls. |
| `--retry-sleep` | `8.0` | Sleep between provider retries. |
| `--transport` | `curl` | HTTP transport layer. |

## 4. `run_evidence_bound_local_window_patch.py`

Provider-free materializer for a precomputed local patch.

| Parameter | Default | Meaning |
|---|---|---|
| `--source-graph-spec` | required | Input graph spec to patch. |
| `--source-packet` | required | Source packet describing the patch context. |
| `--source-stage-dir` | required | Stage directory used as the patch source context. |
| `--patch` | required | Patch file or payload. |
| `--allowed-targets` | required | Nodes/edges that the patch is allowed to touch. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for staged patched candidates. |
| `--label` | required | Candidate label written into outputs. |
| `--paper-spec` | required | Paper spec for the patched row. |
| `--model` | `constraint-local-v2` | Label for this local patch mode. |
| `--copy-staging` | `true` | Copy the patched candidate into the staging directory. |

## 5. `build_after327_failure_typed_controller_queue.py`

Provider-free builder for the final `23`-row controller queue.

| Parameter | Default | Meaning |
|---|---|---|
| `--residual-csv` | `DEFAULT_RESIDUAL_CSV` | Input residual CSV at the after-327 checkpoint. |
| `--summary-json` | `DEFAULT_SUMMARY_JSON` | Input summary JSON for the after-327 checkpoint. |
| `--attempt-root` | `DEFAULT_ATTEMPT_ROOT` | Root directory holding attempt artifacts. |
| `--runs-root` | `DEFAULT_RUNS_ROOT` | Root directory for closeout runs. |
| `--ans-guard-root` | `DEFAULT_ANS_GUARD_ROOT` | Root directory for row-level ANS guard outputs. |
| `--strict-candidate-root` | `DEFAULT_STRICT_CANDIDATE_ROOT` | Root directory for strict-merge candidate artifacts. |
| `--ans-eval-root` | `DEFAULT_ANS_EVAL_ROOT` | Root directory for ANS evaluation artifacts. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output directory for the controller queue package. |

## 6. `build_residual_50_closeout_package.py`

This exported planner has no CLI flags in the public package. It writes the
residual queue and lane summary using the hard-coded package roots in the
script.

Hard-coded planning settings exposed by the public package:

| Item | Value |
|---|---|
| acceptance gate `final_CG` | `1.0` |
| acceptance gate `final_REA` | `1.0` |
| `fresh_evaluation_required` | `true` |
| `provider_error_is_not_semantic_failure` | `true` |

## 7. `build_strict_merge_candidates_from_fresh_eval.py`

Converts fresh strict-eval results into strict-merge candidates.

| Parameter | Default | Meaning |
|---|---|---|
| `--fresh-eval-results` | required | Fresh evaluation result JSON. |
| `--accounting` | `DEFAULT_ACCOUNTING` | Canonical accounting CSV used as the source state. |
| `--queue` | `DEFAULT_QUEUE` | Residual queue CSV used for row metadata. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output directory for the strict-merge candidate package. |

Hard-coded acceptance filter inside the script:

- keep only rows with `strict_gate_passed = true`;
- require `judge_provider_error = false`;
- require `CG >= 1.0`;
- require `REA >= 1.0`.

## 8. `build_controller_filtered_strict_merge_candidates.py`

Filters strict-merge candidates through the final controller queue.

| Parameter | Default | Meaning |
|---|---|---|
| `--controller-queue` | `DEFAULT_CONTROLLER` | Controller queue CSV/JSON used as the final filter source. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output directory for the controller-filtered merge set. |

## 9. `build_ans_claims_for_strict_merge_candidates.py`

Builds the ANS claim packets for final merge candidates.

| Parameter | Default | Meaning |
|---|---|---|
| `--merge-candidates` | `DEFAULT_MERGE_CANDIDATES` | Strict-merge candidate package. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output directory for generated ANS claim files. |
| `--window` | `1` | Evidence window around graph units. |
| `--root-window` | `None` | Optional special evidence window for root nodes. |
| `--traversal-depth` | `2` | Graph traversal depth when collecting support context. |
| `--max-evidence-sentences` | `10` | Maximum number of evidence sentences per claim packet. |
| `--max-evidence-chars` | `1800` | Maximum evidence text length per claim packet. |
| `--candidate-label` | `[]` | Restrict to selected candidate labels. |
| `--include-viewpoints` | `true` | Include viewpoint/context statements in claim packets. |
| `--graph-ordered-evidence` | `true` | Preserve graph order when assembling evidence. |

## 10. `evaluate_ans_factscore_style_350.py`

ANS/FActScore-style evaluator used for row-level and batch-level closeout
guards.

| Parameter | Default | Meaning |
|---|---|---|
| `--out-dir` | `DEFAULT_OUT_DIR` | Output directory for ANS results, cache, and summaries. |
| `--env-file` | `None` | Optional environment file for provider credentials. |
| `--model` | `gpt-5.5` | Provider model for ANS decomposition/judgment. |
| `--api-key-env` | `OPENAI_API_KEY` | Environment variable used for API key lookup. |
| `--base-url-env` | `OPENAI_BASE_URL` | Environment variable used for base URL lookup. |
| `--temperature` | `0.0` | Provider temperature. |
| `--max-tokens` | `6000` | Token budget per ANS batch call. |
| `--timeout` | `120` | Request timeout. |
| `--retries` | `3` | Retry count for ANS provider calls. |
| `--retry-sleep` | `2.0` | Sleep between ANS retries. |
| `--batch-size` | `8` | Number of nodes/claims processed per ANS batch. |
| `--workers` | `1` | Number of concurrent ANS workers. |
| `--top-k` | `4` | Number of evidence passages retained per item. |
| `--max-evidence-chars` | `1800` | Maximum evidence text length per node. |
| `--max-facts-per-node` | `6` | Maximum atomic facts extracted from a node. |
| `--limit-nodes-per-stage` | unset | Optional limit on nodes processed per stage. |
| `--limit-papers-per-stage` | unset | Optional limit on papers processed per stage. |
| `--only-stage` | append list | Restrict evaluation to selected stages. |
| `--custom-stage` | `[]` | Additional `name=claims_input.jsonl` stages. |
| `--exclude-model` | `[]` | Additional model IDs to exclude. |
| `--summary-only` | `false` | Only write summary from existing results. |
| `--combined-judge` | `false` | Use one combined provider call for extraction and support judgment. |

Additional hard-coded defaults in the exported script:

| Item | Value |
|---|---|
| default excluded models | `gpt_5_4`, `gpt_5_5` |

## 11. `build_strict_merge_ans_guard_report.py`

Provider-free row-level ANS guard reporter.

| Parameter | Default | Meaning |
|---|---|---|
| `--merge-candidates` | `DEFAULT_MERGE_CANDIDATES` | Strict-merge candidate package. |
| `--ans-summary` | `DEFAULT_ANS_SUMMARY` | ANS summary CSV used to score candidates. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output directory for row-level ANS guard reports. |

## 12. `build_strict_merge_proposal_350.py`

Provider-free builder for the proposed `350`-row accounting package.

| Parameter | Default | Meaning |
|---|---|---|
| `--accounting` | `DEFAULT_ACCOUNTING` | Canonical accounting CSV used as the source state. |
| `--provenance` | `DEFAULT_PROVENANCE` | Canonical provenance CSV. |
| `--merge-candidates` | `DEFAULT_MERGE_CANDIDATES` | Final strict-merge candidate package. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output directory for the proposal package. |

Non-CLI policy in the exported script:

- writes a proposal package only;
- does not rewrite canonical accounting in place.

## 13. `build_proposal_batch_ans_guard.py`

Provider-free batch ANS guard for a proposed accounting package.

| Parameter | Default | Meaning |
|---|---|---|
| `--proposal-root` | required | Proposed accounting package root. |
| `--canonical-accounting` | `DEFAULT_CANONICAL_ACCOUNTING` | Canonical accounting CSV used as the comparison baseline. |
| `--base-ans-node-results` | `DEFAULT_ANS_NODE_RESULTS` | Base ANS results used to compute the original-version floor. |
| `--stage` | `pearl_terminal_graph` | ANS stage used for aggregation. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output directory for the batch ANS guard package. |

## What This File Does Not Prove

This file records every exported CLI parameter for the released `350/350`
pipeline scripts. It does not prove that every historical run used only the
default values above. Exact override-free command provenance would require the
external run directories that were intentionally excluded from the GitHub-ready
package.
