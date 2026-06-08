#!/usr/bin/env python3
"""Build a provider-free ANS/coverage audit for one residual closeout row.

The audit is intentionally diagnostic. It reads existing ANS node results,
graph JSON artifacts, and strict-merge candidate metadata, then writes a compact
gap ledger that can drive an ANS-safe hybrid/source-bridge patch.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"

DEFAULT_PAPER_SPEC = "gpt_5_2:s41467-025-56921-8"
DEFAULT_ANS_NODE_RESULTS = (
    PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_node_results.jsonl"
)
DEFAULT_STRICT_CANDIDATES = (
    RESIDUAL_ROOT
    / "strict_merge_candidates"
    / "20260606_notation_protocol_replay_v2_from_312base"
    / "STRICT_MERGE_CANDIDATES.json"
)
DEFAULT_REPLAY_EVAL = (
    RESIDUAL_ROOT
    / "strict_merge_candidates"
    / "20260606_notation_protocol_replay_v2_from_312base"
    / "per_row_replay_eval"
    / "gpt_5_2__s41467-025-56921-8"
    / "REPLAY_EVALUATION_RESULTS.json"
)
DEFAULT_V2_LEDGER = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_residual_closeout_v2"
    / "V2_RESIDUAL_LEDGER.csv"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_residual_closeout_v2"
    / "ans_safe_pilot_audits"
    / "gpt_5_2__s41467-025-56921-8"
)

EXCLUDED_UNIT_TYPES = {"root_common_bridge", "graph_node"}
STAGES = ["raw_step1_extraction", "llm_step2_self_fix_final_clean", "pearl_terminal_graph"]


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def safe_spec(value: str) -> str:
    return value.replace(":", "__").replace("/", "_")


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_ans_rows(path: Path, paper_spec: str) -> dict[str, dict[str, dict[str, Any]]]:
    by_stage: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("paper_spec") != paper_spec:
                continue
            if row.get("status") != "ok":
                continue
            if row.get("unit_type") in EXCLUDED_UNIT_TYPES:
                continue
            stage = str(row.get("stage") or "")
            if stage not in STAGES:
                continue
            node_id = str(row.get("node_id") or "")
            if not node_id:
                continue
            by_stage[stage][node_id] = row
    return by_stage


def summarize_nodes(nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    atomic = sum(int(row.get("atomic_fact_count") or 0) for row in nodes.values())
    supported = sum(int(row.get("supported_fact_count") or 0) for row in nodes.values())
    return {
        "node_count": len(nodes),
        "atomic_fact_count": atomic,
        "supported_fact_count": supported,
        "unsupported_fact_count": atomic - supported,
        "ans": supported / atomic if atomic else None,
    }


def unsupported_facts(row: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for fact in row.get("atomic_facts") or []:
        if not fact.get("supported"):
            out.append(
                {
                    "fact": fact.get("fact", ""),
                    "label": fact.get("label", ""),
                    "reason": fact.get("reason", ""),
                }
            )
    return out


def node_record(stage: str, node_id: str, row: dict[str, Any]) -> dict[str, Any]:
    atomic = int(row.get("atomic_fact_count") or 0)
    supported = int(row.get("supported_fact_count") or 0)
    unsupported = atomic - supported
    return {
        "stage": stage,
        "node_id": node_id,
        "unit_type": row.get("unit_type", ""),
        "source_tuple": row.get("source_tuple", ""),
        "node_text": row.get("node_text", ""),
        "atomic_fact_count": atomic,
        "supported_fact_count": supported,
        "unsupported_fact_count": unsupported,
        "node_ans": supported / atomic if atomic else None,
        "unsupported_facts": unsupported_facts(row),
    }


def load_candidate_row(path: Path, paper_spec: str) -> dict[str, Any]:
    payload = read_json(path, {})
    rows = payload.get("rows") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and row.get("paper_spec") == paper_spec:
            return row
    return {}


def load_ledger_row(path: Path, paper_spec: str) -> dict[str, str]:
    if not path.exists():
        return {}
    for row in read_csv(path):
        if row.get("paper_spec") == paper_spec:
            return row
    return {}


def graph_path_from_candidate(candidate: dict[str, Any], stage: str) -> Path | None:
    if stage == "pearl_terminal_graph":
        graph = candidate.get("candidate_graph")
        if graph:
            graph_path = resolve_path(str(graph))
            graph_json = graph_path.with_suffix(".json")
            if graph_json.exists():
                return graph_json
    return None


def default_stage_graph_json(paper_spec: str, stage: str, candidate: dict[str, Any]) -> Path | None:
    model, _, paper = paper_spec.partition(":")
    if stage == "pearl_terminal_graph":
        return graph_path_from_candidate(candidate, stage)
    if stage == "llm_step2_self_fix_final_clean":
        base = (
            PACKAGE_ROOT
            / "08_minicheck"
            / "baselines"
            / "materialized_graphs"
            / stage
            / model
            / paper
        )
        matches = sorted(base.glob("*/llm_step2_self_fix_final_clean_graph.json"))
        return matches[0] if matches else None
    if stage == "raw_step1_extraction":
        base = (
            PACKAGE_ROOT
            / "08_minicheck"
            / "baselines"
            / "materialized_graphs"
            / stage
            / model
            / paper
        )
        matches = sorted(base.glob("*/raw_step1_extraction_graph.json"))
        return matches[0] if matches else None
    return None


def load_graph_nodes(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = read_json(path, {})
    nodes = payload.get("nodes") if isinstance(payload, dict) else []
    out = {}
    for node in nodes or []:
        if isinstance(node, dict) and node.get("id"):
            out[str(node["id"])] = node
    return out


def load_graph_edges(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    payload = read_json(path, {})
    edges = payload.get("edges") if isinstance(payload, dict) else []
    return [edge for edge in edges or [] if isinstance(edge, dict)]


def compare_stage_nodes(
    *,
    stage_a: str,
    stage_b: str,
    rows_by_stage: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    a = rows_by_stage.get(stage_a, {})
    b = rows_by_stage.get(stage_b, {})
    a_ids = set(a)
    b_ids = set(b)
    only_a = sorted(a_ids - b_ids)
    only_b = sorted(b_ids - a_ids)
    shared = sorted(a_ids & b_ids)

    changed = []
    for node_id in shared:
        row_a = a[node_id]
        row_b = b[node_id]
        text_a = str(row_a.get("node_text") or "")
        text_b = str(row_b.get("node_text") or "")
        if text_a == text_b:
            continue
        changed.append(
            {
                "node_id": node_id,
                f"{stage_a}_text": text_a,
                f"{stage_b}_text": text_b,
                f"{stage_a}_support": f"{row_a.get('supported_fact_count')}/{row_a.get('atomic_fact_count')}",
                f"{stage_b}_support": f"{row_b.get('supported_fact_count')}/{row_b.get('atomic_fact_count')}",
            }
        )

    def records(stage: str, ids: list[str]) -> list[dict[str, Any]]:
        return [node_record(stage, node_id, rows_by_stage[stage][node_id]) for node_id in ids]

    return {
        "stage_a": stage_a,
        "stage_b": stage_b,
        "only_in_stage_a": records(stage_a, only_a),
        "only_in_stage_b": records(stage_b, only_b),
        "shared_text_changed": changed,
        "only_in_stage_a_count": len(only_a),
        "only_in_stage_b_count": len(only_b),
        "shared_text_changed_count": len(changed),
    }


def top_unsupported(stage: str, nodes: dict[str, dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    records = [node_record(stage, node_id, row) for node_id, row in sorted(nodes.items())]
    records = [row for row in records if row["unsupported_fact_count"] > 0]
    records.sort(key=lambda row: (-int(row["unsupported_fact_count"]), str(row["node_id"])))
    return records[:limit]


def top_supported(stage: str, nodes: dict[str, dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    records = [node_record(stage, node_id, row) for node_id, row in sorted(nodes.items())]
    records.sort(key=lambda row: (-int(row["supported_fact_count"]), int(row["unsupported_fact_count"]), str(row["node_id"])))
    return records[:limit]


def minimal_ans_moves(candidate_summary: dict[str, Any], guard_floor: float | None) -> dict[str, Any]:
    supported = int(candidate_summary.get("supported_fact_count") or 0)
    total = int(candidate_summary.get("atomic_fact_count") or 0)
    if guard_floor is None or total <= 0:
        return {}
    needed_supported_same_total = max(0, math.ceil((guard_floor * total) - supported - 1e-12))
    one_supported_leaf_ans = (supported + 1) / (total + 1)
    two_supported_leaf_ans = (supported + 2) / (total + 2)
    return {
        "guard_floor": guard_floor,
        "candidate_supported": supported,
        "candidate_atomic_facts": total,
        "candidate_ans": supported / total,
        "supported_fact_delta_needed_if_total_unchanged": needed_supported_same_total,
        "one_new_supported_fact_no_unsupported_ans": one_supported_leaf_ans,
        "one_new_supported_fact_passes": one_supported_leaf_ans + 1e-12 >= guard_floor,
        "two_new_supported_facts_no_unsupported_ans": two_supported_leaf_ans,
        "two_new_supported_facts_pass": two_supported_leaf_ans + 1e-12 >= guard_floor,
    }


def build_action_queue(
    *,
    step2_only: list[dict[str, Any]],
    terminal_top_unsupported: list[dict[str, Any]],
    min_moves: dict[str, Any],
    ledger_row: dict[str, str],
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for row in terminal_top_unsupported:
        text = str(row.get("node_text") or "")
        unsupported = "; ".join(str(item.get("fact") or "") for item in row.get("unsupported_facts", [])[:2])
        priority = 10
        if "should" in text or "propose" in text or "hypothesize" in text:
            priority -= 2
        if "gamma" in text.lower() or "γ" in text:
            priority -= 1
        queue.append(
            {
                "priority": priority,
                "lane": "terminal_minimal_text_narrowing",
                "target_node": row.get("node_id"),
                "unit_type": row.get("unit_type"),
                "support": f"{row.get('supported_fact_count')}/{row.get('atomic_fact_count')}",
                "why": "Reduce or ground one unsupported atomic fact while preserving replayed CG/REA.",
                "unsupported_fact_sample": unsupported,
                "node_text": text,
            }
        )
    for row in step2_only:
        if int(row.get("supported_fact_count") or 0) <= 0:
            continue
        queue.append(
            {
                "priority": 20 + int(row.get("unsupported_fact_count") or 0),
                "lane": "step2_supported_source_leaf_or_bridge",
                "target_node": row.get("node_id"),
                "unit_type": row.get("unit_type"),
                "support": f"{row.get('supported_fact_count')}/{row.get('atomic_fact_count')}",
                "why": "Restore a high-support step2 factual unit as a compact source-backed leaf/bridge if it does not harm REA.",
                "unsupported_fact_sample": "; ".join(
                    str(item.get("fact") or "") for item in row.get("unsupported_facts", [])[:2]
                ),
                "node_text": row.get("node_text"),
            }
        )
    missing = str(ledger_row.get("missing_entities") or "")
    if missing:
        queue.append(
            {
                "priority": 5,
                "lane": "notation_entity_contract_preservation",
                "target_node": "",
                "unit_type": "coverage_protocol",
                "support": "",
                "why": "Keep the notation-normalized coverage contract for the row while editing ANS surface text.",
                "unsupported_fact_sample": "",
                "node_text": f"Must preserve coverage for: {missing}",
            }
        )
    if min_moves.get("supported_fact_delta_needed_if_total_unchanged") == 1:
        queue.append(
            {
                "priority": 1,
                "lane": "one_fact_ans_guard_closure",
                "target_node": "",
                "unit_type": "selection_rule",
                "support": "",
                "why": "The current terminal candidate passes row ANS if one unsupported atomic fact becomes supported with total facts unchanged.",
                "unsupported_fact_sample": "",
                "node_text": "",
            }
        )
    queue.sort(key=lambda row: (int(row["priority"]), str(row.get("target_node") or "")))
    return queue


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    min_moves = report["minimal_ans_moves"]
    lines = [
        "# ANS-Safe Pilot Audit",
        "",
        f"Created: `{report['created_at']}`",
        f"Paper spec: `{summary['paper_spec']}`",
        "",
        "## Current Gate",
        "",
        f"- Candidate CG/REA: `{summary.get('candidate_CG')}` / `{summary.get('candidate_REA')}`",
        f"- Row ANS floor: `{summary.get('row_ans_floor')}`",
        f"- Candidate terminal ANS: `{summary.get('terminal_ans')}`",
        f"- ANS guard passed: `{summary.get('ans_guard_passed')}`",
        f"- Supported fact delta needed if total unchanged: `{min_moves.get('supported_fact_delta_needed_if_total_unchanged')}`",
        f"- One new 1/1 fact would pass: `{min_moves.get('one_new_supported_fact_passes')}`",
        "",
        "## Stage Summary",
        "",
        "| Stage | Nodes | Supported | Atomic | Unsupported | ANS |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["stage_summary_rows"]:
        ans = row["ans"]
        ans_text = f"{ans:.12f}" if isinstance(ans, float) else ""
        lines.append(
            f"| `{row['stage']}` | `{row['node_count']}` | `{row['supported_fact_count']}` | "
            f"`{row['atomic_fact_count']}` | `{row['unsupported_fact_count']}` | `{ans_text}` |"
        )
    lines.extend(
        [
            "",
            "## First Action Queue",
            "",
            "| Priority | Lane | Target | Support | Why |",
            "|---:|---|---|---|---|",
        ]
    )
    for row in report["action_queue"][:12]:
        lines.append(
            f"| `{row['priority']}` | `{row['lane']}` | `{row.get('target_node', '')}` | "
            f"`{row.get('support', '')}` | {row['why']} |"
        )
    lines.append("")
    return "\n".join(lines)


def build(args: argparse.Namespace) -> dict[str, Any]:
    paper_spec = args.paper_spec
    ans_node_results = resolve_path(args.ans_node_results)
    strict_candidates_path = resolve_path(args.strict_candidates)
    replay_eval_path = resolve_path(args.replay_eval)
    ledger_path = resolve_path(args.v2_ledger)
    out_root = resolve_path(args.out_root)

    rows_by_stage = load_ans_rows(ans_node_results, paper_spec)
    candidate = load_candidate_row(strict_candidates_path, paper_spec)
    replay_eval = read_json(replay_eval_path, {})
    ledger_row = load_ledger_row(ledger_path, paper_spec)

    graph_paths = {
        stage: default_stage_graph_json(paper_spec, stage, candidate)
        for stage in STAGES
    }
    graph_node_counts = {stage: len(load_graph_nodes(path)) for stage, path in graph_paths.items()}
    graph_edge_counts = {stage: len(load_graph_edges(path)) for stage, path in graph_paths.items()}

    stage_summary_rows = []
    for stage in STAGES:
        row = {"stage": stage, **summarize_nodes(rows_by_stage.get(stage, {}))}
        row["graph_json"] = rel(graph_paths[stage]) if graph_paths[stage] else ""
        row["graph_node_count"] = graph_node_counts[stage]
        row["graph_edge_count"] = graph_edge_counts[stage]
        stage_summary_rows.append(row)

    stage_summary = {row["stage"]: row for row in stage_summary_rows}
    row_floor = candidate.get("current_best_main_factual_ans")
    if row_floor is None:
        row_floor = (replay_eval.get("ans_guard") or {}).get("guard_threshold")
    row_floor_float = float(row_floor) if row_floor not in {None, ""} else None
    terminal_summary = stage_summary.get("pearl_terminal_graph", {})
    min_moves = minimal_ans_moves(terminal_summary, row_floor_float)

    step2_terminal = compare_stage_nodes(
        stage_a="llm_step2_self_fix_final_clean",
        stage_b="pearl_terminal_graph",
        rows_by_stage=rows_by_stage,
    )
    step2_only = step2_terminal["only_in_stage_a"]
    step2_only.sort(
        key=lambda row: (
            -int(row.get("supported_fact_count") or 0),
            int(row.get("unsupported_fact_count") or 0),
            str(row.get("node_id") or ""),
        )
    )
    terminal_unsupported = top_unsupported(
        "pearl_terminal_graph",
        rows_by_stage.get("pearl_terminal_graph", {}),
        args.top_nodes,
    )
    action_queue = build_action_queue(
        step2_only=step2_only[: args.top_nodes],
        terminal_top_unsupported=terminal_unsupported,
        min_moves=min_moves,
        ledger_row=ledger_row,
    )

    summary = {
        "paper_spec": paper_spec,
        "candidate_CG": candidate.get("candidate_CG"),
        "candidate_REA": candidate.get("candidate_REA"),
        "candidate_graph": candidate.get("candidate_graph"),
        "candidate_fresh_eval_results": candidate.get("candidate_fresh_eval_results"),
        "row_ans_floor": row_floor_float,
        "terminal_ans": terminal_summary.get("ans"),
        "ans_guard_passed": (
            terminal_summary.get("ans") is not None
            and row_floor_float is not None
            and float(terminal_summary["ans"]) + 1e-12 >= row_floor_float
        ),
        "missing_entities": ledger_row.get("missing_entities", ""),
        "notation_sensitive_entities": ledger_row.get("notation_sensitive_entities", ""),
        "v2_module": ledger_row.get("v2_module", ""),
    }
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_ans_node_results": rel(ans_node_results),
        "source_strict_candidates": rel(strict_candidates_path),
        "source_replay_eval": rel(replay_eval_path),
        "source_v2_ledger": rel(ledger_path),
        "summary": summary,
        "stage_summary_rows": stage_summary_rows,
        "minimal_ans_moves": min_moves,
        "stage_diffs": {
            "step2_vs_terminal": step2_terminal,
            "raw_vs_terminal": compare_stage_nodes(
                stage_a="raw_step1_extraction",
                stage_b="pearl_terminal_graph",
                rows_by_stage=rows_by_stage,
            ),
        },
        "top_terminal_unsupported_nodes": terminal_unsupported,
        "top_step2_only_supported_nodes": step2_only[: args.top_nodes],
        "action_queue": action_queue,
    }

    out_root.mkdir(parents=True, exist_ok=True)
    write_json(out_root / "ANS_SAFE_PILOT_AUDIT.json", report)
    write_text(out_root / "ANS_SAFE_PILOT_AUDIT.md", markdown(report))
    write_csv(
        out_root / "STAGE_SUMMARY.csv",
        stage_summary_rows,
        [
            "stage",
            "node_count",
            "atomic_fact_count",
            "supported_fact_count",
            "unsupported_fact_count",
            "ans",
            "graph_json",
            "graph_node_count",
            "graph_edge_count",
        ],
    )
    write_csv(
        out_root / "ACTION_QUEUE.csv",
        action_queue,
        ["priority", "lane", "target_node", "unit_type", "support", "why", "unsupported_fact_sample", "node_text"],
    )
    print(json.dumps({"out_root": rel(out_root), "summary": summary, "minimal_ans_moves": min_moves}, ensure_ascii=False, indent=2))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-spec", default=DEFAULT_PAPER_SPEC)
    parser.add_argument("--ans-node-results", default=str(DEFAULT_ANS_NODE_RESULTS))
    parser.add_argument("--strict-candidates", default=str(DEFAULT_STRICT_CANDIDATES))
    parser.add_argument("--replay-eval", default=str(DEFAULT_REPLAY_EVAL))
    parser.add_argument("--v2-ledger", default=str(DEFAULT_V2_LEDGER))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--top-nodes", type=int, default=12)
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
