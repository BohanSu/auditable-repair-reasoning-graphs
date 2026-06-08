#!/usr/bin/env python3
"""Run the PEARL semantic root recovery module.

This conservative production runner is part of PEARL. It applies vote-guided
evidence filtering, asks GPT-5.5 to recover a true semantic `NROOT`, stops on
the first unresolved protocol problem, reuses clean original votes for retained
raw steps, and judges only the newly introduced semantic root step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

QUALITY_TIER_POLICY = {
    "retention_metric": "retained_correct_ratio = kept_reasoning_units / raw_total_reasoning_steps",
    "effective_density_metric": (
        "effective_reasoning_ratio = (kept_reasoning_units + repaired_reasoning_units) "
        "/ raw_total_reasoning_steps"
    ),
    "tier_A_main": {
        "min_final_CG": 0.90,
        "min_effective_reasoning_ratio": 0.40,
        "description": "High-coverage graph with substantial retained-or-repaired reasoning density.",
    },
    "tier_B_usable": {
        "min_final_CG": 0.80,
        "min_effective_reasoning_ratio": 0.20,
        "description": "Clean usable graph, but less dense than the main tier.",
    },
    "tier_C_thin": {
        "min_final_CG": 0.80,
        "min_effective_reasoning_ratio": "> 0",
        "description": "Valid clean graph with sparse retained/repaired evidence; keep out of main rich-graph claims.",
    },
    "repair": {
        "conditions": [
            "effective_reasoning_ratio == 0",
            "final_CG < 0.80",
            "CG or REA regression",
            "final_REA < 1.0 under vote_reuse_root_only",
        ],
        "description": "Needs separate repair or regeneration before use as a graph dataset entry.",
    },
}

from curate_graph_from_vote_results_gpt55 import (  # noqa: E402
    DEFAULT_OUT_ROOT,
    build_packet,
    infer_ids,
    latest_eval_dir,
    load_env_file,
    write_json,
)
from dot_to_graph_spec import dot_to_graph_spec  # noqa: E402
from graph_spec_to_dot import graph_spec_to_dot  # noqa: E402
from reasoning_graph_validator import assess_dot_quality  # noqa: E402
import evaluator as evaluator_module  # noqa: E402
from evaluator import GraphEvaluator, is_eval_dir_clean  # noqa: E402


class PreflightGateError(RuntimeError):
    def __init__(self, code: str, message: str, details: Dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def latest_clean_eval_dir(work_dir: Path) -> Optional[Path]:
    eval_root = work_dir / "evaluation_outputs"
    if not eval_root.exists():
        return None
    candidates = sorted(
        [path for path in eval_root.glob("*_evaluation") if (path / "evaluation_results.json").exists()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if is_eval_dir_clean(candidate):
            return candidate
    return None


def latest_eval_result_dir(work_dir: Path) -> Optional[Path]:
    eval_root = work_dir / "evaluation_outputs"
    if not eval_root.exists():
        return None
    candidates = sorted(
        [path for path in eval_root.glob("*_evaluation") if (path / "evaluation_results.json").exists()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def run_cmd(cmd: List[str], *, env: Dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def summarize_eval(eval_dir: Path) -> Dict[str, Any]:
    data = read_json(eval_dir / "evaluation_results.json", {})
    summary = data.get("evaluation_summary", {})
    return {
        "CG": float(summary.get("entity_coverage_score", 0.0)),
        "REA": float(summary.get("accuracy_score", 0.0)),
        "covered_entities": int(summary.get("covered_entities", 0)),
        "total_entities": int(summary.get("total_entities", 0)),
        "valid_reasoning_steps": int(summary.get("valid_reasoning_steps", 0)),
        "total_reasoning_steps": int(summary.get("total_reasoning_steps", 0)),
        "eval_dir": str(eval_dir),
    }


def coverage_algorithm_version() -> str:
    return getattr(evaluator_module, "COVERAGE_ALGORITHM_VERSION", "unknown")


def summarize_source_eval_with_current_evaluator(run_dir: Path, eval_dir: Path, recalc_dir: Path) -> Dict[str, Any]:
    """Reuse historical raw votes but recompute CG with the current evaluator.

    Historical source eval dirs were generated before later evaluator fixes
    such as valid-step ordering and implicit-node label fallback. For a fair
    PEARL comparison, keep the original clean judge labels but calculate raw
    coverage with the same code path used for the curated graph.
    """
    stored = summarize_eval(eval_dir)
    data = read_json(eval_dir / "evaluation_results.json", {})
    entities = list(data.get("entities", []))
    reasoning_results = (data.get("accuracy") or {}).get("details") or {}
    total_reasoning_steps = int(stored.get("total_reasoning_steps", 0) or 0)
    if entities and total_reasoning_steps == 0:
        return {
            "CG": 0.0,
            "REA": 0.0,
            "covered_entities": 0,
            "total_entities": len(entities),
            "valid_reasoning_steps": 0,
            "total_reasoning_steps": 0,
            "eval_dir": str(eval_dir),
            "coverage_algorithm_version": coverage_algorithm_version(),
            "baseline_protocol": "source_eval_no_evaluator_visible_reasoning_steps",
            "stored_CG": stored["CG"],
            "stored_REA": stored["REA"],
            "stored_covered_entities": stored["covered_entities"],
            "stored_valid_reasoning_steps": stored["valid_reasoning_steps"],
            "stored_total_reasoning_steps": stored["total_reasoning_steps"],
        }
    if not entities or not reasoning_results:
        raise RuntimeError(f"missing entities/accuracy details in {eval_dir / 'evaluation_results.json'}")

    graph_evaluator = _make_existing_graph_evaluator(run_dir, recalc_dir, entities)
    if not graph_evaluator.load_data():
        raise RuntimeError(f"failed to load source graph/input for baseline recalc: {run_dir}")

    coverage_rate = graph_evaluator.calculate_entity_coverage_from_correct_reasoning(reasoning_results)
    accuracy_result = graph_evaluator.calculate_accuracy_score(reasoning_results)
    return {
        "CG": coverage_rate / 100,
        "REA": float(accuracy_result["accuracy_score"]),
        "covered_entities": int(len(entities) * coverage_rate / 100),
        "total_entities": len(entities),
        "valid_reasoning_steps": int(accuracy_result["valid_steps"]),
        "total_reasoning_steps": int(accuracy_result["total_steps"]),
        "eval_dir": str(eval_dir),
        "coverage_algorithm_version": coverage_algorithm_version(),
        "baseline_protocol": "reuse_original_clean_votes_recompute_cg_with_current_evaluator",
        "stored_CG": stored["CG"],
        "stored_REA": stored["REA"],
        "stored_covered_entities": stored["covered_entities"],
        "stored_valid_reasoning_steps": stored["valid_reasoning_steps"],
        "stored_total_reasoning_steps": stored["total_reasoning_steps"],
    }


def summarize_stable_eval(eval_dir: Path) -> Dict[str, Any]:
    data = read_json(eval_dir / "evaluation_results.json", {})
    summary = data.get("evaluation_summary", {})
    return {
        "CG": float(summary.get("entity_coverage_score", 0.0)),
        "REA": float(summary.get("accuracy_score", 0.0)),
        "covered_entities": int(summary.get("covered_entities", 0)),
        "total_entities": int(summary.get("total_entities", 0)),
        "valid_reasoning_steps": int(summary.get("valid_reasoning_steps", 0)),
        "total_reasoning_steps": int(summary.get("total_reasoning_steps", 0)),
        "eval_dir": str(eval_dir),
        "protocol": data.get("protocol", ""),
        "coverage_algorithm_version": str(data.get("coverage_algorithm_version", "")),
    }


def normalize_frontier_guard(frontier_guard: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(frontier_guard, dict):
        return None

    def as_float(value: Any) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def as_int(value: Any) -> Optional[int]:
        try:
            if value is None or value == "":
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None

    covered = as_int(frontier_guard.get("covered_entities"))
    graph = str(frontier_guard.get("candidate_graph") or "")
    if covered is None and not graph:
        return None
    return {
        "source": frontier_guard.get("source", ""),
        "run": frontier_guard.get("run", ""),
        "eval_name": frontier_guard.get("eval_name", ""),
        "CG": as_float(frontier_guard.get("CG")),
        "REA": as_float(frontier_guard.get("REA")),
        "covered_entities": covered,
        "total_entities": as_int(frontier_guard.get("total_entities")),
        "valid_reasoning_steps": as_int(frontier_guard.get("valid_reasoning_steps")),
        "total_reasoning_steps": as_int(frontier_guard.get("total_reasoning_steps")),
        "noncorrect_targets": frontier_guard.get("noncorrect_targets", ""),
        "noncorrect_vote_count": as_int(frontier_guard.get("noncorrect_vote_count")),
        "candidate_graph": graph,
        "eval_dir": frontier_guard.get("eval_dir", ""),
    }


def merge_frontier_guard_into_feedback(
    feedback: Dict[str, Any],
    frontier_guard: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    guard = normalize_frontier_guard(frontier_guard)
    if not guard:
        return feedback
    merged = dict(feedback)
    merged["audited_frontier"] = guard
    thresholds = dict(merged.get("thresholds") or {})
    guard_covered = guard.get("covered_entities")
    if guard_covered is not None:
        try:
            current = int(float(thresholds.get("min_frontier_covered_entities") or 0))
        except (TypeError, ValueError):
            current = 0
        thresholds["min_frontier_covered_entities"] = max(current, int(guard_covered))
    if guard.get("total_entities") is not None:
        thresholds["frontier_total_entities"] = int(guard["total_entities"])
    thresholds["external_frontier_guard"] = True
    merged["thresholds"] = thresholds
    note = (
        f"External audited frontier guard: preserve at least "
        f"{thresholds.get('min_frontier_covered_entities')}/"
        f"{thresholds.get('frontier_total_entities', 'unknown')} covered core entities from the best "
        "previous fresh-evaluated candidate while repairing the failed target(s)."
    )
    merged["coverage_note"] = f"{merged.get('coverage_note', '')} {note}".strip()
    merged["required_action"] = (
        f"Use the audited frontier as rollback baseline; repair noncorrect target(s) "
        f"`{guard.get('noncorrect_targets', '')}` while preserving coverage-bearing units."
    )
    return merged


def frontier_min_covered(frontier_guard: Optional[Dict[str, Any]]) -> int:
    guard = normalize_frontier_guard(frontier_guard)
    if not guard:
        return 0
    return int(guard.get("covered_entities") or 0)


def summary_passes_strict_gate(summary: Dict[str, Any]) -> bool:
    return (
        float(summary.get("CG", 0.0) or 0.0) + 1e-9 >= 1.0
        and float(summary.get("REA", 0.0) or 0.0) + 1e-9 >= 1.0
    )


def refresh_stable_eval_coverage(eval_dir: Path) -> Optional[Dict[str, Any]]:
    """Upgrade cached vote-reuse metrics to the current coverage algorithm."""
    results_path = eval_dir / "evaluation_results.json"
    data = read_json(results_path, {})
    if not data or data.get("clean") is not True:
        return None
    if data.get("coverage_algorithm_version") == coverage_algorithm_version():
        return summarize_stable_eval(eval_dir)

    output_dir = Path(str(data.get("output_dir", "")))
    entities = list(data.get("entities", []) or [])
    reasoning_results = (data.get("accuracy") or {}).get("details") or {}
    if not output_dir or not entities or not reasoning_results:
        return None

    graph_evaluator = _make_existing_graph_evaluator(output_dir, eval_dir, entities)
    if not graph_evaluator.load_data():
        return None

    coverage_rate = graph_evaluator.calculate_entity_coverage_from_correct_reasoning(reasoning_results)
    coverage_result = {
        "coverage_rate": coverage_rate / 100,
        "total_entities": len(entities),
        "covered_entities": int(len(entities) * coverage_rate / 100),
    }
    data["coverage_algorithm_version"] = coverage_algorithm_version()
    data["coverage"] = coverage_result
    summary = data.setdefault("evaluation_summary", {})
    summary["entity_coverage_score"] = coverage_result["coverage_rate"]
    summary["total_entities"] = len(entities)
    summary["covered_entities"] = coverage_result["covered_entities"]
    write_json(results_path, data)
    summary_path = eval_dir / "evaluation_summary.txt"
    if summary_path.exists():
        text = summary_path.read_text(encoding="utf-8")
        text = re.sub(
            r"- Content Grounding \(CG\): .*\n",
            (
                f"- Content Grounding (CG): {coverage_result['coverage_rate']:.2%} "
                f"({coverage_result['covered_entities']}/{coverage_result['total_entities']})\n"
            ),
            text,
        )
        summary_path.write_text(text, encoding="utf-8")
    return summarize_stable_eval(eval_dir)


def graph_hash(work_dir: Path) -> str:
    graph_path = work_dir / "final_clean_graph.dot"
    try:
        return hashlib.sha256(graph_path.read_bytes()).hexdigest()
    except OSError:
        return ""


def vote_signature(prompt: Dict[str, Any]) -> str:
    payload = {
        "target_node": prompt.get("target_node"),
        "source_nodes": prompt.get("source_nodes"),
        "edge_types": prompt.get("edge_types"),
        "actual_edge_types": prompt.get("actual_edge_types"),
        "reasoning_type": prompt.get("reasoning_type"),
        "target_content": prompt.get("target_content"),
        "source_contents": prompt.get("source_contents"),
        "premise_descriptions": prompt.get("premise_descriptions"),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def compact_model_errors(model_responses: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    errors: Dict[str, Dict[str, str]] = {}
    for model_key, response in model_responses.items():
        if str(response.get("result", "")).lower() != "error":
            continue
        errors[str(model_key)] = {
            "model_name": str(response.get("model_name", "")),
            "reason": str(response.get("reason", ""))[:500],
            "raw_response_prefix": str(response.get("raw_response", ""))[:500],
        }
    return errors


def repair_target_reasoning_id_for_target(target_node: str) -> Optional[int]:
    if not target_node.startswith("R"):
        return None
    try:
        return int(target_node[1:])
    except ValueError:
        return None


def judge_failure_feedback(
    *,
    status: str,
    validation_prompt_id: int,
    target_node: str,
    vote_file: Path,
    vote: Optional[Dict[str, Any]] = None,
    model_errors: Optional[Dict[str, Dict[str, str]]] = None,
    reuse_policy: Optional[str] = None,
) -> Dict[str, Any]:
    """Write both legacy and explicit ids so repair does not confuse order with R id."""
    payload: Dict[str, Any] = {
        "status": status,
        "failed_reasoning_id": validation_prompt_id,
        "failed_validation_prompt_id": validation_prompt_id,
        "failed_target_node": target_node,
        "repair_target_reasoning_id": repair_target_reasoning_id_for_target(target_node),
        "id_semantics": {
            "failed_validation_prompt_id": "Ordinal id assigned to this final validation prompt.",
            "repair_target_reasoning_id": "Graph reasoning id to repair as R<id>; null for NROOT/non-R targets.",
        },
        "vote_file": str(vote_file),
    }
    if vote is not None:
        payload["vote"] = vote
    if model_errors is not None:
        payload["model_errors"] = model_errors
    if reuse_policy:
        payload["reuse_policy"] = reuse_policy
    return payload


def is_non_error_vote(vote: Dict[str, Any]) -> bool:
    if str(vote.get("final_result", "")).lower() == "error":
        return False
    return not any(
        str(result).lower() == "error"
        for result in (vote.get("model_results") or {}).values()
    )


def load_partial_vote_caches(work_dir: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Load reusable stable-eval votes/responses for this graph work dir.

    Clean full eval dirs are reused earlier. This cache is for interrupted or
    provider-error runs: it prevents re-judging prompts that already have clean
    votes and reuses non-error model responses when only one judge failed.
    """
    caches: Dict[str, Dict[str, Dict[str, Any]]] = {
        "correct_votes": {},
        "failed_votes": {},
        "model_responses": {},
    }
    candidate_eval_roots = []
    for sibling in sorted(work_dir.parent.glob("semantic_root_fixed_anchor*")):
        eval_root = sibling / "stable_evaluation_outputs"
        if eval_root.exists():
            candidate_eval_roots.append(eval_root)
    own_eval_root = work_dir / "stable_evaluation_outputs"
    if own_eval_root.exists() and own_eval_root not in candidate_eval_roots:
        candidate_eval_roots.insert(0, own_eval_root)

    # Reuse across report roots for the same model/paper/run only when the
    # full prompt signature matches. This avoids re-paying for judge calls
    # already made in an interrupted report while preserving metric semantics.
    try:
        run_id = work_dir.parent.name
        paper = work_dir.parent.parent.name
        model = work_dir.parent.parent.parent.name
        cross_root_pattern = (
            PROJECT_ROOT
            / "reports"
            / "pearl_runs"
            / "*"
            / "eval_work"
            / model
            / paper
            / run_id
            / "semantic_root_fixed_anchor*"
            / "stable_evaluation_outputs"
        )
        for eval_root in sorted(PROJECT_ROOT.glob(str(cross_root_pattern.relative_to(PROJECT_ROOT)))):
            if eval_root.exists() and eval_root not in candidate_eval_roots:
                candidate_eval_roots.append(eval_root)
        clean_package_terminal_eval = (
            PROJECT_ROOT
            / "reports"
            / "pearl_runs"
            / "00_CURRENT_STANDARD_FLOW_20260527"
            / "standard_flow_490_clean_package_20260528"
            / "04_ec_rea_evaluation"
            / "terminal_metric_eval_outputs"
            / model
            / paper
            / run_id
        )
        if clean_package_terminal_eval.exists() and clean_package_terminal_eval not in candidate_eval_roots:
            candidate_eval_roots.append(clean_package_terminal_eval)
        current_ablation_pattern = (
            PROJECT_ROOT
            / "reports"
            / "pearl_runs"
            / "00_CURRENT_STANDARD_FLOW_20260527"
            / "ablation_work_20260528"
            / "root_only_eval_work_*"
            / "work"
            / model
            / paper
            / run_id
            / "semantic_root_fixed_anchor*"
            / "stable_evaluation_outputs"
        )
        for eval_root in sorted(PROJECT_ROOT.glob(str(current_ablation_pattern.relative_to(PROJECT_ROOT)))):
            if eval_root.exists() and eval_root not in candidate_eval_roots:
                candidate_eval_roots.append(eval_root)
    except (ValueError, IndexError):
        pass

    vote_files: List[Path] = []
    for eval_root in candidate_eval_roots:
        vote_files.extend(eval_root.glob("vote_reuse_*/*/reasoning_validation_*_vote_result.json"))
        # Some historical paths place responses directly under the eval dir.
        vote_files.extend(eval_root.glob("vote_reuse_*/responses/reasoning_validation_*_vote_result.json"))
    vote_files = sorted(vote_files, key=lambda path: path.stat().st_mtime, reverse=True)

    seen_files: set[Path] = set()
    for vote_file in vote_files:
        if vote_file in seen_files:
            continue
        seen_files.add(vote_file)
        vote = read_json(vote_file, {})
        if not isinstance(vote, dict) or not vote.get("target_node"):
            continue
        signature = vote_signature(vote)

        cached_models = caches["model_responses"].setdefault(signature, {})
        for model_key, response in (vote.get("model_responses") or {}).items():
            if not isinstance(response, dict):
                continue
            if str(response.get("result", "")).lower() == "error":
                continue
            cached_models.setdefault(str(model_key), response)

        final_result = str(vote.get("final_result", "")).lower()
        if not is_non_error_vote(vote):
            continue
        item = {
            "final_result": final_result,
            "target_node": vote.get("target_node"),
            "source_reasoning_id": vote.get("reasoning_id"),
            "vote_file": str(vote_file),
            "vote": vote,
        }
        if final_result == "correct":
            caches["correct_votes"].setdefault(signature, item)
        else:
            caches["failed_votes"].setdefault(signature, item)

    # Interrupted runs may have already paid for per-model judge responses but
    # not reached the point where a complete vote_result.json was written.
    # Reuse those non-error model responses by matching the prompt signature.
    response_files: List[Path] = []
    for eval_root in candidate_eval_roots:
        response_files.extend(eval_root.glob("vote_reuse_*/responses/reasoning_validation_*_response_*.json"))
    for response_file in sorted(response_files, key=lambda path: path.stat().st_mtime, reverse=True):
        match = re.search(r"reasoning_validation_(\d+)_response_([a-zA-Z0-9_]+)(?:_repair_\d+)?\.json$", response_file.name)
        if not match:
            continue
        reasoning_id = match.group(1)
        model_key = match.group(2)
        if model_key not in evaluator_module.EVALUATION_MODELS:
            continue
        prompt_file = response_file.parent.parent / "prompts" / f"reasoning_validation_{reasoning_id}_prompt.txt"
        if not prompt_file.exists():
            continue
        response = read_json(response_file, {})
        if not isinstance(response, dict) or str(response.get("result", "")).lower() == "error":
            continue
        target_node = response.get("target_node")
        if not target_node:
            target_match = re.search(r"\nCONCLUSION:\n(.*?)\n\nEVALUATION CRITERIA:", prompt_file.read_text(encoding="utf-8"), re.S)
            target_node = target_match.group(1).strip()[:120] if target_match else ""
        # Full prompt_info signatures are available only at runtime. Store a
        # secondary cache keyed by prompt text hash and let validate_prompt...
        # bridge it when it sees the current prompt.
        prompt_hash = hashlib.sha256(prompt_file.read_bytes()).hexdigest()
        cached_models = caches["model_responses"].setdefault(f"prompt_file:{prompt_hash}", {})
        response = dict(response)
        response["reused_from_response_file"] = str(response_file)
        if target_node:
            response.setdefault("target_node", target_node)
        cached_models.setdefault(model_key, response)

    return caches


def write_cached_vote(
    stable_eval_dir: Path,
    reasoning_id: int,
    prompt: Dict[str, Any],
    cached_vote: Dict[str, Any],
) -> Dict[str, Any]:
    vote = dict(cached_vote.get("vote") or {})
    vote.update(
        {
            "reasoning_id": reasoning_id,
            "target_node": prompt["target_node"],
            "source_nodes": prompt["source_nodes"],
            "edge_types": prompt["edge_types"],
            "actual_edge_types": prompt["actual_edge_types"],
            "reasoning_type": prompt["reasoning_type"],
            "target_content": prompt["target_content"],
            "source_contents": prompt["source_contents"],
            "premise_descriptions": prompt["premise_descriptions"],
            "reused_from_vote_file": cached_vote.get("vote_file"),
            "reuse_policy": "same_prompt_signature_stable_vote_cache",
        }
    )
    write_json(stable_eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_vote_result.json", vote)
    return vote


def validate_prompt_with_error_repair(
    graph_evaluator: GraphEvaluator,
    stable_eval_dir: Path,
    reasoning_id: int,
    prompt: Dict[str, Any],
    *,
    judge_error_retries: int,
    judge_error_retry_sleep: float,
    cached_model_responses: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Dict[str, Any], str, List[Dict[str, Any]]]:
    model_responses: Dict[str, Dict[str, Any]] = {}
    repair_events: List[Dict[str, Any]] = []
    cached_model_responses = cached_model_responses or {}

    for model_key, model_name in evaluator_module.EVALUATION_MODELS.items():
        cached_response = cached_model_responses.get(model_key)
        if cached_response and str(cached_response.get("result", "")).lower() != "error":
            response = dict(cached_response)
            response["reused_from_cache"] = True
        else:
            response = graph_evaluator._validate_single_model(model_key, model_name, prompt)
        model_responses[model_key] = response
        response_file = (
            stable_eval_dir
            / "responses"
            / f"reasoning_validation_{reasoning_id:03d}_response_{model_key}.json"
        )
        write_json(response_file, response)

    model_results = {model_key: str(resp.get("result", "error")).lower() for model_key, resp in model_responses.items()}
    vote_breakdown, final_result = graph_evaluator._vote_on_results(model_results)

    for retry_idx in range(1, max(0, judge_error_retries) + 1):
        error_models = [
            model_key
            for model_key, result in model_results.items()
            if str(result).lower() == "error"
        ]
        if not error_models:
            break
        if judge_error_retry_sleep > 0:
            import time

            time.sleep(judge_error_retry_sleep)

        event = {
            "retry": retry_idx,
            "reasoning_id": reasoning_id,
            "target_node": prompt.get("target_node"),
            "models": list(error_models),
            "before": compact_model_errors(model_responses),
        }
        for model_key in error_models:
            model_name = evaluator_module.EVALUATION_MODELS[model_key]
            response = graph_evaluator._validate_single_model(model_key, model_name, prompt)
            model_responses[model_key] = response
            repair_response_file = (
                stable_eval_dir
                / "responses"
                / f"reasoning_validation_{reasoning_id:03d}_response_{model_key}_repair_{retry_idx:02d}.json"
            )
            final_response_file = (
                stable_eval_dir
                / "responses"
                / f"reasoning_validation_{reasoning_id:03d}_response_{model_key}.json"
            )
            write_json(repair_response_file, response)
            write_json(final_response_file, response)

        model_results = {model_key: str(resp.get("result", "error")).lower() for model_key, resp in model_responses.items()}
        vote_breakdown, final_result = graph_evaluator._vote_on_results(model_results)
        event["after"] = compact_model_errors(model_responses)
        event["final_result_after_retry"] = final_result
        repair_events.append(event)

    remaining_error_models = [
        model_key
        for model_key, result in model_results.items()
        if str(result).lower() == "error"
    ]
    if remaining_error_models:
        vote_breakdown.setdefault("decision", "")
        vote_breakdown["decision"] = (
            f"Provider error retained after retry for {','.join(remaining_error_models)}; "
            "strict clean-vote gate requires all judge calls to resolve."
        )
        final_result = "error"

    return model_responses, model_results, vote_breakdown, final_result, repair_events


def reusable_correct_step_vote(step: Dict[str, Any]) -> Tuple[bool, str]:
    """Return whether a retained raw step has a clean vote that can be reused."""
    vote = step.get("vote")
    if not isinstance(vote, dict):
        return False, "missing_vote"
    if str(vote.get("final_result", "")).lower() != "correct":
        return False, f"non_correct_final_result:{vote.get('final_result')}"
    model_results = vote.get("model_results") or {}
    if any(str(result).lower() == "error" for result in model_results.values()):
        return False, "contains_judge_error"
    return True, "reusable"


def make_anchor(original_eval_results: Path, anchor_path: Path) -> Dict[str, Any]:
    original = read_json(original_eval_results, {})
    anchor = {
        "core_idea": original.get("core_idea", ""),
        "entities": original.get("entities", []),
    }
    if not anchor["core_idea"] or not anchor["entities"]:
        raise RuntimeError(f"missing core_idea/entities in {original_eval_results}")
    write_json(anchor_path, anchor)
    return anchor


def prepare_eval_work(graph_dot: Path, input_data: Path, work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    existing_graph = work_dir / "final_clean_graph.dot"
    stable_eval_root = work_dir / "stable_evaluation_outputs"
    if existing_graph.exists() and stable_eval_root.exists():
        try:
            if existing_graph.read_bytes() != graph_dot.read_bytes():
                shutil.rmtree(stable_eval_root)
        except OSError:
            shutil.rmtree(stable_eval_root)
    shutil.copy2(graph_dot, work_dir / "final_clean_graph.dot")
    shutil.copy2(input_data, work_dir / "input_data.json")


def clear_curation_patch(out_dir: Path) -> None:
    for name in (
        "gpt55_semantic_root_patch.json",
        "gpt55_semantic_root_graph.json",
        "gpt55_semantic_root_graph.dot",
        "gpt55_semantic_root_raw_response.txt",
        "gpt55_semantic_root_raw_response_primary.txt",
        "report.json",
        "README.md",
    ):
        path = out_dir / name
        if path.exists():
            path.unlink()
    for path in out_dir.glob("gpt55_semantic_root_raw_response_*.txt"):
        path.unlink()


def write_graph_spec_outputs(prefix: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    write_json(prefix.with_suffix(".json"), spec)
    dot = graph_spec_to_dot(spec)
    prefix.with_suffix(".dot").write_text(dot, encoding="utf-8")
    parseable, strict_ok, strict_valid, strict_issues = assess_dot_quality(dot, mode="strict")
    return {
        "json": str(prefix.with_suffix(".json")),
        "dot": str(prefix.with_suffix(".dot")),
        "node_count": len(spec.get("nodes", [])),
        "edge_count": len(spec.get("edges", [])),
        "parseable": parseable,
        "strict_ok": strict_ok,
        "strict_valid": strict_valid,
        "strict_issues": strict_issues[:20],
    }


def prune_failed_target_graph(
    graph_dot: Path,
    failed_target: str,
    out_dir: Path,
    *,
    suffix: str = "",
) -> Dict[str, Any]:
    dot_text = graph_dot.read_text(encoding="utf-8")
    spec, issues = dot_to_graph_spec(dot_text)
    if spec is None:
        raise RuntimeError(f"cannot parse graph for pruning: {issues}")
    edges = [
        {
            "source": str(edge.get("source", "")),
            "target": str(edge.get("target", "")),
            "type": str(edge.get("type", "")),
        }
        for edge in spec.get("edges", [])
        if isinstance(edge, dict)
    ]
    outgoing: Dict[str, List[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge["source"], []).append(edge["target"])
    to_remove = {failed_target}
    stack = list(outgoing.get(failed_target, []))
    while stack:
        node = stack.pop()
        if node == "NROOT" or node in to_remove:
            continue
        to_remove.add(node)
        stack.extend(outgoing.get(node, []))

    kept_edges = [
        edge
        for edge in edges
        if edge["source"] not in to_remove
        and edge["target"] not in to_remove
        and edge["target"] != "NROOT"
    ]
    pre_root_terminal_nodes = sorted(
        {edge["target"] for edge in kept_edges} - {edge["source"] for edge in kept_edges}
    )
    if not pre_root_terminal_nodes:
        raise RuntimeError(f"pruning {failed_target} leaves no terminal reasoning nodes")

    node_by_id = {
        str(node.get("id")): dict(node)
        for node in spec.get("nodes", [])
        if isinstance(node, dict) and node.get("id") and str(node.get("id")) not in to_remove
    }
    node_by_id["NROOT"] = node_by_id.get("NROOT") or {
        "id": "NROOT",
        "source": [0, 0, 0],
        "text": "Paper-level scientific research proposal synthesized from retained validated reasoning conclusions.",
    }
    node_by_id["NROOT_COMMON"] = node_by_id.get("NROOT_COMMON") or {
        "id": "NROOT_COMMON",
        "source": [0, 0, 0],
        "text": "The remaining validated terminal conclusions jointly motivate the paper-level research proposal.",
    }
    root_edges = [
        {"source": node_id, "target": "NROOT", "type": "induction-case"}
        for node_id in pre_root_terminal_nodes
        if node_id != "NROOT"
    ]
    root_edges.append({"source": "NROOT_COMMON", "target": "NROOT", "type": "induction-common"})
    final_edges = kept_edges + root_edges
    used = sorted({edge["source"] for edge in final_edges} | {edge["target"] for edge in final_edges})
    base_curation = spec.get("curation", {}) if isinstance(spec.get("curation"), dict) else {}
    previous_pruned = set(base_curation.get("pruned_nodes_cumulative") or base_curation.get("pruned_nodes") or [])
    pruned_nodes = sorted(to_remove)
    prune_history = list(base_curation.get("prune_history", []))
    prune_history.append({"failed_target": failed_target, "pruned_nodes": pruned_nodes})

    pruned_spec = {
        "paper_id": spec.get("paper_id", ""),
        "root": "NROOT",
        "nodes": [node_by_id[node_id] for node_id in used if node_id in node_by_id],
        "edges": final_edges,
        "curation": {
            **base_curation,
            "policy": "gpt55_eval_feedback_repair_pruned_after_judge_failure",
            "terminal_nodes": ["NROOT"],
            "pruned_failed_target": failed_target,
            "pruned_nodes": pruned_nodes,
            "pruned_nodes_cumulative": sorted(previous_pruned | to_remove),
            "prune_history": prune_history,
            "pre_root_terminal_nodes_after_prune": pre_root_terminal_nodes,
        },
    }
    safe_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", suffix) if suffix else ""
    prefix = out_dir / f"gpt55_semantic_root_graph_pruned{safe_suffix}"
    info = write_graph_spec_outputs(prefix, pruned_spec)
    if not info["strict_valid"]:
        raise RuntimeError(f"pruned graph is not strict valid: {info['strict_issues']}")
    info["pruned_nodes"] = pruned_nodes
    info["pruned_nodes_cumulative"] = sorted(previous_pruned | to_remove)
    info["prune_history"] = prune_history
    info["pre_root_terminal_nodes_after_prune"] = pre_root_terminal_nodes
    return info


def graph_node_ids(graph_dot: Path) -> set[str]:
    spec, issues = dot_to_graph_spec(graph_dot.read_text(encoding="utf-8"))
    if spec is None:
        raise RuntimeError(f"cannot parse graph for node-id audit: {issues}")
    node_ids = {
        str(node.get("id", ""))
        for node in spec.get("nodes", [])
        if isinstance(node, dict) and node.get("id")
    }
    for edge in spec.get("edges", []):
        if not isinstance(edge, dict):
            continue
        if edge.get("source"):
            node_ids.add(str(edge["source"]))
        if edge.get("target"):
            node_ids.add(str(edge["target"]))
    return node_ids


def graph_reasoning_targets(graph_dot: Path) -> set[str]:
    spec, issues = dot_to_graph_spec(graph_dot.read_text(encoding="utf-8"))
    if spec is None:
        raise RuntimeError(f"cannot parse graph for reasoning-target audit: {issues}")
    return {
        str(edge.get("target", ""))
        for edge in spec.get("edges", [])
        if isinstance(edge, dict) and edge.get("target") and str(edge.get("target")) != "NROOT"
    }


def reasoning_ids_for_node_ids(packet: Dict[str, Any], node_ids: set[str]) -> List[int]:
    reasoning_ids: set[int] = set()
    for node_id in node_ids:
        match = re.fullmatch(r"R(\d+)", node_id)
        if match:
            reasoning_ids.add(int(match.group(1)))
    target_to_reasoning_id = {
        str(step.get("target_node", "")): int(step["reasoning_id"])
        for step in packet.get("steps", [])
        if step.get("reasoning_id") is not None
    }
    for node_id in node_ids:
        if node_id in target_to_reasoning_id:
            reasoning_ids.add(target_to_reasoning_id[node_id])
    return sorted(reasoning_ids)


def repaired_reasoning_ids_from_node_ids(node_ids: set[str]) -> List[int]:
    reasoning_ids: set[int] = set()
    for node_id in node_ids:
        match = re.fullmatch(r"R(\d+)", str(node_id))
        if match:
            reasoning_ids.add(int(match.group(1)))
    return sorted(reasoning_ids)


def normalize_reasoning_ids(items: Any) -> List[int]:
    if not isinstance(items, list):
        return []
    ids: set[int] = set()
    for item in items:
        value = item.get("reasoning_id") if isinstance(item, dict) else item
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(ids)


def build_repair_preflight(
    packet: Dict[str, Any],
    original_summary: Dict[str, Any],
    *,
    repair_rejected: bool,
    min_anchor_correct_ratio: float,
    max_repair_candidate_ratio: float,
    min_raw_cg: float,
) -> Dict[str, Any]:
    kept_ids = list(packet["deterministic_vote_kept_baseline"]["kept_reasoning_ids"])
    dropped_ids = list(packet["deterministic_vote_kept_baseline"]["dropped_reasoning_ids"])
    raw_total = int(original_summary.get("total_reasoning_steps", 0) or len(packet.get("steps", [])) or 0)
    correct_count = len(kept_ids)
    dropped_count = len(dropped_ids)
    anchor_ratio = correct_count / raw_total if raw_total else 0.0
    repair_candidate_ratio = dropped_count / raw_total if raw_total else 0.0
    raw_cg = float(original_summary.get("CG", 0.0) or 0.0)

    gate = {
        "status": "pass",
        "decision": "run_repair",
        "reason": "raw graph has enough evaluator-accepted anchors for PEARL edge repair",
        "repair_rejected": repair_rejected,
        "raw_total_reasoning_steps": raw_total,
        "initial_kept_reasoning_units": correct_count,
        "dropped_reasoning_units": dropped_count,
        "initial_anchor_ratio": anchor_ratio,
        "repair_candidate_ratio": repair_candidate_ratio,
        "raw_CG": raw_cg,
        "thresholds": {
            "min_anchor_correct_ratio": min_anchor_correct_ratio,
            "max_repair_candidate_ratio": max_repair_candidate_ratio,
            "min_raw_cg": min_raw_cg,
        },
        "metric_note": (
            "Gate uses ratios, not absolute failed-node counts, so large and small graphs are judged "
            "by comparable repair burden."
        ),
    }

    if raw_total <= 0:
        gate.update(
            {
                "status": "empty_raw_graph_regenerate",
                "decision": "regenerate",
                "reason": "raw graph has no evaluator-visible reasoning steps",
            }
        )
    elif correct_count == 0:
        gate.update(
            {
                "status": "no_anchor_regenerate",
                "decision": "regenerate",
                "reason": "raw graph has no majority-correct reasoning unit for PEARL to anchor repair",
            }
        )
    elif anchor_ratio + 1e-12 < min_anchor_correct_ratio:
        gate.update(
            {
                "status": "low_anchor_regenerate",
                "decision": "regenerate",
                "reason": (
                    "majority-correct anchor ratio is below the configured threshold; "
                    "edge repair would be mostly graph invention"
                ),
            }
        )
    elif repair_rejected and repair_candidate_ratio - 1e-12 > max_repair_candidate_ratio:
        gate.update(
            {
                "status": "repair_budget_risk",
                "decision": "skip_edge_repair",
                "reason": (
                    "rejected-step ratio is above the configured threshold; send to regeneration "
                    "or a separate expensive repair queue"
                ),
            }
        )
    elif raw_cg + 1e-12 < min_raw_cg:
        gate.update(
            {
                "status": "low_raw_cg_regenerate",
                "decision": "regenerate",
                "reason": "raw content grounding is below the configured threshold",
            }
        )

    return gate


def run_curation(
    run_dir: Path,
    out_dir: Path,
    env: Dict[str, str],
    timeout: int,
    max_tokens: int,
    *,
    eval_dir: Optional[Path] = None,
    repair_rejected: bool = False,
    gpt_retries: int = 2,
    gpt_retry_sleep: float = 8.0,
    gpt_transport: str = "curl",
    gpt_stream: bool = True,
) -> Dict[str, Any]:
    existing_report = out_dir / "report.json"
    if existing_report.exists():
        report = read_json(existing_report, {})
        report_key = "gpt55_semantic_repair_root" if repair_rejected else "gpt55_semantic_root"
        semantic = report.get(report_key, {})
        graph_dot = PROJECT_ROOT / str(semantic.get("dot", ""))
        if (
            semantic.get("patch_parsed")
            and not semantic.get("patch_issues")
            and semantic.get("strict_valid")
            and graph_dot.exists()
        ):
            print(f"reuse existing curation: {existing_report}", flush=True)
            return report

    cmd = [
        sys.executable,
        "scripts/curate_graph_from_vote_results_gpt55.py",
        "--run-dir",
        str(run_dir),
        "--out-dir",
        str(out_dir),
        "--call-gpt",
        "--semantic-root",
        "--compact-root-prompt",
        "--timeout",
        str(timeout),
        "--max-tokens",
        str(max_tokens),
        "--gpt-retries",
        str(gpt_retries),
        "--gpt-retry-sleep",
        str(gpt_retry_sleep),
        "--gpt-transport",
        gpt_transport,
    ]
    cmd.append("--gpt-stream" if gpt_stream else "--no-gpt-stream")
    if eval_dir is not None:
        cmd.extend(["--eval-dir", str(eval_dir)])
    if repair_rejected:
        cmd.append("--repair-rejected")
    proc = run_cmd(cmd, env=env, cwd=PROJECT_ROOT)
    (out_dir / "curation_stdout.txt").write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"curation failed for {run_dir}: {proc.stdout[-4000:]}")
    report = read_json(out_dir / "report.json", {})
    report_key = "gpt55_semantic_repair_root" if repair_rejected else "gpt55_semantic_root"
    semantic = report.get(report_key, {})
    if not semantic.get("patch_parsed"):
        raise RuntimeError(f"GPT-5.5 patch did not parse for {run_dir}: {semantic}")
    if semantic.get("patch_issues"):
        raise RuntimeError(f"GPT-5.5 patch issues for {run_dir}: {semantic.get('patch_issues')}")
    if not semantic.get("strict_valid"):
        raise RuntimeError(f"semantic-root graph is not strict valid for {run_dir}: {semantic.get('strict_issues')}")
    return report


def run_fixed_eval(work_dir: Path, anchor_json: Path, env: Dict[str, str], inner_workers: int) -> Dict[str, Any]:
    clean_eval = latest_clean_eval_dir(work_dir)
    if clean_eval is not None:
        return summarize_eval(clean_eval)

    eval_env = dict(env)
    eval_env["EVAL_INNER_MAX_WORKERS"] = str(inner_workers)
    cmd = [
        sys.executable,
        "scripts/evaluate_fixed_anchor_single.py",
        "--work-dir",
        str(work_dir),
        "--anchor-json",
        str(anchor_json),
    ]
    proc = run_cmd(cmd, env=eval_env, cwd=PROJECT_ROOT)
    (work_dir / "evaluate_fixed_anchor_stdout.txt").write_text(proc.stdout, encoding="utf-8")
    clean_eval = latest_clean_eval_dir(work_dir)
    if clean_eval is not None:
        return summarize_eval(clean_eval)

    dirty_eval = latest_eval_result_dir(work_dir)
    if dirty_eval is None:
        raise RuntimeError(f"evaluation produced no result for {work_dir}: {proc.stdout[-4000:]}")

    repair_report = work_dir / "judge_repair_report.json"
    repair_cmd = [
        sys.executable,
        "scripts/repair_judge_error_votes.py",
        "--single-eval-dir",
        str(dirty_eval),
        "--env-file",
        "/dev/null",
        "--report-file",
        str(repair_report),
    ]
    repair_proc = run_cmd(repair_cmd, env=eval_env, cwd=PROJECT_ROOT)
    (work_dir / "repair_judge_error_votes_stdout.txt").write_text(repair_proc.stdout, encoding="utf-8")
    if repair_proc.returncode != 0:
        raise RuntimeError(f"judge repair failed for {dirty_eval}: {repair_proc.stdout[-4000:]}")
    if not is_eval_dir_clean(dirty_eval):
        raise RuntimeError(f"evaluation dir remains dirty after repair: {dirty_eval}")
    return summarize_eval(dirty_eval)


def latest_stable_eval_dir(work_dir: Path) -> Optional[Path]:
    eval_root = work_dir / "stable_evaluation_outputs"
    if not eval_root.exists():
        return None
    candidates = sorted(
        [path for path in eval_root.glob("*") if (path / "evaluation_results.json").exists()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        result = read_json(candidate / "evaluation_results.json", {})
        if result.get("clean") is not True:
            continue
        if result.get("coverage_algorithm_version") == coverage_algorithm_version():
            return candidate
        refreshed = refresh_stable_eval_coverage(candidate)
        if refreshed is not None:
            refreshed_result = read_json(candidate / "evaluation_results.json", {})
            if refreshed_result.get("coverage_algorithm_version") == coverage_algorithm_version():
                return candidate
    return None


def _make_existing_graph_evaluator(work_dir: Path, eval_dir: Path, entities: List[str]) -> GraphEvaluator:
    graph_evaluator = GraphEvaluator.__new__(GraphEvaluator)
    graph_evaluator.output_dir = work_dir
    graph_evaluator.graph_file = work_dir / "final_clean_graph.dot"
    graph_evaluator.input_data_file = work_dir / "input_data.json"
    graph_evaluator.eval_dir = eval_dir
    (eval_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (eval_dir / "responses").mkdir(parents=True, exist_ok=True)
    (eval_dir / "coverage").mkdir(parents=True, exist_ok=True)
    (eval_dir / "accuracy").mkdir(parents=True, exist_ok=True)
    graph_evaluator.standard_edge_types = {
        "deduction-rule",
        "deduction-case",
        "induction-case",
        "induction-common",
        "abduction-phenomenon",
        "abduction-knowledge",
    }
    graph_evaluator.reasoning_pairs = {
        "deductive": ("deduction-rule", "deduction-case"),
        "inductive": ("induction-case", "induction-common"),
        "abductive": ("abduction-phenomenon", "abduction-knowledge"),
    }
    graph_evaluator.graph = None
    graph_evaluator.input_data = None
    graph_evaluator.core_idea_entities = list(entities)
    return graph_evaluator


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_vote_reuse_root_only_eval(
    work_dir: Path,
    anchor_json: Path,
    packet: Dict[str, Any],
    env: Dict[str, str],
    *,
    judge_error_retries: int,
    judge_error_retry_sleep: float,
) -> Dict[str, Any]:
    """Evaluate only the new semantic-root step and reuse clean original votes.

    The final graph contains retained original majority-correct steps plus a new
    NROOT synthesis step. Re-judging retained steps adds judge variance without
    measuring the curation intervention, so this protocol reuses their original
    clean vote labels and sends only NROOT to the judge set.
    """
    current_graph_hash = graph_hash(work_dir)
    existing = latest_stable_eval_dir(work_dir)
    if existing is not None:
        result = read_json(existing / "evaluation_results.json", {})
        if result.get("graph_hash") == current_graph_hash:
            return summarize_stable_eval(existing)

    anchor = read_json(anchor_json, {})
    entities = list(anchor.get("entities", []))
    core_idea = str(anchor.get("core_idea", ""))
    if not core_idea or not entities:
        raise RuntimeError(f"missing fixed anchor in {anchor_json}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stable_eval_dir = work_dir / "stable_evaluation_outputs" / f"vote_reuse_root_only_{timestamp}"
    graph_evaluator = _make_existing_graph_evaluator(work_dir, stable_eval_dir, entities)
    if not graph_evaluator.load_data():
        raise RuntimeError(f"failed to load graph/input for stable eval: {work_dir}")

    all_steps, valid_steps = graph_evaluator.filter_valid_reasoning_steps()
    if len(all_steps) != len(valid_steps):
        raise RuntimeError(f"final graph has invalid reasoning steps: total={len(all_steps)} valid={len(valid_steps)}")

    prompts = graph_evaluator.generate_reasoning_validation_prompts_for_steps(valid_steps)
    root_prompts = [prompt for prompt in prompts if prompt.get("target_node") == "NROOT"]
    if len(root_prompts) != 1:
        raise RuntimeError(f"expected exactly one NROOT step, found {len(root_prompts)}")
    root_prompt = root_prompts[0]
    root_reasoning_id = int(root_prompt["reasoning_id"])
    partial_caches = load_partial_vote_caches(work_dir)

    correct_by_target: Dict[str, Dict[str, Any]] = {}
    non_reusable_correct_targets: Dict[str, Dict[str, Any]] = {}
    for step in packet["steps"]:
        if step.get("result") != "correct":
            continue
        target = str(step.get("target_node", ""))
        reusable, reason = reusable_correct_step_vote(step)
        if reusable:
            correct_by_target[target] = step
        else:
            non_reusable_correct_targets[target] = {
                "reasoning_id": step.get("reasoning_id"),
                "target_node": target,
                "reason": reason,
                "policy": "rejudge_instead_of_fatal_vote_reuse",
            }

    root_signature = vote_signature(root_prompt)
    cached_root_vote = (
        partial_caches["correct_votes"].get(root_signature)
        or partial_caches["failed_votes"].get(root_signature)
    )
    if cached_root_vote:
        root_vote = write_cached_vote(stable_eval_dir, root_reasoning_id, root_prompt, cached_root_vote)
        root_final_result = str(root_vote.get("final_result", "error")).lower()
        model_responses = root_vote.get("model_responses") or {}
    else:
        cached_model_responses = dict(partial_caches["model_responses"].get(root_signature, {}))
        prompt_text = str(root_prompt.get("validation_prompt", ""))
        if prompt_text:
            prompt_hash_key = f"prompt_file:{hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()}"
            for model_key, response in partial_caches["model_responses"].get(prompt_hash_key, {}).items():
                cached_model_responses.setdefault(model_key, response)
        model_responses, model_results, vote_breakdown, root_final_result, judge_repair_events = (
            validate_prompt_with_error_repair(
                graph_evaluator,
                stable_eval_dir,
                root_reasoning_id,
                root_prompt,
                judge_error_retries=judge_error_retries,
                judge_error_retry_sleep=judge_error_retry_sleep,
                cached_model_responses=cached_model_responses,
            )
        )
        root_vote = {
            "reasoning_id": root_reasoning_id,
            "target_node": root_prompt["target_node"],
            "source_nodes": root_prompt["source_nodes"],
            "edge_types": root_prompt["edge_types"],
            "actual_edge_types": root_prompt["actual_edge_types"],
            "reasoning_type": root_prompt["reasoning_type"],
            "target_content": root_prompt["target_content"],
            "source_contents": root_prompt["source_contents"],
            "premise_descriptions": root_prompt["premise_descriptions"],
            "model_results": model_results,
            "model_responses": model_responses,
            "vote_breakdown": vote_breakdown,
            "final_result": root_final_result,
            "judge_error_repair_events": judge_repair_events,
        }
        write_json(stable_eval_dir / "responses" / f"reasoning_validation_{root_reasoning_id:03d}_vote_result.json", root_vote)

    if root_final_result == "error":
        root_vote_file = stable_eval_dir / "responses" / f"reasoning_validation_{root_reasoning_id:03d}_vote_result.json"
        write_json(
            stable_eval_dir / "judge_provider_error.json",
            judge_failure_feedback(
                status="judge_provider_error",
                validation_prompt_id=root_reasoning_id,
                target_node=root_prompt["target_node"],
                vote_file=root_vote_file,
                model_errors=compact_model_errors(model_responses),
            ),
        )
        raise RuntimeError(
            "judge_provider_error:"
            f"target={root_prompt['target_node']};"
            f"model_errors={json.dumps(compact_model_errors(model_responses), ensure_ascii=False)[:1200]}"
        )

    if root_final_result != "correct":
        raise RuntimeError(f"NROOT judge vote failed: {json.dumps(root_vote, ensure_ascii=False)[:2000]}")

    reasoning_results: Dict[str, str] = {}
    reused_votes = []
    rejudged_retained_votes = []
    for prompt in prompts:
        idx = int(prompt["reasoning_id"])
        target_node = str(prompt.get("target_node", ""))
        if target_node == "NROOT":
            reasoning_results[str(idx)] = root_final_result
            continue
        reused = correct_by_target.get(target_node)
        if reused is not None:
            reasoning_results[str(idx)] = "correct"
            reused_votes.append(
                {
                    "final_reasoning_id": idx,
                    "target_node": target_node,
                    "source_reasoning_id": reused.get("reasoning_id"),
                    "source_result": reused.get("result"),
                }
            )
            continue
        if target_node not in non_reusable_correct_targets:
            raise RuntimeError(f"final graph contains non-reused non-root target: {target_node}")
        signature = vote_signature(prompt)
        cached_vote = partial_caches["correct_votes"].get(signature)
        if cached_vote and str(cached_vote.get("final_result", "")).lower() == "correct":
            write_cached_vote(stable_eval_dir, idx, prompt, cached_vote)
            reasoning_results[str(idx)] = "correct"
            rejudged_retained_votes.append(
                {
                    "final_reasoning_id": idx,
                    "target_node": target_node,
                    "source_result": "cached_correct_vote_after_non_reusable_source_vote",
                    "source_vote_file": cached_vote.get("vote_file"),
                }
            )
            continue
        cached_model_responses = dict(partial_caches["model_responses"].get(signature, {}))
        prompt_text = str(prompt.get("validation_prompt", ""))
        if prompt_text:
            prompt_hash_key = f"prompt_file:{hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()}"
            for model_key, response in partial_caches["model_responses"].get(prompt_hash_key, {}).items():
                cached_model_responses.setdefault(model_key, response)
        model_responses, model_results, vote_breakdown, final_result, repair_events = (
            validate_prompt_with_error_repair(
                graph_evaluator,
                stable_eval_dir,
                idx,
                prompt,
                judge_error_retries=judge_error_retries,
                judge_error_retry_sleep=judge_error_retry_sleep,
                cached_model_responses=cached_model_responses,
            )
        )
        vote = {
            "reasoning_id": idx,
            "target_node": prompt["target_node"],
            "source_nodes": prompt["source_nodes"],
            "edge_types": prompt["edge_types"],
            "actual_edge_types": prompt["actual_edge_types"],
            "reasoning_type": prompt["reasoning_type"],
            "target_content": prompt["target_content"],
            "source_contents": prompt["source_contents"],
            "premise_descriptions": prompt["premise_descriptions"],
            "model_results": model_results,
            "model_responses": model_responses,
            "vote_breakdown": vote_breakdown,
            "final_result": final_result,
            "judge_error_repair_events": repair_events,
        }
        vote_file = stable_eval_dir / "responses" / f"reasoning_validation_{idx:03d}_vote_result.json"
        write_json(vote_file, vote)
        if final_result == "error":
            write_json(
                stable_eval_dir / "judge_provider_error.json",
                judge_failure_feedback(
                    status="judge_provider_error",
                    validation_prompt_id=idx,
                    target_node=target_node,
                    vote_file=vote_file,
                    model_errors=compact_model_errors(model_responses),
                ),
            )
            raise RuntimeError(
                "judge_provider_error:"
                f"target={target_node};"
                f"model_errors={json.dumps(compact_model_errors(model_responses), ensure_ascii=False)[:1200]}"
            )
        if final_result != "correct":
            raise RuntimeError(f"retained step rejudge failed: {json.dumps(vote, ensure_ascii=False)[:2000]}")
        reasoning_results[str(idx)] = "correct"
        rejudged_retained_votes.append(
            {
                "final_reasoning_id": idx,
                "target_node": target_node,
                "source_result": "rejudged_after_non_reusable_source_vote",
                "source_reason": non_reusable_correct_targets[target_node]["reason"],
                "vote_file": str(vote_file),
            }
        )

    coverage_rate = graph_evaluator.calculate_entity_coverage_from_correct_reasoning(reasoning_results)
    coverage_result = {
        "coverage_rate": coverage_rate / 100,
        "total_entities": len(entities),
        "covered_entities": int(len(entities) * coverage_rate / 100),
    }
    accuracy_result = graph_evaluator.calculate_accuracy_score(reasoning_results)
    result = {
        "timestamp": datetime.now().isoformat(),
        "output_dir": str(work_dir),
        "core_idea": core_idea,
        "entities": entities,
        "protocol": "vote_reuse_root_only",
        "graph_hash": current_graph_hash,
        "coverage_algorithm_version": coverage_algorithm_version(),
        "protocol_note": (
            "Reuse original clean majority-correct votes for retained raw reasoning steps; "
            "judge only the newly introduced semantic NROOT synthesis step."
        ),
        "clean": all(str(value).lower() != "error" for value in reasoning_results.values()),
        "root_reasoning_id": root_reasoning_id,
        "reused_votes": reused_votes,
        "rejudged_retained_votes": rejudged_retained_votes,
        "non_reusable_correct_targets_rejudged": list(non_reusable_correct_targets.values()),
        "root_vote_file": str(stable_eval_dir / "responses" / f"reasoning_validation_{root_reasoning_id:03d}_vote_result.json"),
        "coverage": coverage_result,
        "accuracy": accuracy_result,
        "evaluation_summary": {
            "entity_coverage_score": coverage_result["coverage_rate"],
            "accuracy_score": accuracy_result["accuracy_score"],
            "total_entities": len(entities),
            "covered_entities": coverage_result["covered_entities"],
            "total_reasoning_steps": accuracy_result["total_steps"],
            "valid_reasoning_steps": accuracy_result["valid_steps"],
        },
    }
    write_json(stable_eval_dir / "evaluation_results.json", result)
    _write_text(
        stable_eval_dir / "evaluation_summary.txt",
        (
            "Stable Vote-Reuse Evaluation Summary\n"
            "==================================================\n"
            f"Output Dir: {work_dir}\n"
            f"Eval Dir: {stable_eval_dir}\n"
            "Protocol: reuse retained clean raw votes; judge only NROOT.\n\n"
            f"- Content Grounding (CG): {coverage_result['coverage_rate']:.2%} "
            f"({coverage_result['covered_entities']}/{coverage_result['total_entities']})\n"
            f"- Reasoning Accuracy (REA): {accuracy_result['accuracy_score']:.2%} "
            f"({accuracy_result['valid_steps']}/{accuracy_result['total_steps']})\n"
        ),
    )
    return summarize_stable_eval(stable_eval_dir)


def run_vote_reuse_repair_root_eval(
    work_dir: Path,
    anchor_json: Path,
    packet: Dict[str, Any],
    env: Dict[str, str],
    *,
    changed_raw_targets: Optional[set[str]] = None,
    accepted_vote_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    judge_error_retries: int,
    judge_error_retry_sleep: float,
) -> Dict[str, Any]:
    """Reuse original correct votes, judge repaired R* units and NROOT.

    This protocol is for the SFT data-construction path. It uses evaluator
    feedback to repair rejected units, so those repaired units cannot inherit
    old wrong votes; they must be judged again. Retained original correct units
    still reuse their clean votes to avoid unnecessary judge variance.
    """
    current_graph_hash = graph_hash(work_dir)
    eval_root = work_dir / "stable_evaluation_outputs"
    if eval_root.exists():
        candidates = sorted(
            [
                path
                for path in eval_root.glob("vote_reuse_repair_root_*")
                if (path / "evaluation_results.json").exists()
            ],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            result = read_json(candidate / "evaluation_results.json", {})
            if result.get("clean") is True and result.get("graph_hash") == current_graph_hash:
                return summarize_stable_eval(candidate)

    anchor = read_json(anchor_json, {})
    entities = list(anchor.get("entities", []))
    core_idea = str(anchor.get("core_idea", ""))
    if not core_idea or not entities:
        raise RuntimeError(f"missing fixed anchor in {anchor_json}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stable_eval_dir = work_dir / "stable_evaluation_outputs" / f"vote_reuse_repair_root_{timestamp}"
    graph_evaluator = _make_existing_graph_evaluator(work_dir, stable_eval_dir, entities)
    if not graph_evaluator.load_data():
        raise RuntimeError(f"failed to load graph/input for stable eval: {work_dir}")

    all_steps, valid_steps = graph_evaluator.filter_valid_reasoning_steps()
    if len(all_steps) != len(valid_steps):
        raise RuntimeError(f"final graph has invalid reasoning steps: total={len(all_steps)} valid={len(valid_steps)}")

    prompts = graph_evaluator.generate_reasoning_validation_prompts_for_steps(valid_steps)
    prompts_by_id = {int(prompt["reasoning_id"]): prompt for prompt in prompts}
    target_by_reasoning_id = {
        idx: str(target_node)
        for idx, (target_node, _source_nodes, _edge_types) in enumerate(valid_steps, start=1)
    }
    changed_raw_targets = changed_raw_targets or set()

    correct_by_target: Dict[str, Dict[str, Any]] = {}
    non_reusable_correct_targets: Dict[str, Dict[str, Any]] = {}
    for step in packet["steps"]:
        if step.get("result") != "correct":
            continue
        target = str(step.get("target_node", ""))
        reusable, reason = reusable_correct_step_vote(step)
        if reusable:
            correct_by_target[target] = step
        else:
            non_reusable_correct_targets[target] = {
                "reasoning_id": step.get("reasoning_id"),
                "target_node": target,
                "reason": reason,
                "policy": "rejudge_instead_of_fatal_vote_reuse",
            }

    reasoning_results: Dict[str, str] = {}
    reused_votes = []
    judged_votes = []
    failed_vote: Optional[Dict[str, Any]] = None
    failed_vote_error_message = ""
    judge_error_repair_events: List[Dict[str, Any]] = []
    accepted_vote_cache = accepted_vote_cache if accepted_vote_cache is not None else {}
    partial_caches = load_partial_vote_caches(work_dir)
    targets_to_judge = {
        rid: target
        for rid, target in target_by_reasoning_id.items()
        if target == "NROOT"
        or target.startswith("R")
        or target in changed_raw_targets
        or target not in correct_by_target
        or target in non_reusable_correct_targets
    }

    for reasoning_id, prompt in sorted(prompts_by_id.items()):
        target_node = str(prompt.get("target_node", ""))
        if reasoning_id not in targets_to_judge:
            reused = correct_by_target.get(target_node)
            if reused is None:
                raise RuntimeError(f"final graph contains non-reused non-judged target: {target_node}")
            reasoning_results[str(reasoning_id)] = "correct"
            reused_votes.append(
                {
                    "final_reasoning_id": reasoning_id,
                    "target_node": target_node,
                    "source_reasoning_id": reused.get("reasoning_id"),
                    "source_result": reused.get("result"),
                }
            )
            continue

        signature = vote_signature(prompt)
        cached_vote = accepted_vote_cache.get(signature) or partial_caches["correct_votes"].get(signature)
        if cached_vote and str(cached_vote.get("final_result", "")).lower() == "correct":
            if cached_vote.get("vote"):
                write_cached_vote(stable_eval_dir, reasoning_id, prompt, cached_vote)
            reasoning_results[str(reasoning_id)] = "correct"
            reused_votes.append(
                {
                    "final_reasoning_id": reasoning_id,
                    "target_node": target_node,
                    "source_reasoning_id": cached_vote.get("source_reasoning_id"),
                    "source_result": "cached_correct_repair_vote",
                    "source_vote_file": cached_vote.get("vote_file"),
                    "vote_signature": signature,
                }
            )
            continue

        cached_failed_vote = partial_caches["failed_votes"].get(signature)
        if cached_failed_vote and target_node not in non_reusable_correct_targets:
            vote = write_cached_vote(stable_eval_dir, reasoning_id, prompt, cached_failed_vote)
            vote_file = stable_eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_vote_result.json"
            if failed_vote is None:
                write_json(
                    stable_eval_dir / "repair_feedback.json",
                    judge_failure_feedback(
                        status="judge_failed",
                        validation_prompt_id=reasoning_id,
                        target_node=target_node,
                        vote_file=vote_file,
                        vote=vote,
                        reuse_policy="same_prompt_signature_failed_vote_cache",
                    ),
                )
            reasoning_results[str(reasoning_id)] = str(vote.get("final_result") or "wrong").lower()
            judged_votes.append(
                {
                    "final_reasoning_id": reasoning_id,
                    "target_node": target_node,
                    "final_result": reasoning_results[str(reasoning_id)],
                    "vote_signature": signature,
                    "vote_file": str(vote_file),
                    "cached_failed_vote": True,
                }
            )
            if failed_vote is None:
                failed_vote = vote
                failed_vote_error_message = f"repaired/root judge vote failed: {json.dumps(vote, ensure_ascii=False)[:2000]}"
            continue

        cached_model_responses = dict(partial_caches["model_responses"].get(signature, {}))
        prompt_text = str(prompt.get("validation_prompt", ""))
        if prompt_text:
            prompt_hash_key = f"prompt_file:{hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()}"
            for model_key, response in partial_caches["model_responses"].get(prompt_hash_key, {}).items():
                cached_model_responses.setdefault(model_key, response)

        model_responses, model_results, vote_breakdown, final_result, repair_events = (
            validate_prompt_with_error_repair(
                graph_evaluator,
                stable_eval_dir,
                reasoning_id,
                prompt,
                judge_error_retries=judge_error_retries,
                judge_error_retry_sleep=judge_error_retry_sleep,
                cached_model_responses=cached_model_responses,
            )
        )
        judge_error_repair_events.extend(repair_events)
        vote = {
            "reasoning_id": reasoning_id,
            "target_node": prompt["target_node"],
            "source_nodes": prompt["source_nodes"],
            "edge_types": prompt["edge_types"],
            "actual_edge_types": prompt["actual_edge_types"],
            "reasoning_type": prompt["reasoning_type"],
            "target_content": prompt["target_content"],
            "source_contents": prompt["source_contents"],
            "premise_descriptions": prompt["premise_descriptions"],
            "model_results": model_results,
            "model_responses": model_responses,
            "vote_breakdown": vote_breakdown,
            "final_result": final_result,
            "judge_error_repair_events": repair_events,
        }
        write_json(stable_eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_vote_result.json", vote)
        if final_result == "error":
            vote_file = stable_eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_vote_result.json"
            write_json(
                stable_eval_dir / "judge_provider_error.json",
                judge_failure_feedback(
                    status="judge_provider_error",
                    validation_prompt_id=reasoning_id,
                    target_node=target_node,
                    vote_file=vote_file,
                    model_errors=compact_model_errors(model_responses),
                ),
            )
            raise RuntimeError(
                "judge_provider_error:"
                f"target={target_node};"
                f"model_errors={json.dumps(compact_model_errors(model_responses), ensure_ascii=False)[:1200]}"
            )
        if final_result != "correct":
            vote_file = stable_eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_vote_result.json"
            if failed_vote is None:
                write_json(
                    stable_eval_dir / "repair_feedback.json",
                    judge_failure_feedback(
                        status="judge_failed",
                        validation_prompt_id=reasoning_id,
                        target_node=target_node,
                        vote_file=vote_file,
                        vote=vote,
                    ),
                )
            reasoning_results[str(reasoning_id)] = final_result
            judged_votes.append(
                {
                    "final_reasoning_id": reasoning_id,
                    "target_node": target_node,
                    "final_result": final_result,
                    "vote_signature": signature,
                    "vote_file": str(vote_file),
                    "judge_error_repair_events": repair_events,
                }
            )
            if failed_vote is None:
                failed_vote = vote
                failed_vote_error_message = f"repaired/root judge vote failed: {json.dumps(vote, ensure_ascii=False)[:2000]}"
            continue
        accepted_vote_cache[signature] = {
            "final_result": final_result,
            "target_node": target_node,
            "source_reasoning_id": reasoning_id,
            "vote_file": str(stable_eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_vote_result.json"),
        }
        reasoning_results[str(reasoning_id)] = final_result
        judged_votes.append(
            {
                "final_reasoning_id": reasoning_id,
                "target_node": target_node,
                "final_result": final_result,
                "vote_signature": signature,
                "vote_file": str(stable_eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_vote_result.json"),
                "judge_error_repair_events": repair_events,
            }
        )

    coverage_rate = graph_evaluator.calculate_entity_coverage_from_correct_reasoning(reasoning_results)
    coverage_result = {
        "coverage_rate": coverage_rate / 100,
        "total_entities": len(entities),
        "covered_entities": int(len(entities) * coverage_rate / 100),
    }
    accuracy_result = graph_evaluator.calculate_accuracy_score(reasoning_results)
    result = {
        "timestamp": datetime.now().isoformat(),
        "output_dir": str(work_dir),
        "core_idea": core_idea,
        "entities": entities,
        "protocol": "vote_reuse_repair_root",
        "graph_hash": current_graph_hash,
        "coverage_algorithm_version": coverage_algorithm_version(),
        "protocol_note": (
            "Reuse original clean majority-correct votes for retained raw reasoning steps; "
            "judge evaluator-feedback repaired R* units and the semantic NROOT synthesis step."
        ),
        "clean": failed_vote is None and all(str(value).lower() != "error" for value in reasoning_results.values()),
        "metric_bearing": all(str(value).lower() != "error" for value in reasoning_results.values()),
        "accepted_by_repair_root_judge": failed_vote is None,
        "first_failed_vote": failed_vote,
        "reused_votes": reused_votes,
        "judged_votes": judged_votes,
        "judge_error_repair_events": judge_error_repair_events,
        "non_reusable_correct_targets_rejudged": list(non_reusable_correct_targets.values()),
        "coverage": coverage_result,
        "accuracy": accuracy_result,
        "evaluation_summary": {
            "entity_coverage_score": coverage_result["coverage_rate"],
            "accuracy_score": accuracy_result["accuracy_score"],
            "total_entities": len(entities),
            "covered_entities": coverage_result["covered_entities"],
            "total_reasoning_steps": accuracy_result["total_steps"],
            "valid_reasoning_steps": accuracy_result["valid_steps"],
        },
    }
    write_json(stable_eval_dir / "evaluation_results.json", result)
    _write_text(
        stable_eval_dir / "evaluation_summary.txt",
        (
            "Stable Vote-Reuse Repair+Root Evaluation Summary\n"
            "==================================================\n"
            f"Output Dir: {work_dir}\n"
            f"Eval Dir: {stable_eval_dir}\n"
            "Protocol: reuse retained clean raw votes; judge repaired R* units and NROOT.\n\n"
            f"- Content Grounding (CG): {coverage_result['coverage_rate']:.2%} "
            f"({coverage_result['covered_entities']}/{coverage_result['total_entities']})\n"
            f"- Reasoning Accuracy (REA): {accuracy_result['accuracy_score']:.2%} "
            f"({accuracy_result['valid_steps']}/{accuracy_result['total_steps']})\n"
        ),
    )
    if failed_vote is not None:
        feedback_path = stable_eval_dir / "repair_feedback.json"
        feedback_payload = read_json(feedback_path, {})
        if isinstance(feedback_payload, dict):
            feedback_payload["frontier_metrics"] = {
                "CG": coverage_result["coverage_rate"],
                "REA": accuracy_result["accuracy_score"],
                "covered_entities": coverage_result["covered_entities"],
                "total_entities": coverage_result["total_entities"],
                "valid_reasoning_steps": accuracy_result["valid_steps"],
                "total_reasoning_steps": accuracy_result["total_steps"],
            }
            feedback_payload["thresholds"] = {
                "min_final_CG": 1.0,
                "min_final_REA": 1.0,
                "min_frontier_covered_entities": coverage_result["covered_entities"],
            }
            feedback_payload["coverage_note"] = (
                "This failed candidate is the current repair frontier. The next repair must fix the failed "
                "reasoning target without reducing covered_entities below the frontier count, unless it produces "
                "a different graph that still satisfies final CG=1.0 and final REA=1.0."
            )
            write_json(feedback_path, feedback_payload)
        raise RuntimeError(failed_vote_error_message)
    return summarize_stable_eval(stable_eval_dir)


def evaluate_with_iterative_pruning(
    *,
    report_root: Path,
    ids: Dict[str, str],
    paper: str,
    run_dir: Path,
    original_results: Path,
    out_dir: Path,
    initial_graph_dot: Path,
    packet: Dict[str, Any],
    env: Dict[str, str],
    changed_raw_targets: set[str],
    repair_rejected: bool,
    timeout: int,
    max_tokens: int,
    gpt_retries: int,
    gpt_retry_sleep: float,
    gpt_transport: str,
    gpt_stream: bool,
    max_prune_rounds: int,
    judge_error_retries: int,
    judge_error_retry_sleep: float,
    terminal_preserving_regenerate: bool = False,
    frontier_guard: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Path, Path, List[Dict[str, Any]]]:
    """Evaluate a curated graph, pruning judge-failed repaired nodes until clean."""
    final_graph_dot = initial_graph_dot
    prune_events: List[Dict[str, Any]] = []
    seen_failed_targets: set[str] = set()
    nroot_feedback_regenerated = False
    accepted_vote_cache: Dict[str, Dict[str, Any]] = {}
    feedback_regenerated_targets: set[str] = set()
    round_idx = 0

    while True:
        suffix = "" if round_idx == 0 else f"_pruned_{round_idx:02d}"
        work_dir = (
            report_root
            / "eval_work"
            / ids["model"]
            / paper
            / ids["run_id"]
            / f"semantic_root_fixed_anchor{suffix}"
        )
        prepare_eval_work(final_graph_dot, run_dir / "input_data.json", work_dir)
        anchor_json = work_dir / "fixed_anchor.json"
        make_anchor(original_results, anchor_json)

        cached_feedback_candidates = sorted(
            (work_dir / "stable_evaluation_outputs").glob("vote_reuse_repair_root_*/repair_feedback.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        try:
            if repair_rejected and cached_feedback_candidates:
                raise RuntimeError("reuse cached repair feedback")
            if repair_rejected:
                final_summary = run_vote_reuse_repair_root_eval(
                    work_dir,
                    anchor_json,
                    packet,
                    env,
                    changed_raw_targets=changed_raw_targets,
                    accepted_vote_cache=accepted_vote_cache,
                    judge_error_retries=judge_error_retries,
                    judge_error_retry_sleep=judge_error_retry_sleep,
                )
            else:
                final_summary = run_vote_reuse_root_only_eval(
                    work_dir,
                    anchor_json,
                    packet,
                    env,
                    judge_error_retries=judge_error_retries,
                    judge_error_retry_sleep=judge_error_retry_sleep,
                )
            min_frontier_covered = frontier_min_covered(frontier_guard)
            if (
                repair_rejected
                and min_frontier_covered
                and int(final_summary.get("covered_entities", 0) or 0) < min_frontier_covered
                and not summary_passes_strict_gate(final_summary)
                and "frontier_guard_regression" not in feedback_regenerated_targets
            ):
                feedback_regenerated_targets.add("frontier_guard_regression")
                feedback_payload = read_json(out_dir / "repair_feedback.json", {})
                if not isinstance(feedback_payload, dict):
                    feedback_payload = {}
                feedback_payload.update(
                    {
                        "status": "frontier_coverage_regression",
                        "metric_feedback": (
                            "The latest clean repair removed coverage-bearing reasoning from the audited "
                            "frontier. Repair the failed unit while restoring frontier coverage."
                        ),
                        "final_metrics": final_summary,
                    }
                )
                feedback_payload = merge_frontier_guard_into_feedback(feedback_payload, frontier_guard)
                feedback_dst = out_dir / f"repair_feedback_{len(prune_events) + 1:02d}_frontier_guard.json"
                write_json(feedback_dst, feedback_payload)
                write_json(out_dir / "repair_feedback.json", feedback_payload)
                clear_curation_patch(out_dir)
                curation_report = run_curation(
                    run_dir,
                    out_dir,
                    env,
                    timeout=timeout,
                    max_tokens=max_tokens,
                    repair_rejected=True,
                    gpt_retries=gpt_retries,
                    gpt_retry_sleep=gpt_retry_sleep,
                    gpt_transport=gpt_transport,
                    gpt_stream=gpt_stream,
                )
                semantic = curation_report["gpt55_semantic_repair_root"]
                replacement_dot = PROJECT_ROOT / semantic["dot"]
                if not replacement_dot.exists():
                    replacement_dot = out_dir / "gpt55_semantic_root_graph.dot"
                old_graph_dot = final_graph_dot
                final_graph_dot = replacement_dot
                prune_events.append(
                    {
                        "round": len(prune_events) + 1,
                        "failed_target": "frontier_guard_regression",
                        "feedback": str(feedback_dst),
                        "action": "regenerated_frontier_coverage_preserving_repair",
                        "frontier_guard": normalize_frontier_guard(frontier_guard),
                        "regressed_candidate_graph": str(old_graph_dot),
                        "replacement_graph": str(final_graph_dot),
                    }
                )
                round_idx += 1
                continue
            return final_summary, final_graph_dot, work_dir, prune_events
        except RuntimeError:
            feedback_candidates = cached_feedback_candidates or sorted(
                (work_dir / "stable_evaluation_outputs").glob("vote_reuse_repair_root_*/repair_feedback.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if not repair_rejected or not feedback_candidates:
                raise
            if max_prune_rounds >= 0 and len(prune_events) >= max_prune_rounds:
                raise RuntimeError(
                    "repair_budget_exceeded:"
                    f"max_prune_rounds={max_prune_rounds};"
                    f"last_graph={final_graph_dot};"
                    f"prune_events={json.dumps(prune_events, ensure_ascii=False)[:4000]}"
                )

            feedback = read_json(feedback_candidates[0], {})
            feedback = merge_frontier_guard_into_feedback(feedback if isinstance(feedback, dict) else {}, frontier_guard)
            failed_target = str(feedback.get("failed_target_node", ""))
            if not failed_target.startswith("R"):
                feedback_key = failed_target or "UNKNOWN"
                if (
                    repair_rejected
                    and feedback_key not in feedback_regenerated_targets
                ):
                    feedback_regenerated_targets.add(feedback_key)
                    nroot_feedback_regenerated = True
                    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", feedback_key)
                    feedback_dst = out_dir / f"repair_feedback_{len(prune_events) + 1:02d}_{safe_target}.json"
                    write_json(feedback_dst, feedback)
                    write_json(out_dir / "repair_feedback.json", feedback)
                    clear_curation_patch(out_dir)
                    curation_report = run_curation(
                        run_dir,
                        out_dir,
                        env,
                        timeout=timeout,
                        max_tokens=max_tokens,
                        repair_rejected=True,
                        gpt_retries=gpt_retries,
                        gpt_retry_sleep=gpt_retry_sleep,
                        gpt_transport=gpt_transport,
                        gpt_stream=gpt_stream,
                    )
                    semantic = curation_report["gpt55_semantic_repair_root"]
                    replacement_dot = PROJECT_ROOT / semantic["dot"]
                    if not replacement_dot.exists():
                        replacement_dot = out_dir / "gpt55_semantic_root_graph.dot"
                    final_graph_dot = replacement_dot
                    prune_events.append(
                        {
                            "round": len(prune_events) + 1,
                            "failed_target": failed_target,
                            "feedback": str(feedback_dst),
                            "action": "regenerated_non_r_target_from_judge_feedback",
                            "replacement_graph": str(final_graph_dot),
                        }
                    )
                    round_idx += 1
                    continue
                clear_curation_patch(out_dir)
                raise
            feedback_dst = out_dir / f"repair_feedback_{len(prune_events) + 1:02d}_{failed_target}.json"
            write_json(feedback_dst, feedback)
            latest_feedback_dst = out_dir / "repair_feedback.json"
            write_json(latest_feedback_dst, feedback)

            if failed_target not in feedback_regenerated_targets:
                feedback_regenerated_targets.add(failed_target)
                clear_curation_patch(out_dir)
                curation_report = run_curation(
                    run_dir,
                    out_dir,
                    env,
                    timeout=timeout,
                    max_tokens=max_tokens,
                    repair_rejected=True,
                    gpt_retries=gpt_retries,
                    gpt_retry_sleep=gpt_retry_sleep,
                    gpt_transport=gpt_transport,
                    gpt_stream=gpt_stream,
                )
                semantic = curation_report["gpt55_semantic_repair_root"]
                replacement_dot = PROJECT_ROOT / semantic["dot"]
                if not replacement_dot.exists():
                    replacement_dot = out_dir / "gpt55_semantic_root_graph.dot"
                final_graph_dot = replacement_dot
                prune_events.append(
                    {
                        "round": len(prune_events) + 1,
                        "failed_target": failed_target,
                        "feedback": str(feedback_dst),
                        "action": "regenerated_entity_preserving_repair_from_judge_feedback",
                        "replacement_graph": str(final_graph_dot),
                    }
                )
                round_idx += 1
                continue

            try:
                pruned_info = prune_failed_target_graph(
                    final_graph_dot,
                    failed_target,
                    out_dir,
                    suffix=f"_{len(prune_events) + 1:02d}_{failed_target}",
                )
            except RuntimeError as prune_exc:
                if "leaves no terminal reasoning nodes" not in str(prune_exc):
                    raise
                if terminal_preserving_regenerate:
                    terminal_key = f"terminal_preserving:{failed_target}"
                    if terminal_key not in feedback_regenerated_targets:
                        feedback_regenerated_targets.add(terminal_key)
                        clear_curation_patch(out_dir)
                        curation_report = run_curation(
                            run_dir,
                            out_dir,
                            env,
                            timeout=timeout,
                            max_tokens=max_tokens,
                            repair_rejected=True,
                            gpt_retries=gpt_retries,
                            gpt_retry_sleep=gpt_retry_sleep,
                            gpt_transport=gpt_transport,
                            gpt_stream=gpt_stream,
                        )
                        semantic = curation_report["gpt55_semantic_repair_root"]
                        replacement_dot = PROJECT_ROOT / semantic["dot"]
                        if not replacement_dot.exists():
                            replacement_dot = out_dir / "gpt55_semantic_root_graph.dot"
                        final_graph_dot = replacement_dot
                        prune_events.append(
                            {
                                "round": len(prune_events) + 1,
                                "failed_target": failed_target,
                                "feedback": str(feedback_dst),
                                "action": "regenerated_terminal_preserving_repair_from_judge_feedback",
                                "reason": str(prune_exc),
                                "replacement_graph": str(final_graph_dot),
                            }
                        )
                        round_idx += 1
                        continue
                prune_events.append(
                    {
                        "round": len(prune_events) + 1,
                        "failed_target": failed_target,
                        "feedback": str(feedback_dst),
                        "action": "stop_before_terminal_destroying_prune",
                        "policy": "preserve_last_graph_for_failure_audit",
                        "reason": str(prune_exc),
                        "candidate_graph": str(final_graph_dot),
                    }
                )
                raise RuntimeError(f"no_terminal_after_prune:{prune_exc}") from prune_exc
            if failed_target in seen_failed_targets:
                pruned_info["repeat_failed_target"] = True
                pruned_info["repeat_policy"] = "allow_repeat_prune_within_global_prune_budget"
            seen_failed_targets.add(failed_target)
            final_graph_dot = PROJECT_ROOT / pruned_info["dot"]
            prune_events.append(
                {
                    "round": len(prune_events) + 1,
                    "failed_target": failed_target,
                    "feedback": str(feedback_dst),
                    **pruned_info,
                }
            )
            round_idx += 1


def assign_quality_tier(row: Dict[str, Any]) -> Dict[str, Any]:
    raw_total = int(row.get("original", {}).get("total_reasoning_steps", 0) or 0)
    kept = int(row.get("kept_reasoning_units", 0) or 0)
    repaired = int(row.get("repaired_reasoning_units", 0) or 0)
    retained_correct_ratio = kept / raw_total if raw_total else 0.0
    effective_reasoning_ratio = (kept + repaired) / raw_total if raw_total else 0.0
    final_cg = float(row.get("final", {}).get("CG", 0.0) or 0.0)
    final_rea = float(row.get("final", {}).get("REA", 0.0) or 0.0)
    delta_cg = float(row.get("delta_CG", 0.0) or 0.0)
    delta_rea = float(row.get("delta_REA", 0.0) or 0.0)

    if (
        effective_reasoning_ratio <= 0.0
        or final_cg < 0.80
        or final_rea < 1.0 - 1e-9
        or delta_cg < -1e-9
        or delta_rea < -1e-9
    ):
        tier = "repair"
        rationale = "fails coverage, effective-density, non-regression, or perfect-clean final REA gate"
    elif final_cg >= 0.90 and effective_reasoning_ratio >= 0.40:
        tier = "A_main"
        rationale = "high final coverage and high retained-or-repaired reasoning density"
    elif final_cg >= 0.80 and effective_reasoning_ratio >= 0.20:
        tier = "B_usable"
        rationale = "clean usable graph with moderate retained-or-repaired reasoning density"
    else:
        tier = "C_thin"
        rationale = "clean graph but retained-or-repaired evidence is sparse by ratio"

    return {
        "tier": tier,
        "rationale": rationale,
        "retained_correct_ratio": retained_correct_ratio,
        "effective_reasoning_ratio": effective_reasoning_ratio,
        "repaired_reasoning_units": repaired,
        "retention_metric": QUALITY_TIER_POLICY["retention_metric"],
        "effective_density_metric": QUALITY_TIER_POLICY["effective_density_metric"],
    }


def parse_paper_spec(spec: str, default_model: str = "qwen3_5_397b_a17b") -> Dict[str, str]:
    """Parse `paper` or `model:paper` CLI items."""
    if ":" not in spec:
        return {"model": default_model, "paper": spec}
    model, paper = spec.split(":", 1)
    model = model.strip()
    paper = paper.strip()
    if not model or not paper:
        raise ValueError(f"invalid paper spec: {spec!r}; expected paper or model:paper")
    return {"model": model, "paper": paper}


def default_run_dir(paper: str, model: str = "qwen3_5_397b_a17b") -> Path:
    paper_dir = PROJECT_ROOT / "data" / "teacher_pool" / "model_outputs" / model / paper
    candidates = sorted(path for path in paper_dir.glob("20*") if (path / "final_clean_graph.dot").exists())
    if not candidates:
        raise FileNotFoundError(f"no raw run found for {model}:{paper}")
    return candidates[-1]


def run_one(
    paper_spec: str,
    report_root: Path,
    env: Dict[str, str],
    *,
    timeout: int,
    max_tokens: int,
    inner_workers: int,
    stop_on_regression: bool,
    full_rejudge: bool,
    repair_rejected: bool,
    gpt_retries: int,
    gpt_retry_sleep: float,
    gpt_transport: str,
    gpt_stream: bool,
    max_prune_rounds: int,
    judge_error_retries: int,
    judge_error_retry_sleep: float,
    min_anchor_correct_ratio: float,
    max_repair_candidate_ratio: float,
    min_raw_cg: float,
    min_final_cg: float,
    min_final_rea: float,
    initial_graph_dot: Optional[Path] = None,
    seed_repair_feedback: Optional[Path] = None,
    skip_initial_curation: bool = False,
    terminal_preserving_regenerate: bool = False,
    source_run_dir: Optional[Path] = None,
    source_eval_dir: Optional[Path] = None,
    frontier_guard: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    parsed = parse_paper_spec(paper_spec)
    model = parsed["model"]
    paper = parsed["paper"]
    run_dir = source_run_dir if source_run_dir is not None else default_run_dir(paper, model)
    if not run_dir.is_absolute():
        run_dir = PROJECT_ROOT / run_dir
    eval_dir = source_eval_dir if source_eval_dir is not None else latest_clean_eval_dir(run_dir)
    if eval_dir is not None and not eval_dir.is_absolute():
        eval_dir = PROJECT_ROOT / eval_dir
    if eval_dir is None:
        latest_dirty = latest_eval_dir(run_dir)
        raise RuntimeError(f"source evaluation dir is not clean and cannot be reused: {latest_dirty}")
    original_results = eval_dir / "evaluation_results.json"

    ids = infer_ids(run_dir)
    original_summary = summarize_source_eval_with_current_evaluator(
        run_dir,
        eval_dir,
        report_root / "baseline_recalc" / ids["model"] / paper / ids["run_id"],
    )
    out_dir = report_root / "curated" / ids["model"] / ids["paper_id"] / ids["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    if seed_repair_feedback:
        shutil.copy2(seed_repair_feedback, out_dir / "repair_feedback.json")

    packet, _ = build_packet(run_dir, eval_dir)
    preflight = build_repair_preflight(
        packet,
        original_summary,
        repair_rejected=repair_rejected,
        min_anchor_correct_ratio=min_anchor_correct_ratio,
        max_repair_candidate_ratio=max_repair_candidate_ratio,
        min_raw_cg=min_raw_cg,
    )
    write_json(out_dir / "preflight_gate.json", preflight)
    if preflight["status"] != "pass":
        raise PreflightGateError(
            str(preflight["status"]),
            f"{paper} preflight gate stopped PEARL edge repair: {preflight['reason']}",
            preflight,
        )
    correct_count = int(preflight["initial_kept_reasoning_units"])

    semantic_key = "gpt55_semantic_repair_root" if repair_rejected else "gpt55_semantic_root"
    if skip_initial_curation and initial_graph_dot:
        semantic = {
            "dot": str(initial_graph_dot),
            "json": "",
            "node_count": None,
            "edge_count": None,
        }
        semantic_curation = {}
    else:
        curation_report = run_curation(
            run_dir,
            out_dir,
            env,
            timeout,
            max_tokens,
            eval_dir=eval_dir,
            repair_rejected=repair_rejected,
            gpt_retries=gpt_retries,
            gpt_retry_sleep=gpt_retry_sleep,
            gpt_transport=gpt_transport,
            gpt_stream=gpt_stream,
        )
        semantic = curation_report[semantic_key]
        semantic_json = read_json(PROJECT_ROOT / semantic.get("json", ""), {})
        semantic_curation = semantic_json.get("curation", {}) if isinstance(semantic_json, dict) else {}
    repaired_reasoning_ids = normalize_reasoning_ids(semantic_curation.get("repaired_reasoning_ids", []))
    gpt_dropped_reasoning_ids = normalize_reasoning_ids(semantic_curation.get("dropped_reasoning_ids", []))
    rewritten_edges_using_repaired_nodes = list(
        semantic_curation.get("rewritten_edges_using_repaired_nodes", []) or []
    )
    changed_raw_targets = {
        str(item.get("target", ""))
        for item in rewritten_edges_using_repaired_nodes
        if isinstance(item, dict) and item.get("target")
    }
    graph_dot = initial_graph_dot if initial_graph_dot else PROJECT_ROOT / semantic["dot"]
    if graph_dot and not graph_dot.is_absolute():
        graph_dot = PROJECT_ROOT / graph_dot
    if not graph_dot.exists():
        graph_dot = out_dir / "gpt55_semantic_root_graph.dot"
    final_summary, final_graph_dot, work_dir, prune_events = evaluate_with_iterative_pruning(
        report_root=report_root,
        ids=ids,
        paper=paper,
        run_dir=run_dir,
        original_results=original_results,
        out_dir=out_dir,
        initial_graph_dot=graph_dot,
        packet=packet,
        env=env,
        changed_raw_targets=changed_raw_targets,
        repair_rejected=repair_rejected,
        timeout=timeout,
        max_tokens=max_tokens,
        gpt_retries=gpt_retries,
        gpt_retry_sleep=gpt_retry_sleep,
        gpt_transport=gpt_transport,
        gpt_stream=gpt_stream,
        max_prune_rounds=max_prune_rounds,
        judge_error_retries=judge_error_retries,
        judge_error_retry_sleep=judge_error_retry_sleep,
        terminal_preserving_regenerate=terminal_preserving_regenerate,
        frontier_guard=frontier_guard,
    )
    final_node_ids = graph_node_ids(final_graph_dot)
    graph_repaired_reasoning_ids = repaired_reasoning_ids_from_node_ids(final_node_ids)
    repaired_reasoning_ids = sorted(set(repaired_reasoning_ids) | set(graph_repaired_reasoning_ids))
    final_reasoning_targets = graph_reasoning_targets(final_graph_dot)
    original_correct_reasoning_ids = [
        int(step["reasoning_id"])
        for step in packet["steps"]
        if step.get("result") == "correct" and str(step.get("target_node", "")) in final_reasoning_targets
    ]
    final_repaired_reasoning_ids = [
        rid for rid in repaired_reasoning_ids if f"R{rid}" in final_node_ids
    ]
    pruned_reasoning_ids = reasoning_ids_for_node_ids(
        packet,
        {
            node_id
            for event in prune_events
            for node_id in event.get("pruned_nodes", [])
            if isinstance(node_id, str)
        },
    )
    effective_dropped_reasoning_ids = sorted(
        set(gpt_dropped_reasoning_ids)
        | set(pruned_reasoning_ids)
        | {
            rid
            for event in prune_events
            for rid in reasoning_ids_for_node_ids(
                packet,
                set(event.get("pruned_nodes", []) if isinstance(event.get("pruned_nodes"), list) else []),
            )
        }
    )
    full_rejudge_summary = None
    if full_rejudge:
        anchor_json = work_dir / "fixed_anchor.json"
        full_rejudge_summary = run_fixed_eval(work_dir, anchor_json, env, inner_workers)

    delta_cg = final_summary["CG"] - original_summary["CG"]
    delta_rea = final_summary["REA"] - original_summary["REA"]
    row = {
        "paper_spec": paper_spec,
        "model": ids["model"],
        "paper": paper,
        "run_dir": str(run_dir),
        "source_eval_dir": str(eval_dir),
        "curation_out_dir": str(out_dir),
        "final_graph": str(final_graph_dot),
        "work_dir": str(work_dir),
        "original": original_summary,
        "final": final_summary,
        "full_rejudge": full_rejudge_summary,
        "delta_CG": delta_cg,
        "delta_REA": delta_rea,
        "kept_reasoning_units": len(original_correct_reasoning_ids),
        "initial_kept_reasoning_units": correct_count,
        "kept_reasoning_ids": original_correct_reasoning_ids,
        "dropped_reasoning_units": len(packet["deterministic_vote_kept_baseline"]["dropped_reasoning_ids"]),
        "repaired_reasoning_units": len(final_repaired_reasoning_ids),
        "attempted_repaired_reasoning_units": len(repaired_reasoning_ids),
        "repaired_reasoning_ids": repaired_reasoning_ids,
        "final_repaired_reasoning_ids": final_repaired_reasoning_ids,
        "final_dropped_reasoning_ids": effective_dropped_reasoning_ids,
        "gpt_dropped_reasoning_ids": gpt_dropped_reasoning_ids,
        "pruned_reasoning_ids": pruned_reasoning_ids,
        "rewritten_edges_using_repaired_nodes": rewritten_edges_using_repaired_nodes,
        "pruned_after_judge_failure": prune_events[0] if prune_events else None,
        "prune_events": prune_events,
        "semantic_root_node_count": semantic.get("node_count"),
        "semantic_root_edge_count": semantic.get("edge_count"),
        "repair_rejected": repair_rejected,
        "curation_report_key": semantic_key,
        "preflight": preflight,
        "frontier_guard": normalize_frontier_guard(frontier_guard),
    }
    row["quality"] = assign_quality_tier(row)
    if stop_on_regression and (delta_cg < -1e-9 or delta_rea < -1e-9):
        raise RuntimeError(f"metric regression for {paper}: {json.dumps(row, ensure_ascii=False, indent=2)}")
    final_cg = float(row["final"]["CG"])
    final_rea = float(row["final"]["REA"])
    if final_cg + 1e-9 < min_final_cg or final_rea + 1e-9 < min_final_rea:
        failure_payload = {
            "reason": "final_metric_gate_failed",
            "thresholds": {
                "min_final_CG": min_final_cg,
                "min_final_REA": min_final_rea,
            },
            "final": row["final"],
            "original": row["original"],
            "quality": row["quality"],
            "work_dir": row["work_dir"],
            "final_graph": row["final_graph"],
            "curation_out_dir": row["curation_out_dir"],
        }
        raise RuntimeError(f"final metric gate failed for {paper}: {json.dumps(failure_payload, ensure_ascii=False)}")
    return row


def write_progress(
    report_root: Path,
    rows: List[Dict[str, Any]],
    stopped: Optional[Dict[str, Any]] = None,
    *,
    failures: Optional[List[Dict[str, Any]]] = None,
    requested: Optional[int] = None,
) -> None:
    report_root.mkdir(parents=True, exist_ok=True)
    failures = failures or []
    aggregate: Dict[str, Any] = {
        "papers": len(rows),
        "requested": requested if requested is not None else len(rows) + len(failures),
        "completed": len(rows),
        "failed": len(failures),
        "attempted": len(rows) + len(failures),
    }
    if rows:
        tier_counts: Dict[str, int] = {}
        retained_correct_ratios: List[float] = []
        for row in rows:
            quality = row.setdefault("quality", assign_quality_tier(row))
            tier = str(quality.get("tier", "unknown"))
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            retained_correct_ratios.append(float(quality.get("retained_correct_ratio", 0.0)))
        aggregate.update(
            {
                "avg_original_CG": sum(row["original"]["CG"] for row in rows) / len(rows),
                "avg_final_CG": sum(row["final"]["CG"] for row in rows) / len(rows),
                "avg_delta_CG": sum(row["delta_CG"] for row in rows) / len(rows),
                "avg_original_REA": sum(row["original"]["REA"] for row in rows) / len(rows),
                "avg_final_REA": sum(row["final"]["REA"] for row in rows) / len(rows),
                "avg_delta_REA": sum(row["delta_REA"] for row in rows) / len(rows),
                "avg_retained_correct_ratio": sum(retained_correct_ratios) / len(retained_correct_ratios),
                "quality_tier_counts": tier_counts,
                "cg_regressions": sum(1 for row in rows if row["delta_CG"] < -1e-9),
                "rea_regressions": sum(1 for row in rows if row["delta_REA"] < -1e-9),
            }
        )
    report = {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "report_root": str(report_root),
        "quality_tier_policy": QUALITY_TIER_POLICY,
        "aggregate": aggregate,
        "rows": rows,
        "failures": failures,
        "stopped": stopped,
    }
    (report_root / "batch_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_completed_row_metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh completed-row CG summaries when the local coverage code changes."""
    if not isinstance(row, dict):
        return row

    final = row.get("final") or {}
    final_eval_dir = Path(str(final.get("eval_dir", "")))
    if final_eval_dir and (final_eval_dir / "evaluation_results.json").exists():
        refreshed_final = refresh_stable_eval_coverage(final_eval_dir)
        if refreshed_final is not None:
            row["final"] = refreshed_final

    original = row.get("original") or {}
    if original.get("coverage_algorithm_version") != coverage_algorithm_version():
        run_dir = Path(str(row.get("run_dir", "")))
        source_eval_dir = Path(str(row.get("source_eval_dir", "")))
        recalc_dir = Path(str(row.get("work_dir") or row.get("curation_out_dir") or ".")) / "baseline_recalc_current"
        if run_dir.exists() and (source_eval_dir / "evaluation_results.json").exists():
            try:
                row["original"] = summarize_source_eval_with_current_evaluator(run_dir, source_eval_dir, recalc_dir)
            except Exception as exc:  # noqa: BLE001
                row["baseline_refresh_error"] = str(exc)

    if row.get("original") and row.get("final"):
        row["delta_CG"] = row["final"]["CG"] - row["original"]["CG"]
        row["delta_REA"] = row["final"]["REA"] - row["original"]["REA"]
        row["quality"] = assign_quality_tier(row)
    return row


def load_paper_specs(files: List[str]) -> List[str]:
    specs: List[str] = []
    for name in files:
        path = Path(name)
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            specs.append(stripped)
    return specs


def is_retryable_error(message: str) -> bool:
    lowered = message.lower()
    retry_markers = (
        "remote end closed",
        "connection",
        "timeout",
        "timed out",
        "ssl",
        "unexpected_eof",
        "eof occurred",
        "empty sse",
        "empty response",
        "empty reply",
        "no json object parsed",
        "patch did not parse",
        "unterminated string",
        "name resolution",
        "temporary failure",
        "could not resolve host",
        "bad hostname",
        "url rejected",
        "curation_provider_error",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "504",
        "service unavailable",
        "apierror",
        "judge_provider_error",
        "pre-consumed quota",
        "quota",
        "provider",
    )
    return any(marker in lowered for marker in retry_markers)


def classify_failure_type(exc: Optional[Exception]) -> str:
    if exc is None:
        return "unknown"
    if isinstance(exc, PreflightGateError):
        return f"preflight:{exc.code}"
    message = str(exc).lower()
    if "final metric gate failed" in message or "final_metric_gate_failed" in message:
        return "final_metric_gate_failed"
    if "repaired/root judge vote failed" in message:
        return "final_judge_failed"
    if "patch issues" in message:
        return "patch_contract_error"
    if "patch did not parse" in message:
        if any(marker in message for marker in ("could not resolve host", "bad hostname", "connection", "timeout", "empty reply", "rate limit", "429", "500", "502", "503", "504", "quota", "provider")):
            return "curation_provider_error"
        return "patch_parse_error"
    if "semantic-root graph is not strict valid" in message:
        return "strict_validation_failed"
    if "repeated judge-failed repair target after pruning" in message:
        return "repair_loop_stalled"
    if "curation failed" in message:
        return "curation_failed"
    if "repair_budget_exceeded" in message:
        return "repair_budget_exceeded"
    if "judge_provider_error" in message:
        return "judge_provider_error"
    if "leaves no terminal reasoning nodes" in message:
        return "no_terminal_after_prune"
    if "metric regression" in message:
        return "metric_regression"
    if "source evaluation dir is not clean" in message:
        return "dirty_source_eval"
    if "no raw run found" in message:
        return "missing_raw_run"
    if is_retryable_error(message):
        return "provider_or_network"
    return "runtime"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--papers",
        nargs="+",
        default=[],
        help="Paper ids, or model:paper ids for multi-model runs. Bare paper ids default to qwen3_5_397b_a17b.",
    )
    parser.add_argument(
        "--paper-spec-file",
        nargs="+",
        default=[],
        help="Text files containing one paper spec per line. Supports comments with #.",
    )
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--report-root", default="")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-tokens", type=int, default=5000)
    parser.add_argument(
        "--gpt-retries",
        type=int,
        default=2,
        help="Retries inside each GPT-5.5 patch-generation subprocess.",
    )
    parser.add_argument(
        "--gpt-retry-sleep",
        type=float,
        default=8.0,
        help="Base sleep seconds between GPT-5.5 patch retry attempts.",
    )
    parser.add_argument(
        "--gpt-transport",
        choices=["curl", "urllib"],
        default=os.getenv("GPT55_CURATION_TRANSPORT", "curl"),
        help="HTTP transport for GPT-5.5 curation subprocesses.",
    )
    parser.add_argument(
        "--gpt-stream",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("GPT55_CURATION_STREAM", "").strip().lower() not in {"0", "false", "no", "off"},
        help="Use streaming responses for GPT-5.5 curation subprocesses.",
    )
    parser.add_argument(
        "--max-prune-rounds",
        type=int,
        default=8,
        help="Maximum judge-failed R* prune rounds before marking a sample repair-needed. Use -1 for unlimited.",
    )
    parser.add_argument(
        "--judge-error-retries",
        type=int,
        default=2,
        help=(
            "Extra repair attempts for a judge model whose response is error/parse-failed inside stable "
            "vote-reuse evaluation. Semantic wrong votes are not retried here."
        ),
    )
    parser.add_argument(
        "--judge-error-retry-sleep",
        type=float,
        default=5.0,
        help="Sleep seconds between judge-error repair attempts.",
    )
    parser.add_argument(
        "--min-anchor-correct-ratio",
        type=float,
        default=0.0,
        help=(
            "Minimum raw majority-correct reasoning ratio required before running edge repair. "
            "Default 0 keeps legacy behavior except zero-anchor samples."
        ),
    )
    parser.add_argument(
        "--max-repair-candidate-ratio",
        type=float,
        default=1.0,
        help=(
            "Maximum rejected/total reasoning ratio allowed for --repair-rejected. "
            "Default 1 keeps legacy behavior; lower values provide production triage by ratio."
        ),
    )
    parser.add_argument(
        "--min-raw-cg",
        type=float,
        default=0.0,
        help="Minimum raw CG required before running edge repair. Default 0 keeps legacy behavior.",
    )
    parser.add_argument(
        "--min-final-cg",
        type=float,
        default=1.0,
        help="Minimum final CG required for a completed PEARL closure row.",
    )
    parser.add_argument(
        "--min-final-rea",
        type=float,
        default=1.0,
        help="Minimum final REA required for a completed PEARL closure row.",
    )
    parser.add_argument("--inner-workers", type=int, default=6)
    parser.add_argument("--stop-on-regression", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--continue-on-error",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Record per-paper failures and continue instead of stopping the full batch.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="Retries for retryable provider/network errors per paper.",
    )
    parser.add_argument(
        "--full-rejudge",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Also run the full fixed-anchor evaluator as a robustness check. Main metrics still use vote reuse.",
    )
    parser.add_argument(
        "--repair-rejected",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Use evaluator feedback to repair rejected reasoning units before semantic root recovery. "
            "Retained correct raw units reuse old votes; repaired R* units and NROOT are newly judged."
        ),
    )
    parser.add_argument(
        "--resume-existing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "If batch_summary.json exists under --report-root, keep completed rows, retry failed specs, "
            "and skip already completed specs instead of overwriting progress."
        ),
    )
    args = parser.parse_args()
    paper_specs = list(args.papers) + load_paper_specs(args.paper_spec_file)
    if not paper_specs:
        parser.error("at least one --papers item or --paper-spec-file is required")

    load_env_file(Path(args.env_file))
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    report_root = Path(args.report_root) if args.report_root else (
        DEFAULT_OUT_ROOT / f"batch_semantic_root_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    report_root.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    requested = len(paper_specs)
    if args.resume_existing:
        existing_summary = read_json(report_root / "batch_summary.json", {})
        if isinstance(existing_summary, dict):
            rows = [
                refresh_completed_row_metrics(row)
                for row in list(existing_summary.get("rows", []) or [])
                if isinstance(row, dict)
            ]
            failures = list(existing_summary.get("failures", []) or [])
            existing_requested = int(
                (existing_summary.get("aggregate") or {}).get("requested") or 0
            )
            if existing_requested:
                requested = max(existing_requested, len(paper_specs))
            completed_specs = {
                str(row.get("paper_spec", ""))
                for row in rows
                if isinstance(row, dict) and row.get("paper_spec")
            }
            skipped = len([spec for spec in paper_specs if spec in completed_specs])
            paper_specs = [spec for spec in paper_specs if spec not in completed_specs]
            if skipped:
                print(
                    f"resume existing summary: skipped {skipped} completed specs; "
                    f"remaining {len(paper_specs)}",
                    flush=True,
                )

    for idx, paper_spec in enumerate(paper_specs, start=len(rows) + 1):
        print(f"\n=== [{idx}/{requested}] {paper_spec}", flush=True)
        row = None
        last_exc: Optional[Exception] = None
        max_attempts = max(1, args.max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    print(f"retry {attempt}/{max_attempts}: {paper_spec}", flush=True)
                row = run_one(
                    paper_spec,
                    report_root,
                    env,
                    timeout=args.timeout,
                    max_tokens=args.max_tokens,
                    inner_workers=args.inner_workers,
                    stop_on_regression=args.stop_on_regression,
                    full_rejudge=args.full_rejudge,
                    repair_rejected=args.repair_rejected,
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
                    min_final_cg=args.min_final_cg,
                    min_final_rea=args.min_final_rea,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= max_attempts or not is_retryable_error(str(exc)):
                    break
        if row is None:
            preflight = last_exc.details if isinstance(last_exc, PreflightGateError) else None
            failures = [
                failure
                for failure in failures
                if str(failure.get("paper_spec", "")) != paper_spec
            ]
            failure = {
                "paper_spec": paper_spec,
                "error": str(last_exc) if last_exc else "unknown error",
                "retryable": is_retryable_error(str(last_exc)) if last_exc else False,
                "failure_type": classify_failure_type(last_exc),
                "preflight": preflight,
                "attempts": max_attempts,
            }
            failures.append(failure)
            stopped = failure if not args.continue_on_error else None
            write_progress(
                report_root,
                rows,
                stopped,
                failures=failures,
                requested=requested,
            )
            print(json.dumps(failure, ensure_ascii=False, indent=2), flush=True)
            if not args.continue_on_error:
                return 1
            continue
        failures = [
            failure
            for failure in failures
            if str(failure.get("paper_spec", "")) != paper_spec
        ]
        rows.append(row)
        write_progress(report_root, rows, failures=failures, requested=requested)
        print(
            f"{paper_spec} CG {row['original']['CG']:.4f}->{row['final']['CG']:.4f} "
            f"REA {row['original']['REA']:.4f}->{row['final']['REA']:.4f} "
            f"steps {row['original']['valid_reasoning_steps']}/{row['original']['total_reasoning_steps']}"
            f"->{row['final']['valid_reasoning_steps']}/{row['final']['total_reasoning_steps']}",
            flush=True,
        )

    write_progress(report_root, rows, failures=failures, requested=requested)
    print(f"\nREPORT_ROOT {report_root}", flush=True)
    print(json.dumps(read_json(report_root / "batch_summary.json", {}).get("aggregate", {}), ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
