#!/usr/bin/env python3
"""Build strict merge-candidate package directly from fresh eval results.

This adapter is for already staged, provider-free residual candidates whose
fresh evaluation has just passed CG=1.0/REA=1.0. It emits the same
STRICT_MERGE_CANDIDATES schema used by downstream ANS and proposal builders.
It does not write canonical accounting.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_ACCOUNTING = PACKAGE_ROOT / "01_final_accounting" / "FULL_350_ACCOUNTING.csv"
DEFAULT_QUEUE = RESIDUAL_ROOT / "RESIDUAL_50_CLOSEOUT_QUEUE.csv"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "strict_merge_candidates" / "fresh_eval_direct"


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def candidate_graph_path(row: dict[str, Any]) -> str:
    dot = str(row.get("dot") or "").strip()
    if dot:
        return dot
    staged_run_dir = str(row.get("staged_run_dir") or "").strip()
    if staged_run_dir:
        return rel(resolve_path(staged_run_dir) / "final_clean_graph.dot")
    graph_spec = str(row.get("graph_spec") or "").strip()
    if graph_spec:
        return rel(resolve_path(graph_spec).parent / "final_clean_graph.dot")
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh-eval-results", required=True)
    parser.add_argument("--accounting", default=str(DEFAULT_ACCOUNTING))
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fresh_path = resolve_path(args.fresh_eval_results)
    fresh = read_json(fresh_path)
    rows = fresh.get("rows") if isinstance(fresh, dict) else []
    accounting_by_spec = {row.get("paper_spec", ""): row for row in read_csv(resolve_path(args.accounting))}
    queue_by_spec = {row.get("paper_spec", ""): row for row in read_csv(resolve_path(args.queue))}

    merge_rows: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if (
            row.get("strict_gate_passed") is not True
            or row.get("judge_provider_error") is True
            or float(row.get("CG") or 0) < 1.0
            or float(row.get("REA") or 0) < 1.0
        ):
            continue
        spec = str(row.get("paper_spec") or "")
        source = accounting_by_spec.get(spec, {})
        queue = queue_by_spec.get(spec, {})
        merge_rows.append(
            {
                "paper_spec": spec,
                "model": row.get("model", ""),
                "candidate_label": row.get("candidate_label", ""),
                "source_current_outcome": source.get("current_outcome", ""),
                "source_failure_type": source.get("failure_type", row.get("failure_type", "")),
                "source_final_CG": source.get("final_CG", ""),
                "source_final_REA": source.get("final_REA", ""),
                "candidate_CG": row.get("CG", ""),
                "candidate_REA": row.get("REA", ""),
                "candidate_valid_reasoning_steps": row.get("valid_reasoning_steps", ""),
                "candidate_total_reasoning_steps": row.get("total_reasoning_steps", ""),
                "candidate_covered_entities": row.get("covered_entities", ""),
                "candidate_total_entities": row.get("total_entities", ""),
                "candidate_graph": candidate_graph_path(row),
                "candidate_eval_dir": row.get("eval_dir", ""),
                "candidate_fresh_eval_results": rel(fresh_path),
                "candidate_fresh_eval_results_sha256": sha256_file(fresh_path),
                "candidate_preflight_report": row.get("preflight_report", ""),
                "candidate_patch": row.get("attempt_path", ""),
                "raw_main_factual_ans": queue.get("raw_main_factual_ans", ""),
                "step2_main_factual_ans": queue.get("step2_main_factual_ans", ""),
                "current_best_main_factual_ans": queue.get("current_best_main_factual_ans", ""),
                "mergeable": True,
                "canonical_write_executed": False,
            }
        )

    out_root = resolve_path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "paper_spec",
        "model",
        "candidate_label",
        "source_current_outcome",
        "source_failure_type",
        "source_final_CG",
        "source_final_REA",
        "candidate_CG",
        "candidate_REA",
        "candidate_valid_reasoning_steps",
        "candidate_total_reasoning_steps",
        "candidate_covered_entities",
        "candidate_total_entities",
        "candidate_graph",
        "candidate_eval_dir",
        "candidate_fresh_eval_results",
        "candidate_fresh_eval_results_sha256",
        "candidate_preflight_report",
        "candidate_patch",
        "raw_main_factual_ans",
        "step2_main_factual_ans",
        "current_best_main_factual_ans",
        "mergeable",
        "canonical_write_executed",
    ]
    write_csv(out_root / "STRICT_MERGE_CANDIDATES.csv", merge_rows, fieldnames)
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_fresh_eval_results": rel(fresh_path),
        "out_root": rel(out_root),
        "merge_candidate_count": len(merge_rows),
        "merge_policy": {
            "requires_strict_gate_passed": True,
            "requires_final_CG": 1.0,
            "requires_final_REA": 1.0,
            "requires_no_provider_error": True,
            "requires_fresh_eval_results_sha256": True,
        },
        "rows": merge_rows,
    }
    write_json(out_root / "STRICT_MERGE_CANDIDATES.json", report)
    lines = [
        "# Strict Merge Candidates",
        "",
        f"Created: {report['created_at']}",
        f"Canonical accounting write executed: `{report['canonical_accounting_write']}`",
        f"Merge candidate count: `{len(merge_rows)}`",
        "",
        "| paper_spec | candidate | CG | REA | provider clean |",
        "|---|---|---:|---:|---|",
    ]
    for row in merge_rows:
        lines.append(
            f"| {row['paper_spec']} | {row['candidate_label']} | {row['candidate_CG']} | {row['candidate_REA']} | `true` |"
        )
    write_text(out_root / "STRICT_MERGE_CANDIDATES.md", "\n".join(lines) + "\n")
    print(json.dumps({"out_root": report["out_root"], "merge_candidate_count": len(merge_rows)}, ensure_ascii=False, indent=2))
    return 0 if merge_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
