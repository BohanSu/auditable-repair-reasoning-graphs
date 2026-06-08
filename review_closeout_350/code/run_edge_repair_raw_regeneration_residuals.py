#!/usr/bin/env python3
"""Regenerate no-anchor PEARL residuals and re-enter the strict repair gate.

This module closes the residual lane that local edge repair intentionally
refuses: raw/source graphs with no majority-correct reasoning anchor. It is not
a paper-specific patch path. For each `raw_regeneration` row it creates a new
first-pass source graph with the existing RLT generator, evaluates that source
graph, then hands the clean source run to `run_one()` so the unchanged PEARL
semantic repair/root gate decides whether the row can be merged.

Default mode is a dry-run plan only. Use `--execute` when provider credentials,
`pydot`, and judge/evaluator dependencies are available.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

DEFAULT_RECEIVER = PROJECT_ROOT / "reports" / "pearl_runs" / "02_legacy_reference" / "pearl_edge_repair_full_490_20260520"
DEFAULT_QUEUE = DEFAULT_RECEIVER / "audit" / "residual_queue_current.json"
DEFAULT_OUT_ROOT = DEFAULT_RECEIVER / "framework" / "modules" / "raw_regeneration" / "runs"
DEFAULT_MODEL_OUTPUTS = PROJECT_ROOT / "data" / "teacher_pool" / "model_outputs"
DEFAULT_PROVIDER_PROFILE = PROJECT_ROOT / "reports" / "pearl_runs" / "06_runtime_state" / "provider_preflight_stage1_selected_20260524.json"


class RawRegenerationExhausted(RuntimeError):
    def __init__(self, paper_spec: str, attempts: List[Dict[str, Any]]) -> None:
        last = attempts[-1] if attempts else {}
        last_type = str(last.get("failure_type") or "unknown")
        super().__init__(
            f"raw regeneration candidates exhausted for {paper_spec}; "
            f"attempts={len(attempts)}; last_failure_type={last_type}"
        )
        self.paper_spec = paper_spec
        self.attempts = attempts
        self.failure_type = "raw_regeneration_candidates_exhausted"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def route_api_key_envs(route: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    raw_names = route.get("api_key_envs")
    if isinstance(raw_names, list):
        names.extend(str(name).strip() for name in raw_names if str(name).strip())
    single = str(route.get("api_key_env") or "").strip()
    if single:
        names.append(single)
    deduped: List[str] = []
    for name in names:
        if name not in deduped:
            deduped.append(name)
    return deduped


def first_env_value(names: List[str]) -> str:
    var_pattern = re.compile(r"\$(\w+)|\$\{([^}]+)\}")

    def expand(value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            ref = match.group(1) or match.group(2) or ""
            return os.getenv(ref, match.group(0))

        return var_pattern.sub(repl, value).strip()

    for name in names:
        value = expand(os.getenv(name, ""))
        if value and "$" not in value:
            return value
    return ""


def apply_stage2_provider_profile(profile: Dict[str, Any]) -> None:
    """Apply evaluator/GPT-5.5 routing from the shared provider profile."""
    if not profile:
        return
    from run_stage1_selected_semantic_closure_batch import apply_provider_profile  # noqa: PLC0415

    apply_provider_profile(profile)


def apply_generation_route(profile: Dict[str, Any], model_name: str) -> Dict[str, str]:
    """Expose the selected generation route through RLT_generator.py env names."""
    routes = profile.get("routes") or {}
    route_name = generation_route_name_for_model(model_name)
    if route_name == "claude":
        key_vars = ["CLAUDE_API_KEY"]
        base_vars = ["CLAUDE_BASE_URL"]
    elif route_name == "gemini":
        key_vars = ["GEMINI_API_KEY"]
        base_vars = ["GEMINI_BASE_URL"]
    elif route_name == "o3":
        key_vars = ["OPENAI_API_KEY", "GPT_API_KEY"]
        base_vars = ["OPENAI_BASE_URL", "GPT_BASE_URL"]
    else:
        route_name = "gpt55"
        key_vars = ["OPENAI_API_KEY", "GPT_API_KEY"]
        base_vars = ["OPENAI_BASE_URL", "GPT_BASE_URL"]

    route = routes.get(route_name) if isinstance(routes, dict) else None
    if not isinstance(route, dict):
        return {}
    api_key = first_env_value(route_api_key_envs(route))
    base_url = str(route.get("base_url") or "").strip()
    updates: Dict[str, str] = {}
    if api_key:
        for key_var in key_vars:
            updates[key_var] = api_key
    if base_url:
        for base_var in base_vars:
            updates[base_var] = base_url
    os.environ.update(updates)
    return updates


def generation_route_name_for_model(model_name: str) -> str:
    lowered = model_name.lower()
    if "claude" in lowered:
        return "claude"
    if "gemini" in lowered:
        return "gemini"
    if "o3" in lowered:
        return "o3"
    return "gpt55"


def provider_generation_model(profile: Dict[str, Any], row_model: str, explicit_generation_model: str = "") -> str:
    """Map internal row model ids to provider-callable generation model ids."""
    explicit = explicit_generation_model.strip()
    if explicit:
        return explicit
    route_name = generation_route_name_for_model(row_model)
    routes = profile.get("routes") or {}
    route = routes.get(route_name) if isinstance(routes, dict) else None
    if isinstance(route, dict):
        model = str(route.get("model") or "").strip()
        if model:
            return model
    return row_model


def parse_generation_model_list(value: str) -> List[str]:
    models: List[str] = []
    for chunk in re_split_commas(value):
        model = chunk.strip()
        if model:
            models.append(model)
    return models


def re_split_commas(value: str) -> List[str]:
    import re

    return re.split(r"[\s,]+", value.strip()) if value and value.strip() else []


def generation_model_candidates(
    profile: Dict[str, Any],
    row_model: str,
    explicit_generation_model: str,
    fallback_generation_models: str,
) -> List[str]:
    """Return ordered source-generation candidates for no-anchor recovery.

    The row's original route is tried first. If that regenerated source graph
    still has no anchor, later candidates give the paper a fresh graph rather
    than treating one bad source regeneration as a terminal dataset failure.
    """
    candidates: List[str] = []

    def add(model: str) -> None:
        model = str(model or "").strip()
        if model and model not in candidates:
            candidates.append(model)

    explicit = explicit_generation_model.strip()
    if explicit:
        add(explicit)
    else:
        add(provider_generation_model(profile, row_model, ""))

    for model in parse_generation_model_list(fallback_generation_models):
        add(model)

    if not fallback_generation_models.strip():
        routes = profile.get("routes") or {}
        if isinstance(routes, dict):
            for route_name in ("gpt55", "claude", "gemini", "o3"):
                route = routes.get(route_name)
                if isinstance(route, dict):
                    add(str(route.get("model") or ""))
    return candidates


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


def load_generator_api() -> Dict[str, Any]:
    from RLT_generator import generate_graph_for_file, load_env_file as load_rlt_env_file  # noqa: PLC0415

    return {
        "generate_graph_for_file": generate_graph_for_file,
        "load_rlt_env_file": load_rlt_env_file,
    }


def load_paper_specs(values: List[str], specs_file: str = "") -> List[str]:
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


def select_raw_rows(
    queue: Dict[str, Any],
    limit: int,
    paper_specs: List[str],
    *,
    include_lanes: List[str],
    allow_non_raw_residuals: bool = False,
) -> List[Dict[str, Any]]:
    paper_spec_set = set(paper_specs)
    lane_set = set(include_lanes)
    rows = [
        row
        for row in queue.get("queue", [])
        if (not lane_set or row.get("lane") in lane_set)
        and (
            allow_non_raw_residuals
            or (
                row.get("lane") == "raw_regeneration"
                and row.get("failure_type") == "preflight:no_anchor_regenerate"
            )
        )
        and (not paper_spec_set or row.get("paper_spec") in paper_spec_set)
    ]
    return rows[:limit] if limit > 0 else rows


def find_input_data(model_outputs_root: Path, model: str, paper: str) -> Optional[Path]:
    preferred_dir = model_outputs_root / model / paper
    candidates = sorted(preferred_dir.glob("20*/input_data.json")) if preferred_dir.exists() else []
    if not candidates:
        candidates = sorted(model_outputs_root.glob(f"*/{paper}/20*/input_data.json"))
    return candidates[-1] if candidates else None


def prepare_input_staging(
    rows: List[Dict[str, Any]],
    *,
    report_root: Path,
    requested_data_dir: Path,
    model_outputs_root: Path,
) -> Dict[str, Any]:
    missing: List[Dict[str, str]] = []
    copied: List[Dict[str, str]] = []
    if requested_data_dir.exists():
        missing_requested = []
        for row in rows:
            paper = str(row.get("paper_id") or row.get("paper_spec", "").split(":", 1)[-1])
            if not (requested_data_dir / f"{paper}.json").exists():
                missing_requested.append(paper)
        if not missing_requested:
            return {
                "data_dir": str(requested_data_dir),
                "mode": "provided_data_dir",
                "copied": copied,
                "missing": missing,
            }

    staging_dir = report_root / "raw_regeneration_inputs"
    staging_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        model = str(row.get("model") or row.get("paper_spec", "").split(":", 1)[0])
        paper = str(row.get("paper_id") or row.get("paper_spec", "").split(":", 1)[-1])
        dst = staging_dir / f"{paper}.json"
        src = find_input_data(model_outputs_root, model, paper)
        if src is None:
            if not dst.exists():
                missing.append({"paper_spec": str(row.get("paper_spec")), "paper_id": paper})
            continue
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            shutil.copy2(src, dst)
            action = "copied"
        else:
            action = "already_staged"
        copied.append(
            {
                "paper_spec": str(row.get("paper_spec")),
                "source": str(src),
                "staged": str(dst),
                "action": action,
            }
        )
    return {
        "data_dir": str(staging_dir),
        "mode": "staged_from_model_outputs",
        "copied": copied,
        "missing": missing,
    }


def build_plan_item(
    row: Dict[str, Any],
    report_root: Path,
    args: argparse.Namespace,
    data_dir: Path,
    input_source: Optional[Path],
    provider_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    model = str(row.get("model") or row.get("paper_spec", "").split(":", 1)[0])
    paper = str(row.get("paper_id") or row.get("paper_spec", "").split(":", 1)[-1])
    generation_candidates = generation_model_candidates(
        provider_profile or {},
        model,
        args.generation_model,
        args.fallback_generation_models,
    )
    generation_model = generation_candidates[0] if generation_candidates else provider_generation_model(
        provider_profile or {},
        model,
        args.generation_model,
    )
    source_root = resolve_path(args.source_run_root) if args.source_run_root else report_root / "raw_regeneration_source_runs"
    repair_root = report_root / "strict_repair_gate"
    return {
        "paper_spec": row.get("paper_spec"),
        "model": model,
        "paper_id": paper,
        "lane": row.get("lane"),
        "failure_type": row.get("failure_type"),
        "source": row.get("source"),
        "strategy": (
            "regenerate_source_graph_then_vote_reuse_repair_root_gate"
            if row.get("lane") == "raw_regeneration"
            else "source_regeneration_escalation_then_vote_reuse_repair_root_gate"
        ),
        "source_generation": {
            "generator": "scripts/RLT_generator.py::generate_graph_for_file",
            "model": generation_model,
            "candidate_models": generation_candidates,
            "requested_row_model": model,
            "data_dir": str(data_dir),
            "input_source": str(input_source) if input_source else None,
            "output_root": str(source_root),
            "output_layout": "model_outputs",
            "skip_existing": args.skip_existing_generation,
        },
        "source_evaluation": {
            "evaluator": "scripts/evaluator.py --single-dir",
            "requires_clean_eval_dir": True,
        },
        "strict_gate": {
            "runner": "scripts/run_vote_guided_semantic_root_batch.py::run_one",
            "report_root": str(repair_root),
            "source_run_dir": str(source_root / generation_model / paper),
            "min_final_CG": 1.0,
            "min_final_REA": 1.0,
            "quality_tier": ["A_main", "B_usable", "C_thin"],
        },
        "rationale": (
            "Regenerate a fresh source graph under the existing first-pass prompt, evaluate it, then let the unchanged "
            "PEARL repair/root strict gate accept or reject the result. For no-anchor rows this supplies a safe anchor; "
            "for escalated local residuals it avoids repeatedly editing a candidate frontier that fresh judges show is unstable."
        ),
    }


def run_cmd(cmd: List[str], *, env: Dict[str, str], cwd: Path, timeout: Optional[int] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def latest_clean_eval_dir(run_dir: Path) -> Optional[Path]:
    from evaluator import is_eval_dir_clean  # noqa: PLC0415

    eval_root = run_dir / "evaluation_outputs"
    if not eval_root.exists():
        return None
    candidates = sorted(
        [path for path in eval_root.iterdir() if path.is_dir() and (path / "evaluation_results.json").exists()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if is_eval_dir_clean(candidate):
            return candidate
    return None


def evaluate_source_run(run_dir: Path, env: Dict[str, str], timeout: int) -> Path:
    clean = latest_clean_eval_dir(run_dir)
    if clean is not None:
        return clean
    proc = run_cmd(
        [sys.executable, "scripts/evaluator.py", "--single-dir", str(run_dir)],
        env=env,
        cwd=PROJECT_ROOT,
        timeout=timeout,
    )
    write_text(run_dir / "raw_regeneration_evaluator_stdout.txt", proc.stdout)
    if proc.returncode != 0:
        raise RuntimeError(f"source evaluation failed for {run_dir}: {proc.stdout[-4000:]}")
    clean = latest_clean_eval_dir(run_dir)
    if clean is None:
        raise RuntimeError(f"source evaluation produced no clean eval dir for {run_dir}")
    return clean


def generated_run_dir(output_root: Path, model: str, paper: str, result: Dict[str, Any]) -> Path:
    output_dir = result.get("output_dir")
    if output_dir:
        return resolve_path(str(output_dir))
    paper_dir = output_root / model / paper
    candidates = sorted(path for path in paper_dir.glob("20*") if (path / "final_clean_graph.dot").exists())
    if not candidates:
        raise FileNotFoundError(f"no generated source run found under {paper_dir}")
    return candidates[-1]


def preflight_wants_source_regeneration(exc: Exception) -> bool:
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return False
    if str(details.get("decision") or "").strip().lower() == "regenerate":
        return True
    return str(getattr(exc, "code", "")) in {
        "empty_raw_graph_regenerate",
        "no_anchor_regenerate",
        "low_anchor_regenerate",
        "low_raw_cg_regenerate",
    }


def safe_artifact_stem(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)[:180]


def generation_failure_message(result: Dict[str, Any]) -> str:
    parts: List[str] = []
    error = str(result.get("error") or "").strip()
    if error:
        parts.append(error)
    output_dir = str(result.get("output_dir") or "").strip()
    if output_dir:
        out_path = resolve_path(output_dir)
        if out_path.exists():
            for error_path in sorted(out_path.glob("*_error.txt")):
                try:
                    text = error_path.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if text:
                    parts.append(f"{error_path.name}: {text[-1200:]}")
    return " | ".join(parts) or "source generation returned success=false"


def is_retryable_generation_failure(result: Dict[str, Any], is_retryable_error: Any) -> bool:
    message = generation_failure_message(result)
    lowered = message.lower()
    if any(marker in lowered for marker in ("insufficient_user_quota", "预扣费额度失败", "用户剩余额度", "no credit", "billing")):
        return False
    if "request was blocked" in lowered or "blocked" in lowered:
        return True
    if is_retryable_error(message):
        return True
    if "api" in lowered and "missing api key" not in lowered:
        return True
    return False


def generate_source_with_retries(
    *,
    paper: str,
    paper_spec: str,
    generation_model: str,
    data_dir: Path,
    source_root: Path,
    args: argparse.Namespace,
    report_root: Path,
    generate_graph_for_file: Any,
    is_retryable_error: Any,
) -> Dict[str, Any]:
    max_attempts = max(1, int(args.generation_retries))
    attempts_path = (
        report_root
        / "raw_generation_attempts"
        / f"{safe_artifact_stem(generation_model)}__{safe_artifact_stem(paper)}.json"
    )
    attempts: List[Dict[str, Any]] = []
    last_result: Dict[str, Any] = {}
    last_message = ""
    for attempt in range(1, max_attempts + 1):
        if attempt > 1 and args.generation_retry_sleep > 0:
            time.sleep(args.generation_retry_sleep)
        result = generate_graph_for_file(
            paper,
            generation_model,
            data_dir=str(resolve_path(str(data_dir))),
            output_dir=str(source_root),
            skip_existing=args.skip_existing_generation,
            api_mode=args.api_mode,
            stream=args.stream,
            request_timeout_seconds=args.generation_timeout,
            max_output_tokens=args.generation_max_tokens,
            reasoning_effort=args.reasoning_effort,
            reasoning_summary=args.reasoning_summary,
            output_layout="model_outputs",
            save_debug_artifacts=args.save_debug_artifacts,
        )
        last_result = result
        message = "" if result.get("success") else generation_failure_message(result)
        last_message = message
        retryable = False if result.get("success") else is_retryable_generation_failure(result, is_retryable_error)
        attempts.append(
            {
                "attempt": attempt,
                "success": bool(result.get("success")),
                "skipped": bool(result.get("skipped")),
                "retryable": retryable,
                "output_dir": str(result.get("output_dir") or ""),
                "error": str(result.get("error") or ""),
                "error_detail": message[-2000:] if message else "",
            }
        )
        write_json(attempts_path, attempts)
        if result.get("success"):
            result["raw_generation_attempts"] = attempts
            result["raw_generation_attempts_path"] = str(attempts_path)
            return result
        if attempt >= max_attempts or not retryable:
            break
    raise RuntimeError(
        f"raw regeneration failed for {paper_spec} after {len(attempts)}/{max_attempts} "
        f"generation attempts; attempts={attempts_path}; last_error={last_message or last_result}"
    )


def run_raw_item(
    item: Dict[str, Any],
    *,
    args: argparse.Namespace,
    report_root: Path,
    env: Dict[str, str],
    generator_api: Dict[str, Any],
    runner_api: Dict[str, Any],
) -> Dict[str, Any]:
    generate_graph_for_file = generator_api["generate_graph_for_file"]
    run_one = runner_api["run_one"]

    paper_spec = str(item["paper_spec"])
    model = str(item["model"])
    paper = str(item["paper_id"])
    provider_profile = read_json(resolve_path(args.provider_profile), {})
    if not isinstance(provider_profile, dict):
        provider_profile = {}
    generation_candidates = generation_model_candidates(
        provider_profile if isinstance(provider_profile, dict) else {},
        model,
        args.generation_model,
        args.fallback_generation_models,
    )
    source_root = resolve_path(args.source_run_root) if args.source_run_root else report_root / "raw_regeneration_source_runs"
    repair_root = report_root / "strict_repair_gate"
    data_dir = Path(str(item.get("source_generation", {}).get("data_dir") or args.data_dir))
    model_attempts_path = (
        report_root
        / "raw_regeneration_model_attempts"
        / f"{safe_artifact_stem(paper_spec)}.json"
    )
    model_attempts: List[Dict[str, Any]] = []
    last_exc: Optional[Exception] = None

    for idx, generation_model in enumerate(generation_candidates, 1):
        apply_generation_route(provider_profile, generation_model)
        result: Dict[str, Any] = {}
        source_run: Optional[Path] = None
        source_eval: Optional[Path] = None
        try:
            result = generate_source_with_retries(
                paper=paper,
                paper_spec=paper_spec,
                generation_model=generation_model,
                data_dir=data_dir,
                source_root=source_root,
                args=args,
                report_root=report_root,
                generate_graph_for_file=generate_graph_for_file,
                is_retryable_error=runner_api["is_retryable_error"],
            )
            source_run = generated_run_dir(source_root, generation_model, paper, result)
            source_eval = evaluate_source_run(source_run, env, timeout=args.evaluation_timeout)
            row = run_one(
                paper_spec,
                repair_root,
                env,
                timeout=args.timeout,
                max_tokens=args.max_tokens,
                inner_workers=args.inner_workers,
                stop_on_regression=True,
                full_rejudge=False,
                repair_rejected=True,
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
                min_final_cg=1.0,
                min_final_rea=1.0,
                source_run_dir=source_run,
                source_eval_dir=source_eval,
            )
            model_attempts.append(
                {
                    "candidate_index": idx,
                    "generation_model": generation_model,
                    "status": "completed",
                    "source_run_dir": str(source_run),
                    "source_eval_dir": str(source_eval),
                    "generation_attempts": result.get("raw_generation_attempts", []),
                    "generation_attempts_path": result.get("raw_generation_attempts_path", ""),
                }
            )
            write_json(model_attempts_path, model_attempts)
            row["raw_regeneration"] = {
                "source_run_dir": str(source_run),
                "source_eval_dir": str(source_eval),
                "generation_model": generation_model,
                "candidate_models": generation_candidates,
                "model_attempts": model_attempts,
                "model_attempts_path": str(model_attempts_path),
                "generation_attempts": result.get("raw_generation_attempts", []),
                "generation_attempts_path": result.get("raw_generation_attempts_path", ""),
                "previous_failure_type": item.get("failure_type"),
            }
            return row
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            failure_type = runner_api["classify_failure_type"](exc)
            if "request was blocked" in str(exc).lower() or "blocked" in str(exc).lower():
                failure_type = "generation_provider_blocked"
            can_try_next_source = preflight_wants_source_regeneration(exc) or failure_type in {
                "provider_or_network",
                "judge_provider_error",
                "generation_provider_blocked",
            }
            should_try_next = can_try_next_source and idx < len(generation_candidates)
            model_attempts.append(
                {
                    "candidate_index": idx,
                    "generation_model": generation_model,
                    "status": "try_next_source_model" if should_try_next else "failed",
                    "failure_type": failure_type,
                    "retryable": runner_api["is_retryable_error"](str(exc)),
                    "error": str(exc),
                    "preflight": getattr(exc, "details", None) if hasattr(exc, "details") else None,
                    "source_run_dir": str(source_run) if source_run else "",
                    "source_eval_dir": str(source_eval) if source_eval else "",
                    "generation_attempts": result.get("raw_generation_attempts", []),
                    "generation_attempts_path": result.get("raw_generation_attempts_path", ""),
                }
            )
            write_json(model_attempts_path, model_attempts)
            if should_try_next:
                continue
            if can_try_next_source:
                raise RawRegenerationExhausted(paper_spec, model_attempts) from exc
            raise

    raise RawRegenerationExhausted(paper_spec, model_attempts) from last_exc


def write_markdown_plan(report_root: Path, payload: Dict[str, Any]) -> None:
    lines = [
        "# PEARL Raw-Regeneration Residual Module Plan",
        "",
        f"Created: {payload['created_at']}",
        "",
        "## Scope",
        "",
        "This module handles only `raw_regeneration` residuals whose original raw/source graph had no majority-correct anchor. It regenerates a new source graph and then re-enters the unchanged PEARL repair/root strict gate; it does not merge rows directly and does not overwrite canonical successes.",
        "",
        f"- Queue: `{payload['queue']}`",
        f"- Report root: `{payload['report_root']}`",
        f"- Selected no-anchor residuals: {payload['selected']}",
        f"- Execute mode: {payload['execute']}",
        f"- Canonical merge gate: `{payload.get('merge_gate')}`",
        "- Acceptance before canonical merge remains final CG=1.0, final REA=1.0, usable tier, no duplicate paper_spec, and existing artifacts.",
        "",
        "## Design Basis",
        "",
        "- Local edge repair requires at least one majority-correct anchor. The `preflight:no_anchor_regenerate` rows failed that premise, so bounded prune/repair would create unsupported graph content.",
        "- First-pass regeneration uses the existing `RLT_generator.py` prompt/protocol and writes standard `model_outputs` layout artifacts.",
        "- Source regeneration is not accepted evidence by itself. The regenerated graph must first produce a clean source evaluation, then pass `run_one()` with the same strict final gate used by the canonical receiver.",
        "- Existing 413 canonical successes are read-only and are not re-evaluated by this module.",
    ]
    plan_md = report_root / "raw_regeneration_module_plan.md"
    tmp_path = plan_md.with_name(f".{plan_md.name}.{os.getpid()}.tmp")
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp_path.replace(plan_md)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--report-root", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--paper-specs", nargs="+", default=[])
    parser.add_argument("--paper-specs-file", default="")
    parser.add_argument(
        "--include-lanes",
        nargs="+",
        default=["raw_regeneration"],
        help="Queue lanes to run. Defaults to the original raw_regeneration lane.",
    )
    parser.add_argument(
        "--allow-non-raw-residuals",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow source-regeneration escalation for non-raw residual lanes in a closeout queue.",
    )
    parser.add_argument("--execute", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--provider-profile", default=str(DEFAULT_PROVIDER_PROFILE))
    parser.add_argument(
        "--data-dir",
        default="",
        help=(
            "Directory containing {paper}.json inputs. If omitted or incomplete, inputs are staged from "
            "data/teacher_pool/model_outputs/*/*/*/input_data.json."
        ),
    )
    parser.add_argument("--source-model-outputs", default=str(DEFAULT_MODEL_OUTPUTS))
    parser.add_argument(
        "--source-run-root",
        default="",
        help="Shared raw-regeneration source graph cache root. Report summaries remain under --report-root.",
    )
    parser.add_argument("--generation-model", default="", help="Override source-regeneration model; defaults to row model.")
    parser.add_argument(
        "--fallback-generation-models",
        default="",
        help=(
            "Whitespace/comma-separated source generation models to try after the primary model when a "
            "fresh source graph still fails with a regenerate preflight gate. Defaults to all profile routes."
        ),
    )
    parser.add_argument("--skip-existing-generation", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--api-mode", choices=["chat", "responses", "auto"], default="auto")
    parser.add_argument("--stream", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--generation-timeout", type=float, default=600.0)
    parser.add_argument("--generation-max-tokens", type=int, default=0)
    parser.add_argument("--generation-retries", type=int, default=3)
    parser.add_argument("--generation-retry-sleep", type=float, default=15.0)
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--reasoning-summary", default="")
    parser.add_argument("--save-debug-artifacts", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--evaluation-timeout", type=int, default=900)
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
    parser.add_argument("--max-prune-rounds", type=int, default=2)
    parser.add_argument("--judge-error-retries", type=int, default=2)
    parser.add_argument("--judge-error-retry-sleep", type=float, default=5.0)
    parser.add_argument("--inner-workers", type=int, default=6)
    parser.add_argument("--gemini-min-interval-seconds", type=float, default=6.5)
    parser.add_argument("--judge-max-tokens", type=int, default=2048)
    parser.add_argument("--gemini-max-tokens", type=int, default=12288)
    parser.add_argument("--min-anchor-correct-ratio", type=float, default=0.0)
    parser.add_argument("--max-repair-candidate-ratio", type=float, default=1.0)
    parser.add_argument("--min-raw-cg", type=float, default=0.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue = read_json(resolve_path(args.queue), {})
    report_root = Path(args.report_root) if args.report_root else (
        DEFAULT_OUT_ROOT / f"pearl_edge_repair_raw_regeneration_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    if not report_root.is_absolute():
        report_root = PROJECT_ROOT / report_root
    report_root.mkdir(parents=True, exist_ok=True)
    requested_specs = load_paper_specs(args.paper_specs, args.paper_specs_file)
    selected = select_raw_rows(
        queue,
        args.limit,
        requested_specs,
        include_lanes=args.include_lanes,
        allow_non_raw_residuals=args.allow_non_raw_residuals,
    )
    selected_specs = {str(row.get("paper_spec")) for row in selected}
    missing_requested_specs = [spec for spec in requested_specs if spec not in selected_specs]
    requested_data_dir = resolve_path(args.data_dir) if args.data_dir else Path("__missing_data_dir__")
    input_staging = prepare_input_staging(
        selected,
        report_root=report_root,
        requested_data_dir=requested_data_dir,
        model_outputs_root=resolve_path(args.source_model_outputs),
    )
    data_dir = Path(input_staging["data_dir"])
    provider_profile = read_json(resolve_path(args.provider_profile), {})
    input_source_by_paper = {
        str(item["paper_spec"]).split(":", 1)[-1]: Path(str(item["source"]))
        for item in input_staging.get("copied", [])
        if item.get("paper_spec") and item.get("source")
    }
    plan = [
        build_plan_item(
            row,
            report_root,
            args,
            data_dir,
            input_source_by_paper.get(str(row.get("paper_id") or row.get("paper_spec", "").split(":", 1)[-1])),
            provider_profile if isinstance(provider_profile, dict) else {},
        )
        for row in selected
    ]
    payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "raw_regeneration_then_strict_repair_gate",
        "queue": str(resolve_path(args.queue)),
        "report_root": str(report_root),
        "execute": args.execute,
        "selected": len(selected),
        "filters": {
            "paper_specs": requested_specs,
            "paper_specs_file": str(resolve_path(args.paper_specs_file)) if args.paper_specs_file else "",
            "limit": args.limit,
            "missing_requested_paper_specs": missing_requested_specs,
        },
        "input_staging": input_staging,
        "canonical_gate": {
            "min_final_CG": 1.0,
            "min_final_REA": 1.0,
            "quality_tier": ["A_main", "B_usable", "C_thin"],
        },
        "merge_gate": "scripts/merge_edge_repair_strict_successes.py",
        "plan": plan,
    }
    write_json(report_root / "raw_regeneration_module_plan.json", payload)
    write_markdown_plan(report_root, payload)

    if not args.execute:
        print(json.dumps({
            "status": "planned",
            "report_root": str(report_root),
            "selected": len(selected),
            "missing_requested_paper_specs": len(missing_requested_specs),
            "plan": str(report_root / "raw_regeneration_module_plan.json"),
        }, ensure_ascii=False, indent=2))
        return 0
    if input_staging.get("missing"):
        raise RuntimeError(f"missing input_data for raw regeneration: {input_staging['missing'][:10]}")

    runner_api = load_runner_api()
    generator_api = load_generator_api()
    runner_api["load_env_file"](Path(args.env_file))
    generator_api["load_rlt_env_file"](str(args.env_file))
    if isinstance(provider_profile, dict):
        apply_stage2_provider_profile(provider_profile)
        first_row_model = str(selected[0].get("model") if selected else "gpt-5.5")
        generation_route_model = provider_generation_model(provider_profile, first_row_model, args.generation_model)
        apply_generation_route(provider_profile, generation_route_model)
    os.environ.update(
        {
            "EVAL_INNER_MAX_WORKERS": str(max(1, args.inner_workers)),
            "EVAL_GEMINI_MIN_INTERVAL_SECONDS": str(max(0.0, args.gemini_min_interval_seconds)),
            "EVAL_REQUEST_TIMEOUT": str(max(60, args.timeout)),
            "EVAL_FUTURE_TIMEOUT_SECONDS": str(max(120, args.timeout + 300)),
            "EVAL_JUDGE_MAX_TOKENS": str(max(1024, args.judge_max_tokens)),
            "EVAL_GEMINI_MAX_TOKENS": str(max(8192, args.gemini_max_tokens)),
        }
    )
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    PreflightGateError = runner_api["PreflightGateError"]
    classify_failure_type = runner_api["classify_failure_type"]
    is_retryable_error = runner_api["is_retryable_error"]
    write_progress = runner_api["write_progress"]

    for item in plan:
        paper_spec = str(item["paper_spec"])
        try:
            rows.append(
                run_raw_item(
                    item,
                    args=args,
                    report_root=report_root,
                    env=env,
                    generator_api=generator_api,
                    runner_api=runner_api,
                )
            )
        except Exception as exc:  # noqa: BLE001
            preflight = exc.details if isinstance(exc, PreflightGateError) else None
            failure_type = (
                exc.failure_type
                if isinstance(exc, RawRegenerationExhausted)
                else classify_failure_type(exc)
            )
            if "request was blocked" in str(exc).lower() or "blocked" in str(exc).lower():
                failure_type = "generation_provider_blocked"
            failures.append(
                {
                    "paper_spec": paper_spec,
                    "error": str(exc),
                    "retryable": is_retryable_error(str(exc)),
                    "failure_type": failure_type,
                    "preflight": preflight,
                    "raw_regeneration_model_attempts": (
                        exc.attempts if isinstance(exc, RawRegenerationExhausted) else None
                    ),
                    "strategy": item.get("strategy"),
                }
            )
        write_progress(report_root / "strict_repair_gate", rows, failures=failures, requested=len(plan))

    print(json.dumps({
        "status": "executed",
        "report_root": str(report_root),
        "completed": len(rows),
        "failed": len(failures),
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
