# Original Version Documentation

This directory documents the PEARL original-version state for the fixed
350-input-record benchmark.

## Recommended Reading Order

1. `STANDARD_FLOW_METHOD_DESIGN.md`: method design, module roles, and data path.
2. `CURRENT_RESULT.md`: 300/350 summary and residual breakdown.
3. `SCOPE_AND_REPORTING.md`: reporting language and metric boundaries.
4. `FILE_MAP.md`: public file inventory.

The benchmark contains five model groups with 70 input records each:

- `claude_sonnet_4_5_20250929`
- `gemini_3_1_pro_preview`
- `gpt_5_2`
- `grok_4_1_thinking`
- `qwen3_5_397b_a17b`

## Main Result

| Item | Value |
|---|---:|
| input records | 350 |
| strictly accepted input records | 300 |
| typed residual input records | 50 |
| provider-failure records | 0 |
| final EC/CG average | 0.896055452484024 |
| final REA average | 0.9062380952380953 |

The strict rule requires both `EC/CG = 1.0` and `REA = 1.0`. Input records that
fail either metric remain in the denominator and are recorded as typed
residuals.

## Result Tables

- `results/FULL_350_SUMMARY.json`
- `results/EC_REA_EVALUATION_SUMMARY_350.json`
- `results/STRICT_ACCEPTED_350.csv`
- `results/TYPED_RESIDUAL_350.csv`
- `results/MODEL_OUTCOME_COUNTS_350.csv`
- `results/RESIDUAL_FAILURE_TYPE_COUNTS_350.csv`

## Baseline and Ablations

- `results/LLM_BASELINE_SUMMARY_350.json`
- `results/ABLATION_RESULTS_SUMMARY_350.json`
- `results/ABLATION_RESULTS_TABLE_350.csv`

The baseline compares generation-final graph candidates against the PEARL
original-version result. The ablation table reports how PEARL components affect
strict acceptance, EC/CG, and REA.

## Residual Queue

The original version leaves 50 typed residual input records. The public queue
keeps only input-record identity, failure type, metrics, controller lane, and
next action. It does not include local graph paths or provider-output
directories.

| Failure type | Count |
|---|---:|
| `preflight:no_anchor_regenerate` | 31 |
| `final_metric_gate_failed` | 13 |
| `metric_regression` | 4 |
| `final_judge_failed` | 2 |

## Reporting Boundary

Report this state as:

```text
PEARL original version: 300/350 strictly accepted input records,
50 typed residual input records
```

Do not report this directory as a 350/350 result. The current-version 350/350
evidence is kept separately in `review_closeout_350`.
