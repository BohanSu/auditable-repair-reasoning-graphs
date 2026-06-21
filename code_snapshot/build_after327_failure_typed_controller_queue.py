#!/usr/bin/env python3
"""Build an after327 failure-typed residual controller queue.

This is an offline planning artifact. It reads the current proposal residuals,
materialized attempt indexes, fresh-eval outputs, and ANS guard reports, then
routes each residual row to the next evidence-bound action. It never writes
canonical accounting and never treats provider-free replay as final acceptance.
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
DEFAULT_RESIDUAL_CSV = (
    RESIDUAL_ROOT
    / "proposed_accounting_merges"
    / "20260606_after_327_gemini56921_node5repair_anssafe"
    / "PROPOSED_TYPED_RESIDUAL_350.csv"
)
DEFAULT_SUMMARY_JSON = DEFAULT_RESIDUAL_CSV.with_name("PROPOSED_FULL_350_SUMMARY.json")
DEFAULT_ATTEMPT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration"
DEFAULT_RUNS_ROOT = RESIDUAL_ROOT / "runs"
DEFAULT_ANS_GUARD_ROOT = RESIDUAL_ROOT / "ans_factscore_style" / "strict_merge_ans_guard"
DEFAULT_STRICT_CANDIDATE_ROOT = RESIDUAL_ROOT / "strict_merge_candidates"
DEFAULT_ANS_EVAL_ROOT = RESIDUAL_ROOT / "ans_factscore_style" / "strict_merge_candidate_eval"
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_after327_failure_typed_lit_diagnostic"
    / "controller_queue_v1"
)

LITERATURE_DESIGN_MAP = [
    {
        "source": "FActScore",
        "url": "https://arxiv.org/abs/2305.14251",
        "controller_rule": "Operate on atomic factual units instead of whole graph rewrites.",
    },
    {
        "source": "SAFE",
        "url": "https://arxiv.org/abs/2403.18802",
        "controller_rule": "Verify long-form factual units against external evidence before acceptance.",
    },
    {
        "source": "VERISCORE",
        "url": "https://arxiv.org/abs/2406.19276",
        "controller_rule": "Separate verifiable anchor claims from unverifiable or structural text.",
    },
    {
        "source": "CORE",
        "url": "https://aclanthology.org/2025.findings-acl.1018/",
        "controller_rule": "Prefer unique informative subclaims; avoid adding obvious duplicate source leaves.",
    },
    {
        "source": "RAGAS",
        "url": "https://arxiv.org/abs/2309.15217",
        "controller_rule": "Separate evidence relevance, faithfulness, and answer quality failures.",
    },
    {
        "source": "RAGChecker",
        "url": "https://arxiv.org/abs/2408.08067",
        "controller_rule": "Route residuals by retrieval/evidence versus generation/reasoning failure.",
    },
    {
        "source": "CRAG",
        "url": "https://arxiv.org/abs/2401.15884",
        "controller_rule": "Use an evidence-quality evaluator to choose corrective retrieval or generation.",
    },
    {
        "source": "RARR",
        "url": "https://arxiv.org/abs/2210.08726",
        "controller_rule": "Revise unsupported content while preserving already-correct text.",
    },
    {
        "source": "Re-Ex",
        "url": "https://arxiv.org/abs/2402.17097",
        "controller_rule": "Explain the factual error before applying a narrow revision.",
    },
    {
        "source": "FAVA",
        "url": "https://arxiv.org/abs/2401.06855",
        "controller_rule": "Use typed factual-error categories to constrain the edit template.",
    },
    {
        "source": "Importance-aware factual recall",
        "url": "https://arxiv.org/abs/2604.03141",
        "controller_rule": "Do not route a candidate as complete when it preserves precision but drops important anchor facts.",
    },
    {
        "source": "Cited but Not Verified",
        "url": "https://arxiv.org/abs/2605.06635",
        "controller_rule": "Treat source presence as insufficient unless the cited span supports the exact graph commitment.",
    },
    {
        "source": "LLM-as-judge bias",
        "url": "https://arxiv.org/abs/2305.17926",
        "controller_rule": "Keep provider and judge failures separate from content failures.",
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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def key_for(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("paper_spec") or ""), str(row.get("candidate_label") or ""))


def load_attempt_rows(attempt_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(attempt_root.rglob("ATTEMPT_INDEX.json")):
        payload = read_json(path, {})
        for row in payload.get("rows", []) if isinstance(payload, dict) else []:
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            enriched["attempt_index_json"] = rel(path)
            enriched["attempt_index_csv"] = rel(path.with_suffix(".csv"))
            rows.append(enriched)
    return rows


def load_fresh_rows(runs_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(runs_root.rglob("FRESH_EVAL_RESULTS.json")):
        payload = read_json(path, {})
        for row in payload.get("rows", []) if isinstance(payload, dict) else []:
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            enriched["fresh_eval_results"] = rel(path)
            rows.append(enriched)
    return rows


def load_ans_guard_rows(ans_guard_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(ans_guard_root.rglob("STRICT_MERGE_ANS_GUARD.json")):
        payload = read_json(path, {})
        for row in payload.get("rows", []) if isinstance(payload, dict) else []:
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            enriched["ans_guard_report"] = rel(path)
            rows.append(enriched)
    return rows


def load_strict_candidate_rows(strict_candidate_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(strict_candidate_root.rglob("STRICT_MERGE_CANDIDATES.json")):
        payload = read_json(path, {})
        for row in payload.get("rows", []) if isinstance(payload, dict) else []:
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            enriched["strict_merge_candidates"] = rel(path)
            rows.append(enriched)
    return rows


def load_ans_eval_status_rows(ans_eval_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for summary_csv in sorted(ans_eval_root.rglob("ans_summary.csv")):
        node_results = summary_csv.parent / "ans_node_results.jsonl"
        errors = summary_csv.parent / "ans_errors.jsonl"
        records: dict[str, dict[str, Any]] = {}
        for path, is_error in ((errors, True), (node_results, False)):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = str(row.get("ans_node_key") or "")
                if not key:
                    key = "|".join([str(row.get("paper_spec") or ""), str(row.get("candidate_label") or ""), str(row.get("claim_id") or ""), str(row.get("node_id") or "")])
                status = str(row.get("status") or "").lower()
                if is_error or status.endswith("error"):
                    records.setdefault(key, {"row": row, "status": "error"})
                elif status == "ok":
                    records[key] = {"row": row, "status": "ok"}
        totals: dict[tuple[str, str], dict[str, int]] = {}
        paper_totals: dict[str, dict[str, int]] = {}
        for record in records.values():
            row = record["row"]
            spec = str(row.get("paper_spec") or "")
            if not spec:
                continue
            candidate_label = str(row.get("candidate_label") or "")
            bucket = totals.setdefault((spec, candidate_label), {"ok": 0, "error": 0})
            paper_bucket = paper_totals.setdefault(spec, {"ok": 0, "error": 0})
            if record["status"] == "error":
                bucket["error"] += 1
                paper_bucket["error"] += 1
            elif record["status"] == "ok":
                bucket["ok"] += 1
                paper_bucket["ok"] += 1
        for (spec, candidate_label), counts in totals.items():
            rows.append(
                {
                    "paper_spec": spec,
                    "candidate_label": candidate_label,
                    "ans_eval_status_scope": "candidate_label" if candidate_label else "paper_spec",
                    "ans_eval_dir": rel(summary_csv.parent),
                    "ans_summary_csv": rel(summary_csv),
                    "ans_errors_jsonl": rel(errors) if errors.exists() else "",
                    "ans_ok_node_count": counts["ok"],
                    "ans_error_count": counts["error"],
                    "ans_provider_or_rate_error": counts["error"] > 0,
                }
            )
        for spec, counts in paper_totals.items():
            rows.append(
                {
                    "paper_spec": spec,
                    "candidate_label": "",
                    "ans_eval_status_scope": "paper_rollup",
                    "ans_eval_dir": rel(summary_csv.parent),
                    "ans_summary_csv": rel(summary_csv),
                    "ans_errors_jsonl": rel(errors) if errors.exists() else "",
                    "ans_ok_node_count": counts["ok"],
                    "ans_error_count": counts["error"],
                    "ans_provider_or_rate_error": counts["error"] > 0,
                }
            )
    return rows


def fresh_rank(row: dict[str, Any]) -> tuple[int, int, float, float, int]:
    clean = 1 if not as_bool(row.get("judge_provider_error")) else 0
    strict = 1 if as_bool(row.get("strict_gate_passed")) else 0
    return (clean, strict, as_float(row.get("CG")), as_float(row.get("REA")), 0)


def build_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[key_for(row)].append(row)
    return out


def best_fresh_for(key: tuple[str, str], fresh_by_key: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, Any]:
    candidates = fresh_by_key.get(key, [])
    if not candidates:
        return {}
    return sorted(candidates, key=fresh_rank, reverse=True)[0]


def ans_rank(row: dict[str, Any]) -> tuple[int, int, float]:
    has_candidate_ans = 0 if row.get("candidate_main_factual_ans") in ("", None) else 1
    source = str(row.get("candidate_main_factual_ans_source") or "")
    source_rank = 1 if source == "candidate_label" else 0
    return (
        1 if as_bool(row.get("ans_guard_passed")) else 0,
        source_rank,
        has_candidate_ans,
        as_float(row.get("guard_margin"), -999.0),
    )


def best_ans_for(key: tuple[str, str], ans_by_key: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, Any]:
    candidates = ans_by_key.get(key, [])
    if not candidates:
        return {}
    return sorted(candidates, key=ans_rank, reverse=True)[0]


def best_ans_for_spec(spec: str, ans_rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in ans_rows if str(row.get("paper_spec") or "") == spec]
    if not candidates:
        return {}
    return sorted(candidates, key=ans_rank, reverse=True)[0]


def best_strict_candidate_for(
    paper_spec: str,
    fresh: dict[str, Any],
    strict_by_spec: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    candidates = strict_by_spec.get(paper_spec, [])
    if not candidates:
        return {}
    fresh_path = str(fresh.get("fresh_eval_results") or "")
    dot = str(fresh.get("dot") or "")
    graph_spec = str(fresh.get("graph_spec") or "")
    exact = [
        row
        for row in candidates
        if str(row.get("candidate_fresh_eval_results") or "") == fresh_path
        or str(row.get("candidate_graph") or "") == dot
        or str(row.get("candidate_graph") or "").replace("final_clean_graph.dot", "graph_spec.json") == graph_spec
    ]
    if exact:
        return sorted(exact, key=lambda row: str(row.get("candidate_label") or ""))[0]
    return sorted(candidates, key=lambda row: str(row.get("candidate_label") or ""))[0]


def classify_without_candidate(row: dict[str, str]) -> tuple[str, str, int]:
    failure_type = row.get("failure_type", "")
    final_cg = as_float(row.get("final_CG"))
    final_rea = as_float(row.get("final_REA"))
    if failure_type == "metric_regression":
        return (
            "pareto_safe_rollback_or_hybrid_selector",
            "Build rollback/hybrid candidates from prior fresh-correct topology; broad regeneration risks CG regression.",
            50,
        )
    if failure_type.startswith("preflight:no_anchor"):
        return (
            "claim_graph_reconstruction_from_source_inventory",
            "No majority-correct anchor exists; use source inventory plus claim graph reconstruction before fresh eval.",
            70,
        )
    if failure_type == "final_judge_failed":
        return (
            "judge_reason_targeted_reconstruction",
            "Treat judge rejection as its own lane; convert judge reason into verification questions and edit only disputed units.",
            45,
        )
    if failure_type == "final_metric_gate_failed" and final_rea >= 1.0 and final_cg < 1.0:
        return (
            "single_or_dual_unique_source_leaf_bridge",
            "REA is already closed; add only source leaves for unique missing anchors and preserve current topology.",
            35,
        )
    if failure_type == "final_metric_gate_failed":
        return (
            "claim_typed_reasoning_rewrite",
            "Fresh reasoning is not fully closed; repair reasoning before coverage leaves.",
            60,
        )
    return ("manual_contract_audit", "Failure type is not covered by automated routes.", 90)


def summarize_attempt(
    attempt: dict[str, Any],
    fresh_by_key: dict[tuple[str, str], list[dict[str, Any]]],
    ans_by_key: dict[tuple[str, str], list[dict[str, Any]]],
    ans_rows: list[dict[str, Any]],
    strict_by_spec: dict[str, list[dict[str, Any]]],
    ans_eval_by_spec: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    key = key_for(attempt)
    fresh = best_fresh_for(key, fresh_by_key)
    spec = str(attempt.get("paper_spec") or "")
    strict_candidate = best_strict_candidate_for(spec, fresh, strict_by_spec) if fresh else {}
    candidate_label = (
        attempt.get("candidate_label", "")
        or strict_candidate.get("candidate_label", "")
        or fresh.get("candidate_label", "")
    )
    ans = best_ans_for((spec, str(candidate_label or "")), ans_by_key)
    if not ans and strict_candidate:
        ans = best_ans_for((spec, str(strict_candidate.get("candidate_label") or "")), ans_by_key)
    has_fresh_for_attempt = bool(fresh)
    if not ans and has_fresh_for_attempt:
        ans = best_ans_for_spec(spec, ans_rows)
    candidate_label_text = str(candidate_label or "")
    ans_eval_candidates = ans_eval_by_spec.get(spec, [])
    candidate_scoped = [
        row
        for row in ans_eval_candidates
        if str(row.get("candidate_label") or "") == candidate_label_text
        and str(row.get("ans_eval_status_scope") or "") == "candidate_label"
    ]
    if not candidate_scoped:
        candidate_scoped = [
            row
            for row in ans_eval_candidates
            if str(row.get("ans_eval_status_scope") or "") in {"paper_rollup", "paper_spec", ""}
        ]
    ans_eval_status = sorted(
        candidate_scoped,
        key=lambda row: (
            int(row.get("ans_error_count") or 0),
            -int(row.get("ans_ok_node_count") or 0),
            str(row.get("ans_eval_dir") or ""),
        ),
    )
    ans_eval = ans_eval_status[0] if ans_eval_status else {}
    return {
        "candidate_label": candidate_label,
        "lane": attempt.get("lane", ""),
        "attempt_index_csv": attempt.get("attempt_index_csv", ""),
        "graph_spec": attempt.get("graph_spec", "") or strict_candidate.get("candidate_graph", "").replace("final_clean_graph.dot", "graph_spec.json"),
        "staged_run_dir": attempt.get("staged_run_dir", ""),
        "preflight_report": attempt.get("preflight_report", ""),
        "final_clean_graph_sha256": attempt.get("final_clean_graph_sha256", ""),
        "passed_local_preflight": as_bool(attempt.get("passed_local_preflight")),
        "covered_entities_local": attempt.get("covered_entities_local", ""),
        "total_entities_local": attempt.get("total_entities_local", ""),
        "premise_support_high_risk_count": attempt.get("premise_support_high_risk_count", ""),
        "row_ans_floor": attempt.get("row_ans_floor", ""),
        "fresh_eval_results": fresh.get("fresh_eval_results", ""),
        "fresh_CG": fresh.get("CG", ""),
        "fresh_REA": fresh.get("REA", ""),
        "fresh_strict_gate_passed": as_bool(fresh.get("strict_gate_passed")) if fresh else "",
        "fresh_judge_provider_error": as_bool(fresh.get("judge_provider_error")) if fresh else "",
        "fresh_status": fresh.get("status", ""),
        "ans_guard_report": ans.get("ans_guard_report", ""),
        "candidate_main_factual_ans": ans.get("candidate_main_factual_ans", ""),
        "guard_current_best_main_factual_ans": ans.get("guard_current_best_main_factual_ans", ""),
        "guard_margin": ans.get("guard_margin", ""),
        "ans_guard_passed": as_bool(ans.get("ans_guard_passed")) if ans else "",
        "mergeable_with_ans_guard": as_bool(ans.get("mergeable_with_ans_guard")) if ans else "",
        "ans_eval_dir": ans_eval.get("ans_eval_dir", ""),
        "ans_error_count": ans_eval.get("ans_error_count", ""),
        "ans_provider_or_rate_error": ans_eval.get("ans_provider_or_rate_error", ""),
        "strict_merge_candidates": strict_candidate.get("strict_merge_candidates", ""),
    }


def choose_attempt(
    attempts: list[dict[str, Any]],
    fresh_by_key: dict[tuple[str, str], list[dict[str, Any]]],
    ans_by_key: dict[tuple[str, str], list[dict[str, Any]]],
    ans_rows: list[dict[str, Any]],
    strict_by_spec: dict[str, list[dict[str, Any]]],
    ans_eval_by_spec: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    if not attempts:
        return {}
    scored: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for attempt in attempts:
        summary = summarize_attempt(attempt, fresh_by_key, ans_by_key, ans_rows, strict_by_spec, ans_eval_by_spec)
        fresh_clean = summary["fresh_judge_provider_error"] is False and summary["fresh_eval_results"] != ""
        provider_blocked = summary["fresh_judge_provider_error"] is True
        local_ready = summary["passed_local_preflight"] and not summary["fresh_eval_results"]
        strict = summary["fresh_strict_gate_passed"] is True and fresh_clean
        ans_failed = strict and summary["ans_guard_passed"] is False
        local_cov = as_float(summary["covered_entities_local"]) / max(as_float(summary["total_entities_local"]), 1.0)
        repair_marker = " ".join(
            [
                str(summary.get("lane") or ""),
                str(summary.get("candidate_label") or ""),
                str(summary.get("attempt_index_csv") or ""),
            ]
        ).lower()
        is_after327_repair = (
            str(summary.get("lane") or "") in {"after327_ans_micro_repair"}
            or "ansmicro" in repair_marker
            or "ans_micro" in repair_marker
        )
        repaired_local_ready = (
            local_ready
            and is_after327_repair
            and as_float(summary.get("premise_support_high_risk_count"), 999.0) == 0.0
        )
        repaired_provider_blocked = (
            provider_blocked
            and is_after327_repair
            and summary["passed_local_preflight"] is True
            and as_float(summary.get("premise_support_high_risk_count"), 999.0) == 0.0
        )
        ans_passed = strict and summary["ans_guard_passed"] is True
        high_risk_raw = summary.get("premise_support_high_risk_count")
        high_risk_known = 1 if high_risk_raw not in ("", None) else 0
        high_risk_score = -as_float(high_risk_raw, 999.0)
        preflight_score = 1 if summary["passed_local_preflight"] is True else 0
        selector_rank = as_float(attempt.get("selector_rank"), 999999.0)
        selector_rank_score = -selector_rank
        recency_key = str(summary.get("attempt_index_csv") or summary.get("candidate_label") or "")
        if provider_blocked or local_ready:
            metric_score = local_cov
            reasoning_score = 0.0
        else:
            metric_score = as_float(summary["fresh_CG"]) if summary["fresh_CG"] != "" else local_cov
            reasoning_score = as_float(summary["fresh_REA"]) if summary["fresh_REA"] != "" else 0.0
        score = (
            1 if ans_passed else 0,
            1 if repaired_local_ready or repaired_provider_blocked else 0,
            1 if ans_failed and not (repaired_local_ready or repaired_provider_blocked) else 0,
            1 if strict else 0,
            1 if provider_blocked else 0,
            1 if provider_blocked or local_ready else 0,
            preflight_score,
            metric_score,
            reasoning_score,
            high_risk_score,
            high_risk_known,
            selector_rank_score,
            recency_key,
        )
        scored.append((score, summary))
    return sorted(scored, key=lambda item: item[0], reverse=True)[0][1]


def route_with_candidate(row: dict[str, str], candidate: dict[str, Any]) -> tuple[str, str, str, int]:
    if not candidate:
        route, action, priority = classify_without_candidate(row)
        return route, action, "needs_candidate_generation", priority

    has_fresh = bool(candidate.get("fresh_eval_results"))
    fresh_provider_error = candidate.get("fresh_judge_provider_error") is True
    fresh_clean = has_fresh and candidate.get("fresh_judge_provider_error") is False
    strict = candidate.get("fresh_strict_gate_passed") is True and fresh_clean
    ans_passed = candidate.get("ans_guard_passed") is True
    ans_failed = strict and candidate.get("ans_guard_passed") is False
    ans_missing = strict and candidate.get("ans_guard_report") == ""
    ans_provider_error = strict and candidate.get("ans_provider_or_rate_error") is True and candidate.get("ans_guard_report") == ""
    local_ready = candidate.get("passed_local_preflight") is True and not has_fresh

    if strict and ans_passed:
        return (
            "strict_candidate_ready_for_proposal_merge",
            "Build/update strict merge proposal and run proposal batch ANS guard before counting this row.",
            "ready_for_merge_guard",
            5,
        )
    if ans_missing:
        if ans_provider_error:
            return (
                "ans_provider_error_rerun_queue",
                "Rerun row-level ANS when quota is healthy; do not merge or regenerate based on partial 429-contaminated ANS.",
                "fresh_1_1_ans_provider_error",
                9,
            )
        return (
            "row_ans_guard_queue",
            "Build ANS claims and run row-level strict merge ANS guard before any proposal merge.",
            "fresh_1_1_needs_row_ans_guard",
            8,
        )
    if ans_failed:
        return (
            "ans_regression_repair",
            "Repair only ANS-heavy added nodes while preserving the fresh 1/1 reasoning topology.",
            "fresh_1_1_ans_failed",
            10,
        )
    if fresh_provider_error:
        return (
            "provider_error_rerun_queue",
            "Rerun standard fresh eval when judge quota is healthy; do not edit content based on provider-error output.",
            "ready_standard_fresh_eval_rerun",
            15,
        )
    if local_ready:
        return (
            "standard_fresh_eval_queue",
            "Run standard fresh eval, then row ANS guard and batch ANS guard if fresh 1/1 is clean.",
            "ready_standard_fresh_eval",
            20,
        )
    if fresh_clean and not strict:
        return (
            "route_specific_candidate_revision",
            "Fresh eval is clean but not 1/1; revise only the failed route-specific contract.",
            "fresh_metric_or_reasoning_failed",
            30,
        )

    route, action, priority = classify_without_candidate(row)
    return route, action, "needs_candidate_generation", priority


def build(args: argparse.Namespace) -> dict[str, Any]:
    residual_csv = resolve(args.residual_csv)
    summary_json = resolve(args.summary_json)
    attempt_root = resolve(args.attempt_root)
    runs_root = resolve(args.runs_root)
    ans_guard_root = resolve(args.ans_guard_root)
    strict_candidate_root = resolve(args.strict_candidate_root)
    ans_eval_root = resolve(args.ans_eval_root)
    out_root = resolve(args.out_root)

    residual_rows = read_csv(residual_csv)
    proposal_summary = read_json(summary_json, {})
    attempt_rows = load_attempt_rows(attempt_root)
    fresh_rows = load_fresh_rows(runs_root)
    ans_rows = load_ans_guard_rows(ans_guard_root)
    strict_candidate_rows = load_strict_candidate_rows(strict_candidate_root)
    ans_eval_rows = load_ans_eval_status_rows(ans_eval_root)
    attempts_by_spec: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempt_rows:
        spec = str(attempt.get("paper_spec") or "")
        if spec:
            attempts_by_spec[spec].append(attempt)
    fresh_by_key = build_index(fresh_rows)
    ans_by_key = build_index(ans_rows)
    strict_by_spec: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for strict_row in strict_candidate_rows:
        spec = str(strict_row.get("paper_spec") or "")
        if spec:
            strict_by_spec[spec].append(strict_row)
    ans_eval_by_spec: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ans_eval_row in ans_eval_rows:
        spec = str(ans_eval_row.get("paper_spec") or "")
        if spec:
            ans_eval_by_spec[spec].append(ans_eval_row)

    controller_rows: list[dict[str, Any]] = []
    for row in residual_rows:
        paper_spec = row.get("paper_spec", "")
        candidate = choose_attempt(
            attempts_by_spec.get(paper_spec, []),
            fresh_by_key,
            ans_by_key,
            ans_rows,
            strict_by_spec,
            ans_eval_by_spec,
        )
        route, next_action, status, priority = route_with_candidate(row, candidate)
        controller_rows.append(
            {
                "priority": priority,
                "paper_spec": paper_spec,
                "model": row.get("model", ""),
                "paper": row.get("paper", ""),
                "failure_type": row.get("failure_type", ""),
                "current_final_CG": row.get("final_CG", ""),
                "current_final_REA": row.get("final_REA", ""),
                "controller_route": route,
                "controller_status": status,
                "next_action": next_action,
                "candidate_label": candidate.get("candidate_label", ""),
                "candidate_lane": candidate.get("lane", ""),
                "attempt_index_csv": candidate.get("attempt_index_csv", ""),
                "graph_spec": candidate.get("graph_spec", ""),
                "staged_run_dir": candidate.get("staged_run_dir", ""),
                "preflight_report": candidate.get("preflight_report", ""),
                "final_clean_graph_sha256": candidate.get("final_clean_graph_sha256", ""),
                "passed_local_preflight": candidate.get("passed_local_preflight", ""),
                "covered_entities_local": candidate.get("covered_entities_local", ""),
                "total_entities_local": candidate.get("total_entities_local", ""),
                "premise_support_high_risk_count": candidate.get("premise_support_high_risk_count", ""),
                "row_ans_floor": candidate.get("row_ans_floor", ""),
                "fresh_eval_results": candidate.get("fresh_eval_results", ""),
                "fresh_CG": candidate.get("fresh_CG", ""),
                "fresh_REA": candidate.get("fresh_REA", ""),
                "fresh_strict_gate_passed": candidate.get("fresh_strict_gate_passed", ""),
                "fresh_judge_provider_error": candidate.get("fresh_judge_provider_error", ""),
                "fresh_status": candidate.get("fresh_status", ""),
                "ans_guard_report": candidate.get("ans_guard_report", ""),
                "candidate_main_factual_ans": candidate.get("candidate_main_factual_ans", ""),
                "guard_current_best_main_factual_ans": candidate.get("guard_current_best_main_factual_ans", ""),
                "guard_margin": candidate.get("guard_margin", ""),
                "ans_guard_passed": candidate.get("ans_guard_passed", ""),
                "mergeable_with_ans_guard": candidate.get("mergeable_with_ans_guard", ""),
                "ans_eval_dir": candidate.get("ans_eval_dir", ""),
                "ans_error_count": candidate.get("ans_error_count", ""),
                "ans_provider_or_rate_error": candidate.get("ans_provider_or_rate_error", ""),
                "strict_merge_candidates": candidate.get("strict_merge_candidates", ""),
            }
        )

    controller_rows.sort(
        key=lambda item: (
            int(item["priority"]),
            str(item["model"]),
            str(item["paper"]),
            str(item["candidate_label"]),
        )
    )
    status_counts = Counter(row["controller_status"] for row in controller_rows)
    route_counts = Counter(row["controller_route"] for row in controller_rows)
    failure_counts = Counter(row["failure_type"] for row in controller_rows)

    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "after327_failure_typed_controller_queue",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_residual_csv": rel(residual_csv),
        "source_summary_json": rel(summary_json),
        "source_attempt_root": rel(attempt_root),
        "source_runs_root": rel(runs_root),
        "source_ans_guard_root": rel(ans_guard_root),
        "source_strict_candidate_root": rel(strict_candidate_root),
        "source_ans_eval_root": rel(ans_eval_root),
        "out_root": rel(out_root),
        "current_strict_success_rows": proposal_summary.get("strict_success_rows"),
        "current_typed_residual_rows": proposal_summary.get("typed_residual_rows"),
        "controller_row_count": len(controller_rows),
        "route_counts": dict(route_counts),
        "status_counts": dict(status_counts),
        "failure_type_counts": dict(failure_counts),
        "attempt_rows_scanned": len(attempt_rows),
        "fresh_rows_scanned": len(fresh_rows),
        "ans_guard_rows_scanned": len(ans_rows),
        "strict_candidate_rows_scanned": len(strict_candidate_rows),
        "ans_eval_status_rows_scanned": len(ans_eval_rows),
        "literature_design_map": LITERATURE_DESIGN_MAP,
        "strict_acceptance_contract": {
            "fresh_CG": 1.0,
            "fresh_REA": 1.0,
            "judge_provider_error": False,
            "fresh_eval_hash_matches": True,
            "row_ANS": "non-regression",
            "proposal_batch_ANS": "at_or_above_canonical_300_floor",
        },
    }
    report = {"summary": summary, "rows": controller_rows}
    fieldnames = [
        "priority",
        "paper_spec",
        "model",
        "paper",
        "failure_type",
        "current_final_CG",
        "current_final_REA",
        "controller_route",
        "controller_status",
        "next_action",
        "candidate_label",
        "candidate_lane",
        "attempt_index_csv",
        "graph_spec",
        "staged_run_dir",
        "preflight_report",
        "final_clean_graph_sha256",
        "passed_local_preflight",
        "covered_entities_local",
        "total_entities_local",
        "premise_support_high_risk_count",
        "row_ans_floor",
        "fresh_eval_results",
        "fresh_CG",
        "fresh_REA",
        "fresh_strict_gate_passed",
        "fresh_judge_provider_error",
        "fresh_status",
        "ans_guard_report",
        "candidate_main_factual_ans",
        "guard_current_best_main_factual_ans",
        "guard_margin",
        "ans_guard_passed",
        "mergeable_with_ans_guard",
        "ans_eval_dir",
        "ans_error_count",
        "ans_provider_or_rate_error",
        "strict_merge_candidates",
    ]
    write_csv(out_root / "AFTER327_CONTROLLER_QUEUE.csv", controller_rows, fieldnames)
    write_json(out_root / "AFTER327_CONTROLLER_QUEUE.json", report)
    write_json(out_root / "AFTER327_CONTROLLER_SUMMARY.json", summary)
    write_text(out_root / "AFTER327_CONTROLLER_QUEUE.md", render_markdown(summary, controller_rows))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# After327 Failure-Typed Controller Queue",
        "",
        f"Created: `{summary['created_at']}`",
        "",
        "This is an offline queue. It does not write canonical accounting and it does not relax the fresh-eval or ANS gates.",
        "",
        "## Current State",
        "",
        f"- Strict rows: `{summary.get('current_strict_success_rows')}`",
        f"- Residual rows: `{summary.get('current_typed_residual_rows')}`",
        f"- Provider calls: `{summary.get('provider_calls')}`",
        f"- Canonical accounting write: `{summary.get('canonical_accounting_write')}`",
        "",
        "## Route Counts",
        "",
    ]
    for route, count in sorted(summary["route_counts"].items()):
        lines.append(f"- `{route}`: `{count}`")
    lines.extend(["", "## Status Counts", ""])
    for status, count in sorted(summary["status_counts"].items()):
        lines.append(f"- `{status}`: `{count}`")
    lines.extend(["", "## Top Queue", ""])
    lines.append("| Priority | Paper Spec | Route | Status | Candidate | Next Action |")
    lines.append("|---:|---|---|---|---|---|")
    for row in rows[:12]:
        lines.append(
            "| {priority} | `{paper_spec}` | `{controller_route}` | `{controller_status}` | `{candidate_label}` | {next_action} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Literature-Grounded Rules",
            "",
        ]
    )
    for item in summary["literature_design_map"]:
        lines.append(f"- `{item['source']}`: {item['controller_rule']} {item['url']}")
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- `{summary['out_root']}/AFTER327_CONTROLLER_QUEUE.csv`",
            f"- `{summary['out_root']}/AFTER327_CONTROLLER_QUEUE.json`",
            f"- `{summary['out_root']}/AFTER327_CONTROLLER_SUMMARY.json`",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--residual-csv", default=str(DEFAULT_RESIDUAL_CSV))
    parser.add_argument("--summary-json", default=str(DEFAULT_SUMMARY_JSON))
    parser.add_argument("--attempt-root", default=str(DEFAULT_ATTEMPT_ROOT))
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    parser.add_argument("--ans-guard-root", default=str(DEFAULT_ANS_GUARD_ROOT))
    parser.add_argument("--strict-candidate-root", default=str(DEFAULT_STRICT_CANDIDATE_ROOT))
    parser.add_argument("--ans-eval-root", default=str(DEFAULT_ANS_EVAL_ROOT))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
