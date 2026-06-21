#!/usr/bin/env python3
"""Diagnose candidate-level ANS gaps after after327 controller rebuilds."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_CONTROLLER = (
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
    / "candidate_ans_gap_diagnostic_v1"
)


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
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp.replace(path)


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def classify(row: dict[str, Any]) -> tuple[str, str, int]:
    if as_bool(row.get("fresh_judge_provider_error")):
        return ("fresh_provider_rerun", "Fresh CG/REA result was provider-contaminated; rerun fresh before editing.", 10)
    if as_bool(row.get("ans_provider_or_rate_error")):
        return ("ans_provider_rerun", "ANS eval has active unresolved provider errors for this candidate.", 20)
    if row.get("candidate_main_factual_ans") in ("", None):
        return ("candidate_ans_missing", "Candidate-level ANS is missing; build candidate-only claims and run ANS before content repair.", 30)
    if as_bool(row.get("fresh_strict_gate_passed")) and as_bool(row.get("ans_guard_passed")) is False:
        return ("content_ans_repair", "Fresh 1/1 is clean and candidate ANS is below row floor.", 40)
    return ("not_ans_gap", "Row is not currently an ANS-gap blocker.", 90)


def main() -> int:
    controller_path = DEFAULT_CONTROLLER
    out_root = DEFAULT_OUT_ROOT
    payload = read_json(controller_path, {})
    rows = payload.get("rows") if isinstance(payload, dict) else []
    diagnostics: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if row.get("controller_route") not in {"ans_regression_repair", "ans_provider_error_rerun_queue"}:
            continue
        action, reason, priority = classify(row)
        diagnostics.append(
            {
                "priority": priority,
                "paper_spec": row.get("paper_spec", ""),
                "candidate_label": row.get("candidate_label", ""),
                "recommended_action": action,
                "reason": reason,
                "fresh_CG": row.get("fresh_CG", ""),
                "fresh_REA": row.get("fresh_REA", ""),
                "fresh_judge_provider_error": row.get("fresh_judge_provider_error", ""),
                "candidate_main_factual_ans": row.get("candidate_main_factual_ans", ""),
                "guard_current_best_main_factual_ans": row.get("guard_current_best_main_factual_ans", ""),
                "guard_margin": row.get("guard_margin", ""),
                "ans_guard_report": row.get("ans_guard_report", ""),
                "ans_eval_dir": row.get("ans_eval_dir", ""),
                "ans_error_count": row.get("ans_error_count", ""),
                "ans_provider_or_rate_error": row.get("ans_provider_or_rate_error", ""),
                "strict_merge_candidates": row.get("strict_merge_candidates", ""),
                "attempt_index_csv": row.get("attempt_index_csv", ""),
            }
        )
    diagnostics.sort(key=lambda row: (row["priority"], row["paper_spec"], row["candidate_label"]))
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_controller": rel(controller_path),
        "out_root": rel(out_root),
        "row_count": len(diagnostics),
        "action_counts": {},
        "rows": diagnostics,
    }
    counts: dict[str, int] = {}
    for row in diagnostics:
        counts[str(row["recommended_action"])] = counts.get(str(row["recommended_action"]), 0) + 1
    summary["action_counts"] = counts
    write_json(out_root / "CANDIDATE_ANS_GAP_DIAGNOSTIC.json", summary)
    write_csv(
        out_root / "CANDIDATE_ANS_GAP_DIAGNOSTIC.csv",
        diagnostics,
        [
            "priority",
            "paper_spec",
            "candidate_label",
            "recommended_action",
            "reason",
            "fresh_CG",
            "fresh_REA",
            "fresh_judge_provider_error",
            "candidate_main_factual_ans",
            "guard_current_best_main_factual_ans",
            "guard_margin",
            "ans_guard_report",
            "ans_eval_dir",
            "ans_error_count",
            "ans_provider_or_rate_error",
            "strict_merge_candidates",
            "attempt_index_csv",
        ],
    )
    print(json.dumps({k: summary[k] for k in ["created_at", "row_count", "action_counts", "out_root"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
