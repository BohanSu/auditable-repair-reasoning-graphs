#!/usr/bin/env python3
"""Materialize a constrained local-window graph_spec patch.

Unlike full regeneration, this runner only accepts replacements for explicitly
listed node ids and their incoming edges. It is used when a single reasoning-unit
repair causes a downstream aggregation node to fail, so the repair window must
include both the fixed unit and the immediately affected synthesis node.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
    / "10_standard_flow_350_subset_package"
)
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "local_window_patches"

FRAMEWORK_DIR = PROJECT_ROOT / "code" / "framework"
RESTRUCTURED_DIR = PROJECT_ROOT / "operation_records" / "restructured"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(FRAMEWORK_DIR))
sys.path.insert(0, str(RESTRUCTURED_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from graph_spec_validator import validate_graph_spec  # type: ignore  # noqa: E402
from run_evidence_bound_graph_spec_regeneration import preflight_graph_spec  # type: ignore  # noqa: E402


STANDARD_EDGE_TYPES = {
    "deduction-rule",
    "deduction-case",
    "induction-case",
    "induction-common",
    "abduction-phenomenon",
    "abduction-knowledge",
}


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def safe_slug(value: str, *, limit: int = 140) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    out = re.sub(r"_+", "_", out).strip("_")
    return (out or "item")[:limit]


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


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def edge_pairing_valid(edges: List[Dict[str, Any]]) -> tuple[bool, str]:
    labels = [str(edge.get("type") or "") for edge in edges]
    if any(label not in STANDARD_EDGE_TYPES for label in labels):
        return False, "non_standard_edge_type"
    label_set = set(labels)
    if label_set <= {"deduction-rule", "deduction-case"}:
        return (labels.count("deduction-rule") == 1 and labels.count("deduction-case") == 1), "deduction"
    if label_set <= {"abduction-phenomenon", "abduction-knowledge"}:
        return (labels.count("abduction-phenomenon") == 1 and labels.count("abduction-knowledge") == 1), "abduction"
    if label_set <= {"induction-common", "induction-case"}:
        return (labels.count("induction-common") == 1 and labels.count("induction-case") >= 1), "induction"
    return False, "mixed_reasoning_family"


def validate_patch(patch: Dict[str, Any], spec: Dict[str, Any], allowed_targets: set[str]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    node_ids = {str(node.get("id")) for node in spec.get("nodes") or [] if isinstance(node, dict)}
    replace_nodes = patch.get("replace_nodes")
    replace_incoming = patch.get("replace_incoming_edges")
    replace_outgoing = patch.get("optional_replace_outgoing_edges", [])
    if not isinstance(replace_nodes, list) or not replace_nodes:
        issues.append({"type": "replace_nodes_missing"})
    else:
        seen: set[str] = set()
        for node in replace_nodes:
            if not isinstance(node, dict):
                issues.append({"type": "replace_node_not_object"})
                continue
            node_id = str(node.get("id") or "")
            seen.add(node_id)
            if node_id not in allowed_targets:
                issues.append({"type": "replace_node_outside_allowed_window", "node": node_id})
            if node_id not in node_ids:
                issues.append({"type": "replace_node_unknown", "node": node_id})
            if node.get("source") != [0, 0, 0]:
                issues.append({"type": "replace_node_source_must_be_bridge_tuple", "node": node_id})
            if not isinstance(node.get("text"), str) or not node.get("text", "").strip():
                issues.append({"type": "replace_node_text_missing", "node": node_id})
        if not seen <= allowed_targets:
            issues.append({"type": "replace_node_set_exceeds_allowed_window", "seen": sorted(seen)})

    if not isinstance(replace_incoming, dict):
        issues.append({"type": "replace_incoming_edges_must_be_object_by_target"})
    else:
        for target, edges in replace_incoming.items():
            target = str(target)
            if target not in allowed_targets:
                issues.append({"type": "replace_incoming_target_outside_allowed_window", "target": target})
                continue
            if not isinstance(edges, list) or not edges:
                issues.append({"type": "replace_incoming_edges_missing", "target": target})
                continue
            for edge in edges:
                if not isinstance(edge, dict):
                    issues.append({"type": "incoming_edge_not_object", "target": target})
                    continue
                if edge.get("target") != target:
                    issues.append({"type": "incoming_edge_target_mismatch", "target": target, "edge": edge})
                if edge.get("source") not in node_ids:
                    issues.append({"type": "incoming_edge_source_unknown", "target": target, "edge": edge})
                if edge.get("source") == target:
                    issues.append({"type": "incoming_edge_self_loop", "target": target, "edge": edge})
            ok, family = edge_pairing_valid(edges)
            if not ok:
                issues.append({"type": "incoming_edges_not_legal_reasoning_unit", "target": target, "family": family})

    if replace_outgoing is None:
        replace_outgoing = []
    if not isinstance(replace_outgoing, list):
        issues.append({"type": "optional_replace_outgoing_edges_must_be_list"})
    else:
        for edge in replace_outgoing:
            if not isinstance(edge, dict):
                issues.append({"type": "outgoing_edge_not_object"})
                continue
            if edge.get("source") not in allowed_targets:
                issues.append({"type": "outgoing_edge_source_outside_allowed_window", "edge": edge})
            if edge.get("target") not in node_ids:
                issues.append({"type": "outgoing_edge_target_unknown", "edge": edge})
            if edge.get("type") not in STANDARD_EDGE_TYPES:
                issues.append({"type": "outgoing_edge_type_non_standard", "edge": edge})
    return issues


def merge_patch(spec: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    replace_nodes = {str(node["id"]): node for node in patch.get("replace_nodes") or []}
    replace_incoming = patch.get("replace_incoming_edges") or {}
    outgoing_by_source: Dict[str, List[Dict[str, str]]] = {}
    for edge in patch.get("optional_replace_outgoing_edges") or []:
        source = str(edge["source"])
        outgoing_by_source.setdefault(source, []).append(
            {"source": source, "target": str(edge["target"]), "type": str(edge["type"])}
        )

    out = dict(spec)
    out["nodes"] = [
        {"id": node_id, "source": replacement.get("source"), "text": replacement.get("text")}
        if isinstance(node, dict) and (node_id := str(node.get("id") or "")) in replace_nodes and (replacement := replace_nodes[node_id])
        else node
        for node in spec.get("nodes") or []
    ]
    targets = set(str(target) for target in replace_incoming)
    outgoing_sources = set(outgoing_by_source)
    edges = [
        edge
        for edge in spec.get("edges") or []
        if not (
            isinstance(edge, dict)
            and (str(edge.get("target") or "") in targets or str(edge.get("source") or "") in outgoing_sources)
        )
    ]
    for target, target_edges in replace_incoming.items():
        for edge in target_edges:
            edges.append({"source": str(edge["source"]), "target": str(target), "type": str(edge["type"])})
    for target_edges in outgoing_by_source.values():
        edges.extend(target_edges)
    out["edges"] = edges
    return out


def materialize(
    *,
    source_graph_spec: Path,
    source_packet: Path,
    source_stage_dir: Path,
    patch_path: Path,
    allowed_targets: Sequence[str],
    out_root: Path,
    label: str,
    paper_spec: str,
    model: str,
    copy_staging: bool,
) -> Dict[str, Any]:
    source_spec = read_json(source_graph_spec, {})
    packet = read_json(source_packet, {})
    patch = read_json(patch_path, {})
    attempt_dir = out_root / safe_slug(label) / safe_slug(paper_spec) / safe_slug(model)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    write_json(attempt_dir / "patch.json", patch)

    issues = validate_patch(patch, source_spec, set(allowed_targets))
    if issues:
        write_json(attempt_dir / "local_window_validation_report.json", {"valid": False, "issues": issues})
        return {
            "priority": 1,
            "paper_spec": paper_spec,
            "lane": "local_window_patch",
            "model": model,
            "candidate_label": label,
            "patch": rel(patch_path),
            "graph_spec": "",
            "dot": "",
            "preflight_report": rel(attempt_dir / "local_window_validation_report.json"),
            "staged_run_dir": "",
            "patch_valid": False,
            "passed_local_preflight": False,
            "strict_validator_valid": "",
            "unit_invalid_count": "",
            "entity_coverage_local": "",
            "covered_entities_local": "",
            "total_entities": "",
            "stranded_node_count": "",
            "fresh_gate_required": True,
            "status": "patch_validation_failed",
        }

    merged = merge_patch(source_spec, patch)
    strict_valid, strict_issues = validate_graph_spec(merged, mode="strict")
    preflight = preflight_graph_spec(merged, packet)
    graph_spec_path = attempt_dir / "graph_spec.json"
    dot_path = attempt_dir / "final_clean_graph.dot"
    preflight_path = attempt_dir / "preflight_report.json"
    write_json(graph_spec_path, merged)
    write_text(dot_path, graph_spec_to_dot(merged))
    write_json(
        preflight_path,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "paper_spec": paper_spec,
            "candidate_label": label,
            "model": model,
            "patch": rel(patch_path),
            "allowed_targets": list(allowed_targets),
            "patch_validation": {"valid": True, "issues": []},
            "strict_validator": {"valid": strict_valid, "issues": strict_issues},
            **preflight,
        },
    )

    staged_dir = ""
    if copy_staging and preflight.get("passed_local_preflight") is True:
        stage_dir = out_root / safe_slug(label) / "strict_gate_staging" / safe_slug(paper_spec) / safe_slug(model)
        stage_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dot_path, stage_dir / "final_clean_graph.dot")
        input_data = source_stage_dir / "input_data.json"
        if input_data.exists():
            shutil.copy2(input_data, stage_dir / "input_data.json")
        write_json(
            stage_dir / "evidence_bound_packet_pointer.json",
            {
                "packet": rel(source_packet),
                "local_window_patch": rel(patch_path),
                "preflight_report": rel(preflight_path),
                "source_graph_spec": rel(source_graph_spec),
                "allowed_targets": list(allowed_targets),
            },
        )
        staged_dir = rel(stage_dir)

    return {
        "priority": 1,
        "paper_spec": paper_spec,
        "lane": "local_window_patch",
        "model": model,
        "candidate_label": label,
        "attempt_path": rel(patch_path),
        "patch": rel(patch_path),
        "graph_spec": rel(graph_spec_path),
        "dot": rel(dot_path),
        "preflight_report": rel(preflight_path),
        "staged_run_dir": staged_dir,
        "patch_valid": True,
        "passed_local_preflight": preflight.get("passed_local_preflight") is True,
        "strict_validator_valid": strict_valid,
        "unit_invalid_count": preflight.get("reasoning_units", {}).get("invalid_target_count"),
        "entity_coverage_local": preflight.get("entity_coverage", {}).get("coverage_rate_local"),
        "covered_entities_local": preflight.get("entity_coverage", {}).get("covered_entities"),
        "total_entities": preflight.get("entity_coverage", {}).get("total_entities"),
        "stranded_node_count": len(preflight.get("connectivity", {}).get("stranded_nodes") or []),
        "fresh_gate_required": True,
        "status": "",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-graph-spec", required=True)
    parser.add_argument("--source-packet", required=True)
    parser.add_argument("--source-stage-dir", required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--allowed-targets", nargs="+", required=True)
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--label", required=True)
    parser.add_argument("--paper-spec", required=True)
    parser.add_argument("--model", default="constraint-local-v2")
    parser.add_argument("--copy-staging", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_root = resolve_path(args.out_root)
    row = materialize(
        source_graph_spec=resolve_path(args.source_graph_spec),
        source_packet=resolve_path(args.source_packet),
        source_stage_dir=resolve_path(args.source_stage_dir),
        patch_path=resolve_path(args.patch),
        allowed_targets=args.allowed_targets,
        out_root=out_root,
        label=args.label,
        paper_spec=args.paper_spec,
        model=args.model,
        copy_staging=args.copy_staging,
    )
    fieldnames = [
        "priority",
        "paper_spec",
        "lane",
        "model",
        "candidate_label",
        "attempt_path",
        "patch",
        "graph_spec",
        "dot",
        "preflight_report",
        "staged_run_dir",
        "patch_valid",
        "passed_local_preflight",
        "strict_validator_valid",
        "unit_invalid_count",
        "entity_coverage_local",
        "covered_entities_local",
        "total_entities",
        "stranded_node_count",
        "fresh_gate_required",
        "status",
    ]
    out_dir = out_root / safe_slug(args.label)
    write_csv(out_dir / "LOCAL_WINDOW_ATTEMPT_INDEX.csv", [row], fieldnames)
    write_json(out_dir / "LOCAL_WINDOW_ATTEMPT_INDEX.json", {"rows": [row]})
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "evidence_bound_local_window_patch",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "out_root": rel(out_dir),
        "candidate_label": args.label,
        "paper_spec": args.paper_spec,
        "model": args.model,
        "allowed_targets": args.allowed_targets,
        "attempt_rows": 1,
        "patch_valid": int(row.get("patch_valid") is True),
        "local_preflight_passed": int(row.get("passed_local_preflight") is True),
        "by_lane": dict(Counter([row.get("lane", "")])),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
    }
    write_json(out_dir / "LOCAL_WINDOW_ATTEMPT_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
