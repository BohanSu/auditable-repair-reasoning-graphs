#!/usr/bin/env python3
"""Build a non-regression selection ledger for evidence-bound residual candidates.

The ledger is provider-free. It reads fresh fixed-anchor evaluation outputs,
compares candidate quality under the unchanged strict gate, and records which
candidate is the current best residual candidate. It does not merge candidates
into the 350-row accounting.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
RUNS_ROOT = RESIDUAL_ROOT / "runs"
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "selection_ledger"
    / "20260603_gpt55_feedback_smoke"
)
DEFAULT_CANDIDATES = [
    (
        "r00_initial_evidence_bound_smoke",
        RUNS_ROOT / "evidence_bound_gpt55_smoke_fresh_eval_20260603_0018" / "FRESH_EVAL_RESULTS.json",
    ),
    (
        "r01_feedback_from_smoke",
        RUNS_ROOT / "evidence_bound_gpt55_feedback_r01_fresh_eval_20260603_0201" / "FRESH_EVAL_RESULTS.json",
    ),
    (
        "r02_feedback_from_r01",
        RUNS_ROOT / "evidence_bound_gpt55_feedback_r02_fresh_eval_20260603_0216" / "FRESH_EVAL_RESULTS.json",
    ),
]


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


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_result_rows(result_path: Path) -> List[Dict[str, Any]]:
    payload = read_json(result_path, {})
    rows = payload.get("rows") if isinstance(payload, dict) else []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"missing fresh eval rows: {result_path}")
    out = [row for row in rows if isinstance(row, dict)]
    if not out:
        raise RuntimeError(f"bad fresh eval rows: {result_path}")
    return out


def vote_files(eval_dir: Path) -> List[Path]:
    return sorted((eval_dir / "responses").glob("reasoning_validation_*_vote_result.json"))


def vote_summary(eval_dir: Path) -> Dict[str, Any]:
    counts = {"correct": 0, "wrong": 0, "error": 0}
    wrong_targets: List[str] = []
    provider_error_targets: List[str] = []
    contaminated_wrong_targets: List[str] = []
    for vote_file in vote_files(eval_dir):
        vote = read_json(vote_file, {})
        if not isinstance(vote, dict):
            continue
        final = str(vote.get("final_result") or "").lower()
        if final in counts:
            counts[final] += 1
        target = str(vote.get("target_node") or vote.get("reasoning_id") or "")
        model_results = vote.get("model_results") if isinstance(vote.get("model_results"), dict) else {}
        has_error = any(str(value).lower() == "error" for value in model_results.values())
        has_wrong = any(str(value).lower() == "wrong" for value in model_results.values())
        if final == "wrong":
            wrong_targets.append(target)
        if has_error:
            provider_error_targets.append(target)
        if final == "error" and has_wrong:
            contaminated_wrong_targets.append(target)
    return {
        "vote_correct": counts["correct"],
        "vote_wrong": counts["wrong"],
        "vote_error": counts["error"],
        "wrong_targets": ";".join(wrong_targets),
        "provider_error_targets": ";".join(provider_error_targets),
        "contaminated_wrong_targets": ";".join(contaminated_wrong_targets),
    }


def parse_candidate_specs(values: Sequence[str]) -> List[tuple[str, Path]]:
    if not values:
        return [(label, path) for label, path in DEFAULT_CANDIDATES]
    out: List[tuple[str, Path]] = []
    for value in values:
        if "=" not in value:
            raise RuntimeError(f"candidate must be label=path, got: {value}")
        label, path_text = value.split("=", 1)
        label = label.strip()
        if not label:
            raise RuntimeError(f"empty candidate label: {value}")
        out.append((label, resolve_path(path_text.strip())))
    return out


def load_candidates(candidate_specs: Sequence[tuple[str, Path]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ordinal, (label, result_path) in enumerate(candidate_specs, 1):
        for row_index, row in enumerate(load_result_rows(result_path), 1):
            eval_dir = resolve_path(str(row.get("eval_dir") or ""))
            votes = vote_summary(eval_dir)
            cg = to_float(row.get("CG"))
            rea = to_float(row.get("REA"))
            judge_provider_error = str(row.get("judge_provider_error")).lower() == "true" or row.get("judge_provider_error") is True
            strict_gate_passed = bool(row.get("strict_gate_passed") is True or str(row.get("strict_gate_passed")).lower() == "true")
            rows.append(
                {
                    "ordinal": len(rows) + 1,
                    "candidate_label": label,
                    "candidate_file_ordinal": ordinal,
                    "candidate_row_index": row_index,
                    "paper_spec": row.get("paper_spec", ""),
                    "lane": row.get("lane", ""),
                    "model": row.get("model", ""),
                    "CG": cg,
                    "REA": rea,
                    "covered_entities": row.get("covered_entities", ""),
                    "total_entities": row.get("total_entities", ""),
                    "valid_reasoning_steps": row.get("valid_reasoning_steps", ""),
                    "total_reasoning_steps": row.get("total_reasoning_steps", ""),
                    "strict_gate_passed": strict_gate_passed,
                    "judge_provider_error": judge_provider_error,
                    "status": row.get("status", ""),
                    "fresh_eval_results": rel(result_path),
                    "fresh_eval_results_sha256": sha256_file(result_path),
                    "attempt_path": row.get("attempt_path", ""),
                    "graph_spec": row.get("graph_spec", ""),
                    "dot": row.get("dot", ""),
                    "preflight_report": row.get("preflight_report", ""),
                    "staged_run_dir": row.get("staged_run_dir", ""),
                    "work_dir": row.get("work_dir", ""),
                    "eval_dir": row.get("eval_dir", ""),
                    **votes,
                }
            )
    return rows


def annotate_selection(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    best_idx: int | None = None
    strict_pass_idx: int | None = None
    for idx, row in enumerate(rows):
        if row["strict_gate_passed"] and not row["judge_provider_error"] and row["CG"] >= 1.0 and row["REA"] >= 1.0:
            row["selection_decision"] = "strict_accept_candidate"
            row["selection_reason"] = "fresh gate passed with no provider contamination"
            strict_pass_idx = idx if strict_pass_idx is None else strict_pass_idx
            if best_idx is None:
                best_idx = idx
            else:
                best = rows[best_idx]
                if row["CG"] >= best["CG"] and row["REA"] >= best["REA"]:
                    if best_idx != idx and best.get("selection_decision") == "current_best_residual_candidate":
                        best["selection_decision"] = "superseded_by_strict_accept_candidate"
                        best["selection_reason"] = f"superseded by strict candidate {row['candidate_label']}"
                    best_idx = idx
            continue

        if best_idx is None:
            row["selection_decision"] = "current_best_residual_candidate"
            row["selection_reason"] = "first fresh-evaluated candidate; still residual because strict gate did not pass"
            best_idx = idx
            continue

        best = rows[best_idx]
        non_regressive = row["CG"] >= best["CG"] and row["REA"] >= best["REA"]
        improves = row["CG"] > best["CG"] or row["REA"] > best["REA"]
        if non_regressive and improves:
            row["selection_decision"] = "current_best_residual_candidate"
            row["selection_reason"] = "improves at least one strict metric without regressing the other; still residual until clean 1/1"
            rows[best_idx]["selection_decision"] = "superseded_by_non_regressive_candidate"
            rows[best_idx]["selection_reason"] = f"superseded by {row['candidate_label']}"
            best_idx = idx
        else:
            row["selection_decision"] = "rejected_by_non_regression_selection"
            row["selection_reason"] = (
                f"regresses versus current best {best['candidate_label']} "
                f"(best CG={best['CG']:.6f}, REA={best['REA']:.6f})"
            )

    best = rows[best_idx] if best_idx is not None else {}
    strict = rows[strict_pass_idx] if strict_pass_idx is not None else {}
    selected_is_strict = bool(
        best
        and best.get("strict_gate_passed") is True
        and not best.get("judge_provider_error")
        and best.get("CG", 0.0) >= 1.0
        and best.get("REA", 0.0) >= 1.0
    )
    strict_label = best.get("candidate_label", "") if selected_is_strict else strict.get("candidate_label", "")
    return {
        "strict_candidate_found": bool(strict),
        "strict_candidate_label": strict_label,
        "first_strict_candidate_label": strict.get("candidate_label", ""),
        "selected_best_label": best.get("candidate_label", ""),
        "selected_best_CG": best.get("CG", 0.0),
        "selected_best_REA": best.get("REA", 0.0),
        "selected_best_judge_provider_error": best.get("judge_provider_error", False),
        "selected_best_is_mergeable": selected_is_strict,
        "selection_policy": "non-regression on fresh CG and REA; strict merge requires final CG=1.0, final REA=1.0, and no provider contamination",
    }


def write_markdown(path: Path, report: Dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Evidence-Bound Candidate Selection Ledger",
        "",
        f"Created: {report['created_at']}",
        "",
        "## Result",
        "",
        f"- Strict candidate found: `{summary['strict_candidate_found']}`",
        f"- Selected best residual candidate: `{summary['selected_best_label']}`",
        f"- Selected best CG/REA: `{summary['selected_best_CG']:.6f}` / `{summary['selected_best_REA']:.6f}`",
        f"- Mergeable into 350 strict accounting: `{summary['selected_best_is_mergeable']}`",
        "",
        "No candidate is mergeable unless fresh final CG=1.0 and REA=1.0 with no provider contamination.",
        "",
        "## Candidates",
        "",
        "| Candidate | CG | REA | Provider error | Decision | Reason |",
        "|---|---:|---:|---|---|---|",
    ]
    for row in report["rows"]:
        lines.append(
            "| {candidate_label} | {CG:.6f} | {REA:.6f} | `{judge_provider_error}` | `{selection_decision}` | {selection_reason} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- `CANDIDATE_SELECTION_LEDGER.csv`",
            "- `CANDIDATE_SELECTION_LEDGER.json`",
            "- `CANDIDATE_SELECTION_SUMMARY.json`",
        ]
    )
    write_text(path, "\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", default=[], help="Candidate spec as label=FRESH_EVAL_RESULTS.json")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_candidates(parse_candidate_specs(args.candidate))
    summary = annotate_selection(rows)
    out_root = resolve_path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ordinal",
        "candidate_label",
        "candidate_file_ordinal",
        "candidate_row_index",
        "paper_spec",
        "lane",
        "model",
        "CG",
        "REA",
        "covered_entities",
        "total_entities",
        "valid_reasoning_steps",
        "total_reasoning_steps",
        "vote_correct",
        "vote_wrong",
        "vote_error",
        "wrong_targets",
        "provider_error_targets",
        "contaminated_wrong_targets",
        "strict_gate_passed",
        "judge_provider_error",
        "selection_decision",
        "selection_reason",
        "status",
        "fresh_eval_results",
        "fresh_eval_results_sha256",
        "attempt_path",
        "graph_spec",
        "dot",
        "preflight_report",
        "staged_run_dir",
        "work_dir",
        "eval_dir",
    ]
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "evidence_bound_candidate_selection_ledger",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "out_root": rel(out_root),
        "summary": summary,
        "rows": rows,
    }
    write_csv(out_root / "CANDIDATE_SELECTION_LEDGER.csv", rows, fieldnames)
    write_json(out_root / "CANDIDATE_SELECTION_LEDGER.json", report)
    write_json(out_root / "CANDIDATE_SELECTION_SUMMARY.json", {**summary, "created_at": report["created_at"]})
    write_markdown(out_root / "CANDIDATE_SELECTION_LEDGER.md", report)
    write_json(
        out_root.parent / "CURRENT_SELECTION_LEDGER_POINTER.json",
        {
            "created_at": report["created_at"],
            "current_ledger": rel(out_root / "CANDIDATE_SELECTION_LEDGER.json"),
            "summary": summary,
        },
    )
    print(json.dumps({"out_root": rel(out_root), **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
