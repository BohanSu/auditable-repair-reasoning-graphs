#!/usr/bin/env python3
"""Fresh-evaluate evidence-bound staged graph candidates.

This script is deliberately narrow: it reads staged `final_clean_graph.dot` and
`input_data.json` candidates, uses the fixed packet anchor entities, runs the
standard evaluator reasoning judges, and writes CG/REA summaries. It does not
merge rows into the 350 accounting.
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
from typing import Any, Dict, List, Sequence, Type


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(CODE_DIR))


DEFAULT_ATTEMPT_INDEX = (
    PACKAGE_ROOT
    / "09_residual_50_closeout"
    / "evidence_bound_regeneration"
    / "provider_smoke_runs"
    / "gpt55_smoke_20260602_2116_safe_materialized"
    / "ATTEMPT_INDEX.csv"
)
DEFAULT_OUT_ROOT = (
    PACKAGE_ROOT
    / "09_residual_50_closeout"
    / "runs"
    / "evidence_bound_staged_fresh_eval"
)
PROVIDER_ERROR_PATTERNS = (
    "insufficient_user_quota",
    "user quota",
    "quota",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    "billing",
    "403",
    "429",
    "provider error",
)

GraphEvaluatorType = Any


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def safe_slug(value: str, *, limit: int = 140) -> str:
    out = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in str(value).strip())
    while "__" in out:
        out = out.replace("__", "_")
    return (out.strip("_") or "item")[:limit]


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


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_env_file(path: Path) -> None:
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
            name = match.group(1) or match.group(2) or ""
            return os.environ.get(name, "")

        os.environ[key] = var_pattern.sub(repl, value)


def parse_specs(values: Sequence[str], specs_file: str = "") -> List[str]:
    specs = [str(value).strip() for value in values if str(value).strip()]
    if specs_file:
        path = resolve_path(specs_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                specs.append(line)
    out: List[str] = []
    seen: set[str] = set()
    for spec in specs:
        if spec not in seen:
            seen.add(spec)
            out.append(spec)
    return out


def parse_labels(values: Sequence[str], labels_file: str = "") -> List[str]:
    labels = [str(value).strip() for value in values if str(value).strip()]
    if labels_file:
        path = resolve_path(labels_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                labels.append(line)
    out: List[str] = []
    seen: set[str] = set()
    for label in labels:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def select_rows(
    rows: List[Dict[str, str]],
    *,
    paper_specs: Sequence[str],
    candidate_labels: Sequence[str],
    limit: int,
    require_preflight_passed: bool,
) -> List[Dict[str, str]]:
    spec_set = set(paper_specs)
    label_set = set(candidate_labels)
    selected = [
        row
        for row in rows
        if (not spec_set or row.get("paper_spec") in spec_set)
        and (not label_set or row.get("candidate_label") in label_set)
        and (not require_preflight_passed or str(row.get("passed_local_preflight", "")).lower() == "true")
    ]
    selected.sort(key=lambda row: (int(row.get("priority") or 999999), row.get("paper_spec", "")))
    return selected[:limit] if limit > 0 else selected


def load_evaluator_api() -> tuple[Type[GraphEvaluatorType], Any]:
    from evaluator import GraphEvaluator, find_latest_clean_eval_dir  # type: ignore  # noqa: PLC0415

    return GraphEvaluator, find_latest_clean_eval_dir


def make_graph_evaluator(
    graph_evaluator_cls: Type[GraphEvaluatorType],
    work_dir: Path,
    eval_dir: Path,
    entities: List[str],
) -> GraphEvaluatorType:
    graph_evaluator = graph_evaluator_cls.__new__(graph_evaluator_cls)
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


def summarize_eval_result(result: Dict[str, Any], eval_dir: Path) -> Dict[str, Any]:
    summary = result.get("evaluation_summary") if isinstance(result.get("evaluation_summary"), dict) else {}
    return {
        "CG": float(summary.get("entity_coverage_score", 0.0) or 0.0),
        "REA": float(summary.get("accuracy_score", 0.0) or 0.0),
        "covered_entities": int(summary.get("covered_entities", 0) or 0),
        "total_entities": int(summary.get("total_entities", 0) or 0),
        "valid_reasoning_steps": int(summary.get("valid_reasoning_steps", 0) or 0),
        "total_reasoning_steps": int(summary.get("total_reasoning_steps", 0) or 0),
        "eval_dir": rel(eval_dir),
        "judge_provider_error": bool(result.get("judge_provider_error")),
    }


def reasoning_has_provider_error(reasoning_results: Dict[str, Any]) -> bool:
    return any(str(value).lower() == "error" for value in reasoning_results.values())


def is_provider_error_text(value: Any) -> bool:
    text = str(value or "").lower()
    return any(pattern in text for pattern in PROVIDER_ERROR_PATTERNS)


def provider_error_stop_threshold(args: argparse.Namespace) -> int:
    if args.provider_error_stop_after > 0:
        return args.provider_error_stop_after
    return 1 if args.stop_on_provider_error else 0


def should_stop_for_provider_error(
    *,
    threshold: int,
    scope: str,
    provider_error_total: int,
    provider_error_consecutive: int,
) -> bool:
    if threshold <= 0:
        return False
    if scope == "total":
        return provider_error_total >= threshold
    return provider_error_consecutive >= threshold


def skipped_provider_health_rows(rows: List[Dict[str, str]], reason: str) -> List[Dict[str, Any]]:
    skipped: List[Dict[str, Any]] = []
    for row in rows:
        skipped.append(
            {
                "priority": row.get("priority", ""),
                "paper_spec": row.get("paper_spec", ""),
                "lane": row.get("lane", ""),
                "model": row.get("model", ""),
                "attempt_path": row.get("attempt_path", ""),
                "graph_spec": row.get("graph_spec", ""),
                "dot": row.get("dot", ""),
                "preflight_report": row.get("preflight_report", ""),
                "staged_run_dir": row.get("staged_run_dir", ""),
                "status": "skipped_provider_health",
                "skip_reason": reason,
            }
        )
    return skipped


def evaluate_one(
    row: Dict[str, str],
    out_root: Path,
    *,
    graph_evaluator_cls: Type[GraphEvaluatorType],
    find_latest_clean_eval_dir: Any,
) -> Dict[str, Any]:
    packet_pointer = read_json(resolve_path(row["staged_run_dir"]) / "evidence_bound_packet_pointer.json", {})
    packet_path = resolve_path(packet_pointer.get("packet", ""))
    packet = read_json(packet_path, {})
    entities = list((packet.get("paper_anchor") or {}).get("entities") or [])
    core_idea = str((packet.get("paper_anchor") or {}).get("core_idea") or "")
    if not entities:
        raise RuntimeError(f"missing fixed packet entities for {row.get('paper_spec')}")

    stage_dir = resolve_path(row["staged_run_dir"])
    work_dir = out_root / "work" / safe_slug(row.get("paper_spec", "")) / safe_slug(row.get("model", "model"))
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(stage_dir / "final_clean_graph.dot", work_dir / "final_clean_graph.dot")
    shutil.copy2(stage_dir / "input_data.json", work_dir / "input_data.json")
    write_json(
        work_dir / "fixed_anchor.json",
        {
            "core_idea": core_idea,
            "entities": entities,
            "source_packet": rel(packet_path),
        },
    )

    eval_outputs_dir = work_dir / "evaluation_outputs"
    eval_outputs_dir.mkdir(parents=True, exist_ok=True)
    clean_existing = find_latest_clean_eval_dir(eval_outputs_dir)
    if clean_existing is not None:
        existing = read_json(clean_existing / "evaluation_results.json", {})
        if existing.get("protocol") == "evidence_bound_full_fresh_fixed_anchor":
            summary = summarize_eval_result(existing, clean_existing)
            return {
                **row,
                **summary,
                "work_dir": rel(work_dir),
                "strict_gate_passed": summary["CG"] >= 1.0 and summary["REA"] >= 1.0,
                "status": "reused_existing_clean_eval",
            }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_dir = work_dir / "evaluation_outputs" / f"evidence_bound_full_fresh_{timestamp}"
    graph_evaluator = make_graph_evaluator(graph_evaluator_cls, work_dir, eval_dir, entities)
    if not graph_evaluator.load_data():
        raise RuntimeError(f"failed to load staged graph/input: {work_dir}")

    all_steps, valid_steps = graph_evaluator.filter_valid_reasoning_steps()
    if len(all_steps) != len(valid_steps):
        raise RuntimeError(f"invalid reasoning structure before fresh eval: total={len(all_steps)} valid={len(valid_steps)}")

    _correctness_rate, reasoning_results = graph_evaluator.validate_reasoning_steps_with_llm()
    judge_provider_error = reasoning_has_provider_error(reasoning_results)
    coverage_rate = graph_evaluator.calculate_entity_coverage_from_correct_reasoning(reasoning_results)
    coverage = {
        "coverage_rate": coverage_rate / 100,
        "total_entities": len(entities),
        "covered_entities": int(len(entities) * coverage_rate / 100),
    }
    accuracy = graph_evaluator.calculate_accuracy_score(reasoning_results)
    result = {
        "timestamp": datetime.now().isoformat(),
        "output_dir": str(work_dir),
        "core_idea": core_idea,
        "entities": entities,
        "protocol": "evidence_bound_full_fresh_fixed_anchor",
        "clean": all(str(value).lower() != "error" for value in reasoning_results.values()),
        "judge_provider_error": judge_provider_error,
        "status": "judge_provider_error" if judge_provider_error else "fresh_evaluated",
        "reasoning_validation_results": reasoning_results,
        "coverage": coverage,
        "accuracy": accuracy,
        "evaluation_summary": {
            "entity_coverage_score": coverage["coverage_rate"],
            "accuracy_score": accuracy["accuracy_score"],
            "total_entities": len(entities),
            "covered_entities": coverage["covered_entities"],
            "total_reasoning_steps": accuracy["total_steps"],
            "valid_reasoning_steps": accuracy["valid_steps"],
        },
        "source_attempt": {
            "attempt_path": row.get("attempt_path", ""),
            "graph_spec": row.get("graph_spec", ""),
            "preflight_report": row.get("preflight_report", ""),
            "staged_run_dir": row.get("staged_run_dir", ""),
        },
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0},
    }
    write_json(eval_dir / "evaluation_results.json", result)
    summary = summarize_eval_result(result, eval_dir)
    return {
        **row,
        **summary,
        "work_dir": rel(work_dir),
        "strict_gate_passed": (not judge_provider_error) and summary["CG"] >= 1.0 and summary["REA"] >= 1.0,
        "status": "judge_provider_error" if judge_provider_error else "fresh_evaluated",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-index", default=str(DEFAULT_ATTEMPT_INDEX))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--paper-specs", nargs="+", default=[])
    parser.add_argument("--paper-specs-file", default="")
    parser.add_argument("--candidate-labels", nargs="+", default=[])
    parser.add_argument("--candidate-labels-file", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--require-preflight-passed", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--eval-raw-http-models", default="")
    parser.add_argument("--eval-inner-max-workers", type=int, default=0)
    parser.add_argument(
        "--stop-on-provider-error",
        action="store_true",
        help="Stop the batch after the first provider-contaminated fresh-eval attempt.",
    )
    parser.add_argument(
        "--provider-error-stop-after",
        type=int,
        default=0,
        help="Stop after N provider-contaminated attempts. 0 disables unless --stop-on-provider-error is set.",
    )
    parser.add_argument(
        "--provider-error-stop-scope",
        choices=["consecutive", "total"],
        default="consecutive",
        help="Count provider errors consecutively or across the whole batch before early stopping.",
    )
    parser.add_argument(
        "--provider-fail-fast-inner",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Abort unfinished judge tasks inside a row after a provider/quota error. Defaults to --stop-on-provider-error.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(resolve_path(args.env_file))
    if args.eval_raw_http_models:
        os.environ["EVAL_RAW_HTTP_MODELS"] = args.eval_raw_http_models
    if args.eval_inner_max_workers > 0:
        os.environ["EVAL_INNER_MAX_WORKERS"] = str(args.eval_inner_max_workers)
    provider_fail_fast_inner = (
        args.stop_on_provider_error if args.provider_fail_fast_inner is None else args.provider_fail_fast_inner
    )
    if provider_fail_fast_inner:
        os.environ["EVAL_PROVIDER_FAIL_FAST"] = "1"
    graph_evaluator_cls, find_latest_clean_eval_dir = load_evaluator_api()
    rows = select_rows(
        read_csv(resolve_path(args.attempt_index)),
        paper_specs=parse_specs(args.paper_specs, args.paper_specs_file),
        candidate_labels=parse_labels(args.candidate_labels, args.candidate_labels_file),
        limit=args.limit,
        require_preflight_passed=args.require_preflight_passed,
    )
    out_root = resolve_path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    stop_threshold = provider_error_stop_threshold(args)
    provider_error_total = 0
    provider_error_consecutive = 0
    early_stop: Dict[str, Any] = {
        "enabled": stop_threshold > 0,
        "triggered": False,
        "threshold": stop_threshold,
        "scope": args.provider_error_stop_scope,
        "reason": "",
        "after_attempts": 0,
        "provider_error_total": 0,
        "provider_error_consecutive": 0,
        "skipped": 0,
    }
    for index, row in enumerate(rows):
        attempted_provider_error = False
        try:
            result = evaluate_one(
                row,
                out_root,
                graph_evaluator_cls=graph_evaluator_cls,
                find_latest_clean_eval_dir=find_latest_clean_eval_dir,
            )
            results.append(result)
            attempted_provider_error = result.get("judge_provider_error") is True
        except Exception as exc:  # noqa: BLE001
            attempted_provider_error = is_provider_error_text(exc)
            failures.append(
                {
                    "priority": row.get("priority", ""),
                    "paper_spec": row.get("paper_spec", ""),
                    "model": row.get("model", ""),
                    "error": str(exc),
                    "provider_error_suspected": attempted_provider_error,
                }
            )
        if attempted_provider_error:
            provider_error_total += 1
            provider_error_consecutive += 1
        else:
            provider_error_consecutive = 0
        if should_stop_for_provider_error(
            threshold=stop_threshold,
            scope=args.provider_error_stop_scope,
            provider_error_total=provider_error_total,
            provider_error_consecutive=provider_error_consecutive,
        ):
            reason = (
                f"provider_error_{args.provider_error_stop_scope}_threshold_reached:"
                f"{stop_threshold}"
            )
            skipped = skipped_provider_health_rows(rows[index + 1 :], reason)
            early_stop.update(
                {
                    "triggered": True,
                    "reason": reason,
                    "after_attempts": index + 1,
                    "provider_error_total": provider_error_total,
                    "provider_error_consecutive": provider_error_consecutive,
                    "skipped": len(skipped),
                }
            )
            break
    if not early_stop["triggered"]:
        early_stop.update(
            {
                "after_attempts": len(results) + len(failures),
                "provider_error_total": provider_error_total,
                "provider_error_consecutive": provider_error_consecutive,
            }
        )

    fieldnames = [
        "priority",
        "paper_spec",
        "lane",
        "model",
        "attempt_path",
        "graph_spec",
        "dot",
        "preflight_report",
        "staged_run_dir",
        "CG",
        "REA",
        "covered_entities",
        "total_entities",
        "valid_reasoning_steps",
        "total_reasoning_steps",
        "strict_gate_passed",
        "judge_provider_error",
        "work_dir",
        "eval_dir",
        "status",
    ]
    write_csv(out_root / "FRESH_EVAL_RESULTS.csv", results, fieldnames)
    write_json(out_root / "FRESH_EVAL_FAILURES.json", failures)
    write_csv(
        out_root / "FRESH_EVAL_SKIPPED_PROVIDER_HEALTH.csv",
        skipped,
        [
            "priority",
            "paper_spec",
            "lane",
            "model",
            "attempt_path",
            "graph_spec",
            "dot",
            "preflight_report",
            "staged_run_dir",
            "status",
            "skip_reason",
        ],
    )
    write_json(out_root / "FRESH_EVAL_SKIPPED_PROVIDER_HEALTH.json", {"rows": skipped})
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "attempt_index": rel(resolve_path(args.attempt_index)),
        "out_root": rel(out_root),
        "selected": len(rows),
        "attempted": len(results) + len(failures),
        "completed": len(results),
        "failures": len(failures),
        "skipped_provider_health": len(skipped),
        "strict_gate_passed": sum(1 for row in results if row.get("strict_gate_passed") is True),
        "judge_provider_error": sum(1 for row in results if row.get("judge_provider_error") is True),
        "failure_provider_error_suspected": sum(
            1 for row in failures if row.get("provider_error_suspected") is True
        ),
        "by_lane": dict(Counter(row.get("lane", "") for row in results)),
        "by_status": dict(Counter(row.get("status", "") for row in results)),
        "provider_error_early_stop": early_stop,
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0},
    }
    write_json(out_root / "FRESH_EVAL_RESULTS.json", {"summary": summary, "rows": results})
    write_json(out_root / "FRESH_EVAL_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
