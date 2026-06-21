#!/usr/bin/env python3
"""Build the current-version residual repair design ledger for claim-level reconstruction."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_RESIDUAL_CSV = (
    RESIDUAL_ROOT
    / "proposed_accounting_merges"
    / "20260604_combined_guarded_v5_plus_anchor_v3_plus_same_paper_v2_plus_ans_surface_v4_plus_coverage_v5_plus_rub15_v1"
    / "PROPOSED_TYPED_RESIDUAL_350.csv"
)
DEFAULT_PROPOSED_SUMMARY = DEFAULT_RESIDUAL_CSV.with_name("PROPOSED_FULL_350_SUMMARY.json")
DEFAULT_MISSING_ENTITY_CSV = (
    RESIDUAL_ROOT
    / "missing_entity_audit"
    / "20260604_final_metric_gate_v3_official_steps"
    / "MISSING_ENTITY_PATCH_TASKS.csv"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260604_evidence_bound_claim_reconstruction"
)

LITERATURE_NOTES = [
    {
        "id": "factscore_2023",
        "url": "https://arxiv.org/abs/2305.14251",
        "design_constraint": "Use atomic factual support instead of whole-response binary scoring.",
    },
    {
        "id": "safe_2024",
        "url": "https://arxiv.org/abs/2403.18802",
        "design_constraint": "Break long-form outputs into individual facts and verify each against evidence.",
    },
    {
        "id": "refchecker_2024",
        "url": "https://arxiv.org/abs/2405.14486",
        "design_constraint": "Represent claims as fine-grained triplets before reference checking.",
    },
    {
        "id": "veriscore_2024",
        "url": "https://arxiv.org/abs/2406.19276",
        "design_constraint": "Separate verifiable claims from unverifiable or structural text.",
    },
    {
        "id": "ragchecker_2024",
        "url": "https://arxiv.org/abs/2408.08067",
        "design_constraint": "Diagnose retrieval/evidence and generation correctness separately.",
    },
    {
        "id": "factreasoner_2025",
        "url": "https://arxiv.org/abs/2502.18573",
        "design_constraint": "Model support as evidence-conditioned entailment rather than surface overlap.",
    },
    {
        "id": "verifastscore_2025",
        "url": "https://arxiv.org/abs/2505.16973",
        "design_constraint": "Batch claim extraction and verification to reduce repeated LLM calls.",
    },
    {
        "id": "fastfact_2025",
        "url": "https://arxiv.org/abs/2510.12839",
        "design_constraint": "Use chunk-level claim extraction and document-level evidence, not one-line snippets only.",
    },
]


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def as_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def has_notation_hazard(text: str) -> bool:
    if any(ord(ch) > 127 for ch in text):
        return True
    if re.search(r"\b[A-Z][a-z]?\d|\d[A-Z][a-z]?", text):
        return True
    return False


def classify(row: dict[str, str], missing_rows: list[dict[str, str]]) -> tuple[str, str, str]:
    failure_type = row.get("failure_type", "")
    final_cg = as_float(row.get("final_CG"))
    final_rea = as_float(row.get("final_REA"))
    missing_entities = [item.get("entity", "") for item in missing_rows if item.get("entity")]
    notation_hazard = any(has_notation_hazard(entity) for entity in missing_entities)

    if failure_type.startswith("preflight:no_anchor"):
        return (
            "evidence_bound_claim_graph_reconstruction",
            "Raw graph has no majority-correct anchor; rebuild from paper evidence instead of looping PEARL anchors.",
            "high",
        )
    if failure_type == "metric_regression":
        return (
            "pareto_safe_cg_rea_reconstruction",
            "Existing repair improves one metric but regresses another; require multi-objective candidate selection.",
            "high",
        )
    if failure_type == "final_judge_failed":
        return (
            "fresh_judge_semantic_rewrite",
            "Fresh evaluator rejects at least one reasoning unit; repair the rejected semantic unit before coverage edits.",
            "medium",
        )
    if failure_type == "final_metric_gate_failed":
        if final_cg < 1.0 and final_rea >= 1.0:
            if notation_hazard:
                return (
                    "notation_normalized_replay_then_source_bridge",
                    "CG may be blocked by scientific notation or alphanumeric entity mismatch; replay after protocol normalization before graph edits.",
                    "medium",
                )
            if len(missing_entities) >= 3:
                return (
                    "multi_entity_evidence_bridge_reconstruction",
                    "Several entities are missing; build a compact evidence-backed bridge rather than independent leaves.",
                    "medium",
                )
            return (
                "source_backed_coverage_leaf_bridge",
                "REA is already closed; add minimal source-backed factual leaves or bridges for missing entities.",
                "medium",
            )
        if final_rea < 1.0:
            return (
                "claim_typed_reasoning_rewrite",
                "Coverage repair alone is unsafe because reasoning validity is still below strict closure.",
                "high",
            )
    return (
        "manual_audit_required",
        "Residual state does not match a known automated closeout lane.",
        "high",
    )


def build(args: argparse.Namespace) -> dict[str, Any]:
    residual_csv = resolve(args.residual_csv)
    proposed_summary_path = resolve(args.proposed_summary)
    missing_entity_csv = resolve(args.missing_entity_csv)
    out_root = resolve(args.out_root)

    residual_rows = read_csv(residual_csv)
    missing_rows = read_csv(missing_entity_csv) if missing_entity_csv.exists() else []
    missing_by_spec: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in missing_rows:
        missing_by_spec[item.get("paper_spec", "")].append(item)

    ledger_rows: list[dict[str, Any]] = []
    for row in residual_rows:
        paper_spec = row.get("paper_spec", "")
        row_missing = missing_by_spec.get(paper_spec, [])
        module, rationale, risk = classify(row, row_missing)
        missing_entities = [item.get("entity", "") for item in row_missing if item.get("entity")]
        notation_entities = [entity for entity in missing_entities if has_notation_hazard(entity)]
        ans_conflict = paper_spec == "qwen3_5_397b_a17b:s41467-025-56769-y"
        if ans_conflict:
            module = "claim_typed_reconstruction_with_ans_guard"
            rationale = "Fresh 1/1 candidates exist, but inherited implicit reasoning fails ANS guard."
            risk = "high"

        ledger_rows.append(
            {
                "paper_spec": paper_spec,
                "model": row.get("model", ""),
                "paper": row.get("paper", ""),
                "failure_type": row.get("failure_type", ""),
                "final_CG": row.get("final_CG", ""),
                "final_REA": row.get("final_REA", ""),
                "next_closeout_module": module,
                "risk": risk,
                "missing_entity_count": len(missing_entities),
                "missing_entities": "; ".join(missing_entities),
                "notation_sensitive_entities": "; ".join(notation_entities),
                "ans_guard_conflict_known": ans_conflict,
                "rationale": rationale,
            }
        )

    module_counts = Counter(row["next_closeout_module"] for row in ledger_rows)
    failure_counts = Counter(row["failure_type"] for row in ledger_rows)
    model_counts = Counter(row["model"] for row in ledger_rows)
    risk_counts = Counter(row["risk"] for row in ledger_rows)
    symbol_candidates = [
        row
        for row in ledger_rows
        if row["notation_sensitive_entities"] or row["next_closeout_module"].startswith("notation_normalized")
    ]

    proposed_summary = {}
    if proposed_summary_path.exists():
        proposed_summary = json.loads(proposed_summary_path.read_text(encoding="utf-8"))

    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_residual_csv": rel(residual_csv),
        "source_proposed_summary": rel(proposed_summary_path),
        "source_missing_entity_csv": rel(missing_entity_csv),
        "out_root": rel(out_root),
        "input_residual_rows": len(residual_rows),
        "latest_proposed_strict_success_rows": proposed_summary.get("strict_success_rows"),
        "latest_proposed_typed_residual_rows": proposed_summary.get("typed_residual_rows"),
        "failure_type_counts": dict(failure_counts),
        "next_closeout_module_counts": dict(module_counts),
        "model_counts": dict(model_counts),
        "risk_counts": dict(risk_counts),
        "notation_protocol_replay_candidates": len(symbol_candidates),
        "known_ans_conflict_rows": [
            row["paper_spec"] for row in ledger_rows if row["ans_guard_conflict_known"]
        ],
        "literature_notes": LITERATURE_NOTES,
    }

    fieldnames = [
        "paper_spec",
        "model",
        "paper",
        "failure_type",
        "final_CG",
        "final_REA",
        "next_closeout_module",
        "risk",
        "missing_entity_count",
        "missing_entities",
        "notation_sensitive_entities",
        "ans_guard_conflict_known",
        "rationale",
    ]
    out_root.mkdir(parents=True, exist_ok=True)
    write_csv(out_root / "RESIDUAL_CLOSEOUT_LEDGER.csv", ledger_rows, fieldnames)
    write_json(out_root / "RESIDUAL_CLOSEOUT_LEDGER.json", {"summary": summary, "rows": ledger_rows})
    write_text(out_root / "RESIDUAL_CLOSEOUT_DESIGN.md", render_markdown(summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Residual Closeout Design",
        "",
        f"Created: `{summary['created_at']}`",
        "",
        "This is a design ledger, not a canonical accounting rewrite.",
        "",
        "## Current State",
        "",
        f"- Latest proposed strict success rows: `{summary.get('latest_proposed_strict_success_rows')}`",
        f"- Latest proposed typed residual rows: `{summary.get('latest_proposed_typed_residual_rows')}`",
        f"- Input residual rows in ledger: `{summary.get('input_residual_rows')}`",
        f"- Provider calls: `{summary.get('provider_calls')}`",
        f"- Canonical accounting write: `{summary.get('canonical_accounting_write')}`",
        "",
        "## Closeout Lanes",
        "",
    ]
    for module, count in sorted(summary["next_closeout_module_counts"].items()):
        lines.append(f"- `{module}`: `{count}` rows")
    lines.extend(
        [
            "",
            "## Design Constraints from Literature",
            "",
            "The current version module should not be a blind full-regeneration step. Recent factuality work consistently points to claim-level or atomic-unit verification, evidence-aware checking, and explicit separation between verifiable factual claims and structural text.",
            "",
        ]
    )
    for note in summary["literature_notes"]:
        lines.append(f"- `{note['id']}`: {note['design_constraint']} {note['url']}")
    lines.extend(
        [
            "",
            "## Proposed Module Shape",
            "",
            "1. Evidence-bound claim inventory: extract source-backed factual units from `input_data.json` and candidate graph nodes.",
            "2. Typed graph reconstruction: emit source-backed factual nodes, structural inference operators, and semantic root separately.",
            "3. Scientific-notation normalization replay: replay CG for rows with symbol, subscript, superscript, or alphanumeric entity hazards before editing graphs.",
            "4. Multi-objective candidate selection: accept only candidates that pass fresh CG/REA, do not regress current ANS, and have no provider errors.",
            "5. Typed residual retention: if no candidate satisfies all guards, keep the row as typed residual with the failing guard recorded.",
            "",
            "## Protocol Smoke Evidence",
            "",
            "A provider-free replay on `qwen3_5_397b_a17b:s41467-025-56871-1` reused saved fresh-evaluation judgments and changed only the CG normalization/evidence-tokenization path. The stored fresh result was `CG=0.8333333333333335` with `5/6` entities. The replayed result is `CG=1.0` with `6/6` entities, confirming that at least this row is blocked by notation normalization rather than graph semantics.",
            "",
            f"- `{summary['out_root']}/NOTATION_REPLAY_SMOKE_QWEN56871.json`",
            "",
            "## Output Files",
            "",
            f"- `{summary['out_root']}/RESIDUAL_CLOSEOUT_LEDGER.csv`",
            f"- `{summary['out_root']}/RESIDUAL_CLOSEOUT_LEDGER.json`",
            f"- `{summary['out_root']}/RESIDUAL_CLOSEOUT_DESIGN.md`",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--residual-csv", default=str(DEFAULT_RESIDUAL_CSV))
    parser.add_argument("--proposed-summary", default=str(DEFAULT_PROPOSED_SUMMARY))
    parser.add_argument("--missing-entity-csv", default=str(DEFAULT_MISSING_ENTITY_CSV))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
