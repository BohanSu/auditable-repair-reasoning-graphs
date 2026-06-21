#!/usr/bin/env python3
"""Build a provider-free source/claim closure diagnostic for after327 residuals.

This layer turns the current failure-typed queue into repair-ready source/claim
diagnostics. It never writes canonical accounting, never calls a provider, and
never relaxes the existing fresh-eval or ANS gates.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
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
DEFAULT_ATOMIC_LEDGER = (
    DIAGNOSTIC_ROOT / "atomic_contract_ledger_v1" / "AFTER327_ATOMIC_CONTRACT_LEDGER.json"
)
DEFAULT_OUT_ROOT = DIAGNOSTIC_ROOT / "source_claim_closure_diagnostic_v1"

LITERATURE_RULES = [
    {
        "source": "FActScore",
        "url": "https://arxiv.org/abs/2305.14251",
        "rule": "Use atomic factual units instead of whole-output judgments.",
        "pearl_design": "Score repair readiness at the claim/source-leaf level before materializing edits.",
    },
    {
        "source": "SAFE",
        "url": "https://arxiv.org/abs/2403.18802",
        "rule": "Search-backed factuality still requires independent support decisions per claim.",
        "pearl_design": "Provider-free preflight is only a filter; fresh CG/REA and ANS remain the acceptance gates.",
    },
    {
        "source": "RefChecker",
        "url": "https://arxiv.org/abs/2405.14486",
        "rule": "Structured claims make support failures easier to route than full sentences.",
        "pearl_design": "Route residuals by root claim, source leaf, reasoning closure, and ANS visibility.",
    },
    {
        "source": "RAGChecker",
        "url": "https://arxiv.org/abs/2408.08067",
        "rule": "Retrieval/evidence and generation/reasoning failures should be diagnosed separately.",
        "pearl_design": "Separate source absence, source-claim mismatch, generation overclaim, and judge/provider noise.",
    },
    {
        "source": "OpenFActScore",
        "url": "https://arxiv.org/abs/2507.05965",
        "rule": "Decompose atomic fact generation from atomic fact validation for reproducible factuality checks.",
        "pearl_design": "Keep a provider-free closure ledger that can be rerun independently of fresh judges.",
    },
    {
        "source": "Cited but Not Verified",
        "url": "https://arxiv.org/abs/2605.06635",
        "rule": "Surface citation validity and topical relevance do not prove factual support.",
        "pearl_design": "Flag background/topical source leaves that do not close the root claim contract.",
    },
    {
        "source": "Calibration-Aware Generation",
        "url": "https://arxiv.org/abs/2605.01749",
        "rule": "Separate exploration from final commitment and only commit reliable content.",
        "pearl_design": "For near-miss rows, prune or restate root claims to the reliable source-backed subset.",
    },
]

BACKGROUND_PATTERNS = [
    "previous work",
    "more recently",
    "to date",
    "for example",
    "examples of",
    "beyond",
    "it is still unclear",
    "usually",
    "primarily",
    "recently shown",
    "prior",
    "existing",
    "other types",
]

OVERCLAIM_PATTERNS = [
    "demonstrates",
    "validat",
    "thereby",
    "reveal",
    "universal",
    "preparation-independent",
    "necessary and sufficient",
    "first",
    "independently",
    "more stable than",
    "exceptionally",
    "sensitively",
    "surpassing",
    "long-lived",
    "counter-defense",
]

TRUNCATION_RE = re.compile(r"(?:\b[A-Za-z]{1,4}\.|,\s*$|;\s*$|\bfun\.$|structu\.$|\bA\.$)")


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


def load_controller_rows(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path, {})
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def load_atomic_rows(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path, {})
    rows = payload.get("rows") if isinstance(payload, dict) else []
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("paper_spec"):
            out[str(row["paper_spec"])] = row
    return out


def graph_from_row(row: dict[str, Any], atomic_row: dict[str, Any] | None) -> dict[str, Any]:
    if atomic_row:
        graph = ((atomic_row.get("provider_free_evidence") or {}).get("graph_snapshot") or {})
        if isinstance(graph, dict) and graph:
            return graph
    graph_spec = str(row.get("graph_spec") or "")
    if not graph_spec:
        return {}
    path = resolve(graph_spec)
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {}
    root_id = str(payload.get("root") or "NROOT")
    source_nodes = []
    root_text = ""
    nodes = payload.get("nodes") or []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        if str(node.get("id") or "") == root_id:
            root_text = str(node.get("text") or "")
        source = node.get("source")
        if isinstance(source, list) and source and source[0] not in (0, "0", None):
            source_nodes.append({"node_id": node.get("id", ""), "source": source, "text": node.get("text", "")})
    return {
        "graph_spec": rel(path),
        "root_id": root_id,
        "root_text": root_text,
        "node_count": len(nodes) if isinstance(nodes, list) else 0,
        "source_node_count": len(source_nodes),
        "source_nodes": source_nodes,
    }


def preflight_from_row(row: dict[str, Any], atomic_row: dict[str, Any] | None) -> dict[str, Any]:
    if atomic_row:
        preflight = ((atomic_row.get("provider_free_evidence") or {}).get("preflight_snapshot") or {})
        if isinstance(preflight, dict) and preflight:
            return preflight
    preflight_report = str(row.get("preflight_report") or "")
    if not preflight_report:
        return {}
    payload = read_json(resolve(preflight_report), {})
    return payload if isinstance(payload, dict) else {}


def matching_fresh_row(row: dict[str, Any]) -> dict[str, Any]:
    fresh_path = str(row.get("fresh_eval_results") or "")
    if not fresh_path:
        return {}
    payload = read_json(resolve(fresh_path), {})
    if not isinstance(payload, dict):
        return {}
    paper_spec = str(row.get("paper_spec") or "")
    for item in payload.get("rows", []) if isinstance(payload.get("rows"), list) else []:
        if isinstance(item, dict) and str(item.get("paper_spec") or "") == paper_spec:
            return item
    return {}


def fresh_detail(row: dict[str, Any]) -> dict[str, Any]:
    fresh_row = matching_fresh_row(row)
    detail: dict[str, Any] = {
        "fresh_row": fresh_row,
        "eval_detail": {},
        "fixed_anchor": {},
    }
    eval_dir = fresh_row.get("eval_dir") if isinstance(fresh_row, dict) else ""
    if eval_dir:
        eval_path = resolve(str(eval_dir)) / "evaluation_results.json"
        detail["eval_detail"] = read_json(eval_path, {}) if eval_path.exists() else {}
    work_dir = fresh_row.get("work_dir") if isinstance(fresh_row, dict) else ""
    if work_dir:
        anchor_path = resolve(str(work_dir)) / "fixed_anchor.json"
        detail["fixed_anchor"] = read_json(anchor_path, {}) if anchor_path.exists() else {}
    return detail


def text_has_any(text: str, patterns: list[str]) -> list[str]:
    lower = text.lower()
    return [pattern for pattern in patterns if pattern in lower]


def source_leaf_features(source_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    background = []
    truncated = []
    duplicate_sources: Counter[str] = Counter()
    long_leaf_count = 0
    for node in source_nodes:
        text = str(node.get("text") or "")
        source = node.get("source", "")
        duplicate_sources[str(source)] += 1
        bg_hits = text_has_any(text, BACKGROUND_PATTERNS)
        if bg_hits:
            background.append({"node_id": node.get("node_id", ""), "hits": bg_hits, "text": text})
        if TRUNCATION_RE.search(text.strip()) or len(text.strip()) < 24:
            truncated.append({"node_id": node.get("node_id", ""), "text": text})
        if len(text.split()) > 38:
            long_leaf_count += 1
    return {
        "background_leaf_count": len(background),
        "background_leaves": background[:8],
        "truncated_leaf_count": len(truncated),
        "truncated_leaves": truncated[:8],
        "duplicate_source_tuple_count": sum(1 for count in duplicate_sources.values() if count > 1),
        "long_leaf_count": long_leaf_count,
    }


def root_features(root_text: str) -> dict[str, Any]:
    clauses = [part.strip() for part in re.split(r"[.;]", root_text) if part.strip()]
    overclaim_hits = text_has_any(root_text, OVERCLAIM_PATTERNS)
    return {
        "root_char_count": len(root_text),
        "root_word_count": len(root_text.split()),
        "root_clause_count": len(clauses),
        "root_overclaim_hits": overclaim_hits,
        "root_overclaim_count": len(overclaim_hits),
        "root_truncation_suspected": bool(TRUNCATION_RE.search(root_text.strip())),
        "root_text": root_text,
    }


def fresh_gap(row: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    fresh_row = fresh.get("fresh_row") if isinstance(fresh, dict) else {}
    eval_detail = fresh.get("eval_detail") if isinstance(fresh, dict) else {}
    cg = as_float(row.get("fresh_CG"), as_float(fresh_row.get("CG") if isinstance(fresh_row, dict) else None))
    rea = as_float(row.get("fresh_REA"), as_float(fresh_row.get("REA") if isinstance(fresh_row, dict) else None))
    covered = (
        fresh_row.get("covered_entities")
        if isinstance(fresh_row, dict) and fresh_row.get("covered_entities") not in (None, "")
        else ((eval_detail.get("coverage") or {}).get("covered_entities") if isinstance(eval_detail, dict) else "")
    )
    total = (
        fresh_row.get("total_entities")
        if isinstance(fresh_row, dict) and fresh_row.get("total_entities") not in (None, "")
        else ((eval_detail.get("coverage") or {}).get("total_entities") if isinstance(eval_detail, dict) else "")
    )
    covered_i = int(as_float(covered, 0.0))
    total_i = int(as_float(total, 0.0))
    missing_i = max(total_i - covered_i, 0) if total_i else (1 if 0.0 < cg < 1.0 else 0)
    missing_entities = []
    if isinstance(fresh_row, dict) and fresh_row.get("missing_entities"):
        missing_entities = [
            entity.strip()
            for entity in str(fresh_row.get("missing_entities") or "").split(";")
            if entity.strip()
        ]
    return {
        "fresh_CG": cg,
        "fresh_REA": rea,
        "fresh_missing_entity_count": missing_i,
        "fresh_missing_entities": missing_entities,
        "fresh_total_entities": total_i,
        "fresh_covered_entities": covered_i,
        "judge_provider_error": as_bool(row.get("fresh_judge_provider_error")),
        "strict_gate_passed": as_bool(row.get("fresh_strict_gate_passed")),
    }


def local_fresh_disagreement(preflight: dict[str, Any], gap: dict[str, Any]) -> bool:
    local_rate = as_float(preflight.get("coverage_rate_local"), -1.0)
    if local_rate < 0:
        coverage = preflight.get("entity_coverage") if isinstance(preflight, dict) else {}
        if isinstance(coverage, dict):
            local_rate = as_float(coverage.get("coverage_rate_local"), -1.0)
    return local_rate >= 0.999 and 0.0 < as_float(gap.get("fresh_CG")) < 1.0


def risk_score(
    route: str,
    status: str,
    gap: dict[str, Any],
    root: dict[str, Any],
    leaves: dict[str, Any],
    preflight: dict[str, Any],
) -> int:
    if route in {"provider_error_rerun_queue", "ans_provider_error_rerun_queue"}:
        return 5
    if route == "standard_fresh_eval_queue":
        return 15
    score = 20
    score += int(gap.get("fresh_missing_entity_count") or 0) * 12
    if as_float(gap.get("fresh_CG")) == 0.0 and as_float(gap.get("fresh_REA")) == 0.0 and status == "fresh_metric_or_reasoning_failed":
        score += 35
    score += min(int(leaves.get("background_leaf_count") or 0) * 4, 24)
    score += min(int(leaves.get("truncated_leaf_count") or 0) * 7, 21)
    score += min(int(root.get("root_overclaim_count") or 0) * 4, 24)
    if root.get("root_truncation_suspected"):
        score += 20
    if local_fresh_disagreement(preflight, gap):
        score += 15
    if route == "claim_graph_reconstruction_from_source_inventory":
        score += 35
    if route == "pareto_safe_rollback_or_hybrid_selector":
        score += 25
    if route == "judge_reason_targeted_reconstruction":
        score += 25
    return min(score, 100)


def confidence_band(score: int) -> str:
    if score <= 20:
        return "verify_or_rerun"
    if score <= 45:
        return "micro_repair_likely"
    if score <= 70:
        return "narrow_repair_needed"
    return "rebuild_or_hybrid_needed"


def recommended_module(
    route: str,
    status: str,
    gap: dict[str, Any],
    root: dict[str, Any],
    leaves: dict[str, Any],
    preflight: dict[str, Any],
) -> tuple[str, str, str]:
    cg = as_float(gap.get("fresh_CG"))
    rea = as_float(gap.get("fresh_REA"))
    if route == "ans_provider_error_rerun_queue":
        return (
            "evaluate_ans_factscore_style_350.py",
            "rerun_row_ans_only",
            "Fresh 1/1 is clean; ANS gate is provider-contaminated, so content edits are forbidden.",
        )
    if route == "provider_error_rerun_queue":
        return (
            "evaluate_evidence_bound_staged_candidate.py",
            "rerun_standard_fresh_eval_only",
            "Fresh gate is provider-contaminated; keep candidate fixed until clean rerun.",
        )
    if route == "standard_fresh_eval_queue":
        return (
            "evaluate_evidence_bound_staged_candidate.py",
            "run_standard_fresh_eval_then_ans_guards",
            "Provider-free candidate is locally ready and needs normal fresh/ANS verification.",
        )
    if route == "pareto_safe_rollback_or_hybrid_selector":
        return (
            "run_ans_safe_hybrid_batch_candidates.py",
            "rollback_or_hybrid_portfolio",
            "Metric regression needs selection from prior fresh-correct topology, not new broad generation.",
        )
    if route == "claim_graph_reconstruction_from_source_inventory":
        return (
            "run_evidence_bound_graph_spec_regeneration.py",
            "source_inventory_claim_graph_reconstruction",
            "No majority-correct anchor exists; rebuild from exact source inventory and claim triplets.",
        )
    if route == "judge_reason_targeted_reconstruction":
        return (
            "run_evidence_bound_graph_spec_regeneration.py",
            "judge_reason_to_verification_questions",
            "Judge rejection should become narrow verification questions before editing.",
        )
    if route == "route_specific_candidate_revision":
        if root.get("root_truncation_suspected"):
            return (
                "materialize_minimal_root_edit_candidate.py",
                "repair_truncated_or_incomplete_root",
                "Root text appears syntactically truncated, so repair NROOT before changing topology.",
            )
        if cg >= 0.88 and rea >= 1.0:
            return (
                "materialize_minimal_root_edit_candidate.py",
                "near_miss_root_commitment_prune",
                "Fresh REA is clean and CG is near 1; prune or restate only the unreliable root commitment.",
            )
        if local_fresh_disagreement(preflight, gap):
            return (
                "materialize_source_leaf_bridge_candidate.py",
                "source_claim_closure_bridge",
                "Local coverage says complete but fresh CG disagrees; close source-claim support with a unique leaf or alias bridge.",
            )
        if cg == 0.0 and rea == 0.0:
            return (
                "run_evidence_bound_graph_spec_regeneration.py",
                "claim_graph_reconstruction_from_candidate_inventory",
                "Clean provider output rejected the graph entirely; rebuild around exact source-backed claims.",
            )
        return (
            "materialize_source_leaf_bridge_candidate.py",
            "route_specific_source_leaf_revision",
            "Revise only the failed coverage/support contract while preserving validated reasoning.",
        )
    return (
        "manual_contract_audit",
        "manual_review",
        "No automated route-specific module was selected.",
    )


def claim_closure_flags(
    route: str,
    gap: dict[str, Any],
    root: dict[str, Any],
    leaves: dict[str, Any],
    preflight: dict[str, Any],
) -> list[str]:
    flags = []
    if route in {"provider_error_rerun_queue", "ans_provider_error_rerun_queue"}:
        flags.append("provider_error_not_content_failure")
    if route == "standard_fresh_eval_queue":
        flags.append("provider_free_candidate_needs_standard_eval")
    if local_fresh_disagreement(preflight, gap):
        flags.append("local_fresh_coverage_disagreement")
    if gap.get("fresh_missing_entity_count"):
        flags.append("fresh_entity_gap")
    if gap.get("fresh_missing_entities"):
        flags.append("fresh_named_missing_entities")
    if leaves.get("background_leaf_count"):
        flags.append("background_or_topical_source_leaf")
    if leaves.get("truncated_leaf_count"):
        flags.append("truncated_source_leaf")
    if root.get("root_truncation_suspected"):
        flags.append("truncated_or_incomplete_root")
    if root.get("root_overclaim_count"):
        flags.append("root_commitment_overclaim_terms")
    if as_float(gap.get("fresh_CG")) == 0.0 and as_float(gap.get("fresh_REA")) == 0.0 and route == "route_specific_candidate_revision":
        flags.append("fresh_total_rejection_after_local_preflight")
    return flags


def build_diagnostic_row(row: dict[str, Any], atomic_row: dict[str, Any] | None) -> dict[str, Any]:
    graph = graph_from_row(row, atomic_row)
    preflight = preflight_from_row(row, atomic_row)
    fresh = fresh_detail(row)
    source_nodes = graph.get("source_nodes") or []
    leaves = source_leaf_features(source_nodes if isinstance(source_nodes, list) else [])
    root = root_features(str(graph.get("root_text") or ""))
    gap = fresh_gap(row, fresh)
    route = str(row.get("controller_route") or "")
    status = str(row.get("controller_status") or "")
    score = risk_score(route, status, gap, root, leaves, preflight)
    module, action, rationale = recommended_module(route, status, gap, root, leaves, preflight)
    flags = claim_closure_flags(route, gap, root, leaves, preflight)
    paper_spec = str(row.get("paper_spec") or "")
    return {
        "paper_spec": paper_spec,
        "model": row.get("model", ""),
        "paper": row.get("paper", ""),
        "controller_route": route,
        "controller_status": status,
        "failure_type": row.get("failure_type", ""),
        "candidate_label": row.get("candidate_label", ""),
        "fresh_CG": gap["fresh_CG"],
        "fresh_REA": gap["fresh_REA"],
        "fresh_missing_entity_count": gap["fresh_missing_entity_count"],
        "fresh_missing_entities": gap["fresh_missing_entities"],
        "fresh_total_entities": gap["fresh_total_entities"],
        "passed_local_preflight": row.get("passed_local_preflight", ""),
        "local_fresh_disagreement": local_fresh_disagreement(preflight, gap),
        "root_word_count": root["root_word_count"],
        "root_clause_count": root["root_clause_count"],
        "root_overclaim_count": root["root_overclaim_count"],
        "root_overclaim_hits": root["root_overclaim_hits"],
        "root_truncation_suspected": root["root_truncation_suspected"],
        "source_node_count": graph.get("source_node_count", ""),
        "background_leaf_count": leaves["background_leaf_count"],
        "truncated_leaf_count": leaves["truncated_leaf_count"],
        "duplicate_source_tuple_count": leaves["duplicate_source_tuple_count"],
        "closure_risk_score": score,
        "closure_confidence_band": confidence_band(score),
        "recommended_module": module,
        "recommended_action": action,
        "recommendation_rationale": rationale,
        "claim_closure_flags": flags,
        "graph_spec": graph.get("graph_spec", row.get("graph_spec", "")),
        "preflight_report": (preflight.get("preflight_report") if isinstance(preflight, dict) else "") or row.get("preflight_report", ""),
        "fresh_eval_results": row.get("fresh_eval_results", ""),
        "root_text": root["root_text"],
        "background_leaves": leaves["background_leaves"],
        "truncated_leaves": leaves["truncated_leaves"],
    }


def csv_projection(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("root_overclaim_hits", "claim_closure_flags"):
        out[key] = ";".join(str(item) for item in out.get(key, []))
    out["fresh_missing_entities"] = ";".join(str(item) for item in out.get("fresh_missing_entities", []))
    out["root_text"] = str(out.get("root_text") or "")[:300]
    out.pop("background_leaves", None)
    out.pop("truncated_leaves", None)
    return out


def write_markdown(path: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# After327 Source-Claim Closure Diagnostic",
        "",
        f"Created: `{summary['created_at']}`",
        "",
        "This is an offline diagnostic. It does not call providers, write canonical accounting, or relax fresh/ANS acceptance.",
        "",
        "## Current State",
        "",
        f"- Strict rows: `{summary['current_strict_success_rows']}`",
        f"- Residual rows: `{summary['current_typed_residual_rows']}`",
        f"- Diagnostic rows: `{summary['diagnostic_row_count']}`",
        f"- Provider calls: `{summary['provider_calls']}`",
        f"- Canonical accounting write: `{summary['canonical_accounting_write']}`",
        "",
        "## Recommended Actions",
        "",
    ]
    for action, count in sorted(summary["recommended_action_counts"].items()):
        lines.append(f"- `{action}`: `{count}`")
    lines.extend(["", "## Highest-Value Offline Queue", ""])
    lines.append("| Risk | Paper Spec | Route | Fresh | Flags | Module | Action |")
    lines.append("|---:|---|---|---|---|---|---|")
    actionable = [
        row
        for row in rows
        if row["recommended_action"]
        not in {"rerun_row_ans_only", "rerun_standard_fresh_eval_only", "run_standard_fresh_eval_then_ans_guards"}
    ]
    for row in sorted(actionable, key=lambda item: (-int(item["closure_risk_score"]), str(item["paper_spec"])))[:12]:
        flags = ", ".join(row.get("claim_closure_flags") or [])[:120]
        lines.append(
            "| {risk} | `{spec}` | `{route}` | `{cg:.3g}/{rea:.3g}` | {flags} | `{module}` | `{action}` |".format(
                risk=row["closure_risk_score"],
                spec=row["paper_spec"],
                route=row["controller_route"],
                cg=as_float(row["fresh_CG"]),
                rea=as_float(row["fresh_REA"]),
                flags=flags,
                module=row["recommended_module"],
                action=row["recommended_action"],
            )
        )
    lines.extend(["", "## Literature Rules", ""])
    for rule in LITERATURE_RULES:
        lines.append(f"- `{rule['source']}`: {rule['pearl_design']} {rule['url']}")
    lines.extend(["", "## Output Files", ""])
    lines.append(f"- `{rel(path.with_suffix('.json'))}`")
    lines.append(f"- `{rel(path.with_suffix('.csv'))}`")
    write_text(path, "\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-queue", default=str(DEFAULT_CONTROLLER_QUEUE))
    parser.add_argument("--atomic-ledger", default=str(DEFAULT_ATOMIC_LEDGER))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    controller_queue = resolve(args.controller_queue)
    atomic_ledger = resolve(args.atomic_ledger)
    out_root = resolve(args.out_root)
    controller_payload = read_json(controller_queue, {})
    controller_rows = load_controller_rows(controller_queue)
    atomic_rows = load_atomic_rows(atomic_ledger)
    rows = [build_diagnostic_row(row, atomic_rows.get(str(row.get("paper_spec") or ""))) for row in controller_rows]
    rows.sort(key=lambda item: (-int(item["closure_risk_score"]), str(item["paper_spec"])))

    controller_summary = controller_payload.get("summary") if isinstance(controller_payload, dict) else {}
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "after327_source_claim_closure_diagnostic",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_controller_queue": rel(controller_queue),
        "source_atomic_ledger": rel(atomic_ledger),
        "out_root": rel(out_root),
        "current_strict_success_rows": (controller_summary or {}).get("current_strict_success_rows", ""),
        "current_typed_residual_rows": (controller_summary or {}).get("current_typed_residual_rows", ""),
        "diagnostic_row_count": len(rows),
        "route_counts": dict(Counter(str(row.get("controller_route") or "") for row in rows)),
        "recommended_action_counts": dict(Counter(str(row.get("recommended_action") or "") for row in rows)),
        "closure_confidence_band_counts": dict(Counter(str(row.get("closure_confidence_band") or "") for row in rows)),
        "literature_rules": LITERATURE_RULES,
        "acceptance_contract": {
            "fresh_CG": 1.0,
            "fresh_REA": 1.0,
            "judge_provider_error": False,
            "row_ANS": "non-regression",
            "proposal_batch_ANS": "at_or_above_canonical_300_floor",
            "canonical_accounting_write": False,
        },
    }
    payload = {"summary": summary, "rows": rows}
    json_path = out_root / "AFTER327_SOURCE_CLAIM_CLOSURE_DIAGNOSTIC.json"
    csv_path = out_root / "AFTER327_SOURCE_CLAIM_CLOSURE_DIAGNOSTIC.csv"
    md_path = out_root / "AFTER327_SOURCE_CLAIM_CLOSURE_DIAGNOSTIC.md"
    write_json(json_path, payload)
    fieldnames = [
        "closure_risk_score",
        "closure_confidence_band",
        "paper_spec",
        "model",
        "paper",
        "controller_route",
        "controller_status",
        "failure_type",
        "candidate_label",
        "fresh_CG",
        "fresh_REA",
        "fresh_missing_entity_count",
        "fresh_missing_entities",
        "fresh_total_entities",
        "passed_local_preflight",
        "local_fresh_disagreement",
        "root_word_count",
        "root_clause_count",
        "root_overclaim_count",
        "root_overclaim_hits",
        "root_truncation_suspected",
        "source_node_count",
        "background_leaf_count",
        "truncated_leaf_count",
        "duplicate_source_tuple_count",
        "claim_closure_flags",
        "recommended_module",
        "recommended_action",
        "recommendation_rationale",
        "graph_spec",
        "preflight_report",
        "fresh_eval_results",
        "root_text",
    ]
    write_csv(csv_path, [csv_projection(row) for row in rows], fieldnames)
    write_markdown(md_path, rows, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
