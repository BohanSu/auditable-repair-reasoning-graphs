#!/usr/bin/env python3
"""Build a strict merge-candidate union from controller-ready rows.

This tool only writes a local candidate package. It does not call providers and
does not rewrite canonical accounting.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


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
    / "strict_merge_candidates"
    / "20260607_controller_ready23_after_provider_recovery"
)


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def falsy(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value.strip().lower() in {"", "0", "false", "no", "n"}
    return not bool(value)


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def append_field(fieldnames: List[str], field: str) -> None:
    if field not in fieldnames:
        fieldnames.append(field)


def validate_controller_row(row: Dict[str, Any]) -> None:
    spec = row.get("paper_spec", "")
    if row.get("controller_route") != "strict_candidate_ready_for_proposal_merge":
        raise RuntimeError(f"controller row is not ready for merge: {spec}")
    if row.get("controller_status") != "ready_for_merge_guard":
        raise RuntimeError(f"controller row status is not ready_for_merge_guard: {spec}")
    if not truthy(row.get("fresh_strict_gate_passed")):
        raise RuntimeError(f"fresh strict gate did not pass: {spec}")
    if to_float(row.get("fresh_CG")) < 1.0 or to_float(row.get("fresh_REA")) < 1.0:
        raise RuntimeError(f"fresh metrics are not 1/1: {spec}")
    if not falsy(row.get("fresh_judge_provider_error")):
        raise RuntimeError(f"fresh judge provider error present: {spec}")
    if not truthy(row.get("ans_guard_passed")):
        raise RuntimeError(f"ANS guard did not pass: {spec}")
    if not truthy(row.get("mergeable_with_ans_guard")):
        raise RuntimeError(f"ANS guard marks row unmergeable: {spec}")
    if not falsy(row.get("ans_provider_or_rate_error")):
        raise RuntimeError(f"ANS provider/rate error present: {spec}")
    for key in ("fresh_eval_results", "ans_guard_report", "strict_merge_candidates"):
        path = resolve_path(str(row.get(key) or ""))
        if not path.exists():
            raise RuntimeError(f"{key} missing for {spec}: {path}")


def find_candidate_row(queue_row: Dict[str, Any]) -> Dict[str, Any]:
    package_path = resolve_path(str(queue_row.get("strict_merge_candidates") or ""))
    package = read_json(package_path)
    rows = package.get("rows") if isinstance(package, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"strict merge package lacks rows: {package_path}")
    spec = str(queue_row.get("paper_spec") or "")
    label = str(queue_row.get("candidate_label") or "")
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("paper_spec") or "") == spec
        and str(row.get("candidate_label") or "") == label
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one candidate for {spec} / {label}, got {len(matches)}")
    return dict(matches[0])


def validate_candidate_row(queue_row: Dict[str, Any], candidate: Dict[str, Any]) -> None:
    spec = str(queue_row.get("paper_spec") or "")
    if not truthy(candidate.get("mergeable")):
        raise RuntimeError(f"candidate is not mergeable: {spec}")
    if to_float(candidate.get("candidate_CG")) < 1.0 or to_float(candidate.get("candidate_REA")) < 1.0:
        raise RuntimeError(f"candidate metrics are not 1/1: {spec}")
    fresh_eval = resolve_path(str(candidate.get("candidate_fresh_eval_results") or ""))
    graph = resolve_path(str(candidate.get("candidate_graph") or ""))
    eval_dir = resolve_path(str(candidate.get("candidate_eval_dir") or ""))
    if not fresh_eval.exists():
        raise RuntimeError(f"candidate fresh eval missing: {spec}")
    if not graph.exists():
        raise RuntimeError(f"candidate graph missing: {spec}")
    if not eval_dir.exists():
        raise RuntimeError(f"candidate eval dir missing: {spec}")
    controller_fresh_eval = rel(resolve_path(str(queue_row.get("fresh_eval_results") or "")))
    candidate_fresh_eval = rel(fresh_eval)
    if controller_fresh_eval != candidate_fresh_eval:
        raise RuntimeError(
            f"fresh eval path mismatch for {spec}: controller={controller_fresh_eval}, candidate={candidate_fresh_eval}"
        )
    expected_hash = str(candidate.get("candidate_fresh_eval_results_sha256") or "")
    actual_hash = sha256_file(fresh_eval)
    if not expected_hash or expected_hash != actual_hash:
        raise RuntimeError(f"candidate fresh eval hash mismatch: {spec}")


def build(args: argparse.Namespace) -> Dict[str, Any]:
    controller_path = resolve_path(args.controller_queue)
    controller = read_json(controller_path)
    rows = controller.get("rows") if isinstance(controller, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"controller queue lacks rows: {controller_path}")

    merge_rows: List[Dict[str, Any]] = []
    source_package_counts: Counter[str] = Counter()
    source_ans_guards: set[str] = set()
    for queue_row in rows:
        if not isinstance(queue_row, dict):
            continue
        validate_controller_row(queue_row)
        candidate = find_candidate_row(queue_row)
        validate_candidate_row(queue_row, candidate)
        source_package = rel(resolve_path(str(queue_row.get("strict_merge_candidates") or "")))
        source_package_counts[source_package] += 1
        source_ans_guards.add(rel(resolve_path(str(queue_row.get("ans_guard_report") or ""))))
        merged = dict(candidate)
        merged.update(
            {
                "controller_route": queue_row.get("controller_route", ""),
                "controller_status": queue_row.get("controller_status", ""),
                "controller_failure_type": queue_row.get("failure_type", ""),
                "ans_guard_report": queue_row.get("ans_guard_report", ""),
                "candidate_main_factual_ans": queue_row.get("candidate_main_factual_ans", ""),
                "guard_current_best_main_factual_ans": queue_row.get("guard_current_best_main_factual_ans", ""),
                "guard_margin": queue_row.get("guard_margin", ""),
                "ans_guard_passed": queue_row.get("ans_guard_passed", ""),
                "mergeable_with_ans_guard": queue_row.get("mergeable_with_ans_guard", ""),
                "source_strict_merge_candidates": source_package,
            }
        )
        merge_rows.append(merged)

    specs = [str(row.get("paper_spec") or "") for row in merge_rows]
    dupes = sorted(spec for spec, count in Counter(specs).items() if spec and count > 1)
    if dupes:
        raise RuntimeError(f"duplicate paper_spec rows in union: {dupes}")

    out_root = resolve_path(args.out_root)
    fieldnames: List[str] = []
    for row in merge_rows:
        for field in row.keys():
            append_field(fieldnames, field)

    out_root.mkdir(parents=True, exist_ok=True)
    write_csv(out_root / "STRICT_MERGE_CANDIDATES.csv", merge_rows, fieldnames)
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_controller_queue": rel(controller_path),
        "out_root": rel(out_root),
        "merge_candidate_count": len(merge_rows),
        "source_package_counts": dict(sorted(source_package_counts.items())),
        "source_ans_guards": sorted(source_ans_guards),
        "merge_policy": {
            "requires_controller_route": "strict_candidate_ready_for_proposal_merge",
            "requires_fresh_CG": 1.0,
            "requires_fresh_REA": 1.0,
            "requires_no_provider_error": True,
            "requires_ans_guard_passed": True,
            "requires_fresh_eval_hash_match": True,
        },
        "rows": merge_rows,
    }
    write_json(out_root / "STRICT_MERGE_CANDIDATES.json", report)

    lines = [
        "# Controller-Filtered Strict Merge Candidates",
        "",
        f"Created: {report['created_at']}",
        f"Canonical accounting write executed: `{report['canonical_accounting_write']}`",
        f"Merge candidate count: `{len(merge_rows)}`",
        "",
        "| paper_spec | candidate | CG | REA | ANS | margin |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in merge_rows:
        lines.append(
            f"| {row.get('paper_spec', '')} | {row.get('candidate_label', '')} | "
            f"{row.get('candidate_CG', '')} | {row.get('candidate_REA', '')} | "
            f"{row.get('candidate_main_factual_ans', '')} | {row.get('guard_margin', '')} |"
        )
    write_text(out_root / "STRICT_MERGE_CANDIDATES.md", "\n".join(lines) + "\n")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-queue", default=str(DEFAULT_CONTROLLER))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


def main() -> int:
    report = build(parse_args())
    print(
        json.dumps(
            {
                "out_root": report["out_root"],
                "merge_candidate_count": report["merge_candidate_count"],
                "canonical_accounting_write": report["canonical_accounting_write"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
