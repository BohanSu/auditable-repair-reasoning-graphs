#!/usr/bin/env python3
"""Generate PEARL result figures for the 350-row subset excluding GPT-5.4/5.5."""

from __future__ import annotations

import json
import os
from pathlib import Path
from textwrap import dedent

OUT_DIR = Path(__file__).resolve().parent / "350_no_gpt54_gpt55"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("MPLCONFIGDIR", str(OUT_DIR / ".mpl_cache"))

import matplotlib as mpl

mpl.use("Agg", force=True)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ACCOUNTING_CSV = PACKAGE_ROOT / "03_final_accounting" / "FULL_490_ACCOUNTING.csv"
LLM_BASELINE_CSV = PACKAGE_ROOT / "05_llm_baseline" / "LLM_BASELINE_VS_PEARL_490.csv"
ABLATION_FILES = {
    "A0": PACKAGE_ROOT / "06_ablations" / "A0_raw_extraction_only" / "A0_RAW_ONLY_ACCOUNTING_490.csv",
    "A2": PACKAGE_ROOT / "06_ablations" / "A2_root_only_no_iterative_feedback" / "A2_ROOT_ONLY_ACCOUNTING_490.csv",
    "A3": PACKAGE_ROOT / "06_ablations" / "A3_no_root_generation" / "A3_NO_ROOT_ACCOUNTING_490.csv",
    "A4": PACKAGE_ROOT / "06_ablations" / "A4_retained_root_no_repair" / "A4_RETAINED_ROOT_NO_REPAIR_ACCOUNTING_490.csv",
}
MINICHECK_FILES = {
    "raw_step1_extraction": PACKAGE_ROOT / "08_minicheck" / "baselines" / "raw_step1_extraction" / "minicheck_claim_rows.csv",
    "llm_step2_self_fix_final_clean": PACKAGE_ROOT / "08_minicheck" / "baselines" / "llm_step2_self_fix_final_clean" / "minicheck_claim_rows.csv",
    "pearl_terminal_graph": PACKAGE_ROOT / "08_minicheck" / "current_pearl_terminal_graph" / "minicheck_claim_rows.csv",
}

EXCLUDED_MODELS = {"gpt_5_4", "gpt_5_5"}
MODEL_ORDER = [
    "claude_sonnet_4_5_20250929",
    "gemini_3_1_pro_preview",
    "gpt_5_2",
    "grok_4_1_thinking",
    "qwen3_5_397b_a17b",
]
MODEL_LABELS = {
    "claude_sonnet_4_5_20250929": "Claude\nSonnet 4.5",
    "gemini_3_1_pro_preview": "Gemini\n3.1 Pro",
    "gpt_5_2": "GPT-5.2",
    "grok_4_1_thinking": "Grok 4.1\nThinking",
    "qwen3_5_397b_a17b": "Qwen3.5\n397B-A17B",
}
MODEL_SHORT_LABELS = {
    "claude_sonnet_4_5_20250929": "Claude",
    "gemini_3_1_pro_preview": "Gemini",
    "gpt_5_2": "GPT-5.2",
    "grok_4_1_thinking": "Grok",
    "qwen3_5_397b_a17b": "Qwen",
}

COLORS = {
    # Nature/NMI-style unified palette:
    # one neutral family, one PEARL signal family, and one restrained failure family.
    "ink": "#272727",
    "muted": "#767676",
    "line": "#D8D8D8",
    "baseline": "#A8A8A8",
    "baseline_soft": "#D8D8D8",
    "pearl": "#3775BA",
    "pearl_dark": "#0F4D92",
    "pearl_soft": "#B4C0E4",
    "strict": "#AADCA9",
    "strict_dark": "#8BCF8B",
    "residual": "#E9A6A1",
    "residual_dark": "#C9827C",
    "residual_soft": "#F6CFCB",
}

RESIDUAL_PALETTE = [
    COLORS["residual_dark"],
    COLORS["residual"],
    COLORS["residual_soft"],
    "#D8B6B2",
    "#E8D6D4",
]

ABLATION_PALETTE = [
    COLORS["baseline_soft"],
    COLORS["baseline"],
    COLORS["pearl_soft"],
    "#90A7D0",
    "#6487C0",
    COLORS["pearl_dark"],
]


def configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "font.size": 7.6,
            "axes.labelsize": 8.0,
            "xtick.labelsize": 6.9,
            "ytick.labelsize": 6.9,
            "legend.fontsize": 6.9,
            "axes.linewidth": 0.65,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#3A3A3A",
            "grid.color": "#EEF1F4",
            "grid.linewidth": 0.45,
            "grid.alpha": 0.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.04,
        }
    )


def subset(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["model"].isin(EXCLUDED_MODELS)].copy()


def bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def metric_summary(df: pd.DataFrame) -> dict:
    return {
        "rows": len(df),
        "strict_success": int((df["current_outcome"] == "strict_success").sum()),
        "typed_residual": int((df["current_outcome"] == "typed_residual").sum()),
        "provider_failures": int((bool_series(df["api_clean_current"]) == False).sum()),  # noqa: E712
        "original_EC_CG_avg": float(df["original_CG"].mean()),
        "original_REA_avg": float(df["original_REA"].mean()),
        "final_EC_CG_avg": float(df["final_CG"].mean()),
        "final_REA_avg": float(df["final_REA"].mean()),
        "delta_EC_CG_avg": float(df["delta_CG"].mean()),
        "delta_REA_avg": float(df["delta_REA"].mean()),
    }


def ablation_summary(accounting: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    full = metric_summary(accounting)
    rows = [
        {
            "result_id": "FULL",
            "label": "Full PEARL",
            "strict_success": full["strict_success"],
            "avg_EC_CG": full["final_EC_CG_avg"],
            "avg_REA": full["final_REA_avg"],
        },
        {
            "result_id": "BASELINE",
            "label": "LLM final",
            "strict_success": int(bool_series(baseline["baseline_strict_gate_passed"]).sum()),
            "avg_EC_CG": float(baseline["baseline_EC_CG"].mean()),
            "avg_REA": float(baseline["baseline_REA"].mean()),
        },
    ]
    for aid, path in ABLATION_FILES.items():
        df = subset(pd.read_csv(path))
        rows.append(
            {
                "result_id": aid,
                "label": {
                    "A0": "Raw only",
                    "A2": "Root only",
                    "A3": "No root gen.",
                    "A4": "NROOT only",
                }[aid],
                "strict_success": int(bool_series(df["strict_gate_passed"]).sum()),
                "avg_EC_CG": float(df["EC_CG"].mean()),
                "avg_REA": float(df["REA"].mean()),
            }
        )
    order = ["A0", "BASELINE", "A3", "A4", "A2", "FULL"]
    return pd.DataFrame(rows).set_index("result_id").loc[order].reset_index()


def minicheck_summary(accounting: pd.DataFrame) -> pd.DataFrame:
    subset_specs = set(accounting["paper_spec"])
    rows = []
    labels = {
        "raw_step1_extraction": "Raw extraction",
        "llm_step2_self_fix_final_clean": "LLM second-pass",
        "pearl_terminal_graph": "PEARL terminal",
    }
    for stage, path in MINICHECK_FILES.items():
        claims = pd.read_csv(path)
        claims = claims[claims["paper_spec"].isin(subset_specs)].copy()
        scoreable_specs = set(claims["paper_spec"])
        rows_total = len(subset_specs)
        claims_evaluated = len(claims)
        supported = int(bool_series(claims["node_supported"]).sum()) if claims_evaluated else 0
        no_evidence = int(bool_series(claims["no_evidence"]).sum()) if claims_evaluated else 0
        rows.append(
            {
                "stage": stage,
                "label": labels[stage],
                "rows_total": rows_total,
                "rows_scoreable_for_minicheck": len(scoreable_specs),
                "rows_not_scoreable_for_minicheck": rows_total - len(scoreable_specs),
                "claims_evaluated": claims_evaluated,
                "claims_supported": supported,
                "claims_unsupported": claims_evaluated - supported,
                "claims_no_evidence": no_evidence,
                "claim_weighted_support_rate": supported / claims_evaluated if claims_evaluated else 0.0,
                "no_evidence_rate": no_evidence / claims_evaluated if claims_evaluated else 0.0,
            }
        )
    return pd.DataFrame(rows)


def save(fig: plt.Figure, stem: str, outputs: list[dict]) -> None:
    pdf = OUT_DIR / f"{stem}.pdf"
    png = OUT_DIR / f"{stem}.png"
    fig.savefig(pdf)
    fig.savefig(png)
    plt.close(fig)
    outputs.append({"id": stem, "pdf": pdf.name, "png": png.name})


def format_axis_percent(ax: plt.Axes) -> None:
    ax.set_ylim(0, 1.04)
    ax.set_yticks(np.linspace(0, 1.0, 6))
    ax.set_yticklabels([f"{int(v * 100)}" for v in np.linspace(0, 1.0, 6)])


def fig1_overview(accounting: pd.DataFrame, summary: dict, outputs: list[dict]) -> None:
    strict = summary["strict_success"]
    residual = summary["typed_residual"]
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.35), gridspec_kw={"width_ratios": [1.0, 1.35, 1.25]})
    ax = axes[0]
    wedges, _ = ax.pie(
        [strict, residual],
        colors=[COLORS["strict"], COLORS["residual"]],
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 1.0},
    )
    ax.text(0, 0.08, f"{summary['rows']}", ha="center", va="center", fontsize=13.5, fontweight="bold")
    ax.text(0, -0.17, "accounted", ha="center", va="center", fontsize=6.8, color=COLORS["muted"])
    ax.legend(wedges, [f"Strict success {strict}", f"Typed residual {residual}"], loc="lower center", bbox_to_anchor=(0.5, -0.20), frameon=False)

    ax = axes[1]
    metrics = ["EC/CG", "REA"]
    x = np.arange(2)
    before = [summary["original_EC_CG_avg"], summary["original_REA_avg"]]
    after = [summary["final_EC_CG_avg"], summary["final_REA_avg"]]
    w = 0.32
    bars_a = ax.bar(x - w / 2, before, w, color=COLORS["baseline"], label="LLM generation-final")
    bars_b = ax.bar(x + w / 2, after, w, color=COLORS["pearl"], label="PEARL final")
    format_axis_percent(ax)
    ax.set_ylabel("Average score (%)")
    ax.set_xticks(x)
    ax.set_xticklabels(metrics)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2)
    for bars in (bars_a, bars_b):
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.025, f"{bar.get_height() * 100:.1f}", ha="center", fontsize=6.8)

    ax = axes[2]
    deltas = [summary["delta_EC_CG_avg"], summary["delta_REA_avg"]]
    bars = ax.bar(metrics, deltas, color=[COLORS["pearl_soft"], COLORS["pearl"]], width=0.55)
    ax.set_ylim(0, max(deltas) * 1.24)
    ax.set_ylabel("Absolute gain")
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(deltas) * 0.03, f"+{bar.get_height():.3f}", ha="center", fontsize=7.0)
    ax.text(0.02, 0.93, f"Provider/API failures = {summary['provider_failures']}", transform=ax.transAxes, ha="left", va="top", fontsize=6.8, color=COLORS["muted"])
    for label, ax in zip(["a", "b", "c"], axes):
        ax.text(-0.12, 1.04, label, transform=ax.transAxes, fontsize=9, fontweight="bold")
    fig.subplots_adjust(wspace=0.50, bottom=0.25)
    save(fig, "fig1_350_main_result_overview", outputs)


def fig2_model_outcomes(accounting: pd.DataFrame, outputs: list[dict]) -> None:
    grouped = accounting.groupby(["model", "current_outcome"]).size().unstack(fill_value=0).reindex(MODEL_ORDER)
    strict = grouped.get("strict_success", pd.Series(0, index=grouped.index))
    residual = grouped.get("typed_residual", pd.Series(0, index=grouped.index))
    labels = [MODEL_LABELS[m] for m in grouped.index]
    y = np.arange(len(grouped))
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6), gridspec_kw={"width_ratios": [1.3, 1.1]})
    ax = axes[0]
    ax.barh(y, strict, color=COLORS["strict"], label="Strict success")
    ax.barh(y, residual, left=strict, color=COLORS["residual"], label="Typed residual")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Rows")
    ax.set_xlim(0, 78)
    ax.legend(frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, 1.14))
    for i, (s, r) in enumerate(zip(strict, residual)):
        ax.text(s / 2, i, f"{int(s)}", ha="center", va="center", fontsize=6.8, color="white", fontweight="bold")
        if r:
            ax.text(s + r / 2, i, f"{int(r)}", ha="center", va="center", fontsize=6.8, color="white", fontweight="bold")
    ax = axes[1]
    rate = strict / (strict + residual)
    bars = ax.barh(y, rate, color=COLORS["pearl"])
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.08)
    ax.set_xlabel("Strict-success rate")
    ax.set_xticks(np.linspace(0, 1, 6))
    ax.set_xticklabels([f"{int(v * 100)}%" for v in np.linspace(0, 1, 6)])
    for bar, val in zip(bars, rate):
        ax.text(val + 0.012, bar.get_y() + bar.get_height() / 2, f"{val * 100:.1f}%", va="center", fontsize=6.8)
    for label, ax in zip(["a", "b"], axes):
        ax.text(-0.12, 1.05, label, transform=ax.transAxes, fontsize=9, fontweight="bold")
    fig.subplots_adjust(wspace=0.52)
    save(fig, "fig2_350_model_outcomes", outputs)


def fig3_baseline(accounting: pd.DataFrame, baseline: pd.DataFrame, outputs: list[dict]) -> None:
    rows = []
    for model in MODEL_ORDER:
        b = baseline[baseline["model"] == model]
        a = accounting[accounting["model"] == model]
        rows.append(
            {
                "model": model,
                "baseline_strict": int(bool_series(b["baseline_strict_gate_passed"]).sum()),
                "pearl_strict": int((a["current_outcome"] == "strict_success").sum()),
                "baseline_rea": float(b["baseline_REA"].mean()),
                "pearl_rea": float(a["final_REA"].mean()),
                "baseline_ec": float(b["baseline_EC_CG"].mean()),
                "pearl_ec": float(a["final_CG"].mean()),
            }
        )
    df = pd.DataFrame(rows)
    labels = [MODEL_LABELS[m] for m in df["model"]]
    y = np.arange(len(df))
    width = 0.34
    fig, axes = plt.subplots(1, 3, figsize=(7.5, 2.72), gridspec_kw={"width_ratios": [1.35, 1.05, 1.05]})
    ax = axes[0]
    ax.barh(y - width / 2, df["baseline_strict"], width, color=COLORS["baseline"], label="LLM baseline")
    ax.barh(y + width / 2, df["pearl_strict"], width, color=COLORS["pearl"], label="PEARL")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 74)
    ax.set_xlabel("Strict-success rows")
    ax.legend(frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.56, 1.13))
    for i, (base, pearl) in enumerate(zip(df["baseline_strict"], df["pearl_strict"])):
        ax.text(base + 1.1, i - width / 2, f"{int(base)}", va="center", fontsize=6.4, color=COLORS["baseline"])
        ax.text(pearl + 1.1, i + width / 2, f"{int(pearl)}", va="center", fontsize=6.4, color=COLORS["pearl"])

    for ax, metric, xlabel in zip(axes[1:], ["rea", "ec"], ["Average REA (%)", "Average EC/CG (%)"]):
        before = df[f"baseline_{metric}"]
        after = df[f"pearl_{metric}"]
        ax.barh(y - width / 2, before, width, color=COLORS["baseline"])
        ax.barh(y + width / 2, after, width, color=COLORS["pearl"])
        ax.set_yticks(y)
        ax.set_yticklabels([])
        ax.invert_yaxis()
        ax.set_xlim(0, 1.04)
        ax.set_xticks(np.linspace(0, 1.0, 6))
        ax.set_xticklabels([f"{int(v * 100)}" for v in np.linspace(0, 1.0, 6)])
        ax.set_xlabel(xlabel)
        for i, (base, pearl) in enumerate(zip(before, after)):
            ax.text(base + 0.015, i - width / 2, f"{base * 100:.1f}", va="center", fontsize=5.9, color=COLORS["baseline"])
            ax.text(pearl + 0.015, i + width / 2, f"{pearl * 100:.1f}", va="center", fontsize=5.9, color=COLORS["pearl"])
    for label, ax in zip(["a", "b", "c"], axes):
        ax.text(-0.14, 1.05, label, transform=ax.transAxes, fontsize=9, fontweight="bold")
    fig.subplots_adjust(wspace=0.35)
    save(fig, "fig3_350_llm_baseline_vs_pearl", outputs)


def fig4_ablation(ablation: pd.DataFrame, outputs: list[dict]) -> None:
    labels = ablation["label"].tolist()
    colors = ABLATION_PALETTE[: len(ablation)]
    y = np.arange(len(ablation))
    fig, axes = plt.subplots(1, 3, figsize=(7.45, 3.0))
    ax = axes[0]
    bars = ax.barh(y, ablation["strict_success"], color=colors, height=0.66)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Strict-success rows")
    ax.set_xlim(0, 330)
    for bar, val in zip(bars, ablation["strict_success"]):
        ax.text(val + 5, bar.get_y() + bar.get_height() / 2, f"{int(val)}", va="center", fontsize=6.8)
    for ax, metric, xlabel in zip(axes[1:], ["avg_EC_CG", "avg_REA"], ["Average EC/CG (%)", "Average REA (%)"]):
        bars = ax.barh(y, ablation[metric], color=colors, height=0.66)
        ax.set_yticks(y)
        ax.set_yticklabels([])
        ax.invert_yaxis()
        ax.set_xlim(0, 1.04)
        ax.set_xticks(np.linspace(0, 1.0, 6))
        ax.set_xticklabels([f"{int(v * 100)}" for v in np.linspace(0, 1.0, 6)])
        ax.set_xlabel(xlabel)
        for bar, val in zip(bars, ablation[metric]):
            ax.text(val + 0.015, bar.get_y() + bar.get_height() / 2, f"{val * 100:.1f}", va="center", fontsize=6.6)
    for label, ax in zip(["a", "b", "c"], axes):
        ax.text(-0.12, 1.05, label, transform=ax.transAxes, fontsize=9, fontweight="bold")
    fig.subplots_adjust(wspace=0.36)
    save(fig, "fig4_350_ablation_comparison", outputs)


def fig5_minicheck(minicheck: pd.DataFrame, outputs: list[dict]) -> None:
    labels = ["Raw\nextraction", "LLM\nsecond-pass", "PEARL\nterminal"]
    x = np.arange(len(minicheck))
    w = 0.28
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.45), gridspec_kw={"width_ratios": [1.2, 1.0, 1.0]})
    ax = axes[0]
    bars = ax.bar(x, minicheck["claim_weighted_support_rate"], 0.55, color=COLORS["strict"])
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    format_axis_percent(ax)
    ax.set_ylabel("MiniCheck support (%)")
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.025, f"{bar.get_height() * 100:.1f}", ha="center", fontsize=6.8)
    ax = axes[1]
    bars = ax.bar(x, minicheck["no_evidence_rate"], color=COLORS["residual"], width=0.55)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    format_axis_percent(ax)
    ax.set_ylabel("No-evidence claims (%)")
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.025, f"{bar.get_height() * 100:.1f}", ha="center", fontsize=6.8)
    ax = axes[2]
    scoreable = minicheck["rows_scoreable_for_minicheck"]
    not_scoreable = minicheck["rows_not_scoreable_for_minicheck"]
    ax.bar(x, scoreable, color=COLORS["pearl"], width=0.55, label="Scoreable")
    ax.bar(x, not_scoreable, bottom=scoreable, color=COLORS["residual"], width=0.55, label="Not scoreable")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 375)
    ax.set_ylabel("Rows")
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.16))
    for i, (s, n) in enumerate(zip(scoreable, not_scoreable)):
        ax.text(i, s / 2, f"{int(s)}", ha="center", va="center", color="white", fontsize=7.0, fontweight="bold")
        if n:
            ax.text(i, s + n / 2, f"{int(n)}", ha="center", va="center", color="white", fontsize=6.6, fontweight="bold")
    for label, ax in zip(["a", "b", "c"], axes):
        ax.text(-0.12, 1.05, label, transform=ax.transAxes, fontsize=9, fontweight="bold")
    save(fig, "fig5_350_minicheck_grounding", outputs)


def fig6_residual(accounting: pd.DataFrame, outputs: list[dict]) -> None:
    residual = accounting[accounting["current_outcome"] == "typed_residual"].copy()
    order = residual["failure_type"].value_counts().index.tolist()
    counts = residual["failure_type"].value_counts().loc[order]
    label_map = {
        "preflight:no_anchor_regenerate": "preflight:\nno anchor",
        "final_metric_gate_failed": "final metric\ngate failed",
        "metric_regression": "metric\nregression",
        "final_judge_failed": "final judge\nfailed",
        "repair_budget_exceeded": "repair budget\nexceeded",
    }
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0), gridspec_kw={"width_ratios": [1.0, 1.35]})
    ax = axes[0]
    y = np.arange(len(counts))
    colors = RESIDUAL_PALETTE[: len(counts)]
    bars = ax.barh(y, counts.values, color=colors)
    ax.set_yticks(y)
    ax.set_yticklabels([label_map.get(k, k.replace("_", "\n")) for k in order])
    ax.invert_yaxis()
    ax.set_xlabel("Rows")
    ax.set_xlim(0, max(counts.values) * 1.30)
    for bar, val in zip(bars, counts.values):
        ax.text(val + 0.6, bar.get_y() + bar.get_height() / 2, f"{int(val)}", va="center", fontsize=7.0)
    ax = axes[1]
    residual_by_model = residual.groupby(["model", "failure_type"]).size().unstack(fill_value=0).reindex(MODEL_ORDER)
    bottom = np.zeros(len(MODEL_ORDER))
    x = np.arange(len(MODEL_ORDER))
    for color, failure in zip(colors, order):
        values = residual_by_model.get(failure, pd.Series(0, index=residual_by_model.index)).values
        ax.bar(x, values, bottom=bottom, color=color, label=label_map.get(failure, failure), width=0.64)
        bottom += values
    ax.set_xticks(x)
    ax.set_xticklabels([MODEL_SHORT_LABELS[m] for m in MODEL_ORDER])
    ax.set_ylabel("Typed residual rows")
    ax.set_ylim(0, max(bottom) * 1.15)
    ax.legend(frameon=False, bbox_to_anchor=(1.02, 1.0), loc="upper left")
    for label, ax in zip(["a", "b"], axes):
        ax.text(-0.12, 1.05, label, transform=ax.transAxes, fontsize=9, fontweight="bold")
    fig.subplots_adjust(wspace=0.48)
    save(fig, "fig6_350_residual_taxonomy", outputs)


def fig7_distributions(accounting: pd.DataFrame, outputs: list[dict]) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(7.5, 2.55), gridspec_kw={"width_ratios": [1.0, 1.0, 1.15]})
    for ax, metric, label in zip(axes[:2], ["CG", "REA"], ["EC/CG", "REA"]):
        before = accounting[f"original_{metric}"].astype(float)
        after = accounting[f"final_{metric}"].astype(float)
        bins = np.linspace(0, 1.0, 21)
        ax.hist(before, bins=bins, histtype="stepfilled", alpha=0.42, color=COLORS["baseline"], label="LLM baseline")
        ax.hist(after, bins=bins, histtype="step", lw=1.7, color=COLORS["pearl"], label="PEARL final")
        ax.set_xlabel(label)
        ax.set_ylabel("Rows")
        ax.set_xlim(0, 1.0)
        ax.legend(frameon=False, loc="upper left")
    ax = axes[2]
    colors = accounting["current_outcome"].map({"strict_success": COLORS["strict"], "typed_residual": COLORS["residual"]})
    ax.scatter(accounting["delta_CG"].astype(float), accounting["delta_REA"].astype(float), s=13, c=colors, alpha=0.72, linewidth=0)
    ax.axhline(0, color=COLORS["line"], lw=0.8)
    ax.axvline(0, color=COLORS["line"], lw=0.8)
    ax.set_xlabel("Delta EC/CG")
    ax.set_ylabel("Delta REA")
    ax.text(0.03, 0.94, "strict success\ntyped residual", transform=ax.transAxes, fontsize=6.8, va="top")
    for label, ax in zip(["a", "b", "c"], axes):
        ax.text(-0.12, 1.05, label, transform=ax.transAxes, fontsize=9, fontweight="bold")
    save(fig, "fig7_350_metric_distributions", outputs)


def write_docs(outputs: list[dict], summary: dict, ablation: pd.DataFrame, minicheck: pd.DataFrame) -> None:
    captions = {
        "fig1_350_main_result_overview": "PEARL standard-flow result on the 350-row subset excluding GPT-5.4 and GPT-5.5.",
        "fig2_350_model_outcomes": "Per-model strict-success and typed-residual outcomes for the five-model 350-row subset.",
        "fig3_350_llm_baseline_vs_pearl": "Model-wise comparison between the LLM generation-final baseline and PEARL on the 350-row subset.",
        "fig4_350_ablation_comparison": "Ablation comparison recomputed for the 350-row subset.",
        "fig5_350_minicheck_grounding": "MiniCheck source-grounding audit recomputed for the 350-row subset.",
        "fig6_350_residual_taxonomy": "Typed-residual taxonomy for the 350-row subset.",
        "fig7_350_metric_distributions": "Distributional EC/REA view for the 350-row subset.",
    }
    latex = []
    for item in outputs:
        stem = item["id"]
        latex.append(
            dedent(
                f"""
                % {stem}
                \\begin{{figure}}[t]
                    \\centering
                    \\includegraphics[width=0.95\\textwidth]{{09_image/350_no_gpt54_gpt55/{stem}.pdf}}
                    \\caption{{{captions[stem]}}}
                    \\label{{fig:{stem.replace('_', '-')}}}
                \\end{{figure}}
                """
            ).strip()
        )
    (OUT_DIR / "latex_includes_350.tex").write_text("\n\n".join(latex) + "\n", encoding="utf-8")
    manifest = {
        "scope": "350-row subset excluding gpt_5_4 and gpt_5_5",
        "excluded_models": sorted(EXCLUDED_MODELS),
        "summary": summary,
        "ablation_table": ablation.to_dict(orient="records"),
        "minicheck_summary": minicheck.to_dict(orient="records"),
        "figures": outputs,
    }
    (OUT_DIR / "figure_manifest_350.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# PEARL 350-Row Figures", "", "Scope: excludes `gpt_5_4` and `gpt_5_5`, leaving 350 rows.", "", "## Figures", ""]
    for item in outputs:
        lines.append(f"- `{item['pdf']}` / `{item['png']}`: {captions[item['id']]}")
    lines.extend(["", "## Reproduce", "", "```bash", f"python3 {Path(__file__).relative_to(PACKAGE_ROOT.parent.parent.parent.parent)}", "```", ""])
    (OUT_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / ".mpl_cache").mkdir(exist_ok=True)
    configure_style()
    accounting = subset(pd.read_csv(ACCOUNTING_CSV))
    baseline = subset(pd.read_csv(LLM_BASELINE_CSV))
    summary = metric_summary(accounting)
    ablation = ablation_summary(accounting, baseline)
    minicheck = minicheck_summary(accounting)
    outputs: list[dict] = []
    fig1_overview(accounting, summary, outputs)
    fig2_model_outcomes(accounting, outputs)
    fig3_baseline(accounting, baseline, outputs)
    fig4_ablation(ablation, outputs)
    fig5_minicheck(minicheck, outputs)
    fig6_residual(accounting, outputs)
    fig7_distributions(accounting, outputs)
    write_docs(outputs, summary, ablation, minicheck)
    print(f"Generated {len(outputs)} 350-row figure sets in {OUT_DIR}")


if __name__ == "__main__":
    main()
