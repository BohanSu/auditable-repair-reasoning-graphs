#!/usr/bin/env python3
"""Build a provider-free ANS delta planner for after327 repair rows.

This planner exists because simple unsupported-fact pruning can make ANS worse:
it may remove unsupported facts while also reducing supported-fact recall.  The
script reads candidate-level ANS node results and estimates, per node, the
upper-bound benefit of preserving supported facts while pruning only unsupported
facts, plus the downside of deleting or over-compressing the whole node.

It does not call any provider and does not write canonical accounting.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter
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
    / "ans_delta_planner_v1"
)

ROOT_OR_REASONING_TYPES = {
    "semantic_root",
    "root_common_bridge",
    "repaired_reasoning_node",
    "implicit_reasoning_node",
    "graph_node",
}
SOURCE_TYPES = {"explicit_source_node"}

LITERATURE_DESIGN_MAP = [
    {
        "source": "FActScore",
        "url": "https://arxiv.org/abs/2305.14251",
        "planner_rule": "Operate on atomic facts and preserve supported facts when removing unsupported ones.",
    },
    {
        "source": "SAFE / LongFact",
        "url": "https://arxiv.org/abs/2403.18802",
        "planner_rule": "Track factuality at the individual-fact level rather than trusting whole-node rewrites.",
    },
    {
        "source": "RAGChecker",
        "url": "https://arxiv.org/abs/2408.08067",
        "planner_rule": "Separate generation faithfulness errors from evidence and recall loss.",
    },
    {
        "source": "VERISCORE",
        "url": "https://arxiv.org/abs/2406.19276",
        "planner_rule": "Prefer edits to verifiable claims; avoid scoring structural glue as evidence.",
    },
    {
        "source": "Cited but Not Verified",
        "url": "https://arxiv.org/abs/2605.06635",
        "planner_rule": "A cited or source-like span is not enough; the exact claim must be supported.",
    },
]


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


def fact_supported(fact: dict[str, Any]) -> bool:
    label = str(fact.get("label") or "").lower()
    return fact.get("supported") is True or label == "supported"


def fact_unsupported(fact: dict[str, Any]) -> bool:
    label = str(fact.get("label") or "").lower()
    return fact.get("supported") is False or label == "unsupported"


def ans_score(supported: int, atomic: int) -> float:
    return supported / atomic if atomic > 0 else 0.0


def ceil_required_supported(supported: int, atomic: int, floor: float) -> int:
    if atomic <= 0:
        return 0
    return max(0, math.ceil(floor * atomic - supported - 1e-12))


def candidate_ans_rows(ans_eval_dir: Path, paper_spec: str, candidate_label: str) -> list[dict[str, Any]]:
    rows = [
        row
        for row in read_jsonl(ans_eval_dir / "ans_node_results.jsonl")
        if str(row.get("paper_spec") or "") == paper_spec and str(row.get("status") or "") == "ok"
    ]
    if not candidate_label:
        return rows
    exact = [row for row in rows if str(row.get("candidate_label") or "") == candidate_label]
    if exact:
        return exact
    # Older candidate ANS runs predate candidate_label preservation.  In that
    # case the ans_eval_dir is already candidate-scoped, so unlabeled rows are
    # the correct fallback.  Do not mix in rows from a different label.
    unlabeled = [row for row in rows if not str(row.get("candidate_label") or "")]
    return unlabeled if unlabeled else rows


def node_risk_class(unit_type: str, supported: int, unsupported: int) -> str:
    if unsupported <= 0:
        return "preserve"
    if supported == 0:
        if unit_type == "root_common_bridge":
            return "zero_supported_structural_bridge"
        return "zero_supported_drop_or_rewrite_candidate"
    if unit_type in SOURCE_TYPES:
        return "source_leaf_supported_recall_risk"
    if unit_type == "semantic_root":
        return "root_rewrite_preserve_supported"
    if unit_type in ROOT_OR_REASONING_TYPES:
        return "reasoning_rewrite_preserve_supported"
    return "manual_review"


def recommended_edit(node: dict[str, Any]) -> str:
    risk = str(node.get("risk_class") or "")
    supported = as_int(node.get("supported_fact_count"))
    unsupported = as_int(node.get("unsupported_fact_count"))
    unit_type = str(node.get("unit_type") or "")
    if unsupported <= 0:
        return "preserve"
    if supported == 0 and unit_type == "root_common_bridge":
        return "replace_with_short_supported_or_nonclaim_bridge"
    if supported == 0:
        return "drop_or_rewrite_zero_supported_node"
    if risk == "source_leaf_supported_recall_risk":
        return "do_not_drop_leaf; exact-span rewrite only if supported facts are preserved"
    if risk == "root_rewrite_preserve_supported":
        return "rewrite_root_by_deleting_only_unsupported_clauses"
    if risk == "reasoning_rewrite_preserve_supported":
        return "rewrite_reasoning_node_by_preserving_supported_atomic_facts"
    return "manual_supported-recall_preserving_edit"


def build_node_delta(row: dict[str, Any], totals: dict[str, int], guard_floor: float) -> dict[str, Any]:
    facts = [fact for fact in row.get("atomic_facts", []) if isinstance(fact, dict)]
    supported_facts = [fact for fact in facts if fact_supported(fact)]
    unsupported_facts = [fact for fact in facts if fact_unsupported(fact)]
    atomic = as_int(row.get("atomic_fact_count"), len(facts))
    supported = as_int(row.get("supported_fact_count"), len(supported_facts))
    unsupported = max(0, atomic - supported)
    if unsupported != len(unsupported_facts) and facts:
        unsupported = len(unsupported_facts)
        supported = len(supported_facts)
        atomic = len(facts)

    current_ans = ans_score(totals["supported"], totals["atomic"])
    ideal_atomic = totals["atomic"] - unsupported
    ideal_ans = ans_score(totals["supported"], ideal_atomic)
    delete_atomic = totals["atomic"] - atomic
    delete_supported = totals["supported"] - supported
    delete_ans = ans_score(delete_supported, delete_atomic) if delete_atomic > 0 else 0.0
    unit_type = str(row.get("unit_type") or "")
    risk_class = node_risk_class(unit_type, supported, unsupported)
    node = {
        "paper_spec": row.get("paper_spec", ""),
        "candidate_label": row.get("candidate_label", ""),
        "node_id": row.get("node_id", ""),
        "unit_type": unit_type,
        "source_tuple": row.get("source_tuple", ""),
        "atomic_fact_count": atomic,
        "supported_fact_count": supported,
        "unsupported_fact_count": unsupported,
        "supported_recall_at_risk": supported if unsupported > 0 else 0,
        "unsupported_ratio": unsupported / atomic if atomic else 0.0,
        "current_ans": current_ans,
        "ideal_prune_ans_if_supported_preserved": ideal_ans,
        "ideal_prune_delta": ideal_ans - current_ans,
        "delete_node_ans": delete_ans,
        "delete_node_delta": delete_ans - current_ans,
        "single_node_ideal_prune_passes_floor": ideal_ans >= guard_floor,
        "single_node_delete_passes_floor": delete_ans >= guard_floor and supported == 0,
        "risk_class": risk_class,
        "recommended_edit": "",
        "node_text": row.get("node_text", ""),
        "supported_facts": [fact.get("fact", "") for fact in supported_facts],
        "unsupported_facts": [fact.get("fact", "") for fact in unsupported_facts],
        "unsupported_reasons": [fact.get("reason", "") for fact in unsupported_facts],
    }
    node["recommended_edit"] = recommended_edit(node)
    # Prioritize nodes that can remove unsupported facts without risking many
    # supported facts.  Source leaves with supported facts are intentionally
    # penalized because they caused observed ANS regressions in this package.
    source_penalty = 2.0 if unit_type in SOURCE_TYPES and supported > 0 else 1.0
    node["planner_priority_score"] = (
        unsupported * (2.0 if supported == 0 else 1.0)
    ) / ((supported + 1.0) * source_penalty)
    return node


def choose_preserve_supported_set(nodes: list[dict[str, Any]], totals: dict[str, int], guard_floor: float) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    unsupported_removed = 0
    sorted_nodes = sorted(
        [node for node in nodes if as_int(node.get("unsupported_fact_count")) > 0],
        key=lambda node: (
            -as_float(node.get("planner_priority_score")),
            as_int(node.get("supported_recall_at_risk")),
            str(node.get("node_id") or ""),
        ),
    )
    for node in sorted_nodes:
        selected.append(node)
        unsupported_removed += as_int(node.get("unsupported_fact_count"))
        projected_atomic = totals["atomic"] - unsupported_removed
        projected_ans = ans_score(totals["supported"], projected_atomic)
        if projected_ans >= guard_floor:
            return {
                "pass_possible": True,
                "selected_node_ids": [str(item.get("node_id") or "") for item in selected],
                "unsupported_facts_to_prune": unsupported_removed,
                "supported_facts_to_preserve": sum(as_int(item.get("supported_fact_count")) for item in selected),
                "projected_ans_if_supported_preserved": projected_ans,
                "edit_count": len(selected),
            }
    projected_atomic = totals["atomic"] - unsupported_removed
    return {
        "pass_possible": ans_score(totals["supported"], projected_atomic) >= guard_floor,
        "selected_node_ids": [str(item.get("node_id") or "") for item in selected],
        "unsupported_facts_to_prune": unsupported_removed,
        "supported_facts_to_preserve": sum(as_int(item.get("supported_fact_count")) for item in selected),
        "projected_ans_if_supported_preserved": ans_score(totals["supported"], projected_atomic),
        "edit_count": len(selected),
    }


def choose_zero_supported_delete_set(nodes: list[dict[str, Any]], totals: dict[str, int], guard_floor: float) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    atomic_removed = 0
    for node in sorted(
        [
            node
            for node in nodes
            if as_int(node.get("unsupported_fact_count")) > 0 and as_int(node.get("supported_fact_count")) == 0
        ],
        key=lambda node: (-as_int(node.get("unsupported_fact_count")), str(node.get("node_id") or "")),
    ):
        selected.append(node)
        atomic_removed += as_int(node.get("atomic_fact_count"))
        projected_ans = ans_score(totals["supported"], totals["atomic"] - atomic_removed)
        if projected_ans >= guard_floor:
            return {
                "pass_possible": True,
                "selected_node_ids": [str(item.get("node_id") or "") for item in selected],
                "atomic_facts_removed": atomic_removed,
                "projected_ans_after_delete_or_nonclaim_rewrite": projected_ans,
                "edit_count": len(selected),
            }
    projected_ans = ans_score(totals["supported"], totals["atomic"] - atomic_removed)
    return {
        "pass_possible": projected_ans >= guard_floor,
        "selected_node_ids": [str(item.get("node_id") or "") for item in selected],
        "atomic_facts_removed": atomic_removed,
        "projected_ans_after_delete_or_nonclaim_rewrite": projected_ans,
        "edit_count": len(selected),
    }


def row_strategy(
    nodes: list[dict[str, Any]],
    totals: dict[str, int],
    guard_floor: float,
    preserve_plan: dict[str, Any],
    zero_plan: dict[str, Any],
) -> str:
    if zero_plan.get("pass_possible") and as_int(zero_plan.get("edit_count")) <= 2:
        return "zero_supported_bridge_or_node_rewrite_first"
    if preserve_plan.get("pass_possible"):
        selected_ids = set(preserve_plan.get("selected_node_ids") or [])
        selected = [node for node in nodes if str(node.get("node_id") or "") in selected_ids]
        if all(as_int(node.get("supported_fact_count")) == 0 for node in selected):
            return "zero_supported_rewrite_set"
        if any(str(node.get("unit_type") or "") in SOURCE_TYPES for node in selected):
            return "supported_recall_guarded_exact_span_rewrite"
        return "supported_preserving_reasoning_or_root_rewrite"
    if guard_floor > 0 and totals["atomic"] > 0:
        return "needs_new_supported_evidence_or_claim_split"
    return "manual_review"


def build_row_plan(controller_row: dict[str, Any]) -> dict[str, Any]:
    paper_spec = str(controller_row.get("paper_spec") or "")
    candidate_label = str(controller_row.get("candidate_label") or "")
    ans_eval_dir = resolve(str(controller_row.get("ans_eval_dir") or ""))
    ans_rows = candidate_ans_rows(ans_eval_dir, paper_spec, candidate_label)
    totals = {
        "atomic": sum(as_int(row.get("atomic_fact_count")) for row in ans_rows),
        "supported": sum(as_int(row.get("supported_fact_count")) for row in ans_rows),
    }
    totals["unsupported"] = max(0, totals["atomic"] - totals["supported"])
    guard_floor = as_float(controller_row.get("guard_current_best_main_factual_ans"))
    current_ans = ans_score(totals["supported"], totals["atomic"])
    node_deltas = [build_node_delta(row, totals, guard_floor) for row in ans_rows]
    node_deltas.sort(
        key=lambda node: (
            -as_float(node.get("planner_priority_score")),
            -as_float(node.get("ideal_prune_delta")),
            str(node.get("node_id") or ""),
        )
    )
    preserve_plan = choose_preserve_supported_set(node_deltas, totals, guard_floor)
    zero_plan = choose_zero_supported_delete_set(node_deltas, totals, guard_floor)
    return {
        "paper_spec": paper_spec,
        "candidate_label": candidate_label,
        "controller_route": controller_row.get("controller_route", ""),
        "ans_eval_dir": rel(ans_eval_dir),
        "graph_spec": controller_row.get("graph_spec", ""),
        "fresh_CG": controller_row.get("fresh_CG", ""),
        "fresh_REA": controller_row.get("fresh_REA", ""),
        "guard_floor_ans": guard_floor,
        "controller_candidate_ans": as_float(controller_row.get("candidate_main_factual_ans")),
        "computed_candidate_ans": current_ans,
        "computed_minus_controller_ans": current_ans - as_float(controller_row.get("candidate_main_factual_ans")),
        "guard_margin_computed": current_ans - guard_floor,
        "totals": totals,
        "required_supported_facts_at_current_denominator": ceil_required_supported(
            totals["supported"], totals["atomic"], guard_floor
        ),
        "preserve_supported_prune_plan": preserve_plan,
        "zero_supported_delete_or_nonclaim_plan": zero_plan,
        "strategy": row_strategy(node_deltas, totals, guard_floor, preserve_plan, zero_plan),
        "risk_counts": dict(Counter(str(node.get("risk_class") or "") for node in node_deltas)),
        "node_deltas": node_deltas,
    }


def render_markdown(summary: dict[str, Any], plans: list[dict[str, Any]]) -> str:
    lines = [
        "# After327 ANS Delta Planner",
        "",
        f"Created: `{summary['created_at']}`",
        "",
        "Provider calls: `False`",
        "Canonical accounting write: `False`",
        "",
        "## Design Basis",
        "",
        "- FActScore and SAFE motivate atomic fact accounting.",
        "- RAGChecker/RAGAS-style diagnostics motivate separating unsupported-claim pruning from supported-recall loss.",
        "- Cited-but-not-verified failures motivate exact support checks rather than trusting source-like labels.",
        "",
        "## Row Plans",
        "",
        "| Paper | Candidate | ANS | Floor | Gap | Strategy | Preserve-supported plan | Zero-supported plan |",
        "|---|---|---:|---:|---:|---|---|---|",
    ]
    for plan in plans:
        preserve = plan["preserve_supported_prune_plan"]
        zero = plan["zero_supported_delete_or_nonclaim_plan"]
        lines.append(
            "| `{paper_spec}` | `{candidate_label}` | `{ans:.6f}` | `{floor:.6f}` | `{gap:.6f}` | `{strategy}` | "
            "`{preserve_nodes}` -> {preserve_ans:.6f} | `{zero_nodes}` -> {zero_ans:.6f} |".format(
                paper_spec=plan["paper_spec"],
                candidate_label=plan["candidate_label"],
                ans=plan["computed_candidate_ans"],
                floor=plan["guard_floor_ans"],
                gap=plan["guard_margin_computed"],
                strategy=plan["strategy"],
                preserve_nodes=",".join(preserve.get("selected_node_ids") or []),
                preserve_ans=as_float(preserve.get("projected_ans_if_supported_preserved")),
                zero_nodes=",".join(zero.get("selected_node_ids") or []),
                zero_ans=as_float(zero.get("projected_ans_after_delete_or_nonclaim_rewrite")),
            )
        )
    for plan in plans:
        lines.extend(["", f"## {plan['paper_spec']}", ""])
        lines.append(f"- Candidate: `{plan['candidate_label']}`")
        lines.append(f"- Strategy: `{plan['strategy']}`")
        lines.append(f"- Required supported facts at current denominator: `{plan['required_supported_facts_at_current_denominator']}`")
        lines.append("")
        lines.append("| Node | Type | S/U/A | Risk | Ideal Prune ANS | Delete ANS | Edit | Unsupported Facts |")
        lines.append("|---|---|---:|---|---:|---:|---|---|")
        for node in plan["node_deltas"][:8]:
            if as_int(node.get("unsupported_fact_count")) <= 0:
                continue
            facts = "; ".join(str(item) for item in (node.get("unsupported_facts") or [])[:3])
            lines.append(
                "| `{node_id}` | `{unit_type}` | `{supported_fact_count}/{unsupported_fact_count}/{atomic_fact_count}` | "
                "`{risk_class}` | `{ideal:.6f}` | `{delete:.6f}` | `{edit}` | {facts} |".format(
                    node_id=node.get("node_id", ""),
                    unit_type=node.get("unit_type", ""),
                    supported_fact_count=node.get("supported_fact_count", ""),
                    unsupported_fact_count=node.get("unsupported_fact_count", ""),
                    atomic_fact_count=node.get("atomic_fact_count", ""),
                    risk_class=node.get("risk_class", ""),
                    ideal=as_float(node.get("ideal_prune_ans_if_supported_preserved")),
                    delete=as_float(node.get("delete_node_ans")),
                    edit=node.get("recommended_edit", ""),
                    facts=facts,
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
        for row in rows
        if isinstance(row, dict) and str(row.get("controller_status") or "") == "fresh_1_1_ans_failed"
    ]
    plans = [build_row_plan(row) for row in target_rows]
    plans.sort(
        key=lambda plan: (
            as_float(plan.get("guard_margin_computed")),
            as_int(plan.get("required_supported_facts_at_current_denominator")),
            str(plan.get("paper_spec") or ""),
        )
    )
    summary_rows = []
    node_rows = []
    for plan in plans:
        preserve = plan["preserve_supported_prune_plan"]
        zero = plan["zero_supported_delete_or_nonclaim_plan"]
        summary_rows.append(
            {
                "paper_spec": plan["paper_spec"],
                "candidate_label": plan["candidate_label"],
                "computed_candidate_ans": plan["computed_candidate_ans"],
                "guard_floor_ans": plan["guard_floor_ans"],
                "guard_margin_computed": plan["guard_margin_computed"],
                "required_supported_facts_at_current_denominator": plan[
                    "required_supported_facts_at_current_denominator"
                ],
                "strategy": plan["strategy"],
                "preserve_supported_selected_nodes": ",".join(preserve.get("selected_node_ids") or []),
                "preserve_supported_projected_ans": preserve.get("projected_ans_if_supported_preserved", ""),
                "zero_supported_selected_nodes": ",".join(zero.get("selected_node_ids") or []),
                "zero_supported_projected_ans": zero.get("projected_ans_after_delete_or_nonclaim_rewrite", ""),
                "total_atomic": plan["totals"]["atomic"],
                "total_supported": plan["totals"]["supported"],
                "total_unsupported": plan["totals"]["unsupported"],
            }
        )
        for node in plan["node_deltas"]:
            node_rows.append(
                {
                    "paper_spec": plan["paper_spec"],
                    "candidate_label": plan["candidate_label"],
                    "node_id": node.get("node_id", ""),
                    "unit_type": node.get("unit_type", ""),
                    "atomic_fact_count": node.get("atomic_fact_count", ""),
                    "supported_fact_count": node.get("supported_fact_count", ""),
                    "unsupported_fact_count": node.get("unsupported_fact_count", ""),
                    "supported_recall_at_risk": node.get("supported_recall_at_risk", ""),
                    "risk_class": node.get("risk_class", ""),
                    "planner_priority_score": node.get("planner_priority_score", ""),
                    "ideal_prune_ans_if_supported_preserved": node.get("ideal_prune_ans_if_supported_preserved", ""),
                    "delete_node_ans": node.get("delete_node_ans", ""),
                    "recommended_edit": node.get("recommended_edit", ""),
                    "unsupported_facts": "; ".join(str(item) for item in (node.get("unsupported_facts") or [])[:5]),
                }
            )
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "after327_ans_delta_planner",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_controller_queue": rel(controller_path),
        "out_root": rel(out_root),
        "row_count": len(plans),
        "strategy_counts": dict(Counter(plan["strategy"] for plan in plans)),
        "literature_design_map": LITERATURE_DESIGN_MAP,
    }
    report = {"summary": summary, "plans": plans}
    write_json(out_root / "AFTER327_ANS_DELTA_PLAN.json", report)
    write_json(out_root / "AFTER327_ANS_DELTA_SUMMARY.json", summary)
    write_csv(
        out_root / "AFTER327_ANS_DELTA_ROW_PLAN.csv",
        summary_rows,
        [
            "paper_spec",
            "candidate_label",
            "computed_candidate_ans",
            "guard_floor_ans",
            "guard_margin_computed",
            "required_supported_facts_at_current_denominator",
            "strategy",
            "preserve_supported_selected_nodes",
            "preserve_supported_projected_ans",
            "zero_supported_selected_nodes",
            "zero_supported_projected_ans",
            "total_atomic",
            "total_supported",
            "total_unsupported",
        ],
    )
    write_csv(
        out_root / "AFTER327_ANS_DELTA_NODE_PLAN.csv",
        node_rows,
        [
            "paper_spec",
            "candidate_label",
            "node_id",
            "unit_type",
            "atomic_fact_count",
            "supported_fact_count",
            "unsupported_fact_count",
            "supported_recall_at_risk",
            "risk_class",
            "planner_priority_score",
            "ideal_prune_ans_if_supported_preserved",
            "delete_node_ans",
            "recommended_edit",
            "unsupported_facts",
        ],
    )
    write_text(out_root / "AFTER327_ANS_DELTA_PLAN.md", render_markdown(summary, plans))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-queue", default=str(DEFAULT_CONTROLLER_QUEUE))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
