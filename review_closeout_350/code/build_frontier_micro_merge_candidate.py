#!/usr/bin/env python3
"""Build a deterministic frontier-preserving micro-merge graph.

The script keeps a fresh-audited frontier graph as the rollback baseline and
replaces only selected target units from a donor graph. It is intentionally
offline: it does not call providers and does not claim closure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK_DIR = PROJECT_ROOT / "code" / "framework"
sys.path.insert(0, str(FRAMEWORK_DIR))

from dot_to_graph_spec import dot_to_graph_spec  # type: ignore  # noqa: E402
from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from reasoning_graph_validator import assess_dot_quality  # type: ignore  # noqa: E402


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_spec(path: Path) -> Dict[str, Any]:
    spec, issues = dot_to_graph_spec(path.read_text(encoding="utf-8"))
    if spec is None:
        raise RuntimeError(f"cannot parse DOT {path}: {issues}")
    return spec


def node_map(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(node.get("id")): dict(node)
        for node in spec.get("nodes", [])
        if isinstance(node, dict) and node.get("id")
    }


def edge_list(spec: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {
            "source": str(edge.get("source", "")),
            "target": str(edge.get("target", "")),
            "type": str(edge.get("type", "")),
        }
        for edge in spec.get("edges", [])
        if isinstance(edge, dict) and edge.get("source") and edge.get("target")
    ]


def upstream_nodes(edges: Iterable[Dict[str, str]], targets: Set[str]) -> Set[str]:
    incoming: Dict[str, List[str]] = {}
    for edge in edges:
        incoming.setdefault(edge["target"], []).append(edge["source"])
    needed: Set[str] = set(targets)
    stack = list(targets)
    while stack:
        current = stack.pop()
        for source in incoming.get(current, []):
            if source not in needed:
                needed.add(source)
                stack.append(source)
    return needed


def natural_node_sort(node_id: str) -> tuple[int, str]:
    match = re.match(r"([A-Za-z]+)(\d+)$", node_id)
    if not match:
        return (10**9, node_id)
    prefix, number = match.groups()
    prefix_rank = {"N": 0, "R": 1}.get(prefix, 100)
    return (prefix_rank * 10**6 + int(number), node_id)


def build_micro_merge(
    frontier_spec: Dict[str, Any],
    donor_spec: Dict[str, Any],
    *,
    targets: List[str],
) -> Dict[str, Any]:
    frontier_nodes = node_map(frontier_spec)
    donor_nodes = node_map(donor_spec)
    frontier_edges = edge_list(frontier_spec)
    donor_edges = edge_list(donor_spec)
    target_set = set(targets)
    donor_needed_nodes = upstream_nodes(donor_edges, target_set)

    for target in target_set:
        if target not in donor_nodes:
            raise RuntimeError(f"target {target} not found in donor graph")

    merged_nodes = dict(frontier_nodes)
    for node_id in sorted(donor_needed_nodes, key=natural_node_sort):
        if node_id in donor_nodes:
            merged_nodes[node_id] = dict(donor_nodes[node_id])

    filtered_edges = [
        edge
        for edge in frontier_edges
        if edge["target"] not in target_set
        and not (edge["source"] in target_set and edge["target"] == "NROOT")
    ]
    donor_unit_edges = [
        edge
        for edge in donor_edges
        if edge["target"] in target_set or (edge["source"] in target_set and edge["target"] == "NROOT")
    ]
    seen = set()
    merged_edges: List[Dict[str, str]] = []
    for edge in filtered_edges + donor_unit_edges:
        key = (edge["source"], edge["target"], edge["type"])
        if key in seen:
            continue
        seen.add(key)
        merged_edges.append(edge)

    used = {edge["source"] for edge in merged_edges} | {edge["target"] for edge in merged_edges}
    merged_spec = {
        "paper_id": frontier_spec.get("paper_id") or donor_spec.get("paper_id", ""),
        "root": "NROOT",
        "nodes": [merged_nodes[node_id] for node_id in sorted(used, key=natural_node_sort) if node_id in merged_nodes],
        "edges": merged_edges,
        "curation": {
            **(frontier_spec.get("curation", {}) if isinstance(frontier_spec.get("curation"), dict) else {}),
            "policy": "frontier_preserving_micro_merge",
            "micro_merge_targets": targets,
            "donor_needed_nodes": sorted(donor_needed_nodes, key=natural_node_sort),
            "note": "Only selected target units are replaced from donor; frontier coverage-bearing graph is retained.",
        },
    }
    return merged_spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontier-graph", required=True)
    parser.add_argument("--donor-graph", required=True)
    parser.add_argument("--targets", nargs="+", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--prefix", default="frontier_micro_merge")
    args = parser.parse_args()

    frontier_graph = resolve_path(args.frontier_graph)
    donor_graph = resolve_path(args.donor_graph)
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    frontier_spec = read_spec(frontier_graph)
    donor_spec = read_spec(donor_graph)
    merged_spec = build_micro_merge(frontier_spec, donor_spec, targets=args.targets)
    dot = graph_spec_to_dot(merged_spec)
    parseable, strict_ok, strict_valid, strict_issues = assess_dot_quality(dot, mode="strict")

    spec_path = out_dir / f"{args.prefix}.json"
    dot_path = out_dir / f"{args.prefix}.dot"
    report_path = out_dir / f"{args.prefix}_report.json"
    spec_path.write_text(json.dumps(merged_spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dot_path.write_text(dot, encoding="utf-8")
    report = {
        "frontier_graph": str(frontier_graph),
        "donor_graph": str(donor_graph),
        "targets": args.targets,
        "json": str(spec_path),
        "dot": str(dot_path),
        "node_count": len(merged_spec.get("nodes", [])),
        "edge_count": len(merged_spec.get("edges", [])),
        "parseable": parseable,
        "strict_ok": strict_ok,
        "strict_valid": strict_valid,
        "strict_issues": strict_issues,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if strict_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
