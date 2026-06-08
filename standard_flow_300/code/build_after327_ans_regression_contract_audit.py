#!/usr/bin/env python3
"""Build provider-free ANS regression contracts for after327 candidates.

The audit reads the after327 controller queue and inspects rows that already
passed fresh CG/REA but failed row-level ANS. It produces node-level unsupported
atomic fact diagnostics and narrow repair recommendations. It does not call any
provider and does not write canonical accounting.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_CONTROLLER_QUEUE = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_after327_failure_typed_lit_diagnostic"
    / "controller_queue_v1"
    / "AFTER327_CONTROLLER_QUEUE.json"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_after327_failure_typed_lit_diagnostic"
    / "ans_regression_contract_audit_v1"
)

ROOT_OR_REASONING_TYPES = {
    "semantic_root",
    "root_common_bridge",
    "repaired_reasoning_node",
    "implicit_reasoning_node",
    "graph_node",
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


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


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def node_by_id(graph_path: Path) -> dict[str, dict[str, Any]]:
    graph = read_json(graph_path, {})
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    out: dict[str, dict[str, Any]] = {}
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict) and node.get("id"):
            out[str(node["id"])] = node
    return out


def unsupported_facts(row: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for fact in row.get("atomic_facts", []):
        if not isinstance(fact, dict):
            continue
        supported = fact.get("supported")
        label = str(fact.get("label") or "").lower()
        if supported is False or label == "unsupported":
            out.append(fact)
    return out


def recommend_action(node_id: str, unit_type: str, unsupported: int, atomic: int) -> tuple[str, str]:
    ratio = unsupported / atomic if atomic else 0.0
    if unit_type == "root_common_bridge":
        return (
            "drop_or_exclude_from_ans_claims",
            "Structural bridge text is not paper evidence and should not be ANS-visible.",
        )
    if unit_type == "semantic_root":
        return (
            "rewrite_root_near_extractively",
            "Keep the fresh 1/1 topology but remove unsupported abstract clauses from the root.",
        )
    if unit_type in {"repaired_reasoning_node", "implicit_reasoning_node"}:
        if ratio >= 0.5:
            return (
                "rewrite_node_to_supported_subset",
                "Replace the node with only the supported atomic subset; preserve incoming/outgoing topology.",
            )
        return (
            "micro_prune_unsupported_clause",
            "Delete or soften the unsupported clause without changing graph structure.",
        )
    if unit_type == "explicit_source_node":
        if ratio >= 0.5:
            return (
                "replace_with_exact_source_span_or_drop",
                "The source node exposes unsupported claims; use a shorter extractive span or remove it if not CG-required.",
            )
        return (
            "micro_prune_source_leaf_clause",
            "Keep the source leaf only if CG-required, but remove unsupported embellishment.",
        )
    return (
        "manual_contract_review",
        f"Node `{node_id}` has unsupported ANS facts but no route-specific rule.",
    )


def is_ans_visible_risk(unit_type: str, unsupported: int) -> bool:
    return unsupported > 0 and unit_type in ROOT_OR_REASONING_TYPES.union({"explicit_source_node"})


def build_contract_for_row(controller_row: dict[str, Any]) -> dict[str, Any]:
    paper_spec = str(controller_row.get("paper_spec") or "")
    ans_eval_dir = resolve(str(controller_row.get("ans_eval_dir") or ""))
    graph_spec = resolve(str(controller_row.get("graph_spec") or ""))
    ans_rows = [
        row
        for row in read_jsonl(ans_eval_dir / "ans_node_results.jsonl")
        if str(row.get("paper_spec") or "") == paper_spec
    ]
    nodes = node_by_id(graph_spec)
    node_rows: list[dict[str, Any]] = []
    unsupported_fact_rows: list[dict[str, Any]] = []
    totals = {"atomic": 0, "supported": 0, "unsupported": 0}
    for row in ans_rows:
        node_id = str(row.get("node_id") or "")
        unit_type = str(row.get("unit_type") or "")
        atomic = as_int(row.get("atomic_fact_count"))
        supported = as_int(row.get("supported_fact_count"))
        unsupported = max(0, atomic - supported)
        facts = unsupported_facts(row)
        totals["atomic"] += atomic
        totals["supported"] += supported
        totals["unsupported"] += unsupported
        action, rationale = recommend_action(node_id, unit_type, unsupported, atomic)
        graph_node = nodes.get(node_id, {})
        source_tuple = row.get("source_tuple")
        node_record = {
            "paper_spec": paper_spec,
            "candidate_label": controller_row.get("candidate_label", ""),
            "node_id": node_id,
            "unit_type": unit_type,
            "source_tuple": source_tuple,
            "atomic_fact_count": atomic,
            "supported_fact_count": supported,
            "unsupported_fact_count": unsupported,
            "unsupported_ratio": unsupported / atomic if atomic else 0.0,
            "ans_visible_risk": is_ans_visible_risk(unit_type, unsupported),
            "recommended_action": action if unsupported else "preserve",
            "action_rationale": rationale if unsupported else "Node is fully supported in ANS.",
            "node_text": row.get("node_text") or graph_node.get("text", ""),
            "graph_node_text": graph_node.get("text", ""),
        }
        node_rows.append(node_record)
        for fact in facts:
            unsupported_fact_rows.append(
                {
                    "paper_spec": paper_spec,
                    "candidate_label": controller_row.get("candidate_label", ""),
                    "node_id": node_id,
                    "unit_type": unit_type,
                    "source_tuple": source_tuple,
                    "fact": fact.get("fact", ""),
                    "reason": fact.get("reason", ""),
                    "recommended_action": node_record["recommended_action"],
                }
            )
    node_rows.sort(
        key=lambda row: (
            -as_int(row.get("unsupported_fact_count")),
            -as_float(row.get("unsupported_ratio")),
            str(row.get("node_id")),
        )
    )
    unsupported_fact_rows.sort(key=lambda row: (str(row.get("node_id")), str(row.get("fact"))))
    candidate_ans = as_float(controller_row.get("candidate_main_factual_ans"))
    guard_ans = as_float(controller_row.get("guard_current_best_main_factual_ans"))
    margin = candidate_ans - guard_ans
    required_extra_supported = 0
    if margin < 0 and totals["atomic"] > 0:
        required_extra_supported = max(1, int((guard_ans * totals["atomic"] - totals["supported"]) + 0.999999))
    risk_counts = Counter(str(row.get("unit_type")) for row in node_rows if as_int(row.get("unsupported_fact_count")) > 0)
    editable_nodes = [
        row["node_id"]
        for row in node_rows
        if row["recommended_action"] != "preserve" and row["unit_type"] != "semantic_root"
    ]
    forbidden_edits = [
        row["node_id"]
        for row in node_rows
        if row["recommended_action"] == "preserve" and row["unit_type"] in ROOT_OR_REASONING_TYPES
    ]
    return {
        "paper_spec": paper_spec,
        "candidate_label": controller_row.get("candidate_label", ""),
        "controller_route": controller_row.get("controller_route", ""),
        "fresh_CG": controller_row.get("fresh_CG", ""),
        "fresh_REA": controller_row.get("fresh_REA", ""),
        "candidate_main_factual_ans": candidate_ans,
        "guard_current_best_main_factual_ans": guard_ans,
        "guard_margin": margin,
        "ans_eval_dir": rel(ans_eval_dir),
        "graph_spec": rel(graph_spec),
        "totals": totals,
        "required_extra_supported_facts_for_guard_floor": required_extra_supported,
        "unsupported_unit_type_counts": dict(risk_counts),
        "preserve_topology": True,
        "max_edit_nodes_recommended": min(2, len(editable_nodes)) if paper_spec.endswith("56873-z") else min(3, len(editable_nodes)),
        "editable_nodes": editable_nodes,
        "forbidden_edits": forbidden_edits,
        "node_rows": node_rows,
        "unsupported_fact_rows": unsupported_fact_rows,
    }


def render_markdown(summary: dict[str, Any], contracts: list[dict[str, Any]]) -> str:
    lines = [
        "# After327 ANS Regression Contract Audit",
        "",
        f"Created: `{summary['created_at']}`",
        "",
        "Provider calls: `False`",
        "Canonical accounting write: `False`",
        "",
        "## Rows",
        "",
        "| Paper Spec | Candidate | ANS | Guard | Margin | Unsupported | First Actions |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for contract in contracts:
        actions = []
        for node in contract["node_rows"]:
            if node["recommended_action"] == "preserve":
                continue
            actions.append(f"{node['node_id']}:{node['recommended_action']}")
            if len(actions) >= 4:
                break
        lines.append(
            "| `{paper_spec}` | `{candidate_label}` | `{candidate_main_factual_ans:.12f}` | "
            "`{guard_current_best_main_factual_ans:.12f}` | `{guard_margin:.12f}` | "
            "`{unsupported}` | {actions} |".format(
                paper_spec=contract["paper_spec"],
                candidate_label=contract["candidate_label"],
                candidate_main_factual_ans=contract["candidate_main_factual_ans"],
                guard_current_best_main_factual_ans=contract["guard_current_best_main_factual_ans"],
                guard_margin=contract["guard_margin"],
                unsupported=contract["totals"]["unsupported"],
                actions=", ".join(actions),
            )
        )
    lines.extend(["", "## Repair Rules", ""])
    lines.append("- Preserve fresh `CG=1.0` / `REA=1.0` topology; edit only named ANS-heavy nodes.")
    lines.append("- Prefer deleting unsupported clauses over adding new facts.")
    lines.append("- Treat source leaves as ANS-visible claims; keep only unique CG-required leaves.")
    lines.append("- Provider-error rows are excluded from this audit; rerun them instead of editing.")
    lines.append("")
    for contract in contracts:
        lines.extend(["", f"## {contract['paper_spec']}", ""])
        lines.append(f"- Candidate: `{contract['candidate_label']}`")
        lines.append(f"- Required extra supported facts for guard floor: `{contract['required_extra_supported_facts_for_guard_floor']}`")
        lines.append(f"- Max edit nodes recommended: `{contract['max_edit_nodes_recommended']}`")
        lines.append(f"- Editable nodes: `{', '.join(contract['editable_nodes'])}`")
        lines.append(f"- Forbidden edits: `{', '.join(contract['forbidden_edits'])}`")
        lines.append("")
        lines.append("| Node | Type | Unsup/Atomic | Action | Unsupported Facts |")
        lines.append("|---|---|---:|---|---|")
        for node in contract["node_rows"][:10]:
            if node["recommended_action"] == "preserve" and node["unsupported_fact_count"] == 0:
                continue
            facts = [
                fact["fact"]
                for fact in contract["unsupported_fact_rows"]
                if fact["node_id"] == node["node_id"]
            ][:3]
            lines.append(
                "| `{node_id}` | `{unit_type}` | `{unsupported_fact_count}/{atomic_fact_count}` | "
                "`{recommended_action}` | {facts} |".format(
                    **node,
                    facts="; ".join(facts),
                )
            )
    return "\n".join(lines) + "\n"


def build(args: argparse.Namespace) -> dict[str, Any]:
    controller_path = resolve(args.controller_queue)
    out_root = resolve(args.out_root)
    controller = read_json(controller_path, {})
    rows = controller.get("rows") if isinstance(controller, dict) else []
    target_rows = [
        row
        for row in rows if isinstance(row, dict) and row.get("controller_status") == "fresh_1_1_ans_failed"
    ]
    contracts = [build_contract_for_row(row) for row in target_rows]
    node_fieldnames = [
        "paper_spec",
        "candidate_label",
        "node_id",
        "unit_type",
        "source_tuple",
        "atomic_fact_count",
        "supported_fact_count",
        "unsupported_fact_count",
        "unsupported_ratio",
        "ans_visible_risk",
        "recommended_action",
        "action_rationale",
        "node_text",
    ]
    fact_fieldnames = [
        "paper_spec",
        "candidate_label",
        "node_id",
        "unit_type",
        "source_tuple",
        "fact",
        "reason",
        "recommended_action",
    ]
    all_node_rows = [node for contract in contracts for node in contract["node_rows"]]
    all_fact_rows = [fact for contract in contracts for fact in contract["unsupported_fact_rows"]]
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "after327_ans_regression_contract_audit",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_controller_queue": rel(controller_path),
        "out_root": rel(out_root),
        "contract_count": len(contracts),
        "paper_specs": [contract["paper_spec"] for contract in contracts],
        "total_unsupported_facts": sum(contract["totals"]["unsupported"] for contract in contracts),
        "total_atomic_facts": sum(contract["totals"]["atomic"] for contract in contracts),
        "contracts": [
            {
                "paper_spec": contract["paper_spec"],
                "candidate_label": contract["candidate_label"],
                "guard_margin": contract["guard_margin"],
                "required_extra_supported_facts_for_guard_floor": contract[
                    "required_extra_supported_facts_for_guard_floor"
                ],
                "unsupported_unit_type_counts": contract["unsupported_unit_type_counts"],
                "editable_nodes": contract["editable_nodes"],
                "forbidden_edits": contract["forbidden_edits"],
                "max_edit_nodes_recommended": contract["max_edit_nodes_recommended"],
            }
            for contract in contracts
        ],
    }
    report = {"summary": summary, "contracts": contracts}
    write_json(out_root / "ANS_REGRESSION_CONTRACT_AUDIT.json", report)
    write_json(out_root / "ANS_REGRESSION_CONTRACT_SUMMARY.json", summary)
    write_csv(out_root / "ANS_REGRESSION_NODE_RISK.csv", all_node_rows, node_fieldnames)
    write_csv(out_root / "ANS_REGRESSION_UNSUPPORTED_FACTS.csv", all_fact_rows, fact_fieldnames)
    write_text(out_root / "ANS_REGRESSION_CONTRACT_AUDIT.md", render_markdown(summary, contracts))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-queue", default=str(DEFAULT_CONTROLLER_QUEUE))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
