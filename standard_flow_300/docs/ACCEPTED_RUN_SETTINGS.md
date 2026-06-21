# Original Version Accepted Run Settings

This file records the settings that matter for the public `300/350`
original-version claim.

## Locked Claim Settings

| Setting | Accepted value | How it is fixed |
|---|---|---|
| benchmark scope | `350` input records | locked by the public result package |
| model coverage | five model groups, `70` rows each | locked by the public result package |
| strict acceptance gate | `final_CG = 1.0`, `final_REA = 1.0`, no provider or judge error | locked by summaries and row tables |
| accepted rows | `300` | locked by `STRICT_ACCEPTED_350.csv` and `FULL_350_SUMMARY.json` |
| typed residual rows | `50` | locked by `TYPED_RESIDUAL_350.csv` and `FULL_350_SUMMARY.json` |
| provider failures after recovery | `0` | locked by `FULL_350_SUMMARY.json` |
| residual denominator policy | residual rows stay in the same `350`-row denominator | locked by the result tables and reporting docs |
| ANS role | grounding diagnostic only; not the strict gate | locked by public method and metric docs |

## Locked Residual Breakdown

| Failure type | Count |
|---|---:|
| `preflight:no_anchor_regenerate` | 31 |
| `final_metric_gate_failed` | 13 |
| `metric_regression` | 4 |
| `final_judge_failed` | 2 |

## Exported Runner Defaults Still Recoverable

The lightweight package does not preserve the exact historical shell command for
the original PEARL batch. What it does preserve is the default configuration of
the shipped batch runner.

| Parameter | Exported default |
|---|---|
| `--timeout` | `240` |
| `--max-tokens` | `5000` |
| `--gpt-retries` | `2` |
| `--gpt-retry-sleep` | `8.0` |
| `--max-prune-rounds` | `8` |
| `--judge-error-retries` | `2` |
| `--judge-error-retry-sleep` | `5.0` |
| `--min-anchor-correct-ratio` | `0.0` |
| `--max-repair-candidate-ratio` | `1.0` |
| `--min-raw-cg` | `0.0` |
| `--min-final-cg` | `1.0` |
| `--min-final-rea` | `1.0` |
| `--inner-workers` | `6` |
| `--stop-on-regression` | `true` |
| `--continue-on-error` | `false` |
| `--max-retries` | `1` |
| `--full-rejudge` | `false` |
| `--repair-rejected` | `false` |
| `--resume-existing` | `false` |

## ANS Audit Defaults Recoverable From The Export

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

- the exact historical batch command line;
- any local override of provider endpoint, HTTP transport, or stream mode;
- the exact retry history or cache state for individual papers;
- any machine-local paths or temporary files that were excluded from the
  GitHub-ready release.

For the public original-version claim, the locked settings are the benchmark
boundary, the strict gate, the accepted-count accounting, and the residual
taxonomy. Everything else should be treated as exported default behavior unless
the missing external run directory is restored.
