#!/usr/bin/env python3
"""Build an offline closeout work package for the 350-row residual set.

The package is intentionally planning-only. It does not call providers and does
not rewrite the current 350-row accounting tables. Its job is to make the
remaining 50 residual rows executable as the next candidate-closure iteration.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
    / "10_standard_flow_350_subset_package"
)
PARENT_PACKAGE = PACKAGE_ROOT.parent
OUT_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
MAIN_SCOPE_EXCLUDE_UNIT_TYPES = {"root_common_bridge", "graph_node"}


LANE_BY_FAILURE_TYPE = {
    "preflight:no_anchor_regenerate": "anchor_bootstrap_then_semantic_repair",
    "final_metric_gate_failed": "entity_coverage_targeted_repair",
    "metric_regression": "non_regression_selection_or_merge",
    "final_judge_failed": "bounded_final_judge_feedback_repair",
}

LANE_ORDER = {
    "anchor_bootstrap_then_semantic_repair": 1,
    "entity_coverage_targeted_repair": 2,
    "non_regression_selection_or_merge": 3,
    "bounded_final_judge_feedback_repair": 4,
}

LANE_DETAILS = {
    "anchor_bootstrap_then_semantic_repair": {
        "problem": "The current source graph has no majority-correct reasoning anchor, so ordinary PEARL repair has no safe local starting point.",
        "entry_point": "paper evidence/input_data.json plus a regenerated or cross-model bootstrapped source graph candidate",
        "allowed_actions": [
            "regenerate a bounded source-grounded candidate graph from the same evidence",
            "optionally borrow paper-level anchors from accepted runs of the same paper after exact source-evidence verification",
            "rerun the normal PEARL semantic repair loop after anchors exist",
        ],
        "forbidden_actions": [
            "claim closure from the old no-anchor source graph",
            "skip fresh EC/CG and REA evaluation",
            "use GPT-5.4 or GPT-5.5 generator rows in the 350-only reporting set",
        ],
    },
    "entity_coverage_targeted_repair": {
        "problem": "The terminal graph is reasoning-clean but misses the strict entity coverage/content grounding gate.",
        "entry_point": "existing terminal graph, final evaluation anchor entities, and source evidence",
        "allowed_actions": [
            "audit final coverage against the fixed entity anchor",
            "add or reroute only source-supported units for uncovered core entities",
            "preserve all current correct reasoning unless a fresh judge rejects it",
        ],
        "forbidden_actions": [
            "full regeneration before trying targeted coverage repair",
            "lower the final CG threshold",
            "accept coverage gains with REA below 1.0",
        ],
    },
    "non_regression_selection_or_merge": {
        "problem": "The terminal graph reached REA=1.0 but reduced CG relative to the source graph.",
        "entry_point": "source graph, terminal graph, final evaluation anchor, and non-regression selector",
        "allowed_actions": [
            "rollback to the source graph when source CG is already sufficient and repair is harmful",
            "merge only source-supported coverage units into the clean terminal graph",
            "accept only candidates that satisfy non-regression and the strict final gate",
        ],
        "forbidden_actions": [
            "report a graph whose final CG is lower than original CG",
            "optimize REA by pruning away required paper entities",
            "treat REA=1.0 alone as closure",
        ],
    },
    "bounded_final_judge_feedback_repair": {
        "problem": "The final judge rejected a repaired/new reasoning unit or the semantic root.",
        "entry_point": "failed vote payload and the graph candidate that triggered it",
        "allowed_actions": [
            "repair, reroute, or drop the failed target using the explicit judge feedback",
            "rerun fresh final voting after the bounded edit",
            "fall back to source/step2 candidate if the judge feedback reveals a bad root synthesis",
        ],
        "forbidden_actions": [
            "repeat the rejected target content unchanged",
            "accept without a fresh final judge pass",
            "hide the failed vote as a provider error",
        ],
    },
}


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_json_payload(text: str) -> Dict[str, Any]:
    if not text or "{" not in text:
        return {}
    start = text.find("{")
    payload = text[start:]
    for end in range(len(payload), 0, -1):
        try:
            parsed = json.loads(payload[:end])
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else {}
    return {}


def nested_get(payload: Dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def load_eval_results(eval_dir: Optional[Path]) -> Dict[str, Any]:
    if not eval_dir:
        return {}
    candidates = []
    if eval_dir.is_file() and eval_dir.name == "evaluation_results.json":
        candidates.append(eval_dir)
    else:
        candidates.append(eval_dir / "evaluation_results.json")
    for candidate in candidates:
        if candidate.exists():
            return read_json(candidate)
    return {}


def final_metrics_from_eval_results(results: Dict[str, Any]) -> Dict[str, Any]:
    if not results:
        return {}
    summary = results.get("evaluation_summary") if isinstance(results.get("evaluation_summary"), dict) else {}
    coverage = results.get("coverage") if isinstance(results.get("coverage"), dict) else {}
    accuracy = results.get("accuracy") if isinstance(results.get("accuracy"), dict) else {}
    return {
        "CG": summary.get("entity_coverage_score", coverage.get("coverage_rate")),
        "REA": summary.get("accuracy_score", accuracy.get("accuracy_score")),
        "covered_entities": summary.get("covered_entities", coverage.get("covered_entities")),
        "total_entities": summary.get("total_entities", coverage.get("total_entities")),
        "valid_reasoning_steps": summary.get("valid_reasoning_steps", accuracy.get("valid_steps")),
        "total_reasoning_steps": summary.get("total_reasoning_steps", accuracy.get("total_steps")),
    }


def resolve_package_path(path_text: str) -> Optional[Path]:
    if not path_text:
        return None
    path = Path(path_text)
    candidates = [path]
    if not path.is_absolute():
        candidates = [PACKAGE_ROOT / path, PARENT_PACKAGE / path, PROJECT_ROOT / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def stage_ans_stats(ans_by_spec: Dict[str, Dict[str, Dict[str, int]]], spec: str, stage: str) -> Dict[str, Any]:
    row = ans_by_spec.get(spec, {}).get(stage, {"nodes": 0, "facts": 0, "supported": 0})
    facts = int(row.get("facts", 0))
    supported = int(row.get("supported", 0))
    return {
        "nodes": int(row.get("nodes", 0)),
        "atomic_facts": facts,
        "supported_atomic_facts": supported,
        "ans": supported / facts if facts else None,
    }


def load_ans_by_spec(path: Path, *, main_factual_only: bool) -> Dict[str, Dict[str, Dict[str, int]]]:
    ans_by_spec: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"nodes": 0, "facts": 0, "supported": 0})
    )
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if main_factual_only and row.get("unit_type") in MAIN_SCOPE_EXCLUDE_UNIT_TYPES:
                continue
            spec = str(row.get("paper_spec") or "")
            stage = str(row.get("stage") or "")
            if not spec or not stage:
                continue
            bucket = ans_by_spec[spec][stage]
            bucket["nodes"] += 1
            bucket["facts"] += int(row.get("atomic_fact_count") or 0)
            bucket["supported"] += int(row.get("supported_fact_count") or 0)
    return ans_by_spec


def candidate_source_rank(row: Dict[str, str]) -> int:
    failure_type = row.get("failure_type", "")
    if failure_type == "final_metric_gate_failed":
        final_cg = to_float(row.get("final_CG")) or 0.0
        return 1000 + int(round((1.0 - final_cg) * 1000))
    if failure_type == "metric_regression":
        original = to_float(row.get("original_CG")) or 0.0
        final = to_float(row.get("final_CG")) or 0.0
        return 2000 + int(round((original - final) * 1000))
    if failure_type == "final_judge_failed":
        return 3000
    return 4000


def build_queue_rows() -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    residual_rows = read_csv(PACKAGE_ROOT / "01_final_accounting" / "TYPED_RESIDUAL_350.csv")
    teacher_map = {row["paper_spec"]: row for row in read_csv(PACKAGE_ROOT / "00_documentation" / "TEACHER_POOL_FILE_MAP_350.csv")}
    semantic_map = {
        row["paper_spec"]: row for row in read_csv(PACKAGE_ROOT / "00_documentation" / "SEMANTIC_REPAIR_FILE_MAP_350.csv")
    }
    terminal_map = {
        row["paper_spec"]: row
        for row in read_csv(PACKAGE_ROOT / "02_ec_rea_evaluation" / "TERMINAL_EVAL_FILE_MAP_350.csv")
    }
    ans_by_spec = load_ans_by_spec(
        PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_node_results.jsonl",
        main_factual_only=True,
    )
    paper_counts = Counter(row["paper"] for row in residual_rows)

    rows: List[Dict[str, Any]] = []
    for row in residual_rows:
        spec = row["paper_spec"]
        failure_type = row.get("failure_type", "")
        lane = LANE_BY_FAILURE_TYPE.get(failure_type, "unmapped_residual")
        teacher = teacher_map.get(spec, {})
        semantic = semantic_map.get(spec, {})
        terminal = terminal_map.get(spec, {})
        raw_ans = stage_ans_stats(ans_by_spec, spec, "raw_step1_extraction")
        step2_ans = stage_ans_stats(ans_by_spec, spec, "llm_step2_self_fix_final_clean")
        pearl_ans = stage_ans_stats(ans_by_spec, spec, "pearl_terminal_graph")

        preferred_stage = "generation_final_clean_graph"
        if lane == "entity_coverage_targeted_repair":
            preferred_stage = "existing_terminal_graph"
        elif lane == "non_regression_selection_or_merge":
            preferred_stage = "source_vs_terminal_non_regression_selection"
        elif lane == "bounded_final_judge_feedback_repair":
            preferred_stage = "failed_final_judge_candidate"
        elif lane == "anchor_bootstrap_then_semantic_repair":
            preferred_stage = "new_anchor_bootstrap_candidate"

        step1_path = resolve_package_path(teacher.get("step1_raw_output", ""))
        step2_path = resolve_package_path(teacher.get("step2_autonomous_repair_output", ""))
        generation_graph = resolve_package_path(teacher.get("generation_final_clean_graph", ""))
        source_eval = resolve_package_path(teacher.get("source_eval_dir_in_package", ""))
        terminal_eval = resolve_package_path(row.get("terminal_metric_eval_dir", "") or terminal.get("terminal_metric_eval_dir_in_package", ""))
        final_graph = resolve_package_path(row.get("final_graph", ""))
        terminal_eval_results = load_eval_results(terminal_eval)
        terminal_metrics = final_metrics_from_eval_results(terminal_eval_results)

        payload = parse_json_payload(row.get("error_summary", ""))
        final_payload = nested_get(payload, "final") if isinstance(nested_get(payload, "final"), dict) else {}
        original_coverage = nested_get(payload, "original") if isinstance(nested_get(payload, "original"), dict) else {}
        final_coverage = {**terminal_metrics, **{key: value for key, value in final_payload.items() if value not in ("", None)}}
        covered_entities = final_coverage.get("covered_entities")
        total_entities = final_coverage.get("total_entities")
        missing_entities = ""
        if isinstance(covered_entities, (int, float)) and isinstance(total_entities, (int, float)):
            missing_entities = int(total_entities - covered_entities)
        anchor_entities = terminal_eval_results.get("entities")
        if not isinstance(anchor_entities, list):
            anchor_entities = []

        final_cg = to_float(row.get("final_CG"))
        final_rea = to_float(row.get("final_REA"))
        original_cg = to_float(row.get("original_CG"))
        raw_ans_value = raw_ans["ans"]
        step2_ans_value = step2_ans["ans"]
        pearl_ans_value = pearl_ans["ans"]
        best_source_ans = max(value for value in [raw_ans_value, step2_ans_value] if value is not None)
        current_best_ans = max(value for value in [raw_ans_value, step2_ans_value, pearl_ans_value] if value is not None)

        if lane == "anchor_bootstrap_then_semantic_repair":
            quality_guard = (
                "bootstrap must produce at least one majority-correct reasoning anchor, then pass final CG=1.0 and REA=1.0; "
                f"main-factual ANS should not fall materially below the stronger raw/step2 source signal ({best_source_ans:.3f})"
            )
        elif lane == "entity_coverage_targeted_repair":
            quality_guard = (
                "preserve REA=1.0 and current accepted reasoning; only add source-grounded support for uncovered core entities; "
                f"main-factual ANS should remain near or above current PEARL terminal signal ({pearl_ans_value:.3f})"
            )
        elif lane == "non_regression_selection_or_merge":
            quality_guard = (
                f"candidate must satisfy final CG >= original CG ({original_cg:.6f}) before strict gate; "
                "avoid pruning source-supported entity evidence for a cleaner but narrower graph"
            )
        elif lane == "bounded_final_judge_feedback_repair":
            quality_guard = (
                "use the failed vote payload as the repair target; after edit, rerun fresh final judge and strict CG/REA gate; "
                f"protect the stronger raw/step2 source signal ({best_source_ans:.3f})"
            )
        else:
            quality_guard = "unmapped residual: manual audit required"

        if lane == "anchor_bootstrap_then_semantic_repair":
            next_action = "create source-grounded anchor bootstrap candidate, then rerun PEARL repair/accounting"
        elif lane == "entity_coverage_targeted_repair":
            next_action = "run coverage-diff audit, add bounded support for uncovered anchor entities, then fresh strict evaluation"
        elif lane == "non_regression_selection_or_merge":
            next_action = "compare source and terminal graphs, rollback or merge coverage units, then accept only non-regressing strict candidate"
        elif lane == "bounded_final_judge_feedback_repair":
            next_action = "seed repair with failed final vote payload, bound edits to rejected target/root, then fresh strict evaluation"
        else:
            next_action = "manual triage"

        rows.append(
            {
                "priority": 0,
                "lane": lane,
                "paper_spec": spec,
                "model": row.get("model", ""),
                "paper": row.get("paper", ""),
                "run_id": teacher.get("run_id", ""),
                "failure_type": failure_type,
                "repeated_residual_paper_count": paper_counts[row.get("paper", "")],
                "current_original_CG": row.get("original_CG", ""),
                "current_original_REA": row.get("original_REA", ""),
                "current_final_CG": row.get("final_CG", ""),
                "current_final_REA": row.get("final_REA", ""),
                "delta_CG": row.get("delta_CG", ""),
                "delta_REA": row.get("delta_REA", ""),
                "final_covered_entities": covered_entities if covered_entities is not None else "",
                "final_total_entities": total_entities if total_entities is not None else "",
                "final_missing_entities": missing_entities,
                "final_anchor_entities": "; ".join(str(entity) for entity in anchor_entities),
                "original_covered_entities": original_coverage.get("covered_entities", ""),
                "original_total_entities": original_coverage.get("total_entities", ""),
                "has_terminal_graph": bool(row.get("final_graph", "").strip()),
                "has_terminal_eval": bool(row.get("final_eval_dir", "").strip() or row.get("terminal_metric_eval_dir", "").strip()),
                "preferred_next_candidate_source": preferred_stage,
                "next_action": next_action,
                "quality_guard": quality_guard,
                "raw_main_factual_nodes": raw_ans["nodes"],
                "raw_main_factual_ans": raw_ans_value,
                "step2_main_factual_nodes": step2_ans["nodes"],
                "step2_main_factual_ans": step2_ans_value,
                "pearl_main_factual_nodes": pearl_ans["nodes"],
                "pearl_main_factual_ans": pearl_ans_value,
                "current_best_main_factual_ans": current_best_ans,
                "step1_raw_output": rel(step1_path) if step1_path else "",
                "step2_autonomous_repair_output": rel(step2_path) if step2_path else "",
                "generation_final_clean_graph": rel(generation_graph) if generation_graph else "",
                "source_eval_dir": rel(source_eval) if source_eval else "",
                "semantic_repair_curated_dir": semantic.get("semantic_repair_curated_dir_in_package", ""),
                "terminal_graph": rel(final_graph) if final_graph else "",
                "terminal_eval_dir": rel(terminal_eval) if terminal_eval else "",
                "error_summary_excerpt": row.get("error_summary", "")[:700],
                "_sort_key": (
                    LANE_ORDER.get(lane, 99),
                    candidate_source_rank(row),
                    -paper_counts[row.get("paper", "")],
                    row.get("paper_spec", ""),
                ),
            }
        )

    rows.sort(key=lambda item: item["_sort_key"])
    for idx, row in enumerate(rows, start=1):
        row["priority"] = idx
        row.pop("_sort_key", None)

    summary = summarize_rows(rows)
    return rows, summary


def summarize_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_lane = Counter(row["lane"] for row in rows)
    by_failure = Counter(row["failure_type"] for row in rows)
    by_model = Counter(row["model"] for row in rows)
    by_paper = Counter(row["paper"] for row in rows)
    repeated = {paper: count for paper, count in sorted(by_paper.items()) if count > 1}

    lane_summaries: Dict[str, Dict[str, Any]] = {}
    for lane in sorted(by_lane, key=lambda key: LANE_ORDER.get(key, 99)):
        lane_rows = [row for row in rows if row["lane"] == lane]
        final_cg = [to_float(row["current_final_CG"]) for row in lane_rows]
        final_rea = [to_float(row["current_final_REA"]) for row in lane_rows]
        pearl_ans = [to_float(row["pearl_main_factual_ans"]) for row in lane_rows if row.get("pearl_main_factual_ans") != ""]
        raw_ans = [to_float(row["raw_main_factual_ans"]) for row in lane_rows if row.get("raw_main_factual_ans") != ""]
        step2_ans = [to_float(row["step2_main_factual_ans"]) for row in lane_rows if row.get("step2_main_factual_ans") != ""]

        def avg(values: Iterable[Optional[float]]) -> Optional[float]:
            numeric = [value for value in values if value is not None]
            return sum(numeric) / len(numeric) if numeric else None

        lane_summaries[lane] = {
            "count": len(lane_rows),
            "failure_types": dict(Counter(row["failure_type"] for row in lane_rows)),
            "terminal_graph_rows": sum(1 for row in lane_rows if row["has_terminal_graph"]),
            "avg_final_CG": avg(final_cg),
            "avg_final_REA": avg(final_rea),
            "avg_raw_main_factual_ans": avg(raw_ans),
            "avg_step2_main_factual_ans": avg(step2_ans),
            "avg_pearl_main_factual_ans": avg(pearl_ans),
            "details": LANE_DETAILS.get(lane, {}),
        }

    return {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "350-row original-version subset current-version residual repair plan; excludes gpt_5_4 and gpt_5_5 generator rows",
        "original_result_boundary": {
            "rows": 350,
            "strict_success_rows": 300,
            "typed_residual_rows": 50,
            "note": "This package does not rewrite current accounting. It defines the next residual candidate-closure iteration.",
        },
        "acceptance_gate": {
            "final_CG": 1.0,
            "final_REA": 1.0,
            "quality_tier": ["A_main", "B_usable", "C_thin"],
            "fresh_evaluation_required": True,
            "provider_error_is_not_semantic_failure": True,
        },
        "semantic_quality_guard": {
            "primary": "ANS/FActScore-style support is a diagnostic guard for source grounding, not a replacement for CG/REA.",
            "policy": "Do not accept a candidate that reaches CG/REA by adding unsupported paper-level claims or by pruning away required content.",
            "current_reference": {
                "raw_main_factual_ans": 0.7835740202321166,
                "step2_main_factual_ans": 0.774975035818174,
                "pearl_main_factual_ans": 0.8033754732721555,
                "pearl_without_roots_or_bridges_ans": 0.8288930581613509,
            },
        },
        "counts": {
            "rows": len(rows),
            "by_lane": dict(by_lane),
            "by_failure_type": dict(by_failure),
            "by_model": dict(by_model),
            "repeated_residual_papers": repeated,
        },
        "lane_summaries": lane_summaries,
    }


def write_markdown(summary: Dict[str, Any]) -> None:
    lines = [
        "# Residual 50 Closeout Plan",
        "",
        f"Created: {summary['created_at']}",
        "",
        "## Bottom Line",
        "",
        "The remaining 50 rows are not one failure mode. They should be closed through a new candidate-closure iteration, not by sending every row blindly back into the same semantic repair loop.",
        "",
        "Current 350-row result remains unchanged: 300 strict-success rows and 50 typed residual rows. This closeout package only defines the next executable work queue.",
        "",
        "## Residual Split",
        "",
        "| Lane | Rows | Current state | Correct next entry |",
        "|---|---:|---|---|",
    ]
    for lane, payload in summary["lane_summaries"].items():
        detail = payload.get("details") or {}
        lines.append(
            f"| `{lane}` | {payload['count']} | {detail.get('problem', '')} | {detail.get('entry_point', '')} |"
        )
    lines.extend(
        [
            "",
            "## Why The 31 No-Anchor Rows Are Different",
            "",
            "`preflight:no_anchor_regenerate` means the source graph has no majority-correct reasoning unit that PEARL can safely retain as an anchor. These rows cannot be repaired by local rejected-unit repair alone. The correct route is:",
            "",
            "```text",
            "paper evidence/input_data.json",
            "  -> anchor-bootstrap source candidate",
            "  -> majority-correct anchor preflight",
            "  -> normal PEARL semantic repair loop",
            "  -> fresh EC/CG and REA evaluation",
            "  -> strict closure gate or typed residual",
            "```",
            "",
            "## Acceptance Gate",
            "",
            "- `final CG = 1.0`",
            "- `final REA = 1.0`",
            "- quality tier in `{A_main, B_usable, C_thin}`",
            "- required graph/evaluation artifacts exist",
            "- no GPT-5.4/GPT-5.5 generator rows are introduced into the 350-only report",
            "",
            "ANS/FActScore-style support is kept as a source-grounding guard. It should not replace CG/REA, but it prevents a repair from chasing 1/1 metrics by adding unsupported or over-compressed scientific claims.",
            "",
            "## Files",
            "",
            "- `RESIDUAL_50_CLOSEOUT_QUEUE.csv`: row-level execution queue.",
            "- `RESIDUAL_50_CLOSEOUT_QUEUE.json`: same queue with structured fields.",
            "- `RESIDUAL_50_CLOSEOUT_SUMMARY.json`: counts, lane policies, and current metric summaries.",
            "- `RESIDUAL_50_PAPER_LEVEL_GROUPS.csv`: repeated-paper view for paper-level anchor/bootstrap planning.",
            "",
            "## Recommended Execution Order",
            "",
            "1. Run `entity_coverage_targeted_repair` first. These 13 rows already have terminal graphs and REA=1.0; they usually need small coverage additions.",
            "2. Run `non_regression_selection_or_merge` next. These 4 rows need rollback/merge safeguards, not more unconstrained regeneration.",
            "3. Run `bounded_final_judge_feedback_repair` for the 2 explicit judge failures.",
            "4. Run `anchor_bootstrap_then_semantic_repair` last or in a separate batch. These 31 rows need candidate regeneration/bootstrap before PEARL can operate normally.",
            "",
        ]
    )
    (OUT_ROOT / "RESIDUAL_50_CLOSEOUT_PLAN.md").write_text("\n".join(lines), encoding="utf-8")


def write_paper_groups(rows: List[Dict[str, Any]]) -> None:
    by_paper: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_paper[row["paper"]].append(row)
    grouped: List[Dict[str, Any]] = []
    for paper, items in sorted(by_paper.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        grouped.append(
            {
                "paper": paper,
                "residual_rows": len(items),
                "models": ";".join(sorted({row["model"] for row in items})),
                "lanes": ";".join(sorted({row["lane"] for row in items}, key=lambda lane: LANE_ORDER.get(lane, 99))),
                "failure_types": ";".join(sorted({row["failure_type"] for row in items})),
                "paper_specs": ";".join(row["paper_spec"] for row in items),
                "recommended_group_action": (
                    "paper-level anchor/bootstrap audit before per-model repair"
                    if len(items) > 1
                    else "single-row lane action"
                ),
            }
        )
    write_csv(
        OUT_ROOT / "RESIDUAL_50_PAPER_LEVEL_GROUPS.csv",
        grouped,
        [
            "paper",
            "residual_rows",
            "models",
            "lanes",
            "failure_types",
            "paper_specs",
            "recommended_group_action",
        ],
    )


def write_manifest() -> None:
    files = []
    for path in sorted(OUT_ROOT.rglob("*")):
        if path.is_file():
            if path.name == "RESIDUAL_50_CLOSEOUT_MANIFEST.json":
                continue
            files.append({"file": str(path.relative_to(OUT_ROOT)), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_json(
        OUT_ROOT / "RESIDUAL_50_CLOSEOUT_MANIFEST.json",
        {
            "package": "09_residual_50_closeout",
            "scope": "planning-only closeout package for the 50 typed residuals in the 350 subset",
            "file_count": len(files),
            "files": files,
        },
    )


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    rows, summary = build_queue_rows()
    fieldnames = [
        "priority",
        "lane",
        "paper_spec",
        "model",
        "paper",
        "run_id",
        "failure_type",
        "repeated_residual_paper_count",
        "current_original_CG",
        "current_original_REA",
        "current_final_CG",
        "current_final_REA",
        "delta_CG",
        "delta_REA",
        "final_covered_entities",
        "final_total_entities",
        "final_missing_entities",
        "final_anchor_entities",
        "original_covered_entities",
        "original_total_entities",
        "has_terminal_graph",
        "has_terminal_eval",
        "preferred_next_candidate_source",
        "next_action",
        "quality_guard",
        "raw_main_factual_nodes",
        "raw_main_factual_ans",
        "step2_main_factual_nodes",
        "step2_main_factual_ans",
        "pearl_main_factual_nodes",
        "pearl_main_factual_ans",
        "current_best_main_factual_ans",
        "step1_raw_output",
        "step2_autonomous_repair_output",
        "generation_final_clean_graph",
        "source_eval_dir",
        "semantic_repair_curated_dir",
        "terminal_graph",
        "terminal_eval_dir",
        "error_summary_excerpt",
    ]
    write_csv(OUT_ROOT / "RESIDUAL_50_CLOSEOUT_QUEUE.csv", rows, fieldnames)
    write_json(OUT_ROOT / "RESIDUAL_50_CLOSEOUT_QUEUE.json", rows)
    write_json(OUT_ROOT / "RESIDUAL_50_CLOSEOUT_SUMMARY.json", summary)
    write_markdown(summary)
    write_paper_groups(rows)
    write_manifest()
    print(
        json.dumps(
            {
                "status": "ok",
                "out_root": rel(OUT_ROOT),
                "rows": len(rows),
                "by_lane": summary["counts"]["by_lane"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
