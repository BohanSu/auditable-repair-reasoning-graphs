#!/usr/bin/env python3
"""Build a provider-free v2 plan for closing the remaining 350-row residuals.

The script does not call any model provider and does not rewrite canonical
accounting. It reads the strongest guarded proposal available so far, current
source-sensitive audits, packet indexes, and ANS node results, then emits a
typed residual ledger and a pilot queue for the next closeout iteration.
"""

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

BEST_PROPOSAL_ROOT = (
    RESIDUAL_ROOT
    / "proposed_accounting_merges"
    / "20260606_after_315_gemini56657_entity_complete_anssafe"
)
DEFAULT_BASE_ACCOUNTING = BEST_PROPOSAL_ROOT / "PROPOSED_FULL_350_ACCOUNTING.csv"
DEFAULT_BASE_SUMMARY = BEST_PROPOSAL_ROOT / "PROPOSED_FULL_350_SUMMARY.json"
DEFAULT_CANONICAL_ACCOUNTING = PACKAGE_ROOT / "01_final_accounting" / "FULL_350_ACCOUNTING.csv"
DEFAULT_MISSING_ENTITY_TASKS = (
    RESIDUAL_ROOT
    / "missing_entity_audit"
    / "20260604_final_metric_gate_v3_official_steps"
    / "MISSING_ENTITY_PATCH_TASKS.csv"
)
DEFAULT_MISSING_ENTITY_ROW_AUDIT = (
    RESIDUAL_ROOT
    / "missing_entity_audit"
    / "20260604_final_metric_gate_v3_official_steps"
    / "ROW_AUDIT.csv"
)
DEFAULT_PACKET_INDEX = RESIDUAL_ROOT / "evidence_bound_regeneration" / "PACKET_INDEX.csv"
DEFAULT_ANS_NODE_RESULTS = PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_node_results.jsonl"
DEFAULT_FRESH_EVAL_RESULTS = (
    RESIDUAL_ROOT
    / "runs"
    / "source_seed_state_machine_fresh_eval_20260606"
    / "FRESH_EVAL_RESULTS.json"
)
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "closeout_design" / "20260606_residual_closeout_v2"

EXCLUDED_ANS_UNIT_TYPES = {"root_common_bridge", "graph_node"}
MAIN_FACTUAL_STAGES = [
    "raw_step1_extraction",
    "llm_step2_self_fix_final_clean",
    "pearl_terminal_graph",
]

LITERATURE_MAP = [
    {
        "id": "rarr_2022",
        "title": "RARR: Researching and Revising What Language Models Say, Using Language Models",
        "url": "https://arxiv.org/abs/2210.08726",
        "design_import": "Use attribution-aware minimal revision instead of broad rewrites.",
        "pearl_v2_module": "source_backed_coverage_leaf_bridge",
    },
    {
        "id": "factscore_2023",
        "title": "FActScore: Fine-grained Atomic Evaluation of Factual Precision",
        "url": "https://arxiv.org/abs/2305.14251",
        "design_import": "Decompose graph text into atomic factual units before support checks.",
        "pearl_v2_module": "schema_aware_ans_guard",
    },
    {
        "id": "chain_of_verification_2023",
        "title": "Chain-of-Verification Reduces Hallucination in Large Language Models",
        "url": "https://arxiv.org/abs/2309.11495",
        "design_import": "Plan verification questions and answer them independently from the draft.",
        "pearl_v2_module": "independent_anchor_verification_checklist",
    },
    {
        "id": "self_rag_2023",
        "title": "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection",
        "url": "https://arxiv.org/abs/2310.11511",
        "design_import": "Separate retrieve/generate/critique decisions rather than always regenerating.",
        "pearl_v2_module": "candidate_portfolio_selector",
    },
    {
        "id": "crag_2024",
        "title": "Corrective Retrieval Augmented Generation",
        "url": "https://arxiv.org/abs/2401.15884",
        "design_import": "Evaluate evidence quality first, then trigger corrective retrieval or filtering.",
        "pearl_v2_module": "target_anchor_contract_builder",
    },
    {
        "id": "safe_2024",
        "title": "Long-form factuality in large language models",
        "url": "https://arxiv.org/abs/2403.18802",
        "design_import": "Search-augmented factuality evaluation supports independent fact verification.",
        "pearl_v2_module": "schema_aware_ans_guard",
    },
    {
        "id": "refchecker_2024",
        "title": "RefChecker: Reference-based Fine-grained Hallucination Checker",
        "url": "https://arxiv.org/abs/2405.14486",
        "design_import": "Claim-triplets are a better unit than whole sentence/node checks.",
        "pearl_v2_module": "typed_claim_triplet_inventory",
    },
    {
        "id": "ragchecker_2024",
        "title": "RAGChecker: A Fine-grained Framework for Diagnosing RAG",
        "url": "https://arxiv.org/abs/2408.08067",
        "design_import": "Diagnose retrieval/evidence failures separately from generation failures.",
        "pearl_v2_module": "failure_typed_closeout_state_machine",
    },
    {
        "id": "icat_2025",
        "title": "Beyond Factual Accuracy: Evaluating Coverage of Diverse Factual Information",
        "url": "https://arxiv.org/abs/2501.03545",
        "design_import": "Jointly enforce factuality and coverage rather than optimizing either alone.",
        "pearl_v2_module": "pareto_safe_cg_rea_ans_selector",
    },
    {
        "id": "factreasoner_2025",
        "title": "FactReasoner: A Probabilistic Approach to Long-Form Factuality Assessment for Large Language Models",
        "url": "https://arxiv.org/abs/2502.18573",
        "design_import": "Reason over claims and evidence jointly instead of validating each claim in isolation.",
        "pearl_v2_module": "source_seed_reasoning_graph_reconstruction",
    },
    {
        "id": "kg_enhanced_rag_survey_2025",
        "title": "Knowledge Graph-enhanced Large Language Models via Path Selection and Evidence Grounding",
        "url": "https://arxiv.org/abs/2502.18813",
        "design_import": "Use graph/path evidence to constrain long-range entity reasoning and attribution.",
        "pearl_v2_module": "source_seed_reasoning_graph_reconstruction",
    },
    {
        "id": "ragtrace_2025",
        "title": "RAGTrace: Understanding and Refining Retrieval-Generation Dynamics in RAG",
        "url": "https://arxiv.org/abs/2508.06056",
        "design_import": "Trace cross-component interactions when retrieval and generation failures mix.",
        "pearl_v2_module": "residual_lifecycle_trace",
    },
    {
        "id": "trec_rag_2025",
        "title": "Overview of the TREC 2025 Retrieval Augmented Generation Track",
        "url": "https://arxiv.org/abs/2603.09891",
        "design_import": "Use multi-layer evaluation: relevance, completeness, attribution, and agreement.",
        "pearl_v2_module": "multi_guard_acceptance_contract",
    },
    {
        "id": "importance_aware_recall_2026",
        "title": "Beyond Precision: Importance-aware Recall for Evaluating Factuality in Long-Form LLM Generation",
        "url": "https://arxiv.org/abs/2604.03141",
        "design_import": "Do not let factual precision hide important omissions; weight missing facts by importance.",
        "pearl_v2_module": "pareto_safe_cg_rea_ans_selector",
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


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


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
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def has_notation_hazard(text: str) -> bool:
    text = str(text or "")
    if any(ord(ch) > 127 for ch in text):
        return True
    if re.search(r"\b[A-Z][a-z]?\d|\d[A-Z][a-z]?", text):
        return True
    if re.search(r"[-+*/=()[\]{}^_]", text):
        return True
    return False


def count_by_spec(rows: list[dict[str, str]], key: str = "paper_spec") -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        out[row.get(key, "")].append(row)
    return out


def split_semicolon_values(value: Any) -> list[str]:
    values: list[str] = []
    for item in str(value or "").split(";"):
        item = item.strip()
        if item:
            values.append(item)
    return values


def load_fresh_eval_rows(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path, {})
    rows = payload.get("rows") if isinstance(payload, dict) else []
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        spec = str(row.get("paper_spec") or "")
        if not spec:
            continue
        previous = out.get(spec)
        if previous is None:
            out[spec] = row
            continue
        previous_score = (
            to_float(previous.get("CG")),
            to_float(previous.get("REA")),
            1 if previous.get("strict_gate_passed") else 0,
        )
        current_score = (
            to_float(row.get("CG")),
            to_float(row.get("REA")),
            1 if row.get("strict_gate_passed") else 0,
        )
        if current_score > previous_score:
            out[spec] = row
    return out


def load_ans_stats(path: Path) -> dict[str, Any]:
    stage_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"supported": 0, "total": 0, "nodes": 0}
    )
    unit_stats: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"supported": 0, "total": 0, "nodes": 0}
    )
    if not path.exists():
        return {"by_spec": {}, "source": rel(path), "exists": False}

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            stage = str(row.get("stage") or "")
            if stage not in MAIN_FACTUAL_STAGES:
                continue
            if row.get("unit_type") in EXCLUDED_ANS_UNIT_TYPES:
                continue
            spec = str(row.get("paper_spec") or "")
            supported = int(row.get("supported_fact_count") or 0)
            total = int(row.get("atomic_fact_count") or 0)
            cell = stage_stats[(spec, stage)]
            cell["supported"] += supported
            cell["total"] += total
            cell["nodes"] += 1
            unit_cell = unit_stats[(spec, stage, str(row.get("unit_type") or ""))]
            unit_cell["supported"] += supported
            unit_cell["total"] += total
            unit_cell["nodes"] += 1

    by_spec: dict[str, dict[str, Any]] = {}
    all_specs = {spec for spec, _stage in stage_stats}
    for spec in all_specs:
        stage_rows: dict[str, Any] = {}
        best_score = None
        best_stage = ""
        for stage in MAIN_FACTUAL_STAGES:
            cell = stage_stats[(spec, stage)]
            score = cell["supported"] / cell["total"] if cell["total"] else None
            stage_rows[stage] = {
                "supported": cell["supported"],
                "total": cell["total"],
                "nodes": cell["nodes"],
                "ans": score,
            }
            if score is not None and (best_score is None or score > best_score):
                best_score = score
                best_stage = stage
        by_spec[spec] = {
            "stages": stage_rows,
            "current_best_stage": best_stage,
            "current_best_main_factual_ans": best_score,
        }
    return {"by_spec": by_spec, "source": rel(path), "exists": True}


def score_group_ans(ans_by_spec: dict[str, Any], specs: set[str], stage: str) -> dict[str, Any]:
    supported = 0
    total = 0
    nodes = 0
    covered_specs = 0
    for spec in specs:
        item = ans_by_spec.get(spec) or {}
        stage_item = (item.get("stages") or {}).get(stage) or {}
        if stage_item.get("total"):
            covered_specs += 1
        supported += int(stage_item.get("supported") or 0)
        total += int(stage_item.get("total") or 0)
        nodes += int(stage_item.get("nodes") or 0)
    return {
        "stage": stage,
        "specs_requested": len(specs),
        "specs_with_ans": covered_specs,
        "nodes": nodes,
        "supported": supported,
        "total": total,
        "ans": supported / total if total else None,
    }


def module_for_row(
    row: dict[str, str],
    missing_tasks: list[dict[str, str]],
    row_audit: dict[str, str],
    packet: dict[str, str],
    fresh_eval: dict[str, Any],
    same_paper_success_count: int,
    ans: dict[str, Any],
) -> tuple[str, str, str, int, list[str]]:
    spec = row.get("paper_spec", "")
    failure = row.get("failure_type", "")
    final_cg = to_float(row.get("final_CG"))
    final_rea = to_float(row.get("final_REA"))
    missing_entities = [task.get("entity", "") for task in missing_tasks if task.get("entity")]
    fresh_missing_entities = split_semicolon_values(fresh_eval.get("missing_entities"))
    if fresh_missing_entities:
        missing_entities = fresh_missing_entities
    notation_entities = [entity for entity in missing_entities if has_notation_hazard(entity)]
    weak_count = int(packet.get("weak_or_missing_entity_count") or 0) if packet else 0
    current_best_ans = ans.get("current_best_main_factual_ans")
    fresh_cg = to_float(fresh_eval.get("CG"), final_cg)
    fresh_rea = to_float(fresh_eval.get("REA"), final_rea)

    if spec == "qwen3_5_397b_a17b:s41467-025-56769-y":
        return (
            "claim_typed_reconstruction_with_ans_guard",
            "high",
            "Prior fresh 1/1 source-leaf candidates did not satisfy the ANS guard; rebuild implicit reasoning as source-backed or typed factual units instead of adding another leaf.",
            38,
            ["FActScore", "RefChecker", "SAFE"],
        )

    if failure.startswith("preflight:no_anchor"):
        if fresh_eval and fresh_rea >= 1.0 and 0.0 < fresh_cg < 1.0:
            if len(missing_entities) == 1 and fresh_missing_entities:
                return (
                    "no_anchor_anchor_abstraction_bridge",
                    "high",
                    "Source-seed fresh evaluation has valid reasoning but one abstract anchor remains uncovered; verify anchor normalization before adding graph text.",
                    18,
                    ["RAGChecker", "ICAT", "RefChecker"],
                )
            if len(missing_entities) <= 2:
                return (
                    "no_anchor_source_leaf_completion",
                    "medium",
                    "Source-seed fresh evaluation already has REA=1.0; add exact source-backed leaves for the few missing anchors and keep root as source inventory.",
                    16 + len(missing_entities),
                    ["RARR", "SAFE", "ICAT"],
                )
            return (
                "no_anchor_multi_anchor_source_bridge",
                "medium",
                "Source-seed fresh evaluation already has REA=1.0 but several anchors are still missing; build a compact evidence-backed bridge with ANS guard.",
                24 + len(missing_entities),
                ["RARR", "RAGChecker", "FactReasoner"],
            )
        if weak_count:
            return (
                "no_anchor_retrieve_expand_then_source_seed",
                "high",
                "The old graph has no safe anchor and the packet reports weak/missing evidence; expand or normalize evidence before graph generation.",
                70 + weak_count,
                ["RAGChecker", "CRAG", "FactReasoner"],
            )
        if same_paper_success_count > 0 and current_best_ans is not None and current_best_ans >= 0.75:
            return (
                "no_anchor_same_paper_template_plus_source_seed",
                "medium",
                "Use accepted same-paper structure only as a template, then rebuild factual nodes from this row's own source evidence.",
                45,
                ["Self-RAG", "FactReasoner", "TREC RAG 2025"],
            )
        return (
            "no_anchor_source_seed_reconstruction",
            "high",
            "Build a minimal source-grounded seed graph before invoking semantic repair; do not repair the old no-anchor graph.",
            60,
            ["RAGChecker", "CRAG", "FactReasoner"],
        )

    if failure == "final_metric_gate_failed":
        if final_rea >= 1.0 and final_cg < 1.0:
            if notation_entities:
                return (
                    "notation_normalized_source_bridge",
                    "medium",
                    "Replay entity normalization and add source-backed bridge only if normalized coverage still fails.",
                    15 + len(missing_entities),
                    ["ICAT", "RefChecker", "RARR"],
                )
            if len(missing_entities) <= 2:
                return (
                    "single_or_dual_source_leaf_bridge",
                    "low",
                    "Add one or two source-backed leaves or low-risk bridges while preserving all currently correct reasoning.",
                    10 + len(missing_entities),
                    ["RARR", "ICAT", "SAFE"],
                )
            return (
                "multi_entity_source_bridge_reconstruction",
                "medium",
                "Bundle missing entities into a compact source-backed bridge instead of independent broad rewrites.",
                20 + len(missing_entities),
                ["RARR", "ICAT", "RefChecker"],
            )
        return (
            "metric_gate_with_reasoning_rewrite",
            "high",
            "CG and REA are both unsafe; reconstruct claim-typed reasoning units before coverage edits.",
            50,
            ["RefChecker", "FactReasoner"],
        )

    if failure == "metric_regression":
        return (
            "pareto_safe_rollback_or_hybrid_selector",
            "medium",
            "Compare source, terminal, donor, and hybrid candidates and accept only non-regressing CG/REA/ANS candidates.",
            25,
            ["ICAT", "RARR", "RAGTrace"],
        )

    if failure == "final_judge_failed":
        return (
            "judge_reason_targeted_reconstruction",
            "medium",
            "Use rejected reasoning targets as a verification checklist and rebuild only those semantic units.",
            22,
            ["Chain-of-Verification", "RefChecker"],
        )

    return (
        "manual_state_audit",
        "high",
        "The row does not match a known v2 closeout lane.",
        90,
        ["RAGTrace"],
    )


def build(args: argparse.Namespace) -> dict[str, Any]:
    base_accounting = resolve(args.base_accounting)
    base_summary_path = resolve(args.base_summary)
    canonical_accounting = resolve(args.canonical_accounting)
    missing_entity_tasks_path = resolve(args.missing_entity_tasks)
    missing_entity_row_audit_path = resolve(args.missing_entity_row_audit)
    packet_index_path = resolve(args.packet_index)
    ans_node_results_path = resolve(args.ans_node_results)
    fresh_eval_results_path = resolve(args.fresh_eval_results)
    out_root = resolve(args.out_root)

    accounting_rows = read_csv(base_accounting)
    canonical_rows = read_csv(canonical_accounting)
    base_summary = read_json(base_summary_path, {})
    missing_tasks = read_csv(missing_entity_tasks_path) if missing_entity_tasks_path.exists() else []
    row_audits = read_csv(missing_entity_row_audit_path) if missing_entity_row_audit_path.exists() else []
    packet_rows = read_csv(packet_index_path) if packet_index_path.exists() else []
    fresh_eval_by_spec = load_fresh_eval_rows(fresh_eval_results_path) if fresh_eval_results_path.exists() else {}

    missing_by_spec = count_by_spec(missing_tasks)
    row_audit_by_spec = {row.get("paper_spec", ""): row for row in row_audits}
    packet_by_spec = {row.get("paper_spec", ""): row for row in packet_rows}
    ans_stats = load_ans_stats(ans_node_results_path)
    ans_by_spec = ans_stats["by_spec"]

    strict_specs = {row.get("paper_spec", "") for row in accounting_rows if row.get("current_outcome") == "strict_success"}
    residual_rows = [row for row in accounting_rows if row.get("current_outcome") == "typed_residual"]
    canonical_strict_specs = {
        row.get("paper_spec", "") for row in canonical_rows if row.get("current_outcome") == "strict_success"
    }
    strict_papers = Counter(row.get("paper", "") for row in accounting_rows if row.get("current_outcome") == "strict_success")

    canonical_300_ans = score_group_ans(ans_by_spec, canonical_strict_specs, "pearl_terminal_graph")
    proposal_strict_ans = score_group_ans(ans_by_spec, strict_specs, "pearl_terminal_graph")

    ledger_rows: list[dict[str, Any]] = []
    for row in residual_rows:
        spec = row.get("paper_spec", "")
        paper = row.get("paper", "")
        row_missing_tasks = missing_by_spec.get(spec, [])
        row_audit = row_audit_by_spec.get(spec, {})
        packet = packet_by_spec.get(spec, {})
        fresh_eval = fresh_eval_by_spec.get(spec, {})
        ans = ans_by_spec.get(spec, {})
        module, risk, rationale, priority, sources = module_for_row(
            row=row,
            missing_tasks=row_missing_tasks,
            row_audit=row_audit,
            packet=packet,
            fresh_eval=fresh_eval,
            same_paper_success_count=strict_papers.get(paper, 0),
            ans=ans,
        )
        missing_entities = [task.get("entity", "") for task in row_missing_tasks if task.get("entity")]
        fresh_missing_entities = split_semicolon_values(fresh_eval.get("missing_entities"))
        if fresh_missing_entities:
            missing_entities = fresh_missing_entities
        notation_entities = [entity for entity in missing_entities if has_notation_hazard(entity)]
        current_best_ans = ans.get("current_best_main_factual_ans")
        terminal_ans = ((ans.get("stages") or {}).get("pearl_terminal_graph") or {}).get("ans")
        if current_best_ans is not None and current_best_ans < 0.65:
            priority += 8
        if packet and not packet.get("weak_or_missing_entity_count"):
            priority -= 2
        if row_missing_tasks and all(task.get("top_evidence_exact") == "True" for task in row_missing_tasks):
            priority -= 3

        ledger_rows.append(
            {
                "paper_spec": spec,
                "model": row.get("model", ""),
                "paper": paper,
                "failure_type": row.get("failure_type", ""),
                "final_CG": row.get("final_CG", ""),
                "final_REA": row.get("final_REA", ""),
                "v2_module": module,
                "risk": risk,
                "priority_score": priority,
                "same_paper_strict_success_count": strict_papers.get(paper, 0),
                "missing_entity_count": len(missing_entities),
                "missing_entities": "; ".join(missing_entities),
                "notation_sensitive_entities": "; ".join(notation_entities),
                "row_audit_missing_count": row_audit.get("computed_missing_count", ""),
                "row_audit_anchor_entity_count": row_audit.get("anchor_entity_count", ""),
                "packet_available": bool(packet),
                "packet_json": packet.get("packet_json", ""),
                "packet_prompt": packet.get("generation_prompt", ""),
                "packet_entity_count": packet.get("entity_count", ""),
                "packet_weak_or_missing_entity_count": packet.get("weak_or_missing_entity_count", ""),
                "source_seed_candidate_label": fresh_eval.get("candidate_label", ""),
                "source_seed_CG": fresh_eval.get("CG", ""),
                "source_seed_REA": fresh_eval.get("REA", ""),
                "source_seed_strict_gate_passed": fresh_eval.get("strict_gate_passed", ""),
                "source_seed_missing_entities": fresh_eval.get("missing_entities", ""),
                "source_seed_judge_provider_error": fresh_eval.get("judge_provider_error", ""),
                "source_seed_fresh_eval_dir": fresh_eval.get("eval_dir", ""),
                "source_seed_graph": fresh_eval.get("dot", ""),
                "current_best_main_factual_ans": current_best_ans if current_best_ans is not None else "",
                "current_best_ans_stage": ans.get("current_best_stage", ""),
                "terminal_main_factual_ans": terminal_ans if terminal_ans is not None else "",
                "ans_guard_floor": current_best_ans if current_best_ans is not None else "",
                "batch_ans_floor": canonical_300_ans.get("ans"),
                "acceptance_contract": "fresh_CG=1.0;fresh_REA=1.0;judge_provider_error=false;ANS>=row_floor;batch_ANS>=canonical_300_floor;fresh_eval_hash_matches=true",
                "next_artifact": f"{module}/{spec.replace(':', '__')}",
                "design_sources": "; ".join(sources),
                "rationale": rationale,
            }
        )

    ledger_rows.sort(key=lambda item: (int(item["priority_score"]), item["paper_spec"]))
    module_counts = Counter(row["v2_module"] for row in ledger_rows)
    failure_counts = Counter(row["failure_type"] for row in ledger_rows)
    risk_counts = Counter(row["risk"] for row in ledger_rows)
    model_counts = Counter(row["model"] for row in ledger_rows)

    pilot_rows = select_pilot_rows(ledger_rows)
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "objective": "Move the 350-row package from the selected guarded proposal toward 350/0 while preserving or improving the current 300-row ANS level.",
        "source_base_accounting": rel(base_accounting),
        "source_base_summary": rel(base_summary_path),
        "source_canonical_accounting": rel(canonical_accounting),
        "source_missing_entity_tasks": rel(missing_entity_tasks_path),
        "source_packet_index": rel(packet_index_path),
        "source_ans_node_results": rel(ans_node_results_path),
        "source_fresh_eval_results": rel(fresh_eval_results_path),
        "out_root": rel(out_root),
        "base_summary_counts": {
            "strict_success_rows": base_summary.get("strict_success_rows"),
            "typed_residual_rows": base_summary.get("typed_residual_rows"),
            "failure_type_counts": base_summary.get("failure_type_counts"),
        },
        "ledger_residual_rows": len(ledger_rows),
        "module_counts": dict(module_counts),
        "failure_type_counts": dict(failure_counts),
        "risk_counts": dict(risk_counts),
        "model_counts": dict(model_counts),
        "canonical_300_terminal_main_factual_ans": canonical_300_ans,
        "proposal_strict_terminal_main_factual_ans_on_current_ans_artifacts": proposal_strict_ans,
        "pilot_queue_rows": len(pilot_rows),
        "pilot_policy": {
            "coverage": "Include easy metric-gate rows, at least one judge/regression row, and no-anchor seed rows.",
            "no_canonical_write": True,
            "acceptance_contract": "fresh_CG=1.0, fresh_REA=1.0, no judge/provider contamination, row-level ANS non-regression, batch ANS >= canonical_300 floor.",
        },
        "literature_map": LITERATURE_MAP,
    }

    ledger_fieldnames = [
        "paper_spec",
        "model",
        "paper",
        "failure_type",
        "final_CG",
        "final_REA",
        "v2_module",
        "risk",
        "priority_score",
        "same_paper_strict_success_count",
        "missing_entity_count",
        "missing_entities",
        "notation_sensitive_entities",
        "row_audit_missing_count",
        "row_audit_anchor_entity_count",
        "packet_available",
        "packet_json",
                "packet_prompt",
                "packet_entity_count",
                "packet_weak_or_missing_entity_count",
                "source_seed_candidate_label",
                "source_seed_CG",
                "source_seed_REA",
                "source_seed_strict_gate_passed",
                "source_seed_missing_entities",
                "source_seed_judge_provider_error",
                "source_seed_fresh_eval_dir",
                "source_seed_graph",
                "current_best_main_factual_ans",
        "current_best_ans_stage",
        "terminal_main_factual_ans",
        "ans_guard_floor",
        "batch_ans_floor",
        "acceptance_contract",
        "next_artifact",
        "design_sources",
        "rationale",
    ]
    pilot_fieldnames = ["pilot_rank", *ledger_fieldnames]
    for idx, row in enumerate(pilot_rows, start=1):
        row["pilot_rank"] = idx

    out_root.mkdir(parents=True, exist_ok=True)
    write_csv(out_root / "V2_RESIDUAL_LEDGER.csv", ledger_rows, ledger_fieldnames)
    write_json(out_root / "V2_RESIDUAL_LEDGER.json", {"summary": summary, "rows": ledger_rows})
    write_csv(out_root / "V2_PILOT_QUEUE.csv", pilot_rows, pilot_fieldnames)
    write_json(out_root / "V2_PILOT_QUEUE.json", {"summary": summary, "rows": pilot_rows})
    write_json(out_root / "V2_LITERATURE_MAP.json", LITERATURE_MAP)
    write_text(out_root / "V2_DESIGN.md", render_markdown(summary, pilot_rows))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def select_pilot_rows(ledger_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_specs: set[str] = set()

    def add_matching(predicate: Any, limit: int) -> None:
        for row in ledger_rows:
            if len([item for item in selected if predicate(item)]) >= limit:
                return
            if row["paper_spec"] in selected_specs or not predicate(row):
                continue
            selected.append(dict(row))
            selected_specs.add(row["paper_spec"])

    add_matching(lambda r: r["v2_module"] == "single_or_dual_source_leaf_bridge", 3)
    add_matching(lambda r: r["v2_module"] == "notation_normalized_source_bridge", 2)
    add_matching(lambda r: r["v2_module"] == "pareto_safe_rollback_or_hybrid_selector", 2)
    add_matching(lambda r: r["v2_module"] == "judge_reason_targeted_reconstruction", 1)
    add_matching(lambda r: str(r["v2_module"]).startswith("no_anchor"), 3)

    for row in ledger_rows:
        if len(selected) >= 12:
            break
        if row["paper_spec"] in selected_specs:
            continue
        selected.append(dict(row))
        selected_specs.add(row["paper_spec"])
    return selected


def render_markdown(summary: dict[str, Any], pilot_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Residual Closeout V2 Plan",
        "",
        f"Created: `{summary['created_at']}`",
        "",
        "This artifact is provider-free and does not rewrite canonical accounting.",
        "",
        "## Current Boundary",
        "",
        f"- Base accounting: `{summary['source_base_accounting']}`",
        f"- Base strict/residual: `{summary['base_summary_counts']['strict_success_rows']}` / `{summary['base_summary_counts']['typed_residual_rows']}`",
        f"- Ledger residual rows: `{summary['ledger_residual_rows']}`",
        f"- Canonical write: `{summary['canonical_accounting_write']}`",
        "",
        "## ANS Guard",
        "",
        "Acceptance must preserve both row-level and batch-level ANS:",
        "",
        f"- Canonical 300 terminal main-factual ANS floor: `{summary['canonical_300_terminal_main_factual_ans'].get('ans')}`",
        f"- Specs covered by the ANS floor: `{summary['canonical_300_terminal_main_factual_ans'].get('specs_with_ans')}` / `{summary['canonical_300_terminal_main_factual_ans'].get('specs_requested')}`",
        "- Row-level floor: candidate main-factual ANS must be no lower than that row's current best available main-factual ANS.",
        "",
        "## V2 Modules",
        "",
    ]
    for module, count in sorted(summary["module_counts"].items()):
        lines.append(f"- `{module}`: `{count}` rows")
    lines.extend(
        [
            "",
            "## Pilot Queue",
            "",
            "| rank | paper_spec | failure | module | priority | row ANS floor |",
            "|---:|---|---|---|---:|---:|",
        ]
    )
    for row in pilot_rows:
        lines.append(
            "| {rank} | {spec} | {failure} | {module} | {priority} | {ans} |".format(
                rank=row.get("pilot_rank", ""),
                spec=row.get("paper_spec", ""),
                failure=row.get("failure_type", ""),
                module=row.get("v2_module", ""),
                priority=row.get("priority_score", ""),
                ans=row.get("ans_guard_floor", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Literature-to-Design Mapping",
            "",
            "The design follows recent work by turning residual repair into claim/evidence/verification/selection rather than blind regeneration:",
            "",
        ]
    )
    for item in summary["literature_map"]:
        lines.append(
            f"- `{item['id']}` -> `{item['pearl_v2_module']}`: {item['design_import']} {item['url']}"
        )
    lines.extend(
        [
            "",
            "## Contribution Boundary",
            "",
            "The intended contribution is not just applying a RAG evaluator. The PEARL-specific contribution is a current-version residual repair state machine that treats fixed-anchor CG, fresh REA, source tuples, and ANS as separate contracts, then selects candidates on the Pareto frontier rather than optimizing one metric at a time.",
            "",
            "## Next Execution Step",
            "",
            "Run the pilot queue in module order. Each candidate must produce a strict-merge package only after fresh evaluation reports `CG=1.0`, `REA=1.0`, no provider/judge contamination, a matching fresh-eval hash, and row/batch ANS non-regression.",
            "",
            "## Output Files",
            "",
            f"- `{summary['out_root']}/V2_RESIDUAL_LEDGER.csv`",
            f"- `{summary['out_root']}/V2_RESIDUAL_LEDGER.json`",
            f"- `{summary['out_root']}/V2_PILOT_QUEUE.csv`",
            f"- `{summary['out_root']}/V2_PILOT_QUEUE.json`",
            f"- `{summary['out_root']}/V2_LITERATURE_MAP.json`",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-accounting", default=str(DEFAULT_BASE_ACCOUNTING))
    parser.add_argument("--base-summary", default=str(DEFAULT_BASE_SUMMARY))
    parser.add_argument("--canonical-accounting", default=str(DEFAULT_CANONICAL_ACCOUNTING))
    parser.add_argument("--missing-entity-tasks", default=str(DEFAULT_MISSING_ENTITY_TASKS))
    parser.add_argument("--missing-entity-row-audit", default=str(DEFAULT_MISSING_ENTITY_ROW_AUDIT))
    parser.add_argument("--packet-index", default=str(DEFAULT_PACKET_INDEX))
    parser.add_argument("--ans-node-results", default=str(DEFAULT_ANS_NODE_RESULTS))
    parser.add_argument("--fresh-eval-results", default=str(DEFAULT_FRESH_EVAL_RESULTS))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
