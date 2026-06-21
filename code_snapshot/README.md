# Shared Code Snapshot

This directory is the single Python source snapshot for the GitHub-ready PEARL
350-row release. The original version and current version report different
audited states, but they are explained by the same exported code base.

The previous duplicated copies under `standard_flow_300/code/` and
`review_closeout_350/code/` were byte-for-byte identical. They have been
collapsed here so the release has one canonical source snapshot.

## What Is In This Directory

- `63` Python source files used to build, evaluate, audit, and package the
  350-row benchmark states.
- `CODE_SNAPSHOT_MANIFEST_20260602.json`, a retained provenance manifest from
  the exported code subset. It records the core subset that was packaged during
  the original export and does not enumerate every helper script in this
  simplified GitHub release.

## Script Groups

| Group | Main scripts | Role |
|---|---|---|
| original-version graph construction | `run_vote_guided_semantic_root_batch.py`, `curate_graph_from_vote_results_gpt55.py` | Build PEARL graph candidates and recover the semantic root used in the original-version run. |
| evaluation and scoring | `evaluator.py`, `evaluate_evidence_bound_staged_candidate.py`, `evaluate_ans_factscore_style_350.py` | Run the strict EC/CG and REA gate and the ANS support audit. |
| residual extraction and closeout planning | `build_residual_50_closeout_package.py`, `build_residual_closeout_v2_plan.py`, `extract_residual_50_candidate_frontier.py`, `run_residual_50_closure_engine.py` | Turn non-strict rows into an auditable residual queue and lane-specific work plan. |
| controller and diagnostics | `build_after327_failure_typed_controller_queue.py`, `build_after327_ans_delta_planner.py`, `build_after327_ans_regression_contract_audit.py`, `build_after327_candidate_ans_gap_diagnostic.py`, `build_after327_claim_evidence_closure_solver.py`, `build_after327_source_claim_closure_diagnostic.py` | Explain residual failure modes and route rows into the final closeout queue. |
| targeted repair lanes | `materialize_*.py`, `run_evidence_bound_graph_spec_regeneration.py`, `run_evidence_bound_local_window_patch.py`, `run_evidence_bound_micro_edit_patch.py`, `run_edge_repair_raw_regeneration_residuals.py`, `repair_evidence_bound_fresh_eval_provider_errors.py` | Build lane-specific repair candidates under evidence and non-regression constraints. |
| merge construction and guards | `build_strict_merge_candidates_from_fresh_eval.py`, `build_controller_filtered_strict_merge_candidates.py`, `build_strict_merge_proposal_350.py`, `build_strict_merge_candidate_package.py`, `build_ans_claims_for_strict_merge_candidates.py`, `build_strict_merge_ans_guard_report.py`, `build_proposal_batch_ans_guard.py` | Verify the final merge set and enforce row-level and batch-level ANS checks. |
| reporting utilities | `generate_pearl_figures_350_no_gpt54_gpt55.py`, `replay_coverage_with_saved_judgments.py`, `batch_replay_notation_sensitive_coverage.py`, `refresh_350_subset_manifests.py` | Generate figures, replay audits, and refresh package metadata. |

## How To Use It

Run scripts from the release root:

```bash
cd deliverables/github_ready/pearl_300_350_code_release_20260608
python code_snapshot/<script>.py ...
```

This release is audit-oriented. Full regeneration still requires the external
graph directories, evaluator outputs, and provider credentials that are kept
outside the GitHub-ready package.
