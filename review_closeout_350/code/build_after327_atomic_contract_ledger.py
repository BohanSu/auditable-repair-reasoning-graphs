#!/usr/bin/env python3
"""Build after327 atomic residual contracts and repair explanations.

This provider-free controller layer turns the current after327 queue into a
row-local contract ledger. It does not write canonical accounting and does not
accept any row; it only records the exact route, allowed edit surface, evidence
preflight, and remaining verification gates for each residual.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DIAGNOSTIC_ROOT = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_after327_failure_typed_lit_diagnostic"
)
DEFAULT_CONTROLLER_QUEUE = DIAGNOSTIC_ROOT / "controller_queue_v1" / "AFTER327_CONTROLLER_QUEUE.json"
DEFAULT_ANS_AUDIT = (
    DIAGNOSTIC_ROOT
    / "ans_regression_contract_audit_v1"
    / "ANS_REGRESSION_CONTRACT_AUDIT.json"
)
DEFAULT_OUT_ROOT = DIAGNOSTIC_ROOT / "atomic_contract_ledger_v1"

EXTERNAL_METHOD_RULES = [
    {
        "source": "FActScore",
        "url": "https://arxiv.org/abs/2305.14251",
        "rule": "Represent long-form factuality as atomic support decisions.",
        "pearl_use": "Each residual row gets atomic anchor/source contracts before generation.",
    },
    {
        "source": "SAFE",
        "url": "https://arxiv.org/abs/2403.18802",
        "rule": "Search-augmented factuality checks need independent support for each claim.",
        "pearl_use": "Local preflight can filter candidates, but fresh eval and ANS remain final gates.",
    },
    {
        "source": "RefChecker",
        "url": "https://arxiv.org/abs/2405.14486",
        "rule": "Claim triplets make support failures easier to route than whole sentences.",
        "pearl_use": "Contracts classify entity, relation, mechanism, result, and limitation anchors.",
    },
    {
        "source": "VERISCORE",
        "url": "https://arxiv.org/abs/2406.19276",
        "rule": "Separate verifiable claims from unverifiable or structural text.",
        "pearl_use": "Structural bridge text should be hidden from ANS or rewritten as source-backed claims.",
    },
    {
        "source": "Core",
        "url": "https://aclanthology.org/2025.findings-acl.1018/",
        "rule": "Use unique informative subclaims; avoid obvious or duplicate claims.",
        "pearl_use": "Source leaves are allowed only when they are unique required CG anchors.",
    },
    {
        "source": "RAGChecker",
        "url": "https://arxiv.org/abs/2408.08067",
        "rule": "Diagnose retrieval/evidence and generation failures separately.",
        "pearl_use": "Routes distinguish evidence absence, source attachment, reasoning drift, and ANS regression.",
    },
    {
        "source": "RAG error taxonomy",
        "url": "https://arxiv.org/abs/2510.13975",
        "rule": "Real RAG systems fail through multiple component-specific error types.",
        "pearl_use": "Provider errors, judge disagreements, no-anchor cases, and metric regressions get separate lanes.",
    },
    {
        "source": "RARR",
        "url": "https://arxiv.org/abs/2210.08726",
        "rule": "Revise unsupported content while preserving supported text.",
        "pearl_use": "Repair explanations list preserved and editable nodes before materializing a candidate.",
    },
    {
        "source": "FAVA",
        "url": "https://arxiv.org/abs/2401.06855",
        "rule": "Typed factual-error classes should constrain the edit.",
        "pearl_use": "Unsupported source clauses are pruned; reasoning drift is rewritten only on named nodes.",
    },
    {
        "source": "Cited but Not Verified",
        "url": "https://arxiv.org/abs/2605.06635",
        "rule": "Citation presence and topical relevance do not guarantee factual support.",
        "pearl_use": "Source-loop closure checks exact spans and rejects merely topical source leaves.",
    },
    {
        "source": "LLM-as-judge bias",
        "url": "https://arxiv.org/abs/2305.17926",
        "rule": "Judge and provider instability must be isolated from content failures.",
        "pearl_use": "Quota/rate/provider errors are rerun-only contracts and never trigger content edits.",
    },
]

ROUTE_POLICY = {
    "ans_provider_error_rerun_queue": {
        "failure_channel": "provider_or_rate_error",
        "allowed_actions": ["rerun_row_ans_only"],
        "forbidden_actions": ["content_regeneration", "candidate_graph_edit", "canonical_merge"],
        "repair_family": "rerun_only",
        "risk": "low_content_risk_provider_blocked",
    },
    "provider_error_rerun_queue": {
        "failure_channel": "provider_or_rate_error",
        "allowed_actions": ["rerun_standard_fresh_eval_only"],
        "forbidden_actions": ["content_regeneration", "candidate_graph_edit", "canonical_merge"],
        "repair_family": "rerun_only",
        "risk": "low_content_risk_provider_blocked",
    },
    "standard_fresh_eval_queue": {
        "failure_channel": "ready_provider_free_candidate",
        "allowed_actions": ["run_standard_fresh_eval", "then_row_ans_guard", "then_batch_ans_guard"],
        "forbidden_actions": ["canonical_merge_before_fresh_and_ans", "broad_regeneration"],
        "repair_family": "verify_then_guard",
        "risk": "provider_verification_pending",
    },
    "ans_regression_repair": {
        "failure_channel": "fresh_1_1_but_ans_regressed",
        "allowed_actions": ["prune_ANS_heavy_nodes", "rewrite_named_nodes_to_supported_subset"],
        "forbidden_actions": ["broad_root_regeneration", "edit_preserved_topology"],
        "repair_family": "ans_heavy_node_micro_repair",
        "risk": "ans_regression",
    },
    "route_specific_candidate_revision": {
        "failure_channel": "clean_fresh_eval_not_1_1",
        "allowed_actions": ["revise_failed_route_contract", "preserve_clean_subpaths"],
        "forbidden_actions": ["untyped_broad_regeneration", "edit_unfailed_reasoning"],
        "repair_family": "route_specific_narrow_revision",
        "risk": "fresh_metric_or_reasoning_failed",
    },
    "single_or_dual_unique_source_leaf_bridge": {
        "failure_channel": "coverage_anchor_missing",
        "allowed_actions": ["add_one_or_two_unique_source_leaves", "attach_to_correct_topology"],
        "forbidden_actions": ["source_inventory_dump", "broad_NROOT_rewrite"],
        "repair_family": "minimal_source_leaf_bridge",
        "risk": "coverage_gap",
    },
    "pareto_safe_rollback_or_hybrid_selector": {
        "failure_channel": "metric_regression",
        "allowed_actions": ["build_rollback_candidate", "build_hybrid_candidate", "select_by_fresh_and_ans"],
        "forbidden_actions": ["overwrite_prior_correct_topology", "canonical_merge_without_batch_guard"],
        "repair_family": "rollback_hybrid_portfolio",
        "risk": "metric_regression",
    },
    "claim_graph_reconstruction_from_source_inventory": {
        "failure_channel": "no_majority_correct_anchor",
        "allowed_actions": ["build_source_inventory", "near_extractive_root", "claim_triplet_reconstruction"],
        "forbidden_actions": ["unsupported_abstraction", "large_source_leaf_inventory_without_span_closure"],
        "repair_family": "source_inventory_claim_graph",
        "risk": "high_no_anchor",
    },
    "judge_reason_targeted_reconstruction": {
        "failure_channel": "judge_reason_failure",
        "allowed_actions": ["convert_judge_reason_to_verification_questions", "edit_only_disputed_units"],
        "forbidden_actions": ["treat_judge_failure_as_evidence_gap", "provider_error_content_edit"],
        "repair_family": "judge_reason_verification_lane",
        "risk": "judge_disagreement",
    },
    "claim_typed_reasoning_rewrite": {
        "failure_channel": "reasoning_not_closed",
        "allowed_actions": ["rewrite_named_reasoning_nodes", "keep_source_spans_exact"],
        "forbidden_actions": ["coverage_leaf_only_fix", "unbounded_graph_reconstruction"],
        "repair_family": "claim_typed_reasoning_rewrite",
        "risk": "reasoning_failure",
    },
    "manual_contract_audit": {
        "failure_channel": "unknown",
        "allowed_actions": ["manual_inspection"],
        "forbidden_actions": ["automated_merge"],
        "repair_family": "manual_audit",
        "risk": "unknown",
    },
}


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
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


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def slug_for(paper_spec: str) -> str:
    return (
        paper_spec.replace(":", "__")
        .replace("/", "_")
        .replace(" ", "_")
        .replace(".", "_")
    )


def route_policy(route: str) -> dict[str, Any]:
    return ROUTE_POLICY.get(route, ROUTE_POLICY["manual_contract_audit"])


def graph_snapshot(graph_spec: str) -> dict[str, Any]:
    if not graph_spec:
        return {}
    path = resolve(graph_spec)
    payload = read_json(path, {})
    nodes = payload.get("nodes") if isinstance(payload, dict) else []
    edges = payload.get("edges") if isinstance(payload, dict) else []
    source_nodes = []
    root_text = ""
    root_id = str(payload.get("root") or "NROOT") if isinstance(payload, dict) else "NROOT"
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        if str(node.get("id") or "") == root_id:
            root_text = str(node.get("text") or "")
        source = node.get("source")
        if isinstance(source, list) and source and source[0] not in (0, "0", None):
            source_nodes.append(
                {
                    "node_id": node.get("id", ""),
                    "source": source,
                    "text": node.get("text", ""),
                }
            )
    return {
        "graph_spec_exists": path.exists(),
        "graph_spec": rel(path),
        "root_id": root_id,
        "root_text": root_text,
        "node_count": len(nodes) if isinstance(nodes, list) else 0,
        "edge_count": len(edges) if isinstance(edges, list) else 0,
        "source_node_count": len(source_nodes),
        "source_nodes": source_nodes,
    }


def preflight_snapshot(preflight_report: str) -> dict[str, Any]:
    if not preflight_report:
        return {}
    path = resolve(preflight_report)
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {"preflight_report": rel(path), "preflight_report_exists": path.exists()}
    entity_coverage = payload.get("entity_coverage") or {}
    premise = payload.get("immediate_premise_support") or {}
    reasoning = payload.get("reasoning_units") or {}
    connectivity = payload.get("connectivity") or {}
    uncovered_entities = []
    for entity in entity_coverage.get("entities", []) if isinstance(entity_coverage, dict) else []:
        if isinstance(entity, dict) and not entity.get("covered"):
            uncovered_entities.append(entity.get("entity", ""))
    high_risk_targets = []
    for target in premise.get("targets", []) if isinstance(premise, dict) else []:
        if isinstance(target, dict) and target.get("high_risk"):
            high_risk_targets.append(target.get("target", ""))
    return {
        "preflight_report": rel(path),
        "preflight_report_exists": path.exists(),
        "passed_local_preflight": payload.get("passed_local_preflight"),
        "root_id_is_NROOT": payload.get("root_id_is_NROOT"),
        "strict_validator_valid": (payload.get("strict_validator") or {}).get("valid"),
        "teacher_compatible_validator_valid": (payload.get("teacher_compatible_validator") or {}).get("valid"),
        "reasoning_target_count": reasoning.get("target_count") if isinstance(reasoning, dict) else "",
        "reasoning_valid_target_count": reasoning.get("valid_target_count") if isinstance(reasoning, dict) else "",
        "reasoning_invalid_target_count": reasoning.get("invalid_target_count") if isinstance(reasoning, dict) else "",
        "premise_audited_targets": premise.get("audited_targets") if isinstance(premise, dict) else "",
        "premise_high_risk_count": premise.get("high_risk_count") if isinstance(premise, dict) else "",
        "premise_high_risk_targets": high_risk_targets,
        "covered_entities": entity_coverage.get("covered_entities") if isinstance(entity_coverage, dict) else "",
        "total_entities": entity_coverage.get("total_entities") if isinstance(entity_coverage, dict) else "",
        "coverage_rate_local": entity_coverage.get("coverage_rate_local") if isinstance(entity_coverage, dict) else "",
        "uncovered_entities": uncovered_entities,
        "connectivity_nodes_reaching_root": connectivity.get("nodes_reaching_root") if isinstance(connectivity, dict) else "",
        "connectivity_total_nodes": connectivity.get("total_nodes") if isinstance(connectivity, dict) else "",
        "connectivity_stranded_nodes": connectivity.get("stranded_nodes") if isinstance(connectivity, dict) else [],
        "fresh_gate_still_required": payload.get("fresh_gate_still_required") or {"final_CG": 1.0, "final_REA": 1.0},
    }


def load_ans_contracts(ans_audit_path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(ans_audit_path, {})
    contracts = payload.get("contracts") if isinstance(payload, dict) else []
    out: dict[str, dict[str, Any]] = {}
    for contract in contracts if isinstance(contracts, list) else []:
        if isinstance(contract, dict) and contract.get("paper_spec"):
            out[str(contract["paper_spec"])] = contract
    return out


def build_atomic_claim_contracts(
    row: dict[str, Any],
    policy: dict[str, Any],
    graph: dict[str, Any],
    preflight: dict[str, Any],
    ans_contract: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    route = str(row.get("controller_route") or "")
    contracts: list[dict[str, Any]] = []
    if ans_contract:
        for node in ans_contract.get("node_rows", []):
            if not isinstance(node, dict):
                continue
            unsupported = int(node.get("unsupported_fact_count") or 0)
            if unsupported <= 0 and node.get("recommended_action") == "preserve":
                continue
            contracts.append(
                {
                    "contract_id": f"{row['paper_spec']}::{node.get('node_id')}",
                    "node_id": node.get("node_id", ""),
                    "claim_type": node.get("unit_type", ""),
                    "required_anchor": "ANS-supported factual subset",
                    "support_mode": "exact_or_supported_subset",
                    "ans_visibility": "high",
                    "unique_required_anchor": False,
                    "source_sentence_ids": node.get("source_tuple", ""),
                    "current_status": "unsupported_or_partial" if unsupported else "supported",
                    "recommended_action": node.get("recommended_action", "preserve"),
                    "unsupported_fact_count": unsupported,
                    "atomic_fact_count": node.get("atomic_fact_count", ""),
                    "node_text": node.get("node_text", ""),
                }
            )
        return contracts

    if route in {"provider_error_rerun_queue", "ans_provider_error_rerun_queue"}:
        contracts.append(
            {
                "contract_id": f"{row['paper_spec']}::provider-clean-rerun",
                "node_id": "",
                "claim_type": "provider_or_rate_error",
                "required_anchor": "unchanged candidate graph",
                "support_mode": "rerun_clean_provider",
                "ans_visibility": "unchanged",
                "unique_required_anchor": False,
                "source_sentence_ids": "",
                "current_status": "provider_blocked",
                "recommended_action": policy["allowed_actions"][0],
                "unsupported_fact_count": "",
                "atomic_fact_count": "",
                "node_text": "Do not edit content until provider-contaminated gate is rerun cleanly.",
            }
        )
        return contracts

    if route == "standard_fresh_eval_queue":
        source_nodes = graph.get("source_nodes") or []
        for source_node in source_nodes:
            contracts.append(
                {
                    "contract_id": f"{row['paper_spec']}::{source_node.get('node_id')}",
                    "node_id": source_node.get("node_id", ""),
                    "claim_type": "source_leaf_or_source_bound_claim",
                    "required_anchor": "fresh CG/REA and later ANS support",
                    "support_mode": "provider_free_preflight_passed_then_fresh_eval",
                    "ans_visibility": "medium",
                    "unique_required_anchor": True,
                    "source_sentence_ids": source_node.get("source", ""),
                    "current_status": "local_ready_fresh_pending",
                    "recommended_action": "run_standard_fresh_eval",
                    "unsupported_fact_count": "",
                    "atomic_fact_count": "",
                    "node_text": source_node.get("text", ""),
                }
            )
        if not contracts:
            contracts.append(
                {
                    "contract_id": f"{row['paper_spec']}::fresh-eval-candidate",
                    "node_id": "",
                    "claim_type": "candidate_graph",
                    "required_anchor": "fresh CG=1 and REA=1",
                    "support_mode": "standard_fresh_eval_required",
                    "ans_visibility": "unknown",
                    "unique_required_anchor": False,
                    "source_sentence_ids": "",
                    "current_status": "local_ready_fresh_pending",
                    "recommended_action": "run_standard_fresh_eval",
                    "unsupported_fact_count": "",
                    "atomic_fact_count": "",
                    "node_text": graph.get("root_text", ""),
                }
            )
        return contracts

    for entity in preflight.get("uncovered_entities") or []:
        contracts.append(
            {
                "contract_id": f"{row['paper_spec']}::missing::{entity}",
                "node_id": "",
                "claim_type": "entity_or_anchor",
                "required_anchor": entity,
                "support_mode": "exact_source_span_required",
                "ans_visibility": "high",
                "unique_required_anchor": True,
                "source_sentence_ids": "",
                "current_status": "missing_local_coverage",
                "recommended_action": "add_minimal_source_leaf_or_alias_bridge",
                "unsupported_fact_count": "",
                "atomic_fact_count": "",
                "node_text": "",
            }
        )
    if not contracts:
        contracts.append(
            {
                "contract_id": f"{row['paper_spec']}::{route or 'manual'}",
                "node_id": "",
                "claim_type": policy["failure_channel"],
                "required_anchor": route_required_anchor(row),
                "support_mode": route_support_mode(route),
                "ans_visibility": route_ans_visibility(route),
                "unique_required_anchor": route in {
                    "single_or_dual_unique_source_leaf_bridge",
                    "claim_graph_reconstruction_from_source_inventory",
                    "route_specific_candidate_revision",
                },
                "source_sentence_ids": "",
                "current_status": row.get("controller_status", ""),
                "recommended_action": policy["allowed_actions"][0],
                "unsupported_fact_count": "",
                "atomic_fact_count": "",
                "node_text": graph.get("root_text", ""),
            }
        )
    return contracts


def route_required_anchor(row: dict[str, Any]) -> str:
    route = str(row.get("controller_route") or "")
    if route == "route_specific_candidate_revision":
        return "failed fresh CG/REA contract from clean fresh evaluation"
    if route == "pareto_safe_rollback_or_hybrid_selector":
        return "prior fresh-correct topology with non-regressing metrics"
    if route == "claim_graph_reconstruction_from_source_inventory":
        return "near-extractive source inventory anchors for no-anchor row"
    if route == "judge_reason_targeted_reconstruction":
        return "judge rejection reason converted to verification questions"
    if route == "single_or_dual_unique_source_leaf_bridge":
        return "unique missing coverage anchor while preserving REA=1 path"
    return str(row.get("failure_type") or "manual anchor review")


def route_support_mode(route: str) -> str:
    if route in {"claim_graph_reconstruction_from_source_inventory", "single_or_dual_unique_source_leaf_bridge"}:
        return "exact_source_span_required"
    if route == "pareto_safe_rollback_or_hybrid_selector":
        return "portfolio_selection_by_fresh_and_ans"
    if route == "judge_reason_targeted_reconstruction":
        return "verification_question_evidence"
    if route == "route_specific_candidate_revision":
        return "named_failed_contract_revision"
    return "manual_review"


def route_ans_visibility(route: str) -> str:
    if route in {
        "claim_graph_reconstruction_from_source_inventory",
        "single_or_dual_unique_source_leaf_bridge",
        "ans_regression_repair",
    }:
        return "high"
    if route in {"standard_fresh_eval_queue", "pareto_safe_rollback_or_hybrid_selector"}:
        return "medium"
    return "unknown"


def repair_explanation(
    row: dict[str, Any],
    policy: dict[str, Any],
    contracts: list[dict[str, Any]],
    ans_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    editable_nodes: list[str] = []
    forbidden_edits: list[str] = []
    if ans_contract:
        editable_nodes = list(ans_contract.get("editable_nodes") or [])
        forbidden_edits = list(ans_contract.get("forbidden_edits") or [])
    elif row.get("controller_route") in {"provider_error_rerun_queue", "ans_provider_error_rerun_queue"}:
        forbidden_edits = ["all_candidate_nodes"]
    elif row.get("controller_route") == "standard_fresh_eval_queue":
        forbidden_edits = ["candidate_graph_until_clean_fresh_eval"]
    else:
        editable_nodes = [contract.get("node_id", "") for contract in contracts if contract.get("node_id")]
    return {
        "failed_anchor": route_required_anchor(row),
        "failure_type": row.get("failure_type", ""),
        "failure_channel": policy["failure_channel"],
        "why_current_graph_fails": why_current_graph_fails(row, policy, ans_contract),
        "preserved_nodes": preserved_nodes_for(row, ans_contract),
        "editable_nodes": editable_nodes,
        "forbidden_edits": forbidden_edits,
        "minimal_edit": minimal_edit_for(row, policy, ans_contract),
        "acceptance_gates_remaining": acceptance_gates_for(row),
    }


def why_current_graph_fails(
    row: dict[str, Any], policy: dict[str, Any], ans_contract: dict[str, Any] | None
) -> str:
    route = str(row.get("controller_route") or "")
    if ans_contract:
        margin = ans_contract.get("guard_margin")
        return f"Fresh CG/REA passed, but row ANS regressed below guard margin {margin}."
    if route in {"provider_error_rerun_queue", "ans_provider_error_rerun_queue"}:
        return "The latest relevant gate is contaminated by provider/rate/quota errors, so content status is unchanged."
    if route == "standard_fresh_eval_queue":
        return "Provider-free preflight is ready, but standard fresh evaluation and ANS guards have not accepted the row."
    if route == "route_specific_candidate_revision":
        return "A clean fresh evaluation exists but does not satisfy the strict 1/1 metric gate."
    if route == "pareto_safe_rollback_or_hybrid_selector":
        return "The residual is a metric-regression case where broad regeneration may damage prior correct topology."
    if route == "claim_graph_reconstruction_from_source_inventory":
        return "No majority-correct anchor is available; reconstruction must start from exact source inventory."
    if route == "judge_reason_targeted_reconstruction":
        return "The failure is judge-reason-specific and should be converted into narrow verification questions."
    return f"Route policy requires {policy['repair_family']} before acceptance."


def preserved_nodes_for(row: dict[str, Any], ans_contract: dict[str, Any] | None) -> list[str]:
    if ans_contract:
        return list(ans_contract.get("forbidden_edits") or [])
    route = str(row.get("controller_route") or "")
    if route in {"provider_error_rerun_queue", "ans_provider_error_rerun_queue", "standard_fresh_eval_queue"}:
        return ["entire_current_candidate_graph"]
    if route == "route_specific_candidate_revision":
        return ["fresh-clean subpaths not named by failed contract"]
    if route == "pareto_safe_rollback_or_hybrid_selector":
        return ["prior fresh-correct topology candidates"]
    return []


def minimal_edit_for(
    row: dict[str, Any], policy: dict[str, Any], ans_contract: dict[str, Any] | None
) -> str:
    if ans_contract:
        nodes = ans_contract.get("editable_nodes") or []
        return "Prune or rewrite only ANS-heavy nodes: " + ", ".join(nodes[:4])
    return "; ".join(policy["allowed_actions"])


def acceptance_gates_for(row: dict[str, Any]) -> list[str]:
    status = str(row.get("controller_status") or "")
    if status == "fresh_1_1_ans_provider_error":
        return ["clean row ANS rerun", "strict merge ANS guard", "proposal batch ANS guard"]
    if status == "ready_standard_fresh_eval_rerun":
        return ["clean standard fresh eval rerun", "row ANS guard", "proposal batch ANS guard"]
    if status == "ready_standard_fresh_eval":
        return ["standard fresh eval CG=1/REA=1", "row ANS guard", "proposal batch ANS guard"]
    if status == "fresh_1_1_ans_failed":
        return ["materialize narrow ANS repair", "standard fresh eval CG=1/REA=1", "row ANS non-regression", "proposal batch ANS guard"]
    return ["materialize route-specific candidate", "standard fresh eval CG=1/REA=1", "row ANS guard", "proposal batch ANS guard"]


def build_row_contract(row: dict[str, Any], ans_contracts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    paper_spec = str(row.get("paper_spec") or "")
    route = str(row.get("controller_route") or "manual_contract_audit")
    policy = route_policy(route)
    graph = graph_snapshot(str(row.get("graph_spec") or ""))
    preflight = preflight_snapshot(str(row.get("preflight_report") or ""))
    ans_contract = ans_contracts.get(paper_spec)
    risk_flags = compute_risk_flags(row, preflight, ans_contract)
    atomic_contracts = build_atomic_claim_contracts(row, policy, graph, preflight, ans_contract)
    explanation = repair_explanation(row, policy, atomic_contracts, ans_contract)
    policy = dict(policy)
    policy["risk"] = risk_label(policy["risk"], risk_flags)
    return {
        "paper_spec": paper_spec,
        "model": row.get("model", ""),
        "paper": row.get("paper", ""),
        "priority": row.get("priority", ""),
        "failure_type": row.get("failure_type", ""),
        "current_final_CG": row.get("current_final_CG", ""),
        "current_final_REA": row.get("current_final_REA", ""),
        "controller_route": route,
        "controller_status": row.get("controller_status", ""),
        "candidate_label": row.get("candidate_label", ""),
        "candidate_lane": row.get("candidate_lane", ""),
        "provider_free_evidence": {
            "attempt_index_csv": row.get("attempt_index_csv", ""),
            "staged_run_dir": row.get("staged_run_dir", ""),
            "preflight_report": row.get("preflight_report", ""),
            "passed_local_preflight": as_bool(row.get("passed_local_preflight")),
            "covered_entities_local": row.get("covered_entities_local", ""),
            "total_entities_local": row.get("total_entities_local", ""),
            "premise_support_high_risk_count": row.get("premise_support_high_risk_count", ""),
            "row_ans_floor": row.get("row_ans_floor", ""),
            "preflight_snapshot": preflight,
            "graph_snapshot": graph,
        },
        "fresh_and_ans_evidence": {
            "fresh_eval_results": row.get("fresh_eval_results", ""),
            "fresh_CG": row.get("fresh_CG", ""),
            "fresh_REA": row.get("fresh_REA", ""),
            "fresh_strict_gate_passed": as_bool(row.get("fresh_strict_gate_passed")),
            "fresh_judge_provider_error": as_bool(row.get("fresh_judge_provider_error")),
            "fresh_status": row.get("fresh_status", ""),
            "ans_guard_report": row.get("ans_guard_report", ""),
            "candidate_main_factual_ans": row.get("candidate_main_factual_ans", ""),
            "guard_current_best_main_factual_ans": row.get("guard_current_best_main_factual_ans", ""),
            "guard_margin": row.get("guard_margin", ""),
            "ans_guard_passed": as_bool(row.get("ans_guard_passed")),
            "mergeable_with_ans_guard": as_bool(row.get("mergeable_with_ans_guard")),
            "ans_eval_dir": row.get("ans_eval_dir", ""),
            "ans_error_count": row.get("ans_error_count", ""),
            "ans_provider_or_rate_error": as_bool(row.get("ans_provider_or_rate_error")),
            "strict_merge_candidates": row.get("strict_merge_candidates", ""),
        },
        "route_policy": policy,
        "risk_flags": risk_flags,
        "atomic_claim_contracts": atomic_contracts,
        "repair_explanation": explanation,
        "strict_acceptance_contract": {
            "fresh_CG": 1.0,
            "fresh_REA": 1.0,
            "judge_provider_error": False,
            "fresh_eval_hash_matches": True,
            "row_ANS": "non-regression",
            "proposal_batch_ANS": "at_or_above_canonical_300_floor",
            "canonical_accounting_write": False,
        },
    }


def compute_risk_flags(
    row: dict[str, Any],
    preflight: dict[str, Any],
    ans_contract: dict[str, Any] | None,
) -> list[str]:
    flags: list[str] = []
    route = str(row.get("controller_route") or "")
    if route in {"provider_error_rerun_queue", "ans_provider_error_rerun_queue"}:
        flags.append("provider_gate_unclean")
    if preflight:
        if preflight.get("passed_local_preflight") is False:
            flags.append("local_preflight_failed")
        high_risk = as_float(preflight.get("premise_high_risk_count"), 0.0)
        if high_risk > 0:
            flags.append("local_premise_support_high_risk")
        invalid_reasoning = as_float(preflight.get("reasoning_invalid_target_count"), 0.0)
        if invalid_reasoning > 0:
            flags.append("local_reasoning_validator_failed")
        if preflight.get("connectivity_stranded_nodes"):
            flags.append("local_connectivity_stranded_nodes")
    if ans_contract:
        required = as_float(ans_contract.get("required_extra_supported_facts_for_guard_floor"), 0.0)
        if required >= 5:
            flags.append("large_ans_repair_gap")
        elif required > 0:
            flags.append("small_ans_repair_gap")
    if as_bool(row.get("fresh_judge_provider_error")) is True:
        flags.append("fresh_provider_error")
    if as_bool(row.get("ans_provider_or_rate_error")) is True:
        flags.append("ans_provider_or_rate_error")
    return sorted(set(flags))


def risk_label(base: str, flags: list[str]) -> str:
    if "provider_gate_unclean" in flags and (
        "local_preflight_failed" in flags or "local_premise_support_high_risk" in flags
    ):
        return "provider_blocked_with_local_preflight_risk"
    if "large_ans_repair_gap" in flags:
        return "high_ans_repair_gap"
    if "small_ans_repair_gap" in flags:
        return "bounded_ans_repair_gap"
    if "local_preflight_failed" in flags:
        return "local_preflight_failed"
    if "local_premise_support_high_risk" in flags:
        return "local_premise_support_high_risk"
    return base


def flatten_contracts(row_contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in row_contracts:
        for contract in row.get("atomic_claim_contracts", []):
            rows.append(
                {
                    "paper_spec": row["paper_spec"],
                    "priority": row["priority"],
                    "controller_route": row["controller_route"],
                    "controller_status": row["controller_status"],
                    "candidate_label": row["candidate_label"],
                    "contract_id": contract.get("contract_id", ""),
                    "node_id": contract.get("node_id", ""),
                    "claim_type": contract.get("claim_type", ""),
                    "required_anchor": contract.get("required_anchor", ""),
                    "support_mode": contract.get("support_mode", ""),
                    "ans_visibility": contract.get("ans_visibility", ""),
                    "unique_required_anchor": contract.get("unique_required_anchor", ""),
                    "current_status": contract.get("current_status", ""),
                    "recommended_action": contract.get("recommended_action", ""),
                    "unsupported_fact_count": contract.get("unsupported_fact_count", ""),
                    "atomic_fact_count": contract.get("atomic_fact_count", ""),
                }
            )
    return rows


def render_markdown(summary: dict[str, Any], row_contracts: list[dict[str, Any]]) -> str:
    lines = [
        "# After327 Atomic Contract Ledger",
        "",
        f"Created: `{summary['created_at']}`",
        "",
        "Provider calls: `False`",
        "Canonical accounting write: `False`",
        "",
        "## Current State",
        "",
        f"- Strict rows: `{summary['current_strict_success_rows']}`",
        f"- Residual rows: `{summary['current_typed_residual_rows']}`",
        f"- Contract rows: `{summary['row_contract_count']}`",
        f"- Atomic contracts: `{summary['atomic_contract_count']}`",
        "",
        "## Route Counts",
        "",
    ]
    for route, count in sorted(summary["route_counts"].items()):
        lines.append(f"- `{route}`: `{count}`")
    lines.extend(
        [
            "",
            "## Execution Queue",
            "",
            "| Priority | Paper Spec | Route | Status | Candidate | Minimal Edit / Next Gate |",
            "|---:|---|---|---|---|---|",
        ]
    )
    for row in sorted(row_contracts, key=lambda item: (int(item.get("priority") or 999), item["paper_spec"])):
        explanation = row["repair_explanation"]
        lines.append(
            "| {priority} | `{paper_spec}` | `{route}` | `{status}` | `{candidate}` | {edit} |".format(
                priority=row.get("priority", ""),
                paper_spec=row["paper_spec"],
                route=row["controller_route"],
                status=row["controller_status"],
                candidate=row.get("candidate_label", ""),
                edit=explanation.get("minimal_edit", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Literature-Grounded Design Rules",
            "",
        ]
    )
    for rule in EXTERNAL_METHOD_RULES:
        lines.append(f"- `{rule['source']}`: {rule['pearl_use']} {rule['url']}")
    lines.extend(["", "## Row Contracts", ""])
    for row in sorted(row_contracts, key=lambda item: (int(item.get("priority") or 999), item["paper_spec"])):
        explanation = row["repair_explanation"]
        gates = ", ".join(explanation["acceptance_gates_remaining"])
        lines.extend(
            [
                f"### {row['paper_spec']}",
                "",
                f"- Route: `{row['controller_route']}`",
                f"- Status: `{row['controller_status']}`",
                f"- Failure channel: `{row['route_policy']['failure_channel']}`",
                f"- Risk: `{row['route_policy']['risk']}`",
                f"- Risk flags: `{', '.join(row['risk_flags'])}`",
                f"- Why it fails: {explanation['why_current_graph_fails']}",
                f"- Editable nodes: `{', '.join(explanation['editable_nodes'])}`",
                f"- Forbidden edits: `{', '.join(explanation['forbidden_edits'])}`",
                f"- Remaining gates: `{gates}`",
                "",
                "| Contract | Claim Type | Support Mode | ANS Visibility | Action |",
                "|---|---|---|---|---|",
            ]
        )
        for contract in row["atomic_claim_contracts"][:8]:
            lines.append(
                "| `{contract_id}` | `{claim_type}` | `{support_mode}` | `{ans_visibility}` | `{recommended_action}` |".format(
                    **contract
                )
            )
        lines.append("")
    lines.extend(
        [
            "## Output Files",
            "",
            f"- `{summary['out_root']}/AFTER327_ATOMIC_CONTRACT_LEDGER.json`",
            f"- `{summary['out_root']}/AFTER327_ATOMIC_CONTRACT_SUMMARY.json`",
            f"- `{summary['out_root']}/AFTER327_ATOMIC_CONTRACTS.csv`",
            f"- `{summary['out_root']}/row_contracts/`",
        ]
    )
    return "\n".join(lines) + "\n"


def build(args: argparse.Namespace) -> dict[str, Any]:
    controller_path = resolve(args.controller_queue)
    ans_audit_path = resolve(args.ans_audit)
    out_root = resolve(args.out_root)
    controller = read_json(controller_path, {})
    controller_rows = controller.get("rows") if isinstance(controller, dict) else []
    ans_contracts = load_ans_contracts(ans_audit_path)
    row_contracts = [
        build_row_contract(row, ans_contracts)
        for row in controller_rows
        if isinstance(row, dict)
    ]
    row_contracts.sort(key=lambda row: (int(row.get("priority") or 999), row["paper_spec"]))
    route_counts = Counter(row["controller_route"] for row in row_contracts)
    status_counts = Counter(row["controller_status"] for row in row_contracts)
    atomic_rows = flatten_contracts(row_contracts)
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "after327_atomic_contract_ledger",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_controller_queue": rel(controller_path),
        "source_ans_audit": rel(ans_audit_path),
        "out_root": rel(out_root),
        "current_strict_success_rows": (controller.get("summary") or {}).get("current_strict_success_rows"),
        "current_typed_residual_rows": (controller.get("summary") or {}).get("current_typed_residual_rows"),
        "row_contract_count": len(row_contracts),
        "atomic_contract_count": len(atomic_rows),
        "route_counts": dict(route_counts),
        "status_counts": dict(status_counts),
        "literature_design_rules": EXTERNAL_METHOD_RULES,
        "acceptance_contract": {
            "fresh_CG": 1.0,
            "fresh_REA": 1.0,
            "judge_provider_error": False,
            "fresh_eval_hash_matches": True,
            "row_ANS": "non-regression",
            "proposal_batch_ANS": "at_or_above_canonical_300_floor",
            "canonical_accounting_write": False,
        },
    }
    ledger = {"summary": summary, "rows": row_contracts}
    write_json(out_root / "AFTER327_ATOMIC_CONTRACT_LEDGER.json", ledger)
    write_json(out_root / "AFTER327_ATOMIC_CONTRACT_SUMMARY.json", summary)
    write_csv(
        out_root / "AFTER327_ATOMIC_CONTRACTS.csv",
        atomic_rows,
        [
            "paper_spec",
            "priority",
            "controller_route",
            "controller_status",
            "candidate_label",
            "contract_id",
            "node_id",
            "claim_type",
            "required_anchor",
            "support_mode",
            "ans_visibility",
            "unique_required_anchor",
            "current_status",
            "recommended_action",
            "unsupported_fact_count",
            "atomic_fact_count",
        ],
    )
    row_dir = out_root / "row_contracts"
    for row in row_contracts:
        write_json(row_dir / f"{slug_for(row['paper_spec'])}.json", row)
    write_text(out_root / "AFTER327_ATOMIC_CONTRACT_LEDGER.md", render_markdown(summary, row_contracts))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return ledger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-queue", default=str(DEFAULT_CONTROLLER_QUEUE))
    parser.add_argument("--ans-audit", default=str(DEFAULT_ANS_AUDIT))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
