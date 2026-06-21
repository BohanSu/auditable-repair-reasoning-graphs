# Original Version Run Configuration Matrix

This file records the run-control settings that can still be recovered from the
GitHub-ready `300/350` package.

It separates three cases:

- `artifact-locked`: the public package fixes this setting through result files
  or acceptance summaries;
- `exported default`: the shipped script exposes a default value, but the
  lightweight package does not prove that no local override was used in the
  historical run;
- `not recoverable`: the lightweight package does not retain this detail.

## Artifact-Locked Settings

| Item | Value | Status |
|---|---|---|
| benchmark size | `350` input records | artifact-locked |
| model groups | `claude_sonnet_4_5_20250929`, `gemini_3_1_pro_preview`, `gpt_5_2`, `grok_4_1_thinking`, `qwen3_5_397b_a17b` | artifact-locked |
| strict gate | `final_CG = 1.0` and `final_REA = 1.0`, with no provider or judge error | artifact-locked |
| original-version result | `300/350` strict rows, `50` typed residual rows | artifact-locked |
| provider failures after recovery | `0` | artifact-locked |
| residual accounting rule | all `350` rows remain in the denominator | artifact-locked |
| ANS role | grounding diagnostic and guard; not a replacement for `EC/CG` or `REA` | artifact-locked |

## Script Matrix

| Script | Role | Provider calls | Recoverable settings |
|---|---|---|---|
| `run_vote_guided_semantic_root_batch.py` | Primary original-version PEARL batch runner. | yes | exported defaults: `--timeout 240`, `--max-tokens 5000`, `--gpt-retries 2`, `--gpt-retry-sleep 8.0`, `--gpt-transport` default from `GPT55_CURATION_TRANSPORT` or `curl`, `--gpt-stream` default from `GPT55_CURATION_STREAM`, `--max-prune-rounds 8`, `--judge-error-retries 2`, `--judge-error-retry-sleep 5.0`, `--min-anchor-correct-ratio 0.0`, `--max-repair-candidate-ratio 1.0`, `--min-raw-cg 0.0`, `--min-final-cg 1.0`, `--min-final-rea 1.0`, `--inner-workers 6`, `--stop-on-regression true`, `--continue-on-error false`, `--max-retries 1`, `--full-rejudge false`, `--repair-rejected false`, `--resume-existing false`. |
| `build_residual_50_closeout_package.py` | Provider-free planner that exports the `50`-row residual queue and lane summaries. | no | no CLI flags in the exported script. Hard-coded planning summary keeps `final_CG = 1.0`, `final_REA = 1.0`, `fresh_evaluation_required = true`, and `provider_error_is_not_semantic_failure = true`. |
| `evaluate_ans_factscore_style_350.py` | ANS/FActScore-style grounding audit for full-run or custom stages. | yes | exported defaults: `--model gpt-5.5`, `--temperature 0.0`, `--max-tokens 6000`, `--timeout 120`, `--retries 3`, `--retry-sleep 2.0`, `--batch-size 8`, `--workers 1`, `--top-k 4`, `--max-evidence-chars 1800`, `--max-facts-per-node 6`, `--combined-judge false`. Default excluded models: `gpt_5_4`, `gpt_5_5`. |

## What The Public Package Does Not Recover

- the exact historical `--papers` or `--paper-spec-file` invocation order for
  the batch that produced the locked `300/350` result;
- any local override of `--gpt-transport`, `--gpt-stream`, provider base URL,
  or environment variables;
- per-paper retry events, resume behavior, or local cache hits during the
  historical run;
- machine-specific runtime details that were intentionally excluded from the
  GitHub-ready package.

Use this file as the configuration boundary for the released original-version
claim. If exact shell-command provenance is needed, the missing external run
directories must be restored.
