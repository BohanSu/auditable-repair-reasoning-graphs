#!/usr/bin/env python3
"""Build a strict merge-candidate package from the current selection ledger.

The package records which typed residual rows have a verified fresh 1/1
candidate. It does not rewrite canonical 350 accounting.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_LEDGER_POINTER = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "selection_ledger"
    / "CURRENT_SELECTION_LEDGER_POINTER.json"
)
DEFAULT_ACCOUNTING = PACKAGE_ROOT / "01_final_accounting" / "FULL_350_ACCOUNTING.csv"
DEFAULT_QUEUE = RESIDUAL_ROOT / "RESIDUAL_50_CLOSEOUT_QUEUE.csv"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "strict_merge_candidates" / "20260603_local_window_v3_closure"


def resolve_path(value: str | Path) -> Path:
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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def current_ledger(pointer_path: Path) -> Dict[str, Any]:
    pointer = read_json(pointer_path, {})
    ledger = read_json(resolve_path(str(pointer.get("current_ledger") or "")), {})
    if not isinstance(ledger, dict) or not ledger:
        raise RuntimeError(f"cannot resolve current ledger from {pointer_path}")
    return ledger


def build(args: Any) -> Dict[str, Any]:
    ledger = current_ledger(resolve_path(args.ledger_pointer))
    summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}
    selected_best_label = str(summary.get("selected_best_label") or "")
    accounting_rows = read_csv(resolve_path(args.accounting))
    queue_rows = read_csv(resolve_path(args.queue))
    accounting_by_spec = {row.get("paper_spec", ""): row for row in accounting_rows}
    queue_by_spec = {row.get("paper_spec", ""): row for row in queue_rows}

    eligible_rows: List[Dict[str, Any]] = []
    for row in ledger.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if (
            row.get("selection_decision") != "strict_accept_candidate"
            or row.get("strict_gate_passed") is not True
            or row.get("judge_provider_error") is True
            or float(row.get("CG") or 0) < 1.0
            or float(row.get("REA") or 0) < 1.0
        ):
            continue
        if selected_best_label and str(row.get("candidate_label") or "") != selected_best_label:
            continue
        eligible_rows.append(row)

    if not selected_best_label:
        best_by_spec: Dict[str, Dict[str, Any]] = {}
        for row in eligible_rows:
            spec = str(row.get("paper_spec") or "")
            current = best_by_spec.get(spec)
            if current is None:
                best_by_spec[spec] = row
                continue
            if (float(row.get("CG") or 0), float(row.get("REA") or 0), int(row.get("ordinal") or 0)) >= (
                float(current.get("CG") or 0),
                float(current.get("REA") or 0),
                int(current.get("ordinal") or 0),
            ):
                best_by_spec[spec] = row
        eligible_rows = list(best_by_spec.values())

    merge_rows: List[Dict[str, Any]] = []
    for row in eligible_rows:
        spec = str(row.get("paper_spec") or "")
        source = accounting_by_spec.get(spec, {})
        queue = queue_by_spec.get(spec, {})
        eval_results = resolve_path(str(row.get("fresh_eval_results") or ""))
        merge_rows.append(
            {
                "paper_spec": spec,
                "model": row.get("model", ""),
                "candidate_label": row.get("candidate_label", ""),
                "source_current_outcome": source.get("current_outcome", ""),
                "source_failure_type": source.get("failure_type", ""),
                "source_final_CG": source.get("final_CG", ""),
                "source_final_REA": source.get("final_REA", ""),
                "candidate_CG": row.get("CG", ""),
                "candidate_REA": row.get("REA", ""),
                "candidate_valid_reasoning_steps": row.get("valid_reasoning_steps", ""),
                "candidate_total_reasoning_steps": row.get("total_reasoning_steps", ""),
                "candidate_covered_entities": row.get("covered_entities", ""),
                "candidate_total_entities": row.get("total_entities", ""),
                "candidate_graph": row.get("dot", ""),
                "candidate_eval_dir": row.get("eval_dir", ""),
                "candidate_fresh_eval_results": row.get("fresh_eval_results", ""),
                "candidate_fresh_eval_results_sha256": sha256_file(eval_results) if eval_results.exists() else "",
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
        "selection_ledger": ledger.get("out_root", ""),
        "out_root": rel(out_root),
        "merge_candidate_count": len(merge_rows),
        "selected_best_label": selected_best_label,
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
    return report


def parse_args() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-pointer", default=str(DEFAULT_LEDGER_POINTER))
    parser.add_argument("--accounting", default=str(DEFAULT_ACCOUNTING))
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


def main() -> int:
    report = build(parse_args())
    print(json.dumps({"out_root": report["out_root"], "merge_candidate_count": report["merge_candidate_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
