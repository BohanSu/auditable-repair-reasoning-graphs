#!/usr/bin/env python3
"""Residual Closure Engine for the PEARL 350-row closeout package.

This controller keeps the current 350-row accounting immutable. It converts
typed residuals into lane-specific next-candidate jobs and then, only in
``--execute`` mode, re-enters the existing PEARL semantic repair/evaluation
gate. Dry-run mode is the default and writes an auditable execution packet.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

PACKAGE_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
    / "10_standard_flow_350_subset_package"
)
DEFAULT_QUEUE = PACKAGE_ROOT / "09_residual_50_closeout" / "RESIDUAL_50_CLOSEOUT_QUEUE.csv"
DEFAULT_OUT_ROOT = PACKAGE_ROOT / "09_residual_50_closeout" / "runs"
DEFAULT_CANDIDATE_FRONTIER = DEFAULT_OUT_ROOT / "CANDIDATE_FRONTIER.csv"

EXECUTABLE_LOCAL_LANES = {
    "entity_coverage_targeted_repair",
    "non_regression_selection_or_merge",
    "bounded_final_judge_feedback_repair",
}
RAW_REGENERATION_LANE = "anchor_bootstrap_then_semantic_repair"


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def project_rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return payload


def load_env_file_light(path: Path) -> None:
    if not path.exists():
        return
    var_pattern = re.compile(r"\$(\w+)|\$\{([^}]+)\}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        def repl(match: re.Match[str]) -> str:
            var_name = match.group(1) or match.group(2) or ""
            return os.environ.get(var_name, "")

        os.environ[key] = var_pattern.sub(repl, value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def row_gate_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    final = row.get("final") if isinstance(row.get("final"), dict) else {}
    original = row.get("original") if isinstance(row.get("original"), dict) else {}
    final_cg = to_float(final.get("CG"))
    final_rea = to_float(final.get("REA"))
    passed_strict_gate = (
        final_cg is not None
        and final_rea is not None
        and final_cg + 1e-9 >= 1.0
        and final_rea + 1e-9 >= 1.0
    )
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    return {
        "paper_spec": row.get("paper_spec", ""),
        "lane": row.get("residual_closure_lane", ""),
        "strategy": row.get("residual_closure_strategy", ""),
        "passed_strict_gate": passed_strict_gate,
        "original_CG": original.get("CG", ""),
        "original_REA": original.get("REA", ""),
        "final_CG": final.get("CG", ""),
        "final_REA": final.get("REA", ""),
        "delta_CG": row.get("delta_CG", ""),
        "delta_REA": row.get("delta_REA", ""),
        "quality_tier": quality.get("tier", ""),
        "final_graph": row.get("final_graph", ""),
        "work_dir": row.get("work_dir", ""),
        "curation_out_dir": row.get("curation_out_dir", ""),
        "source_eval_dir": row.get("source_eval_dir", ""),
    }


def write_execution_indexes(run_root: Path, rows: List[Dict[str, Any]], failures: List[Dict[str, Any]]) -> Dict[str, Any]:
    summaries = [row_gate_summary(row) for row in rows]
    strict_candidates = [row for row in summaries if row["passed_strict_gate"]]
    fields = [
        "paper_spec",
        "lane",
        "strategy",
        "passed_strict_gate",
        "original_CG",
        "original_REA",
        "final_CG",
        "final_REA",
        "delta_CG",
        "delta_REA",
        "quality_tier",
        "final_graph",
        "work_dir",
        "curation_out_dir",
        "source_eval_dir",
    ]
    write_json(run_root / "local_repair_rows.json", rows)
    write_json(run_root / "local_repair_failures.json", failures)
    write_json(run_root / "closure_candidates_strict_gate.json", strict_candidates)
    write_csv(run_root / "local_repair_rows_summary.csv", summaries, fields)
    write_csv(run_root / "closure_candidates_strict_gate.csv", strict_candidates, fields)
    return {
        "local_repair_rows": str(run_root / "local_repair_rows.json"),
        "local_repair_failures": str(run_root / "local_repair_failures.json"),
        "local_repair_rows_summary": str(run_root / "local_repair_rows_summary.csv"),
        "closure_candidates_strict_gate": str(run_root / "closure_candidates_strict_gate.csv"),
        "strict_gate_passed": len(strict_candidates),
    }


def read_queue(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_specs(values: Iterable[str], specs_file: str = "") -> List[str]:
    specs = [str(value).strip() for value in values if str(value).strip()]
    if specs_file:
        path = resolve_path(specs_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                specs.append(line)
    seen: set[str] = set()
    out: List[str] = []
    for spec in specs:
        if spec not in seen:
            seen.add(spec)
            out.append(spec)
    return out


def load_candidate_graph_overrides(path_text: str) -> Dict[str, Path]:
    if not path_text:
        return {}
    path = resolve_path(path_text)
    data = read_json(path, {})
    if isinstance(data, dict) and isinstance(data.get("overrides"), list):
        items = data["overrides"]
    elif isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [{"paper_spec": key, "candidate_graph": value} for key, value in data.items()]
    else:
        raise ValueError(f"unsupported candidate override file shape: {path}")

    overrides: Dict[str, Path] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        paper_spec = str(item.get("paper_spec") or "").strip()
        candidate = str(item.get("candidate_graph") or item.get("initial_graph_dot") or "").strip()
        if not paper_spec or not candidate:
            continue
        candidate_path = resolve_path(candidate)
        if not candidate_path.exists():
            raise FileNotFoundError(f"candidate graph override does not exist for {paper_spec}: {candidate_path}")
        overrides[paper_spec] = candidate_path
    return overrides


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def load_candidate_frontier(path_text: str) -> Dict[str, Dict[str, Any]]:
    if not path_text:
        return {}
    path = resolve_path(path_text)
    frontier: Dict[str, Dict[str, Any]] = {}
    for row in read_csv_rows(path):
        paper_spec = str(row.get("paper_spec") or "").strip()
        graph_text = str(row.get("candidate_graph") or "").strip()
        if not paper_spec or not graph_text:
            continue
        graph_path = resolve_path(graph_text)
        if not graph_path.exists():
            continue
        cg = to_float(row.get("CG")) or 0.0
        rea = to_float(row.get("REA")) or 0.0
        covered = int_or_none(row.get("covered_entities")) or 0
        total = int_or_none(row.get("total_entities")) or 0
        bad_votes = int_or_none(row.get("noncorrect_vote_count")) or 0
        candidate = {
            "source": str(path),
            "run": row.get("run", ""),
            "paper_spec": paper_spec,
            "eval_name": row.get("eval_name", ""),
            "CG": cg,
            "REA": rea,
            "covered_entities": covered,
            "total_entities": total,
            "valid_reasoning_steps": int_or_none(row.get("valid_reasoning_steps")),
            "total_reasoning_steps": int_or_none(row.get("total_reasoning_steps")),
            "noncorrect_targets": row.get("noncorrect_targets", ""),
            "noncorrect_vote_count": bad_votes,
            "candidate_graph": str(graph_path),
            "eval_dir": str(resolve_path(str(row.get("eval_dir") or ""))) if row.get("eval_dir") else "",
        }
        previous = frontier.get(paper_spec)
        score = (
            int(cg >= 1.0 - 1e-9),
            covered,
            rea,
            -bad_votes,
            int(total),
        )
        previous_score = previous.get("_score") if isinstance(previous, dict) else None
        if previous is None or score > previous_score:
            candidate["_score"] = score
            frontier[paper_spec] = candidate
    for candidate in frontier.values():
        candidate.pop("_score", None)
    return frontier


def select_rows(
    rows: List[Dict[str, str]],
    *,
    lanes: Iterable[str],
    paper_specs: Iterable[str],
    limit: int,
) -> List[Dict[str, str]]:
    lane_set = set(lanes)
    spec_set = set(paper_specs)
    selected = [
        row
        for row in rows
        if (not lane_set or row.get("lane") in lane_set)
        and (not spec_set or row.get("paper_spec") in spec_set)
    ]
    return selected[:limit] if limit > 0 else selected


def latest_clean_eval_dir(eval_root_or_dir: Path) -> Optional[Path]:
    candidates: List[Path] = []
    if (eval_root_or_dir / "evaluation_results.json").exists():
        candidates.append(eval_root_or_dir)
    if eval_root_or_dir.name == "evaluation_outputs":
        candidates.extend(
            sorted(
                [path for path in eval_root_or_dir.iterdir() if path.is_dir() and (path / "evaluation_results.json").exists()],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )
    else:
        root = eval_root_or_dir / "evaluation_outputs"
        if root.exists():
            candidates.extend(
                sorted(
                    [path for path in root.iterdir() if path.is_dir() and (path / "evaluation_results.json").exists()],
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
            )
    for candidate in candidates:
        if is_eval_dir_clean_light(candidate):
            return candidate
    return candidates[0] if candidates else None


def is_eval_dir_clean_light(eval_dir: Path) -> bool:
    """Dependency-light clean-eval check for planning.

    The execution runner still performs its own strict checks. This copy avoids
    importing ``evaluator.py`` during dry-run because that module requires pydot.
    """
    if not (eval_dir / "evaluation_results.json").exists():
        return False
    responses_dir = eval_dir / "responses"
    if not responses_dir.exists():
        return True
    for response_file in responses_dir.glob("reasoning_validation_*_response_*.json"):
        data = read_json(response_file, {})
        text = json.dumps(data, ensure_ascii=False).lower() if isinstance(data, dict) else ""
        if str(data.get("result", "")).lower() == "error":
            return False
        if data.get("success") is False:
            return False
        if "json parsing failed" in text or "insufficient_user_quota" in text or "api call failed" in text:
            return False
    for vote_file in responses_dir.glob("reasoning_validation_*_vote_result.json"):
        data = read_json(vote_file, {})
        if str(data.get("final_result", "")).lower() == "error":
            return False
        model_results = data.get("model_results", {}) if isinstance(data, dict) else {}
        if any(str(result).lower() == "error" for result in model_results.values()):
            return False
    return True


def run_dir_from_generation_graph(graph_path: Path) -> Path:
    return graph_path.parent


def lane_seed_feedback(
    row: Dict[str, str],
    seed_dir: Path,
    *,
    audited_frontier: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    lane = row.get("lane", "")
    if lane == RAW_REGENERATION_LANE:
        return None

    paper_spec = str(row["paper_spec"])
    model = str(row.get("model") or paper_spec.split(":", 1)[0])
    paper = str(row.get("paper") or paper_spec.split(":", 1)[-1])
    out = seed_dir / model / paper / "repair_feedback.json"

    final_metrics = {
        "CG": to_float(row.get("current_final_CG")),
        "REA": to_float(row.get("current_final_REA")),
        "covered_entities": int(float(row["final_covered_entities"])) if row.get("final_covered_entities") else None,
        "total_entities": int(float(row["final_total_entities"])) if row.get("final_total_entities") else None,
        "missing_entities": int(float(row["final_missing_entities"])) if row.get("final_missing_entities") else None,
        "terminal_eval_dir": row.get("terminal_eval_dir", ""),
        "terminal_graph": row.get("terminal_graph", ""),
    }
    anchor_entities = [item.strip() for item in str(row.get("final_anchor_entities") or "").split(";") if item.strip()]
    frontier_threshold = int(audited_frontier.get("covered_entities") or 0) if audited_frontier else 0
    frontier_total = int(audited_frontier.get("total_entities") or 0) if audited_frontier else 0

    if lane == "entity_coverage_targeted_repair":
        payload = {
            "status": "metric_gate_failed",
            "lane": lane,
            "paper_spec": paper_spec,
            "metric_feedback": "Final graph passed reasoning cleanliness but missed strict entity coverage/content grounding.",
            "final_metrics": final_metrics,
            "anchor_entities": anchor_entities,
            "thresholds": {"min_final_CG": 1.0, "min_final_REA": 1.0},
            "coverage_note": (
                "Run a fresh coverage audit against the fixed anchor; add or reroute only source-grounded support "
                "for uncovered core entities while preserving current judge-correct reasoning."
            ),
            "missing_entities": final_metrics["missing_entities"],
            "required_action": "Repair coverage only; preserve REA=1.0 and rerun strict PEARL evaluation.",
        }
    elif lane == "non_regression_selection_or_merge":
        payload = {
            "status": "metric_regression",
            "lane": lane,
            "paper_spec": paper_spec,
            "metric_feedback": "Terminal graph is reasoning-clean but regressed in entity coverage/content grounding.",
            "original_metrics": {
                "CG": to_float(row.get("current_original_CG")),
                "REA": to_float(row.get("current_original_REA")),
            },
            "final_metrics": final_metrics,
            "anchor_entities": anchor_entities,
            "thresholds": {
                "min_final_CG": 1.0,
                "min_final_REA": 1.0,
                "min_non_regression_CG": to_float(row.get("current_original_CG")),
            },
            "required_action": (
                "Rollback or merge source-supported coverage units; never accept a candidate with final CG below "
                "the original CG, and then pass the strict final gate."
            ),
        }
    elif lane == "bounded_final_judge_feedback_repair":
        payload = {
            "status": "judge_failed",
            "lane": lane,
            "paper_spec": paper_spec,
            "metric_feedback": "Previous final judge rejected a repaired/new reasoning target or NROOT.",
            "final_metrics": final_metrics,
            "error_summary_excerpt": row.get("error_summary_excerpt", ""),
            "required_action": (
                "Use the failed vote payload when available; repair, reroute, or drop the rejected target/root, "
                "then rerun fresh final judge and strict gate."
            ),
        }
    else:
        return None

    if audited_frontier:
        payload["audited_frontier"] = audited_frontier
        payload.setdefault("thresholds", {})
        payload["thresholds"]["min_frontier_covered_entities"] = frontier_threshold
        if frontier_total:
            payload["thresholds"]["frontier_total_entities"] = frontier_total
        payload["coverage_note"] = (
            f"External audited frontier guard: preserve at least {frontier_threshold}/{frontier_total or 'unknown'} "
            "covered core entities from the best previous fresh-evaluated candidate while repairing only the failed "
            "target(s). Do not trade coverage away for REA unless the replacement graph passes final CG=1.0 and "
            "REA=1.0."
        )
        payload["required_action"] = (
            f"Start from the audited frontier candidate if provided; keep its coverage-bearing reasoning intact, "
            f"repair noncorrect target(s) `{audited_frontier.get('noncorrect_targets', '')}`, then rerun the strict gate."
        )

    write_json(out, payload)
    return out


def local_plan_item(
    row: Dict[str, str],
    run_root: Path,
    seed_path: Optional[Path],
    candidate_override: Optional[Path] = None,
    audited_frontier: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source_eval_root = resolve_path(str(row.get("source_eval_dir") or ""))
    source_eval_dir = latest_clean_eval_dir(source_eval_root) if source_eval_root.exists() else None
    run_dir = run_dir_from_generation_graph(resolve_path(row["generation_final_clean_graph"]))
    item = {
        "paper_spec": row["paper_spec"],
        "lane": row["lane"],
        "failure_type": row["failure_type"],
        "strategy": {
            "entity_coverage_targeted_repair": "seed_metric_feedback_then_repair_root_gate_from_existing_terminal_graph",
            "non_regression_selection_or_merge": "seed_non_regression_feedback_then_repair_root_gate_from_existing_terminal_graph",
            "bounded_final_judge_feedback_repair": "seed_final_judge_feedback_then_repair_root_gate",
        }.get(row["lane"], "unknown"),
        "executable_by_controller": True,
        "seed_repair_feedback": str(seed_path) if seed_path else "",
        "seed_candidate_reference_graph": str(resolve_path(str(row.get("terminal_graph") or ""))) if row.get("terminal_graph") else "",
        "run_one_kwargs": {
            "seed_repair_feedback": str(seed_path) if seed_path else "",
            "skip_initial_curation": False,
            "terminal_preserving_regenerate": row["lane"] in {"entity_coverage_targeted_repair", "non_regression_selection_or_merge"},
            "source_run_dir": str(run_dir),
            "source_eval_dir": str(source_eval_dir) if source_eval_dir else "",
            "repair_rejected": True,
            "stop_on_regression": True,
            "min_final_CG": 1.0,
            "min_final_REA": 1.0,
        },
        "current_metrics": {
            "original_CG": to_float(row.get("current_original_CG")),
            "original_REA": to_float(row.get("current_original_REA")),
            "final_CG": to_float(row.get("current_final_CG")),
            "final_REA": to_float(row.get("current_final_REA")),
            "final_missing_entities": to_float(row.get("final_missing_entities")),
        },
    }
    if audited_frontier:
        item["audited_frontier"] = audited_frontier
        item["run_one_kwargs"]["frontier_guard"] = audited_frontier
        frontier_graph = str(audited_frontier.get("candidate_graph") or "")
        if not candidate_override and frontier_graph:
            candidate_override = resolve_path(frontier_graph)
    if candidate_override is not None:
        item["strategy"] = f"{item['strategy']}+reuse_existing_candidate_graph"
        item["candidate_graph_override"] = str(candidate_override)
        item["seed_candidate_reference_graph"] = str(candidate_override)
        item["run_one_kwargs"]["initial_graph_dot"] = str(candidate_override)
        item["run_one_kwargs"]["skip_initial_curation"] = True
    return item


def raw_regeneration_queue(rows: List[Dict[str, str]], run_root: Path) -> Dict[str, Any]:
    queue_rows = []
    for row in rows:
        if row.get("lane") != RAW_REGENERATION_LANE:
            continue
        generation_graph = resolve_path(str(row.get("generation_final_clean_graph") or ""))
        input_data = generation_graph.parent / "input_data.json" if generation_graph.name else Path("")
        queue_rows.append(
            {
                "paper_spec": row["paper_spec"],
                "model": row["model"],
                "paper_id": row["paper"],
                "lane": "raw_regeneration",
                "failure_type": "preflight:no_anchor_regenerate",
                "source": project_rel(DEFAULT_QUEUE),
                "source_graph_mode": "anchor_bootstrap_source_candidate",
                "retryable": True,
                "preflight": {
                    "status": "no_anchor_regenerate",
                    "reason": "source graph has no majority-correct reasoning unit for PEARL to anchor repair",
                },
                "source_generation": {
                    "input_data": project_rel(input_data) if input_data.exists() else "",
                    "generation_final_clean_graph": row.get("generation_final_clean_graph", ""),
                },
            }
        )
    payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "residual_50_closeout_engine",
        "queue_count": len(queue_rows),
        "queue": queue_rows,
    }
    out = run_root / "raw_regeneration_lane" / "raw_regeneration_queue.json"
    write_json(out, payload)
    return {"path": str(out), "queue_count": len(queue_rows), "payload": payload}


def load_runner_api() -> Dict[str, Any]:
    from run_vote_guided_semantic_root_batch import (  # noqa: PLC0415
        PreflightGateError,
        classify_failure_type,
        is_retryable_error,
        load_env_file,
        run_one,
        write_progress,
    )

    return {
        "PreflightGateError": PreflightGateError,
        "classify_failure_type": classify_failure_type,
        "is_retryable_error": is_retryable_error,
        "load_env_file": load_env_file,
        "run_one": run_one,
        "write_progress": write_progress,
    }


def execute_local_items(
    plan_items: List[Dict[str, Any]],
    *,
    run_root: Path,
    args: argparse.Namespace,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    load_env_file_light(resolve_path(args.env_file))
    os.environ["EVAL_RAW_HTTP_MODELS"] = args.eval_raw_http_models
    runner = load_runner_api()
    PreflightGateError = runner["PreflightGateError"]
    classify_failure_type = runner["classify_failure_type"]
    is_retryable_error = runner["is_retryable_error"]
    run_one = runner["run_one"]
    write_progress = runner["write_progress"]
    env = dict(os.environ)
    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    report_root = run_root / "local_repair_execution"
    report_root.mkdir(parents=True, exist_ok=True)

    for item in plan_items:
        kwargs = dict(item.get("run_one_kwargs") or {})
        for key in ("initial_graph_dot", "seed_repair_feedback", "source_run_dir", "source_eval_dir"):
            if kwargs.get(key):
                kwargs[key] = resolve_path(str(kwargs[key]))
            elif key in kwargs:
                kwargs.pop(key, None)
        repair_rejected = bool(kwargs.pop("repair_rejected", True))
        stop_on_regression = bool(kwargs.pop("stop_on_regression", True))
        min_final_cg = float(kwargs.pop("min_final_CG", 1.0))
        min_final_rea = float(kwargs.pop("min_final_REA", 1.0))
        frontier_guard = kwargs.pop("frontier_guard", None)
        try:
            row = run_one(
                str(item["paper_spec"]),
                report_root,
                env,
                timeout=args.timeout,
                max_tokens=args.max_tokens,
                inner_workers=args.inner_workers,
                stop_on_regression=stop_on_regression,
                full_rejudge=False,
                repair_rejected=repair_rejected,
                gpt_retries=args.gpt_retries,
                gpt_retry_sleep=args.gpt_retry_sleep,
                gpt_transport=args.gpt_transport,
                gpt_stream=args.gpt_stream,
                max_prune_rounds=args.max_prune_rounds,
                judge_error_retries=args.judge_error_retries,
                judge_error_retry_sleep=args.judge_error_retry_sleep,
                min_anchor_correct_ratio=args.min_anchor_correct_ratio,
                max_repair_candidate_ratio=args.max_repair_candidate_ratio,
                min_raw_cg=args.min_raw_cg,
                min_final_cg=min_final_cg,
                min_final_rea=min_final_rea,
                frontier_guard=frontier_guard,
                **kwargs,
            )
            row["residual_closure_lane"] = item.get("lane")
            row["residual_closure_strategy"] = item.get("strategy")
            rows.append(row)
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "paper_spec": item.get("paper_spec"),
                    "lane": item.get("lane"),
                    "strategy": item.get("strategy"),
                    "failure_type": classify_failure_type(exc),
                    "retryable": is_retryable_error(str(exc)),
                    "preflight": exc.details if isinstance(exc, PreflightGateError) else None,
                    "error": str(exc),
                }
            )
        write_progress(report_root, rows, failures=failures, requested=len(plan_items))
    return rows, failures


def raw_runner_command(raw_queue_path: str, run_root: Path, args: argparse.Namespace) -> List[str]:
    return [
        sys.executable,
        "scripts/run_edge_repair_raw_regeneration_residuals.py",
        "--queue",
        raw_queue_path,
        "--report-root",
        str(run_root / "raw_regeneration_lane" / "execution"),
        "--env-file",
        str(resolve_path(args.env_file)),
        "--provider-profile",
        str(resolve_path(args.provider_profile)),
        "--source-run-root",
        str(run_root / "raw_regeneration_lane" / "source_runs"),
        "--limit",
        "0",
        "--no-execute",
    ]


def source_regeneration_escalation_queue(rows: List[Dict[str, str]], run_root: Path) -> Dict[str, Any]:
    queue_rows = []
    for row in rows:
        if row.get("lane") not in EXECUTABLE_LOCAL_LANES:
            continue
        generation_graph = resolve_path(str(row.get("generation_final_clean_graph") or ""))
        input_data = generation_graph.parent / "input_data.json" if generation_graph.name else Path("")
        queue_rows.append(
            {
                "paper_spec": row["paper_spec"],
                "model": row["model"],
                "paper_id": row["paper"],
                "lane": "source_regeneration_escalation",
                "original_residual_lane": row.get("lane", ""),
                "failure_type": row.get("failure_type", ""),
                "source": project_rel(DEFAULT_QUEUE),
                "retryable": True,
                "preflight": {
                    "status": "local_residual_escalated_to_source_regeneration",
                    "reason": (
                        "Candidate-frontier repair did not satisfy the strict gate; regenerate a fresh "
                        "source graph from paper evidence, then re-enter the unchanged PEARL strict gate."
                    ),
                },
                "source_generation": {
                    "input_data": project_rel(input_data) if input_data.exists() else "",
                    "generation_final_clean_graph": row.get("generation_final_clean_graph", ""),
                    "terminal_graph": row.get("terminal_graph", ""),
                },
            }
        )
    payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "residual_50_closeout_engine",
        "mode": "source_regeneration_escalation",
        "queue_count": len(queue_rows),
        "queue": queue_rows,
    }
    out = run_root / "source_regeneration_escalation_lane" / "source_regeneration_escalation_queue.json"
    write_json(out, payload)
    return {"path": str(out), "queue_count": len(queue_rows), "payload": payload}


def source_regeneration_runner_command(queue_path: str, run_root: Path, args: argparse.Namespace) -> List[str]:
    cmd = [
        sys.executable,
        "scripts/run_edge_repair_raw_regeneration_residuals.py",
        "--queue",
        queue_path,
        "--report-root",
        str(run_root / "source_regeneration_escalation_lane" / "execution"),
        "--env-file",
        str(resolve_path(args.env_file)),
        "--provider-profile",
        str(resolve_path(args.provider_profile)),
        "--source-run-root",
        str(run_root / "source_regeneration_escalation_lane" / "source_runs"),
        "--include-lanes",
        "source_regeneration_escalation",
        "--allow-non-raw-residuals",
        "--limit",
        "0",
        "--generation-model",
        args.escalation_generation_model,
        "--fallback-generation-models",
        args.escalation_fallback_generation_models,
        "--generation-timeout",
        str(args.escalation_generation_timeout),
        "--generation-retries",
        str(args.escalation_generation_retries),
        "--generation-retry-sleep",
        str(args.escalation_generation_retry_sleep),
        "--timeout",
        str(args.timeout),
        "--max-tokens",
        str(args.max_tokens),
        "--gpt-retries",
        str(args.gpt_retries),
        "--gpt-retry-sleep",
        str(args.gpt_retry_sleep),
        "--gpt-transport",
        args.gpt_transport,
        "--max-prune-rounds",
        str(args.max_prune_rounds),
        "--judge-error-retries",
        str(args.judge_error_retries),
        "--judge-error-retry-sleep",
        str(args.judge_error_retry_sleep),
        "--inner-workers",
        str(args.inner_workers),
        "--min-anchor-correct-ratio",
        str(args.min_anchor_correct_ratio),
        "--max-repair-candidate-ratio",
        str(args.max_repair_candidate_ratio),
        "--min-raw-cg",
        str(args.min_raw_cg),
    ]
    cmd.append("--gpt-stream" if args.gpt_stream else "--no-gpt-stream")
    cmd.append("--no-execute")
    return cmd


def maybe_execute_source_regeneration(escalation_info: Dict[str, Any], run_root: Path, args: argparse.Namespace) -> Dict[str, Any]:
    if escalation_info["queue_count"] == 0:
        return {"status": "skipped", "reason": "no source-regeneration escalation rows"}
    load_env_file_light(resolve_path(args.env_file))
    os.environ["EVAL_RAW_HTTP_MODELS"] = args.eval_raw_http_models
    cmd = source_regeneration_runner_command(escalation_info["path"], run_root, args)
    if not args.execute_source_regeneration_escalation:
        return {
            "status": "planned_only",
            "reason": "use --execute-source-regeneration-escalation to launch escalated source regeneration lane",
            "command": cmd[:-1] + ["--execute"],
        }
    cmd = cmd[:-1] + ["--execute"]
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=dict(os.environ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    out = run_root / "source_regeneration_escalation_lane" / "runner_stdout.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(proc.stdout, encoding="utf-8")
    return {"status": "executed", "returncode": proc.returncode, "stdout": str(out), "command": cmd}


def maybe_execute_raw_lane(raw_queue_info: Dict[str, Any], run_root: Path, args: argparse.Namespace) -> Dict[str, Any]:
    if raw_queue_info["queue_count"] == 0:
        return {"status": "skipped", "reason": "no raw-regeneration rows"}
    cmd = raw_runner_command(raw_queue_info["path"], run_root, args)
    if not args.execute_raw_regeneration:
        return {
            "status": "planned_only",
            "reason": "use --execute-raw-regeneration to launch source regeneration lane",
            "command": cmd[:-1] + ["--execute"],
        }
    cmd = cmd[:-1] + ["--execute"]
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=dict(os.environ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    out = run_root / "raw_regeneration_lane" / "runner_stdout.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(proc.stdout, encoding="utf-8")
    return {"status": "executed", "returncode": proc.returncode, "stdout": str(out), "command": cmd}


def write_markdown_packet(path: Path, payload: Dict[str, Any]) -> None:
    lane_counts = Counter(item["lane"] for item in payload.get("selected_rows", []))
    lines = [
        "# Residual Closure Engine Execution Packet",
        "",
        f"Created: {payload['created_at']}",
        "",
        "## Scope",
        "",
        "This packet is for the 350-row current-version residual repair only. It does not rewrite current 350 accounting; successful outputs must still pass fresh final CG=1.0 and REA=1.0 before merge.",
        "",
        "## Selected Rows",
        "",
    ]
    for lane, count in lane_counts.most_common():
        lines.append(f"- `{lane}`: {count}")
    lines.extend(
        [
            "",
            "## Execution Policy",
            "",
            "- Local lanes use `run_vote_guided_semantic_root_batch.py::run_one` with lane-specific feedback seeds.",
            "- No-anchor rows are delegated to `run_edge_repair_raw_regeneration_residuals.py` because they need a new anchor-bearing source candidate before PEARL can operate.",
            "- Strict acceptance remains final CG=1.0 and final REA=1.0.",
            "",
            "## Files",
            "",
            f"- Plan JSON: `{payload['plan_json']}`",
            f"- Feedback seeds: `{payload['feedback_seed_dir']}`",
            f"- Raw-regeneration queue: `{payload['raw_regeneration_queue']}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--report-root", default="")
    parser.add_argument("--lanes", nargs="+", default=[])
    parser.add_argument("--paper-specs", nargs="+", default=[])
    parser.add_argument("--paper-specs-file", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--execute", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--skip-local-repair", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--execute-raw-regeneration", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--execute-source-regeneration-escalation", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument(
        "--provider-profile",
        default=str(PROJECT_ROOT / "reports" / "pearl_runs" / "06_runtime_state" / "provider_preflight_stage1_selected_20260524.json"),
    )
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-tokens", type=int, default=5000)
    parser.add_argument("--gpt-retries", type=int, default=2)
    parser.add_argument("--gpt-retry-sleep", type=float, default=8.0)
    parser.add_argument("--gpt-transport", choices=["curl", "urllib"], default=os.getenv("GPT55_CURATION_TRANSPORT", "curl"))
    parser.add_argument(
        "--gpt-stream",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("GPT55_CURATION_STREAM", "").strip().lower() not in {"0", "false", "no", "off"},
    )
    parser.add_argument("--max-prune-rounds", type=int, default=3)
    parser.add_argument("--judge-error-retries", type=int, default=2)
    parser.add_argument("--judge-error-retry-sleep", type=float, default=5.0)
    parser.add_argument(
        "--eval-raw-http-models",
        default="o3",
        help="Comma-separated evaluator judge model keys to route through raw HTTP in this closeout run.",
    )
    parser.add_argument(
        "--candidate-graph-overrides",
        default="",
        help="JSON mapping/list of paper_spec to existing candidate graph .dot files; skips GPT curation for those rows and reruns fresh final evaluation.",
    )
    parser.add_argument(
        "--candidate-frontier",
        default=str(DEFAULT_CANDIDATE_FRONTIER),
        help="CSV from extract_residual_50_candidate_frontier.py. Best audited frontier is injected as a coverage guard.",
    )
    parser.add_argument(
        "--no-frontier-guard",
        action="store_true",
        help="Disable external audited frontier injection even if --candidate-frontier exists.",
    )
    parser.add_argument("--inner-workers", type=int, default=6)
    parser.add_argument("--min-anchor-correct-ratio", type=float, default=0.0)
    parser.add_argument("--max-repair-candidate-ratio", type=float, default=1.0)
    parser.add_argument("--min-raw-cg", type=float, default=0.0)
    parser.add_argument("--escalation-generation-model", default="gpt-5.5")
    parser.add_argument("--escalation-fallback-generation-models", default="")
    parser.add_argument("--escalation-generation-timeout", type=float, default=600.0)
    parser.add_argument("--escalation-generation-retries", type=int, default=2)
    parser.add_argument("--escalation-generation-retry-sleep", type=float, default=12.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_root = resolve_path(args.report_root) if args.report_root else (
        DEFAULT_OUT_ROOT / f"residual_50_closure_engine_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    run_root.mkdir(parents=True, exist_ok=True)
    queue_path = resolve_path(args.queue)
    all_rows = read_queue(queue_path)
    requested_specs = parse_specs(args.paper_specs, args.paper_specs_file)
    selected = select_rows(all_rows, lanes=args.lanes, paper_specs=requested_specs, limit=args.limit)
    candidate_overrides = load_candidate_graph_overrides(args.candidate_graph_overrides)
    candidate_frontier = {} if args.no_frontier_guard else load_candidate_frontier(args.candidate_frontier)

    seed_dir = run_root / "feedback_seeds"
    local_items: List[Dict[str, Any]] = []
    delegated_items: List[Dict[str, Any]] = []
    for row in selected:
        if row.get("lane") in EXECUTABLE_LOCAL_LANES:
            frontier = candidate_frontier.get(str(row.get("paper_spec", "")))
            seed = lane_seed_feedback(row, seed_dir, audited_frontier=frontier)
            local_items.append(
                local_plan_item(
                    row,
                    run_root,
                    seed,
                    candidate_override=candidate_overrides.get(str(row.get("paper_spec", ""))),
                    audited_frontier=frontier,
                )
            )
        elif row.get("lane") == RAW_REGENERATION_LANE:
            delegated_items.append({"paper_spec": row["paper_spec"], "lane": row["lane"], "delegated_to": "raw_regeneration"})

    raw_info = raw_regeneration_queue(selected, run_root)
    escalation_info = source_regeneration_escalation_queue(selected, run_root)
    payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "residual_50_closure_engine",
        "queue": str(queue_path),
        "report_root": str(run_root),
        "execute": args.execute,
        "execute_raw_regeneration": args.execute_raw_regeneration,
        "selected": len(selected),
        "selected_rows": [
            {key: row.get(key, "") for key in ("priority", "lane", "paper_spec", "failure_type", "current_final_CG", "current_final_REA")}
            for row in selected
        ],
        "local_plan": local_items,
        "delegated_plan": delegated_items,
        "raw_regeneration_queue": raw_info["path"],
        "source_regeneration_escalation_queue": escalation_info["path"],
        "feedback_seed_dir": str(seed_dir),
        "candidate_graph_overrides": {paper_spec: str(path) for paper_spec, path in candidate_overrides.items()},
        "candidate_frontier": str(resolve_path(args.candidate_frontier)) if args.candidate_frontier else "",
        "frontier_guard_enabled": not args.no_frontier_guard,
        "frontier_guard_rows": len(candidate_frontier),
        "strict_gate": {"final_CG": 1.0, "final_REA": 1.0},
    }
    plan_json = run_root / "residual_50_closure_engine_plan.json"
    payload["plan_json"] = str(plan_json)
    write_json(plan_json, payload)
    write_markdown_packet(run_root / "residual_50_closure_engine_plan.md", payload)

    local_rows: List[Dict[str, Any]] = []
    local_failures: List[Dict[str, Any]] = []
    raw_result = {"status": "not_requested"}
    escalation_result = {"status": "not_requested"}
    if args.execute:
        if not args.skip_local_repair:
            local_rows, local_failures = execute_local_items(local_items, run_root=run_root, args=args)
        raw_result = maybe_execute_raw_lane(raw_info, run_root, args)
        escalation_result = maybe_execute_source_regeneration(escalation_info, run_root, args)
        index_result = write_execution_indexes(run_root, local_rows, local_failures)
        write_json(
            run_root / "residual_50_closure_engine_execution_summary.json",
            {
                "completed_local": len(local_rows),
                "failed_local": len(local_failures),
                "strict_gate_passed_local": index_result["strict_gate_passed"],
                "raw_regeneration": raw_result,
                "source_regeneration_escalation": escalation_result,
                "indexes": index_result,
                "local_failures": local_failures,
            },
        )
    else:
        raw_result = maybe_execute_raw_lane(raw_info, run_root, args)
        escalation_result = maybe_execute_source_regeneration(escalation_info, run_root, args)
        index_result = {}

    result = {
        "status": "executed" if args.execute else "planned",
        "report_root": str(run_root),
        "selected": len(selected),
        "local_lanes": len(local_items),
        "raw_regeneration_rows": raw_info["queue_count"],
        "source_regeneration_escalation_rows": escalation_info["queue_count"],
        "completed_local": len(local_rows),
        "failed_local": len(local_failures),
        "raw_regeneration": raw_result,
        "source_regeneration_escalation": escalation_result,
        "indexes": index_result,
        "plan": str(plan_json),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not local_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
