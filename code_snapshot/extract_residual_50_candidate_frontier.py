#!/usr/bin/env python3
"""Extract candidate-frontier metrics from residual-50 closeout runs.

This is offline only. It scans executed closeout run folders, summarizes each
fresh final evaluation, and writes a compact frontier table for choosing the
next coverage-preserving repair attempt.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
    / "10_standard_flow_350_subset_package"
    / "09_residual_50_closeout"
    / "runs"
)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def infer_paper_spec(run_root: Path) -> str:
    plan = read_json(run_root / "residual_50_closure_engine_plan.json", {})
    rows = plan.get("selected_rows") if isinstance(plan, dict) else None
    if isinstance(rows, list) and rows:
        return str(rows[0].get("paper_spec") or "")
    return ""


def candidate_graph_for_eval(eval_dir: Path) -> str:
    work_dir = eval_dir
    while work_dir.name != "stable_evaluation_outputs" and work_dir.parent != work_dir:
        work_dir = work_dir.parent
    semantic_work = work_dir.parent if work_dir.name == "stable_evaluation_outputs" else eval_dir.parent
    graph = semantic_work / "final_clean_graph.dot"
    return rel(graph) if graph.exists() else ""


def noncorrect_votes(eval_dir: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for vote_file in sorted((eval_dir / "responses").glob("reasoning_validation_*_vote_result.json")):
        vote = read_json(vote_file, {})
        final_result = str(vote.get("final_result", "")).lower()
        model_results = vote.get("model_results") if isinstance(vote.get("model_results"), dict) else {}
        has_error = any(str(value).lower() == "error" for value in model_results.values())
        if final_result != "correct" or has_error:
            out.append(
                {
                    "vote_file": rel(vote_file),
                    "target_node": vote.get("target_node", ""),
                    "final_result": final_result,
                    "model_results": model_results,
                }
            )
    return out


def collect_rows(runs_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for run_root in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        paper_spec = infer_paper_spec(run_root)
        for eval_json in sorted(run_root.rglob("stable_evaluation_outputs/*/evaluation_results.json")):
            eval_dir = eval_json.parent
            payload = read_json(eval_json, {})
            summary = payload.get("evaluation_summary") if isinstance(payload.get("evaluation_summary"), dict) else {}
            bad_votes = noncorrect_votes(eval_dir)
            rows.append(
                {
                    "run": run_root.name,
                    "paper_spec": paper_spec,
                    "eval_name": eval_dir.name,
                    "CG": summary.get("entity_coverage_score", ""),
                    "REA": summary.get("accuracy_score", ""),
                    "covered_entities": summary.get("covered_entities", ""),
                    "total_entities": summary.get("total_entities", ""),
                    "valid_reasoning_steps": summary.get("valid_reasoning_steps", ""),
                    "total_reasoning_steps": summary.get("total_reasoning_steps", ""),
                    "noncorrect_targets": ";".join(str(item.get("target_node", "")) for item in bad_votes),
                    "noncorrect_vote_count": len(bad_votes),
                    "candidate_graph": candidate_graph_for_eval(eval_dir),
                    "eval_dir": rel(eval_dir),
                    "noncorrect_votes": bad_votes,
                }
            )
    rows.sort(
        key=lambda row: (
            str(row.get("paper_spec", "")),
            -float(row.get("CG") or 0.0),
            -float(row.get("REA") or 0.0),
            int(row.get("noncorrect_vote_count") or 0),
            str(row.get("run", "")),
        )
    )
    return rows


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "run",
        "paper_spec",
        "eval_name",
        "CG",
        "REA",
        "covered_entities",
        "total_entities",
        "valid_reasoning_steps",
        "total_reasoning_steps",
        "noncorrect_targets",
        "noncorrect_vote_count",
        "candidate_graph",
        "eval_dir",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    parser.add_argument("--out-json", default="")
    parser.add_argument("--out-csv", default="")
    args = parser.parse_args()

    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = PROJECT_ROOT / runs_root
    rows = collect_rows(runs_root)
    out_json = Path(args.out_json) if args.out_json else runs_root / "CANDIDATE_FRONTIER.json"
    out_csv = Path(args.out_csv) if args.out_csv else runs_root / "CANDIDATE_FRONTIER.csv"
    if not out_json.is_absolute():
        out_json = PROJECT_ROOT / out_json
    if not out_csv.is_absolute():
        out_csv = PROJECT_ROOT / out_csv
    out_json.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(out_csv, rows)
    print(json.dumps({"rows": len(rows), "out_json": str(out_json), "out_csv": str(out_csv)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
