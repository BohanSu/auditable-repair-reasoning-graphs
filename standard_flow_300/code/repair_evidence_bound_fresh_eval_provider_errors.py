#!/usr/bin/env python3
"""Copy-repair provider-error judges in evidence-bound fresh eval outputs.

This script never edits the source fresh-eval run in place. It copies the
candidate work/eval directory into a new closeout run, re-runs only saved judge
responses whose result is `error`, recomputes votes and CG/REA, and writes a new
FRESH_EVAL_RESULTS ledger. It is intended for strict-gate accounting where a
candidate is blocked by provider errors rather than by a graph change.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


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
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "runs" / "evidence_bound_provider_error_repair"

VOTE_RE = re.compile(r"reasoning_validation_(\d+)_vote_result\.json$")


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


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


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


def read_result_rows(path: Path) -> List[Dict[str, Any]]:
    payload = read_json(path, {})
    rows = payload.get("rows") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        raise RuntimeError(f"bad FRESH_EVAL_RESULTS rows: {path}")
    return [row for row in rows if isinstance(row, dict)]


def load_evaluator_module() -> Any:
    import evaluator  # type: ignore  # noqa: PLC0415

    return evaluator


def vote_on_results(model_results: Dict[str, str]) -> tuple[Dict[str, Any], str]:
    vote_counts: Dict[str, int] = {}
    for result in model_results.values():
        vote_counts[result] = vote_counts.get(result, 0) + 1
    vote_breakdown = {"votes": model_results, "counts": vote_counts, "total_models": len(model_results)}
    if "error" in vote_counts:
        vote_breakdown["decision"] = "At least one judge failed"
        return vote_breakdown, "error"
    if len(vote_counts) == 1:
        final = next(iter(vote_counts))
        vote_breakdown["decision"] = f"Unanimous: {final}"
        return vote_breakdown, final
    max_votes = max(vote_counts.values())
    winners = [result for result, count in vote_counts.items() if count == max_votes]
    if len(winners) == 1:
        final = winners[0]
        vote_breakdown["decision"] = f"Majority: {final} ({max_votes}/{len(model_results)})"
        return vote_breakdown, final
    if "correct" in winners:
        vote_breakdown["decision"] = "Tie broken in favor of 'correct'"
        return vote_breakdown, "correct"
    final = winners[0]
    vote_breakdown["decision"] = f"Tie: defaulted to {final}"
    return vote_breakdown, final


def parse_vote_id(path: Path) -> Optional[int]:
    match = VOTE_RE.match(path.name)
    return int(match.group(1)) if match else None


def make_graph_evaluator(evaluator: Any, work_dir: Path, eval_dir: Path, entities: List[str]) -> Any:
    graph_evaluator = evaluator.GraphEvaluator.__new__(evaluator.GraphEvaluator)
    graph_evaluator.output_dir = work_dir
    graph_evaluator.graph_file = work_dir / "final_clean_graph.dot"
    graph_evaluator.input_data_file = work_dir / "input_data.json"
    graph_evaluator.eval_dir = eval_dir
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


def rerun_one_judge(evaluator: Any, eval_dir: Path, reasoning_id: int, model_key: str) -> Dict[str, Any]:
    prompt_path = eval_dir / "prompts" / f"reasoning_validation_{reasoning_id:03d}_prompt.txt"
    if not prompt_path.exists():
        raise RuntimeError(f"missing prompt: {prompt_path}")
    validation_prompt = prompt_path.read_text(encoding="utf-8")
    model_name = evaluator.get_model_name(model_key)
    system_message = (
        "You are an expert at evaluating logical reasoning in scientific contexts. "
        "Always respond in valid JSON format."
    )
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": validation_prompt},
    ]
    create_params: Dict[str, Any] = {"model": model_name, "messages": messages}
    if evaluator._use_raw_http_for_model(model_key):
        messages = [{"role": "user", "content": f"{system_message}\n\n{validation_prompt}"}]
        create_params = {"model": model_name, "messages": messages}
    if model_key == "gemini":
        create_params["temperature"] = 0.1
    elif "o3" not in model_name.lower() and model_key != "claude":
        create_params.update({"temperature": 0, "max_tokens": 200})

    last_error = ""
    last_raw = ""
    for attempt in range(evaluator._eval_judge_max_retries()):
        try:
            if evaluator._use_raw_http_for_model(model_key):
                extra = {"temperature": 0.1} if model_key == "gemini" else None
                text = evaluator._raw_chat_completion(model_key, model_name, messages, extra)
            else:
                response = evaluator.clients[model_key].chat.completions.create(**create_params)
                text = evaluator._extract_response_text(response)
            last_raw = text
            parsed = evaluator._extract_json_object(text)
            if parsed is not None:
                return {
                    "model_key": model_key,
                    "model_name": model_name,
                    "result": str(parsed.get("result", "error")).lower(),
                    "reason": str(parsed.get("reason", "No reason provided")),
                    "raw_response": text,
                    "success": True,
                    "repair_attempted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            last_error = "JSON parsing failed"
        except Exception as exc:  # noqa: BLE001
            last_error = f"API call failed: {exc}"
        if attempt + 1 < evaluator._eval_judge_max_retries():
            time.sleep(evaluator._suggest_retry_delay(Exception(last_error), attempt))
    return {
        "model_key": model_key,
        "model_name": model_name,
        "result": "error",
        "reason": last_error or "Unknown error",
        "raw_response": last_raw,
        "success": False if last_error.startswith("API call failed:") else True,
        "repair_attempted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def repair_eval_dir(evaluator: Any, eval_dir: Path) -> Dict[str, Any]:
    repairs: List[Dict[str, Any]] = []
    for vote_file in sorted((eval_dir / "responses").glob("reasoning_validation_*_vote_result.json")):
        reasoning_id = parse_vote_id(vote_file)
        if reasoning_id is None:
            continue
        vote = read_json(vote_file, {})
        model_responses = vote.get("model_responses") if isinstance(vote.get("model_responses"), dict) else {}
        changed = False
        for model_key in ("o3", "claude", "gemini"):
            response_path = eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_response_{model_key}.json"
            response = read_json(response_path, {})
            issue = evaluator._classify_model_response_issue(response) if isinstance(response, dict) else "bad-json"
            if not issue:
                if isinstance(response, dict):
                    model_responses[model_key] = response
                continue
            repaired = rerun_one_judge(evaluator, eval_dir, reasoning_id, model_key)
            write_json(response_path, repaired)
            model_responses[model_key] = repaired
            repairs.append(
                {
                    "reasoning_id": reasoning_id,
                    "target_node": vote.get("target_node", ""),
                    "model_key": model_key,
                    "old_issue": issue,
                    "new_result": repaired.get("result", ""),
                    "new_success": repaired.get("success", False),
                    "response": rel(response_path),
                }
            )
            changed = True
        if changed:
            model_results = {
                key: str(model_responses.get(key, {}).get("result", "error")).lower()
                for key in ("o3", "claude", "gemini")
            }
            breakdown, final = vote_on_results(model_results)
            vote["model_responses"] = model_responses
            vote["model_results"] = model_results
            vote["vote_breakdown"] = breakdown
            vote["final_result"] = final
            write_json(vote_file, vote)
    return {"repairs": repairs}


def recompute_result(evaluator: Any, work_dir: Path, eval_dir: Path) -> Dict[str, Any]:
    result = read_json(eval_dir / "evaluation_results.json", {})
    entities = list(result.get("entities") or [])
    graph_evaluator = make_graph_evaluator(evaluator, work_dir, eval_dir, entities)
    if not graph_evaluator.load_data():
        raise RuntimeError(f"failed to load graph/input for {work_dir}")

    reasoning_results: Dict[str, str] = {}
    for vote_file in sorted((eval_dir / "responses").glob("reasoning_validation_*_vote_result.json")):
        reasoning_id = parse_vote_id(vote_file)
        if reasoning_id is None:
            continue
        vote = read_json(vote_file, {})
        reasoning_results[str(reasoning_id)] = str(vote.get("final_result", "error")).lower()

    clean = evaluator.is_eval_dir_clean(eval_dir)
    coverage_rate = graph_evaluator.calculate_entity_coverage_from_correct_reasoning(reasoning_results)
    accuracy = graph_evaluator.calculate_accuracy_score(reasoning_results)
    result["reasoning_validation_results"] = reasoning_results
    result["clean"] = clean
    result["judge_provider_error"] = not clean
    result["status"] = "fresh_evaluated" if clean else "judge_provider_error"
    result["coverage"] = {
        "coverage_rate": coverage_rate / 100,
        "total_entities": len(entities),
        "covered_entities": int(len(entities) * coverage_rate / 100),
    }
    result["accuracy"] = accuracy
    result["evaluation_summary"] = {
        "entity_coverage_score": result["coverage"]["coverage_rate"],
        "accuracy_score": accuracy["accuracy_score"],
        "total_entities": len(entities),
        "covered_entities": result["coverage"]["covered_entities"],
        "total_reasoning_steps": accuracy["total_steps"],
        "valid_reasoning_steps": accuracy["valid_steps"],
    }
    result["provider_error_repair"] = {
        "recomputed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_preserved_elsewhere": True,
    }
    write_json(eval_dir / "evaluation_results.json", result)
    return result


def repair_row(evaluator: Any, row: Dict[str, Any], out_root: Path) -> Dict[str, Any]:
    source_work_dir = resolve_path(str(row.get("work_dir") or ""))
    source_eval_dir = resolve_path(str(row.get("eval_dir") or ""))
    if not source_work_dir.exists() or not source_eval_dir.exists():
        raise RuntimeError(f"bad source work/eval dir for {row.get('paper_spec')}")

    work_dir = out_root / "work" / safe_slug(row.get("paper_spec", "")) / safe_slug(row.get("model", "model"))
    eval_dir = work_dir / "evaluation_outputs" / source_eval_dir.name
    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(source_work_dir, work_dir)
    copied_eval_dir = work_dir / "evaluation_outputs" / source_eval_dir.name
    if copied_eval_dir != eval_dir:
        raise RuntimeError("copied eval dir mismatch")

    repair_log = repair_eval_dir(evaluator, eval_dir)
    result = recompute_result(evaluator, work_dir, eval_dir)
    summary = result.get("evaluation_summary") if isinstance(result.get("evaluation_summary"), dict) else {}
    clean = result.get("clean") is True and result.get("judge_provider_error") is not True
    out = {
        **row,
        "CG": float(summary.get("entity_coverage_score", 0.0) or 0.0),
        "REA": float(summary.get("accuracy_score", 0.0) or 0.0),
        "covered_entities": int(summary.get("covered_entities", 0) or 0),
        "total_entities": int(summary.get("total_entities", 0) or 0),
        "valid_reasoning_steps": int(summary.get("valid_reasoning_steps", 0) or 0),
        "total_reasoning_steps": int(summary.get("total_reasoning_steps", 0) or 0),
        "judge_provider_error": not clean,
        "strict_gate_passed": clean
        and float(summary.get("entity_coverage_score", 0.0) or 0.0) >= 1.0
        and float(summary.get("accuracy_score", 0.0) or 0.0) >= 1.0,
        "work_dir": rel(work_dir),
        "eval_dir": rel(eval_dir),
        "status": "fresh_evaluated" if clean else "judge_provider_error",
        "provider_error_repairs": len(repair_log["repairs"]),
    }
    write_json(eval_dir / "provider_error_repair_log.json", repair_log)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh-eval-results", required=True)
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--eval-raw-http-models", default="")
    parser.add_argument("--eval-judge-max-retries", type=int, default=0)
    parser.add_argument("--eval-request-timeout", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(resolve_path(args.env_file))
    if args.eval_raw_http_models:
        os.environ["EVAL_RAW_HTTP_MODELS"] = args.eval_raw_http_models
    if args.eval_judge_max_retries > 0:
        os.environ["EVAL_JUDGE_MAX_RETRIES"] = str(args.eval_judge_max_retries)
    if args.eval_request_timeout > 0:
        os.environ["EVAL_REQUEST_TIMEOUT"] = str(args.eval_request_timeout)

    rows = read_result_rows(resolve_path(args.fresh_eval_results))
    if args.limit > 0:
        rows = rows[: args.limit]
    out_root = resolve_path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    evaluator = load_evaluator_module()

    repaired_rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for row in rows:
        try:
            repaired_rows.append(repair_row(evaluator, row, out_root))
        except Exception as exc:  # noqa: BLE001
            failures.append({"paper_spec": row.get("paper_spec", ""), "model": row.get("model", ""), "error": str(exc)})

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
        "provider_error_repairs",
        "work_dir",
        "eval_dir",
        "status",
    ]
    write_csv(out_root / "FRESH_EVAL_RESULTS.csv", repaired_rows, fieldnames)
    write_json(out_root / "FRESH_EVAL_RESULTS.json", {"rows": repaired_rows})
    write_json(out_root / "FRESH_EVAL_FAILURES.json", failures)
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_fresh_eval_results": rel(resolve_path(args.fresh_eval_results)),
        "out_root": rel(out_root),
        "selected": len(rows),
        "completed": len(repaired_rows),
        "failures": len(failures),
        "strict_gate_passed": sum(1 for row in repaired_rows if row.get("strict_gate_passed") is True),
        "judge_provider_error": sum(1 for row in repaired_rows if row.get("judge_provider_error") is True),
        "provider_error_repairs": sum(int(row.get("provider_error_repairs") or 0) for row in repaired_rows),
        "by_lane": dict(Counter(row.get("lane", "") for row in repaired_rows)),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
    }
    write_json(out_root / "FRESH_EVAL_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
