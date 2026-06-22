# Current Version Parameter Reference

This file is the full parameter and fixed-setting reference for the public
`350/350` current-version package.

It covers the shipped scripts on the current-version closeout path in
`../../code_snapshot/`, including:

- residual planning and controller routing;
- provider-backed regeneration, micro-edit, and raw-regeneration wrappers;
- packet builders, preflight utilities, and after-`327/350` diagnostics;
- strict-merge candidate construction, ANS guards, and proposal accounting;
- lane-specific materializers and replay utilities that are still part of the
  released closeout source tree.

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

## Default Path Roots

Many defaults below are expressed in script code through shared root constants.
The two roots that matter for the public current-version package are:

| Symbol | Release-relative path |
|---|---|
| `PACKAGE_ROOT` | `reports/pearl_runs/00_CURRENT_STANDARD_FLOW_20260527/standard_flow_490_clean_package_20260528/10_standard_flow_350_subset_package` |
| `RESIDUAL_ROOT` | `PACKAGE_ROOT/09_residual_50_closeout` |

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

## 14. Residual Planning and Frontier Utilities

### `build_residual_closeout_v2_plan.py`

Provider-free residual ledger builder for the next closeout iteration.

| Parameter | Default | Meaning |
|---|---|---|
| `--base-accounting` | `DEFAULT_BASE_ACCOUNTING` | Best proposal accounting CSV used as the current working state. |
| `--base-summary` | `DEFAULT_BASE_SUMMARY` | Summary JSON paired with the working accounting state. |
| `--canonical-accounting` | `DEFAULT_CANONICAL_ACCOUNTING` | Canonical full accounting CSV used as the fixed benchmark baseline. |
| `--missing-entity-tasks` | `DEFAULT_MISSING_ENTITY_TASKS` | Missing-entity patch task table. |
| `--missing-entity-row-audit` | `DEFAULT_MISSING_ENTITY_ROW_AUDIT` | Row audit paired with the missing-entity task table. |
| `--packet-index` | `DEFAULT_PACKET_INDEX` | Regeneration packet index used to connect residual rows to packet artifacts. |
| `--ans-node-results` | `DEFAULT_ANS_NODE_RESULTS` | Full ANS node-result cache used to compute support deltas. |
| `--fresh-eval-results` | `DEFAULT_FRESH_EVAL_RESULTS` | Fresh strict-eval results used as the current closeout signal. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the v2 residual design package. |

Hard-coded script policy:

- provider-free only; no canonical accounting rewrite;
- `EXCLUDED_ANS_UNIT_TYPES = {root_common_bridge, graph_node}`;
- `MAIN_FACTUAL_STAGES = {raw_step1_extraction, llm_step2_self_fix_final_clean, pearl_terminal_graph}`;
- default working proposal root is `20260606_after_315_gemini56657_entity_complete_anssafe`;
- default output root is `closeout_design/20260606_residual_closeout_v2`.

### `extract_residual_50_candidate_frontier.py`

Offline frontier extractor for executed closeout runs.

| Parameter | Default | Meaning |
|---|---|---|
| `--runs-root` | `DEFAULT_RUNS_ROOT` | Executed closeout-run root to scan. |
| `--out-json` | empty | Output JSON path. Empty means `CANDIDATE_FRONTIER.json` under `--runs-root`. |
| `--out-csv` | empty | Output CSV path. Empty means `CANDIDATE_FRONTIER.csv` under `--runs-root`. |

Hard-coded ranking policy:

- sort by `paper_spec`;
- then higher `CG`;
- then higher `REA`;
- then fewer noncorrect votes;
- then run name.

### `build_residual_claim_closeout_ledger.py`

Provider-free ledger that lines up residual rows, proposal summary, and
missing-entity audit outputs.

| Parameter | Default | Meaning |
|---|---|---|
| `--residual-csv` | `DEFAULT_RESIDUAL_CSV` | Residual CSV used as the source row set. |
| `--proposed-summary` | `DEFAULT_PROPOSED_SUMMARY` | Proposed summary JSON paired with the residual CSV. |
| `--missing-entity-csv` | `DEFAULT_MISSING_ENTITY_CSV` | Missing-entity audit table. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the closeout ledger package. |

## 15. After-327 Diagnostics and Selector Utilities

### `build_after327_ans_delta_planner.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--controller-queue` | `DEFAULT_CONTROLLER_QUEUE` | Controller queue JSON/CSV used as the diagnostic source. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the ANS-delta planning package. |

Hard-coded typed node family:

- `semantic_root`
- `root_common_bridge`
- `repaired_reasoning_node`
- `implicit_reasoning_node`
- `graph_node`

### `build_after327_ans_regression_contract_audit.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--controller-queue` | `DEFAULT_CONTROLLER_QUEUE` | Controller queue JSON/CSV used as the diagnostic source. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the ANS-regression contract audit. |

Hard-coded typed node family is the same as
`build_after327_ans_delta_planner.py`.

### `build_after327_atomic_contract_ledger.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--controller-queue` | `DEFAULT_CONTROLLER_QUEUE` | Controller queue JSON/CSV used as the row source. |
| `--ans-audit` | `DEFAULT_ANS_AUDIT` | ANS audit package used to build atomic contracts. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the atomic-contract ledger. |

### `build_after327_source_claim_closure_diagnostic.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--controller-queue` | `DEFAULT_CONTROLLER_QUEUE` | Controller queue JSON/CSV used as the row source. |
| `--atomic-ledger` | `DEFAULT_ATOMIC_LEDGER` | Atomic contract ledger used for closure diagnosis. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the source-claim closure diagnostic. |

### `build_after327_claim_evidence_closure_solver.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--controller-queue` | `DEFAULT_CONTROLLER_QUEUE` | Controller queue JSON/CSV used as the hard-residual source. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the claim/evidence closure solver. |
| `--include-routes` | `claim_graph_reconstruction_from_source_inventory,judge_reason_targeted_reconstruction,route_specific_candidate_revision` | Comma-separated route filter for emitted repair instructions. |
| `--packet-root` | append list, default empty | Extra packet roots. Empty means use the script’s five default packet roots. |

Hard-coded script policy:

- scans five default packet roots under `RESIDUAL_ROOT/evidence_bound_regeneration/`;
- uses a fixed stop-token list when matching claims against evidence text;
- remains provider-free and diagnostic-only.

### `build_after327_candidate_ans_gap_diagnostic.py`

This script exposes no CLI flags in the public package. It reads:

- `DEFAULT_CONTROLLER = AFTER327_CONTROLLER_QUEUE.json`
- `DEFAULT_OUT_ROOT = closeout_design/20260606_after327_failure_typed_lit_diagnostic/candidate_ans_gap_diagnostic_v1`

Hard-coded priority ladder:

- `fresh_provider_rerun = 10`
- `ans_provider_rerun = 20`
- `candidate_ans_missing = 30`
- `content_ans_repair = 40`
- `not_ans_gap = 90`

### `run_after327_pareto_safe_selector.py`

Provider-free selector for deterministic rollback and hybrid candidates in the
after-`327/350` metric-regression tail.

| Parameter | Default | Meaning |
|---|---|---|
| `--controller-queue` | `DEFAULT_CONTROLLER_QUEUE` | After-327 controller queue JSON. |
| `--accounting` | `DEFAULT_ACCOUNTING` | Residual accounting CSV used as the state source. |
| `--v2-ledger` | `DEFAULT_V2_LEDGER` | Residual v2 ledger used for typed row metadata. |
| `--queue` | `DEFAULT_QUEUE` | Residual queue CSV. |
| `--ans-node-results` | `DEFAULT_ANS_NODE_RESULTS` | ANS node-result cache used for support filtering. |
| `--ans-cache` | `DEFAULT_ANS_CACHE` | ANS evaluation cache used for evidence retrieval. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for selector candidates. |
| `--paper-spec` | append list | Restrict to selected paper specs. |
| `--inventory-root-mode` | `inventory`, `core` | Inventory modes used to build the source portfolio. |
| `--max-per-entity` | `1` | Maximum candidate leaves retained per entity. |
| `--min-entity-score` | `0.55` | Minimum entity overlap score for a candidate source unit. |
| `--max-step2-nodes` | `8` | Maximum step-2 nodes copied into a hybrid candidate. |
| `--min-step2-support-ratio` | `1.0` | Minimum support ratio for step-2 nodes included in the hybrid. |
| `--max-bridge-nodes` | `12` | Maximum bridge nodes retained in the hybrid candidate. |
| `--include-source-seed` | `true` | Include compact source-seed candidates. |
| `--max-source-seed-entities` | `14` | Maximum entities used in a source-seed rebuild. |
| `--top-per-paper` | `2` | Maximum selector outputs retained per paper. |
| `--window` | `1` | Evidence window for ANS-backed claim construction. |
| `--root-window` | `None` | Optional special root-node evidence window. |
| `--traversal-depth` | `2` | Graph traversal depth for evidence collection. |
| `--max-evidence-sentences` | `10` | Maximum evidence sentences per packet. |
| `--max-evidence-chars` | `1800` | Maximum evidence text length per packet. |
| `--include-viewpoints` | `true` | Include viewpoint/context statements. |
| `--graph-ordered-evidence` | `true` | Keep graph order when assembling evidence. |

## 16. Packet Builders and Preflight Generators

### `build_evidence_bound_regeneration_packets.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--queue` | `DEFAULT_QUEUE` | Residual closeout queue CSV. |
| `--candidate-frontier` | `DEFAULT_FRONTIER` | Frontier CSV injected as a coverage guard. |
| `--report-root` | `DEFAULT_OUT_ROOT` | Output root for generated packet bundles. |
| `--lanes` | `[]` | Restrict to selected controller lanes. |
| `--paper-specs` | `[]` | Restrict to selected paper specs. |
| `--paper-specs-file` | empty | File listing paper specs. |
| `--limit` | `0` | Process all rows when `0`; otherwise stop at the given count. |
| `--top-k-evidence` | `4` | Number of evidence sentences retained per entity/claim unit. |
| `--min-entity-score` | `1.2` | Minimum entity-overlap score for kept evidence. |
| `--max-prompt-sentences` | `32` | Maximum evidence sentences inserted into the regeneration prompt. |
| `--copy-input-data` | `false` | Copy input-data payloads into packet directories. |
| `--overwrite` | `true` | Overwrite existing packet outputs. |

Hard-coded script policy:

- standard edge vocabulary is fixed to the six PEARL edge types;
- Unicode and Greek-symbol alias normalization is enabled for evidence matching.

### `build_evidence_bound_feedback_regeneration_packets.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--fresh-eval-results` | `DEFAULT_FRESH_EVAL_RESULTS` | Fresh-eval results used to build feedback packets. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for feedback regeneration packets. |
| `--paper-specs` | `[]` | Restrict to selected paper specs. |
| `--paper-specs-file` | empty | File listing paper specs. |

### `build_evidence_bound_micro_edit_packets.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--selection-ledger` | `DEFAULT_LEDGER` | Candidate-selection ledger used to build micro-edit packets. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for micro-edit packets. |
| `--targets` | `[]` | Restrict to selected target IDs. |

### `build_evidence_bound_candidate_selection_ledger.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--candidate` | append list, default empty | Candidate spec as `label=FRESH_EVAL_RESULTS.json`. Empty means use the script’s default candidate set. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the candidate-selection ledger. |

Hard-coded default candidate labels:

- `r00_initial_evidence_bound_smoke`
- `r01_feedback_from_smoke`
- `r02_feedback_from_r01`

### `build_targeted_unit_prune_attempt.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--attempt-index` | required | Input attempt index JSON. |
| `--out-attempts-root` | required | Output root for the targeted prune attempt. |
| `--paper-spec` | required | Paper spec for the repaired row. |
| `--target-node` | required | Graph node to prune or isolate. |
| `--model` | `gpt-5.5` | Candidate metadata model label. |
| `--candidate-label` | `targeted_unit_prune_v1` | Candidate label written into outputs. |
| `--rationale` | `remove a fresh-judge rejected non-essential bridge while preserving coverage` | Human-readable rationale stored with the attempt. |

### `build_frontier_micro_merge_candidate.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--frontier-graph` | required | Frontier graph used as the coverage donor. |
| `--donor-graph` | required | Base graph used as the merge target. |
| `--targets` | required | Target nodes/edges to import from the frontier graph. |
| `--out-dir` | required | Output directory for the merged candidate. |
| `--prefix` | `frontier_micro_merge` | Output-file prefix. |

### `build_no_anchor_after_notation_packets.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--ledger` | `DEFAULT_LEDGER` | Residual ledger used to select no-anchor rows. |
| `--accounting` | `DEFAULT_ACCOUNTING` | Accounting CSV used as the graph-state source. |
| `--ans-node-results` | `DEFAULT_ANS_NODE_RESULTS` | ANS node-result cache used for source support lookup. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for no-anchor packets. |
| `--top-k-evidence` | `5` | Number of evidence sentences retained per unit. |
| `--max-prompt-sentences` | `34` | Maximum evidence sentences inserted into the prompt. |

Hard-coded script policy:

- `ANS_STAGES = {raw_step1_extraction, llm_step2_self_fix_final_clean, pearl_terminal_graph}`;
- `EXCLUDED_ANS_UNIT_TYPES = {root_common_bridge, graph_node}`.

## 17. Fresh-Eval and Rerun Wrappers

### `evaluate_evidence_bound_staged_candidate.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--attempt-index` | `DEFAULT_ATTEMPT_INDEX` | Attempt index JSON used to locate staged candidates. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for fresh-eval results. |
| `--paper-specs` | `[]` | Restrict to selected paper specs. |
| `--paper-specs-file` | empty | File listing paper specs. |
| `--candidate-labels` | `[]` | Restrict to selected candidate labels. |
| `--candidate-labels-file` | empty | File listing candidate labels. |
| `--limit` | `0` | Process all candidates when `0`; otherwise stop at the given count. |
| `--env-file` | `PROJECT_ROOT/.env` | Environment file for provider credentials. |
| `--require-preflight-passed` | `true` | Skip candidates whose local preflight did not pass. |
| `--eval-raw-http-models` | empty | Comma-separated evaluator judge model keys routed through raw HTTP. |
| `--eval-inner-max-workers` | `0` | Inner evaluator worker override. `0` keeps evaluator defaults. |
| `--stop-on-provider-error` | `false` | Stop after the first provider-contaminated attempt. |
| `--provider-error-stop-after` | `0` | Stop after `N` provider-contaminated attempts. `0` disables unless `--stop-on-provider-error` is set. |
| `--provider-error-stop-scope` | `consecutive` | Count provider errors consecutively or across the whole batch. |
| `--provider-fail-fast-inner` | unset | Abort unfinished judge tasks after a provider error. Unset means follow `--stop-on-provider-error`. |

### `run_edge_repair_raw_regeneration_residuals.py`

Raw-regeneration wrapper for residual rows that need a new source graph.

| Parameter | Default | Meaning |
|---|---|---|
| `--queue` | `DEFAULT_QUEUE` | Residual queue JSON used as the source row set. |
| `--report-root` | empty | Output directory. Empty means the script’s default run root. |
| `--limit` | `0` | Process all selected rows when `0`; otherwise stop at the given count. |
| `--paper-specs` | `[]` | Restrict to selected paper specs. |
| `--paper-specs-file` | empty | File listing paper specs. |
| `--include-lanes` | `raw_regeneration` | Queue lanes to execute. |
| `--allow-non-raw-residuals` | `false` | Allow source-regeneration escalation outside the original raw-regeneration lane. |
| `--execute` | `false` | Execute provider calls. Default behavior is dry-run planning. |
| `--env-file` | `PROJECT_ROOT/.env` | Environment file for provider credentials. |
| `--provider-profile` | `DEFAULT_PROVIDER_PROFILE` | Provider/runtime profile. |
| `--data-dir` | empty | Directory containing `{paper}.json` input files. |
| `--source-model-outputs` | `DEFAULT_MODEL_OUTPUTS` | Model-output tree used when staging raw regeneration inputs. |
| `--source-run-root` | empty | Shared source-generation cache root. |
| `--generation-model` | empty | Override generation model. Empty means use the row model. |
| `--fallback-generation-models` | empty | Whitespace/comma-separated fallback generation models. |
| `--skip-existing-generation` | `true` | Reuse existing source-generation artifacts when present. |
| `--api-mode` | `auto` | Provider API mode: `chat`, `responses`, or `auto`. |
| `--stream` | `false` | Stream source-generation responses. |
| `--generation-timeout` | `600.0` | Source-generation request timeout. |
| `--generation-max-tokens` | `0` | Source-generation token cap. `0` means provider default. |
| `--generation-retries` | `3` | Source-generation retry count. |
| `--generation-retry-sleep` | `15.0` | Sleep between source-generation retries. |
| `--reasoning-effort` | empty | Optional reasoning-effort override. |
| `--reasoning-summary` | empty | Optional reasoning-summary override. |
| `--save-debug-artifacts` | `false` | Keep request/response debug artifacts. |
| `--evaluation-timeout` | `900` | Timeout for downstream evaluation. |
| `--timeout` | `240` | Per-row PEARL semantic-repair timeout after regeneration. |
| `--max-tokens` | `5000` | GPT token budget for PEARL semantic repair. |
| `--gpt-retries` | `2` | Retry count for GPT-based curation. |
| `--gpt-retry-sleep` | `8.0` | Sleep between GPT retry attempts. |
| `--gpt-transport` | env `GPT55_CURATION_TRANSPORT` or `curl` | HTTP transport for GPT-based curation. |
| `--gpt-stream` | env-driven boolean | Stream GPT responses unless disabled by environment. |
| `--max-prune-rounds` | `2` | Maximum local prune rounds after raw regeneration. |
| `--judge-error-retries` | `2` | Retry count for judge parse/error failures. |
| `--judge-error-retry-sleep` | `5.0` | Sleep between judge-error retries. |
| `--inner-workers` | `6` | Internal worker count for local semantic repair. |
| `--gemini-min-interval-seconds` | `6.5` | Minimum inter-request interval for Gemini routes. |
| `--judge-max-tokens` | `2048` | Judge request token cap. |
| `--gemini-max-tokens` | `12288` | Gemini request token cap. |
| `--min-anchor-correct-ratio` | `0.0` | Minimum anchor-correct ratio before local repair. |
| `--max-repair-candidate-ratio` | `1.0` | Maximum rejected/total ratio allowed for local repair. |
| `--min-raw-cg` | `0.0` | Minimum raw CG required before local repair. |

### `repair_evidence_bound_fresh_eval_provider_errors.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--fresh-eval-results` | required | Fresh-eval result JSON containing provider-contaminated rows. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for repaired provider-error results. |
| `--env-file` | `PROJECT_ROOT/.env` | Environment file for provider credentials. |
| `--limit` | `0` | Process all provider-error rows when `0`; otherwise stop at the given count. |
| `--eval-raw-http-models` | empty | Comma-separated evaluator judge model keys routed through raw HTTP. |
| `--eval-judge-max-retries` | `0` | Evaluator judge retry override. `0` keeps evaluator defaults. |
| `--eval-request-timeout` | `0` | Evaluator request-timeout override. `0` keeps evaluator defaults. |

## 18. Merge Packaging and ANS-Safe Auxiliary Utilities

### `build_strict_merge_candidate_package.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--ledger-pointer` | `DEFAULT_LEDGER_POINTER` | Pointer file identifying the selected merge ledger. |
| `--accounting` | `DEFAULT_ACCOUNTING` | Canonical accounting CSV used as the state source. |
| `--queue` | `DEFAULT_QUEUE` | Residual queue CSV used for row metadata. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the packaged merge-candidate bundle. |

### `run_ans_safe_hybrid_batch_candidates.py`

Provider-free hybrid-candidate builder for ANS-safe local merge proposals.

| Parameter | Default | Meaning |
|---|---|---|
| `--accounting` | `DEFAULT_ACCOUNTING` | Accounting CSV used as the source graph state. |
| `--queue` | `DEFAULT_QUEUE` | Residual queue CSV used as the row source. |
| `--v2-ledger` | `DEFAULT_V2_LEDGER` | Residual v2 ledger used for typed metadata. |
| `--ans-node-results` | `DEFAULT_ANS_NODE_RESULTS` | ANS node-result cache. |
| `--ans-cache` | `DEFAULT_ANS_CACHE` | ANS eval cache used for evidence retrieval. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for hybrid candidates. |
| `--paper-spec` | append list | Restrict to selected paper specs. |
| `--failure-type` | append list | Restrict to selected failure types. |
| `--limit` | `0` | Process all selected rows when `0`; otherwise stop at the given count. |
| `--max-leaf-nodes` | `4` | Maximum added leaf nodes in the hybrid candidate. |
| `--max-bridge-nodes` | `6` | Maximum added bridge nodes in the hybrid candidate. |
| `--source-seed-no-anchor` | `true` | Allow compact source-seed construction for no-anchor rows. |
| `--max-source-seed-entities` | `12` | Maximum entities used in the compact source-seed. |
| `--min-step2-support-ratio` | `1.0` | Minimum support ratio for step-2 nodes copied into the hybrid. |
| `--entity-complete-anchor` | `true` | Require entity-complete anchors in candidate construction. |
| `--min-anchor-entity-score` | `0.67` | Minimum anchor/entity overlap score. |
| `--window` | `1` | Evidence window for ANS-backed claim construction. |
| `--root-window` | `None` | Optional special evidence window for root nodes. |
| `--traversal-depth` | `2` | Graph traversal depth for evidence collection. |
| `--max-evidence-sentences` | `10` | Maximum evidence sentences per packet. |
| `--max-evidence-chars` | `1800` | Maximum evidence text length per packet. |
| `--include-viewpoints` | `true` | Include viewpoint/context statements. |
| `--graph-ordered-evidence` | `true` | Preserve graph order when assembling evidence. |

### `build_ans_safe_pilot_audit.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--paper-spec` | `gpt_5_2:s41467-025-56921-8` | Pilot paper used by the audit script. |
| `--ans-node-results` | `DEFAULT_ANS_NODE_RESULTS` | ANS node-result cache used for the audit. |
| `--strict-candidates` | `DEFAULT_STRICT_CANDIDATES` | Strict-candidate bundle used for comparison. |
| `--replay-eval` | `DEFAULT_REPLAY_EVAL` | Replay evaluation bundle used as the audit source. |
| `--v2-ledger` | `DEFAULT_V2_LEDGER` | Residual v2 ledger used for metadata. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the pilot audit. |
| `--top-nodes` | `12` | Number of nodes surfaced in the pilot audit summary. |

Hard-coded stages:

- `raw_step1_extraction`
- `llm_step2_self_fix_final_clean`
- `pearl_terminal_graph`

## 19. Lane-Specific Materializers

These scripts are provider-free. Most of them write staged candidates for a
single lane or even for a single frozen paper-level case.

### Transfer and local-edit materializers

#### `materialize_anchor_adapted_transfer.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--source-graph-spec` | required | Source graph spec to adapt. |
| `--source-stage-dir` | required | Source stage directory paired with the graph spec. |
| `--target-specs` | required | Target paper specs that receive the adapted transfer. |
| `--packet-root` | `DEFAULT_PACKET_ROOT` | Packet root used to resolve target packet metadata. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for adapted-transfer candidates. |
| `--label` | required | Candidate label written into outputs. |
| `--model` | empty | Optional model label stored with the candidate. |
| `--variant` | `v1_anchor_adapted` | Adaptation variant. Choices: `v1_anchor_adapted`, `v2_bridge_hard_units`, `v3_source_promotion`. |
| `--copy-staging` | `true` | Copy outputs into staging directories. |

#### `materialize_same_paper_graph_transfer.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--source-graph-spec` | required | Source graph spec to transfer. |
| `--source-stage-dir` | required | Source stage directory paired with the graph spec. |
| `--target-packets` | `[]` | Explicit target packet list. |
| `--target-specs` | `[]` | Target paper-spec list when packets are resolved by packet root. |
| `--packet-root` | `DEFAULT_PACKET_ROOT` | Packet root used to resolve target packet metadata. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for same-paper transfer candidates. |
| `--label` | required | Candidate label written into outputs. |
| `--model` | empty | Optional model label stored with the candidate. |
| `--copy-staging` | `true` | Copy outputs into staging directories. |

#### `materialize_source_leaf_bridge_candidate.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--base-staged-dir` | required | Base staged directory that provides the graph context. |
| `--base-graph-spec` | empty | Optional explicit base graph spec. |
| `--input-data` | empty | Optional explicit input-data JSON. |
| `--packet` | required | Packet JSON describing the bridge task. |
| `--paper-spec` | required | Paper spec for the candidate. |
| `--label` | required | Candidate label written into outputs. |
| `--add-node` | append list | Node additions in `NODE_ID|x,y,z|text` format. |
| `--add-edge` | append list | Edge additions in `SOURCE|TARGET|TYPE` format. |
| `--edit` | append list | Node edits in `NODE_ID|x,y,z` or `NODE_ID|x,y,z|text` format. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for source-leaf bridge candidates. |
| `--row-ans-floor` | empty | Optional row-level ANS floor or non-regression marker. |
| `--failure-type` | empty | Optional failure-type tag stored with the candidate. |

#### `materialize_source_tuple_calibration_candidate.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--base-staged-dir` | required | Base staged directory that provides the graph context. |
| `--base-graph-spec` | empty | Optional explicit base graph spec. |
| `--input-data` | empty | Optional explicit input-data JSON. |
| `--packet` | required | Packet JSON describing the calibration task. |
| `--paper-spec` | required | Paper spec for the candidate. |
| `--label` | required | Candidate label written into outputs. |
| `--edit` | required append list | Tuple/text edits in `NODE_ID|x,y,z` or `NODE_ID|x,y,z|new text` format. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for tuple-calibration candidates. |
| `--row-ans-floor` | empty | Optional row-level ANS floor or non-regression marker. |
| `--failure-type` | empty | Optional failure-type tag stored with the candidate. |

#### `materialize_minimal_root_edit_candidate.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--base-staged-dir` | required | Base staged directory that provides the graph context. |
| `--packet` | empty | Optional packet JSON paired with the edit. |
| `--paper-spec` | required | Paper spec for the candidate. |
| `--label` | required | Candidate label written into outputs. |
| `--root-text` | required | Replacement root text. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for minimal-root edits. |
| `--row-ans-floor` | `1.0` | Row-level ANS floor stored with the candidate. |
| `--failure-type` | empty | Optional failure-type tag stored with the candidate. |

### ANS-aware materializers

#### `materialize_ans_safe_step2_hybrid_candidate.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--paper-spec` | `gpt_5_2:s41467-025-56921-8` | Default pilot paper spec. |
| `--node-ids` | `N18` | Default step-2 node set used in the hybrid. |
| `--label` | `step2-source-leaf-N18-to-NROOT-v1` | Candidate label written into outputs. |
| `--strict-candidates` | `DEFAULT_CANDIDATES` | Strict-candidate bundle used as the base source. |
| `--v2-ledger` | `DEFAULT_V2_LEDGER` | Residual v2 ledger used for metadata. |
| `--step2-graph` | `DEFAULT_STEP2_GRAPH` | Step-2 graph used as the hybrid donor. |
| `--ans-cache` | `DEFAULT_ANS_CACHE` | ANS cache used for evidence retrieval. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for hybrid candidates. |
| `--window` | `1` | Evidence window for ANS-backed claim construction. |
| `--traversal-depth` | `2` | Graph traversal depth for evidence collection. |
| `--max-evidence-sentences` | `10` | Maximum evidence sentences per packet. |
| `--max-evidence-chars` | `1800` | Maximum evidence text length per packet. |
| `--include-viewpoints` | `true` | Include viewpoint/context statements. |
| `--graph-ordered-evidence` | `true` | Preserve graph order when assembling evidence. |
| `--narrow-n26` | `false` | Enable the narrow `N26` special-case edit path. |

#### `materialize_qwen56635_ans_microedit_candidate.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--paper-spec` | `qwen3_5_397b_a17b:s41467-025-56635-x` | Default frozen paper spec. |
| `--label` | `qwen56635-ans-microedit-v1` | Candidate label written into outputs. |
| `--source-label` | `after327-pareto-terminal-plus-compact-bridge-v1-qwen3_5_397b_a17b_s41467-025-56635-x` | Source candidate label to patch. |
| `--patch-set` | `full` | Patch family. Choices: `full`, `root_only`. |
| `--strict-candidates` | `DEFAULT_STRICT_CANDIDATES` | Strict-candidate bundle used as the base source. |
| `--v2-ledger` | `DEFAULT_V2_LEDGER` | Residual v2 ledger used for metadata. |
| `--ans-cache` | `DEFAULT_ANS_CACHE` | ANS cache used for evidence retrieval. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for ANS micro-edit candidates. |
| `--window` | `1` | Evidence window for ANS-backed claim construction. |
| `--root-window` | `None` | Optional special root-node evidence window. |
| `--traversal-depth` | `2` | Graph traversal depth for evidence collection. |
| `--max-evidence-sentences` | `10` | Maximum evidence sentences per packet. |
| `--max-evidence-chars` | `1800` | Maximum evidence text length per packet. |
| `--include-viewpoints` | `true` | Include viewpoint/context statements. |
| `--graph-ordered-evidence` | `true` | Preserve graph order when assembling evidence. |

#### `materialize_after327_56075_ans_micro_repair.py`

This script exposes no CLI flags in the public package. It writes a frozen
repair candidate with:

- fixed model label `gemini_3_1_pro_preview`;
- fixed output root under `RESIDUAL_ROOT`.

### Frozen compact source-seed materializers

These scripts all expose the same parameter surface:

| Shared parameter | Meaning |
|---|---|
| `--paper-spec` | Frozen paper spec for the candidate. |
| `--label` | Frozen candidate label. |
| `--packet` | Packet JSON used as the source bundle. |
| `--input-data` | Input-data JSON used as the paper evidence source. |
| `--out-root` | Output root for the compact source-seed candidate. |
| `--row-ans-floor` | Row-level ANS floor or non-regression marker stored with the candidate. |

Per-script frozen defaults:

| Script | `paper_spec` default | `label` default | `row_ans_floor` default |
|---|---|---|---|
| `materialize_gemini56283_compact_source_seed_candidate.py` | `gemini_3_1_pro_preview:s41467-025-56283-1` | `gemini56283-compact-source-seed-v6` | `1.0` |
| `materialize_gemini56698_compact_source_seed_candidate.py` | `gemini_3_1_pro_preview:s41467-025-56698-w` | `gemini56698-compact-source-seed-v1` | `1.0` |
| `materialize_gemini56852_compact_source_seed_candidate.py` | `gemini_3_1_pro_preview:s41467-025-56852-4` | `gemini56852-compact-source-seed-v1` | `0.7916666666666666` |
| `materialize_grok56611_compact_source_seed_candidate.py` | `grok_4_1_thinking:s41467-025-56611-5` | `grok56611-compact-source-seed-v1` | `0.8795180722891566` |
| `materialize_grok56786_compact_source_seed_candidate.py` | `grok_4_1_thinking:s41467-025-56786-x` | `grok56786-compact-source-seed-v3` | `non_regression` |
| `materialize_grok56819_compact_source_seed_candidate.py` | `grok_4_1_thinking:s41467-025-56819-5` | `grok56819-compact-source-seed-v1` | `0.921875` |
| `materialize_grok56882_compact_source_seed_candidate.py` | `grok_4_1_thinking:s41467-025-56882-y` | `grok56882-compact-source-seed-v1` | `0.7407407407407407` |
| `materialize_qwen56106_compact_source_seed_candidate.py` | `qwen3_5_397b_a17b:s41467-025-56106-3` | `qwen56106-compact-source-seed-v1` | `0.8285714285714286` |
| `materialize_qwen56769_compact_source_seed_candidate.py` | `qwen3_5_397b_a17b:s41467-025-56769-y` | `qwen56769-compact-source-seed-v1` | `0.7352941176470589` |
| `materialize_qwen56795_compact_source_seed_candidate.py` | `qwen3_5_397b_a17b:s41467-025-56795-w` | `qwen56795-compact-source-seed-v1` | `0.984375` |

## 20. Replay and Notation Audit Utilities

### `batch_replay_notation_sensitive_coverage.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--ledger` | `DEFAULT_LEDGER` | Residual ledger used as the replay source. |
| `--use-v2-ledger` | `false` | Switch to `DEFAULT_V2_LEDGER` unless `--ledger` is explicitly set. |
| `--missing-entity-csv` | `DEFAULT_MISSING_ENTITY_CSV` | Missing-entity audit CSV used to focus the replay. |
| `--out-root` | `DEFAULT_OUT_ROOT` | Output root for the notation-sensitive replay package. |
| `--evaluator` | `DEFAULT_EVALUATOR` | Evaluator entrypoint used to replay coverage with saved judgments. |

### `replay_coverage_with_saved_judgments.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--work-dir` | required | Replay work directory. |
| `--eval-json` | required | Evaluation JSON carrying saved judge outputs. |
| `--graph-file` | empty | Optional explicit graph file path. |
| `--input-data` | empty | Optional explicit input-data JSON path. |
| `--evaluator` | `DEFAULT_EVALUATOR` | Evaluator entrypoint used for replay. |
| `--out` | empty | Optional explicit output path. |

### `build_notation_protocol_replay_merge.py`

| Parameter | Default | Meaning |
|---|---|---|
| `--base-accounting` | `DEFAULT_BASE_ACCOUNTING` | Base accounting CSV used as the pre-merge state. |
| `--base-provenance` | `DEFAULT_BASE_PROVENANCE` | Base provenance CSV paired with the accounting state. |
| `--notation-replay` | `DEFAULT_NOTATION_REPLAY` | Notation-replay package used as the merge source. |
| `--ans-node-results` | `DEFAULT_ANS_NODE_RESULTS` | ANS node-result cache used for support checks. |
| `--candidate-root` | `DEFAULT_CANDIDATE_ROOT` | Candidate root used for replay merge materialization. |
| `--proposal-root` | `DEFAULT_PROPOSAL_ROOT` | Output root for the replay proposal package. |
| `--canonical-summary` | `DEFAULT_CANONICAL_SUMMARY` | Canonical summary JSON used as the baseline. |

Hard-coded script policy:

- `ANS_STAGES = {raw_step1_extraction, llm_step2_self_fix_final_clean, pearl_terminal_graph}`;
- `EXCLUDED_ANS_UNIT_TYPES = {root_common_bridge, graph_node}`.

## What This File Does Not Prove

This file records the exported CLI parameters and the major hard-coded policy
settings for the released `350/350` current-version scripts. It does not prove
that every historical run used only the default values above. Exact
override-free command provenance would require the external run directories that
were intentionally excluded from the GitHub-ready package.
