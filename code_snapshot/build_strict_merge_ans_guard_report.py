#!/usr/bin/env python3
"""Build an ANS guard report for strict merge candidates."""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_MERGE_CANDIDATES = RESIDUAL_ROOT / "strict_merge_candidates" / "20260603_local_window_v5_strict_ans" / "STRICT_MERGE_CANDIDATES.json"
DEFAULT_ANS_SUMMARY = RESIDUAL_ROOT / "ans_factscore_style" / "strict_merge_candidate_eval" / "20260603_local_window_v5_strict_ans" / "ans_summary.csv"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "ans_factscore_style" / "strict_merge_ans_guard" / "20260603_local_window_v5_strict_ans"


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


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


def main_factual_ans(summary_rows: List[Dict[str, str]]) -> float:
    for row in summary_rows:
        if row.get("scope") == "main_factual_nodes" and row.get("group") == "ALL":
            return float(row.get("ANS") or 0.0)
    raise RuntimeError("main_factual_nodes/ALL row not found")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def per_candidate_main_factual_ans(ans_summary_path: Path) -> tuple[Dict[tuple[str, str], float], Dict[str, float], bool, bool]:
    node_results = read_jsonl(ans_summary_path.parent / "ans_node_results.jsonl")
    candidate_totals: Dict[tuple[str, str], Dict[str, int]] = {}
    paper_totals: Dict[str, Dict[str, int]] = {}
    saw_candidate_label = False
    for row in node_results:
        paper_spec = str(row.get("paper_spec") or "")
        if not paper_spec:
            continue
        candidate_label = str(row.get("candidate_label") or "")
        if candidate_label:
            saw_candidate_label = True
        atomic = int(row.get("atomic_fact_count") or 0)
        supported = int(row.get("supported_fact_count") or 0)
        paper_bucket = paper_totals.setdefault(paper_spec, {"atomic": 0, "supported": 0})
        paper_bucket["atomic"] += atomic
        paper_bucket["supported"] += supported
        candidate_bucket = candidate_totals.setdefault((paper_spec, candidate_label), {"atomic": 0, "supported": 0})
        candidate_bucket["atomic"] += atomic
        candidate_bucket["supported"] += supported
    candidate_ans = {
        key: values["supported"] / values["atomic"]
        for key, values in candidate_totals.items()
        if values["atomic"] > 0
    }
    paper_ans = {
        paper_spec: values["supported"] / values["atomic"]
        for paper_spec, values in paper_totals.items()
        if values["atomic"] > 0
    }
    return candidate_ans, paper_ans, saw_candidate_label, bool(node_results)


def build(args: argparse.Namespace) -> Dict[str, Any]:
    merge_path = resolve_path(args.merge_candidates)
    ans_summary_path = resolve_path(args.ans_summary)
    out_root = resolve_path(args.out_root)
    package = json.loads(merge_path.read_text(encoding="utf-8"))
    rows = package.get("rows") if isinstance(package, dict) else []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"expected at least one merge candidate row, got {len(rows) if isinstance(rows, list) else 'bad'}")
    summary_ans = main_factual_ans(read_csv(ans_summary_path))
    candidate_ans, paper_ans, candidate_level_available, node_level_available = per_candidate_main_factual_ans(ans_summary_path)
    guarded_rows: List[Dict[str, Any]] = []
    filtered_rows: List[Dict[str, Any]] = []
    for candidate in rows:
        paper_spec = str(candidate.get("paper_spec") or "")
        candidate_label = str(candidate.get("candidate_label") or "")
        ans_value = candidate_ans.get((paper_spec, candidate_label))
        ans_source = "candidate_label"
        if ans_value is None:
            if candidate_level_available:
                ans_source = "missing_candidate_ans"
            elif paper_spec in paper_ans:
                ans_value = paper_ans[paper_spec]
                ans_source = "paper_spec"
            elif node_level_available:
                ans_source = "missing_paper_ans"
            else:
                ans_value = summary_ans
                ans_source = "summary"
        guard = float(candidate.get("current_best_main_factual_ans") or 0.0)
        passed = ans_value is not None and ans_value >= guard
        mergeable = bool(candidate.get("mergeable") is True and passed)
        guarded = {
            "paper_spec": paper_spec,
            "candidate_label": candidate_label,
            "candidate_CG": candidate.get("candidate_CG", ""),
            "candidate_REA": candidate.get("candidate_REA", ""),
            "candidate_main_factual_ans": ans_value,
            "candidate_main_factual_ans_source": ans_source,
            "guard_current_best_main_factual_ans": guard,
            "guard_margin": (ans_value - guard) if ans_value is not None else "",
            "ans_guard_passed": passed,
            "mergeable_with_ans_guard": mergeable,
        }
        guarded_rows.append(guarded)
        if mergeable:
            filtered = dict(candidate)
            filtered["ans_guard_passed"] = True
            filtered["candidate_main_factual_ans"] = ans_value
            filtered["candidate_main_factual_ans_source"] = ans_source
            filtered["guard_current_best_main_factual_ans"] = guard
            filtered["guard_margin"] = ans_value - guard if ans_value is not None else ""
            filtered_rows.append(filtered)
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_merge_candidates": rel(merge_path),
        "source_ans_summary": rel(ans_summary_path),
        "out_root": rel(out_root),
        "candidate_count": len(rows),
        "summary_main_factual_ans": summary_ans,
        "candidate_level_ans_available": candidate_level_available,
        "node_level_ans_available": node_level_available,
        "paper_level_ans_available": bool(paper_ans),
        "ans_guard_passed_count": sum(1 for row in guarded_rows if row["ans_guard_passed"]),
        "mergeable_with_ans_guard_count": len(filtered_rows),
        "rows": guarded_rows,
    }
    out_root.mkdir(parents=True, exist_ok=True)
    write_json(out_root / "STRICT_MERGE_ANS_GUARD.json", report)
    filtered_package = dict(package)
    filtered_package["source_merge_candidates_before_ans_guard"] = rel(merge_path)
    filtered_package["source_ans_guard"] = rel(out_root / "STRICT_MERGE_ANS_GUARD.json")
    filtered_package["merge_candidate_count_before_ans_guard"] = len(rows)
    filtered_package["merge_candidate_count"] = len(filtered_rows)
    filtered_package["rows"] = filtered_rows
    write_json(out_root / "ANS_FILTERED_STRICT_MERGE_CANDIDATES.json", filtered_package)
    lines = [
        "# Strict Merge ANS Guard",
        "",
        f"Candidates: `{len(rows)}`",
        f"Passed: `{report['mergeable_with_ans_guard_count']}`",
        f"Summary main-factual ANS: `{summary_ans:.12f}`",
        "",
        "| Paper spec | Candidate ANS | Guard ANS | Margin | Mergeable |",
        "|---|---:|---:|---:|---|",
    ]
    for row in guarded_rows:
        candidate_ans_text = (
            f"{row['candidate_main_factual_ans']:.12f}" if row.get("candidate_main_factual_ans") is not None else "NA"
        )
        guard_margin_text = f"{row['guard_margin']:.12f}" if row.get("guard_margin") not in ("", None) else "NA"
        lines.append(
            "| `{}` | `{}` | `{:.12f}` | `{}` | `{}` |".format(
                row["paper_spec"],
                candidate_ans_text,
                row["guard_current_best_main_factual_ans"],
                guard_margin_text,
                row["mergeable_with_ans_guard"],
            )
        )
    lines.append("")
    write_text(out_root / "STRICT_MERGE_ANS_GUARD.md", "\n".join(lines))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merge-candidates", default=str(DEFAULT_MERGE_CANDIDATES))
    parser.add_argument("--ans-summary", default=str(DEFAULT_ANS_SUMMARY))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
