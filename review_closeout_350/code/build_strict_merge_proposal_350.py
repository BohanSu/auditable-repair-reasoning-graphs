#!/usr/bin/env python3
"""Build proposed 350 accounting with verified strict merge candidates.

This script intentionally writes a proposal package only. It never rewrites the
canonical 350 accounting. A candidate can replace a typed-residual row only when
the strict merge package proves fresh CG=1.0, REA=1.0, no provider contamination,
and all referenced artifacts exist.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[6]
PACKAGE_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
    / "10_standard_flow_350_subset_package"
)
ACCOUNTING_ROOT = PACKAGE_ROOT / "01_final_accounting"
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_ACCOUNTING = ACCOUNTING_ROOT / "FULL_350_ACCOUNTING.csv"
DEFAULT_PROVENANCE = ACCOUNTING_ROOT / "FULL_350_PROVENANCE.csv"
DEFAULT_MERGE_CANDIDATES = (
    RESIDUAL_ROOT
    / "strict_merge_candidates"
    / "20260603_local_window_v3_closure"
    / "STRICT_MERGE_CANDIDATES.json"
)
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "proposed_accounting_merges" / "20260603_local_window_v3_closure"


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


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


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


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def ensure_single_row_per_spec(rows: List[Dict[str, Any]], *, label: str) -> None:
    counts = Counter(str(row.get("paper_spec") or "") for row in rows)
    dupes = sorted(spec for spec, count in counts.items() if spec and count > 1)
    if dupes:
        raise RuntimeError(f"{label} has duplicate paper_spec rows: {dupes[:10]}")


def load_verified_candidates(path: Path) -> Dict[str, Dict[str, Any]]:
    package = read_json(path)
    rows = package.get("rows") if isinstance(package, dict) else []
    if not isinstance(rows, list):
        raise RuntimeError(f"merge candidate package lacks rows: {path}")

    out: Dict[str, Dict[str, Any]] = {}
    audit_rows: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        spec = str(row.get("paper_spec") or "")
        graph = resolve_path(str(row.get("candidate_graph") or ""))
        eval_dir = resolve_path(str(row.get("candidate_eval_dir") or ""))
        fresh_eval = resolve_path(str(row.get("candidate_fresh_eval_results") or ""))
        expected_hash = str(row.get("candidate_fresh_eval_results_sha256") or "")
        actual_hash = sha256_file(fresh_eval) if fresh_eval.exists() else ""
        mergeable = (
            spec
            and row.get("mergeable") is True
            and to_float(row.get("candidate_CG")) >= 1.0
            and to_float(row.get("candidate_REA")) >= 1.0
            and graph.exists()
            and eval_dir.exists()
            and fresh_eval.exists()
            and bool(expected_hash)
            and expected_hash == actual_hash
        )
        audit_rows.append(
            {
                "paper_spec": spec,
                "candidate_label": row.get("candidate_label", ""),
                "candidate_CG": row.get("candidate_CG", ""),
                "candidate_REA": row.get("candidate_REA", ""),
                "graph_exists": graph.exists(),
                "eval_dir_exists": eval_dir.exists(),
                "fresh_eval_exists": fresh_eval.exists(),
                "fresh_eval_hash_matches": expected_hash == actual_hash,
                "mergeable_verified": mergeable,
            }
        )
        if mergeable:
            if spec in out:
                raise RuntimeError(f"multiple verified candidates for {spec}")
            out[spec] = row
    return out, audit_rows  # type: ignore[return-value]


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    strict = [row for row in rows if row.get("current_outcome") == "strict_success"]
    residual = [row for row in rows if row.get("current_outcome") == "typed_residual"]
    by_model: Dict[str, Dict[str, Any]] = {}
    for model, model_rows in sorted(group_by(rows, "model").items()):
        by_model[model] = {
            "rows": len(model_rows),
            "strict_success_rows": sum(1 for row in model_rows if row.get("current_outcome") == "strict_success"),
            "typed_residual_rows": sum(1 for row in model_rows if row.get("current_outcome") == "typed_residual"),
            "final_CG_avg": avg(to_float(row.get("final_CG")) for row in model_rows),
            "final_REA_avg": avg(to_float(row.get("final_REA")) for row in model_rows),
        }
    return {
        "scope": "proposed 350-row subset excluding gpt_5_4 and gpt_5_5",
        "canonical_accounting_write": False,
        "accounted_rows": len(rows),
        "strict_success_rows": len(strict),
        "typed_residual_rows": len(residual),
        "outcome_counts": dict(Counter(row.get("current_outcome", "") for row in rows)),
        "failure_type_counts": dict(Counter(row.get("failure_type", "") or "none" for row in rows)),
        "final_CG_avg": avg(to_float(row.get("final_CG")) for row in rows),
        "final_REA_avg": avg(to_float(row.get("final_REA")) for row in rows),
        "by_model": by_model,
    }


def group_by(rows: List[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[str(row.get(key) or "")].append(row)
    return out


def avg(values: Any) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def merged_accounting_row(source: Dict[str, str], candidate: Dict[str, Any]) -> Dict[str, Any]:
    original_cg = to_float(source.get("original_CG"))
    original_rea = to_float(source.get("original_REA"))
    candidate_cg = to_float(candidate.get("candidate_CG"))
    candidate_rea = to_float(candidate.get("candidate_REA"))
    out: Dict[str, Any] = dict(source)
    out.update(
        {
            "current_outcome": "strict_success",
            "strict_gate_passed": "true",
            "failure_type": "",
            "api_clean_current": "true",
            "final_CG": f"{candidate_cg:.12g}",
            "final_REA": f"{candidate_rea:.12g}",
            "delta_CG": f"{candidate_cg - original_cg:.12g}",
            "delta_REA": f"{candidate_rea - original_rea:.12g}",
            "quality_tier": source.get("quality_tier") or "evidence_bound_closeout",
            "protocol": "vote_reuse_repair_root + evidence_bound_local_window_closeout",
            "final_graph": candidate.get("candidate_graph", ""),
            "final_eval_dir": candidate.get("candidate_eval_dir", ""),
            "error_summary": "",
            "terminal_metric_policy": "fresh_evidence_bound_local_window_eval",
            "terminal_metric_eval_dir": candidate.get("candidate_eval_dir", ""),
            "terminal_graph_policy": "strict_merge_candidate_graph",
            "metric_fallback_applied": "false",
            "terminal_metric_notes": (
                "Proposed strict merge candidate: fresh final CG=1.0 and REA=1.0, "
                "no provider contamination, canonical accounting not overwritten."
            ),
            "result_scope": "proposed_350_closeout_excluding_gpt54_gpt55",
        }
    )
    return out


def build(args: argparse.Namespace) -> Dict[str, Any]:
    accounting_path = resolve_path(args.accounting)
    provenance_path = resolve_path(args.provenance)
    candidates_path = resolve_path(args.merge_candidates)
    out_root = resolve_path(args.out_root)

    accounting_rows = read_csv(accounting_path)
    provenance_rows = read_csv(provenance_path)
    ensure_single_row_per_spec(accounting_rows, label="accounting")
    ensure_single_row_per_spec(provenance_rows, label="provenance")
    verified_candidates, candidate_audit_rows = load_verified_candidates(candidates_path)
    accounting_by_spec = {row["paper_spec"]: row for row in accounting_rows}

    merge_audit_rows: List[Dict[str, Any]] = []
    proposed_rows: List[Dict[str, Any]] = []
    for row in accounting_rows:
        spec = row.get("paper_spec", "")
        candidate = verified_candidates.get(spec)
        if not candidate:
            proposed_rows.append(dict(row))
            continue
        if row.get("current_outcome") != "typed_residual":
            raise RuntimeError(f"candidate target is not a typed residual: {spec}")
        merged = merged_accounting_row(row, candidate)
        proposed_rows.append(merged)
        merge_audit_rows.append(
            {
                "paper_spec": spec,
                "previous_outcome": row.get("current_outcome", ""),
                "previous_failure_type": row.get("failure_type", ""),
                "previous_final_CG": row.get("final_CG", ""),
                "previous_final_REA": row.get("final_REA", ""),
                "candidate_label": candidate.get("candidate_label", ""),
                "candidate_graph": candidate.get("candidate_graph", ""),
                "candidate_eval_dir": candidate.get("candidate_eval_dir", ""),
                "candidate_fresh_eval_results": candidate.get("candidate_fresh_eval_results", ""),
                "candidate_fresh_eval_results_sha256": candidate.get("candidate_fresh_eval_results_sha256", ""),
                "proposed_final_CG": merged.get("final_CG", ""),
                "proposed_final_REA": merged.get("final_REA", ""),
                "merge_source": "strict_merge_candidate",
            }
        )

    unknown_specs = sorted(set(verified_candidates) - set(accounting_by_spec))
    if unknown_specs:
        raise RuntimeError(f"candidate specs missing from accounting: {unknown_specs}")

    out_root.mkdir(parents=True, exist_ok=True)
    fieldnames = list(accounting_rows[0].keys()) if accounting_rows else []
    write_csv(out_root / "PROPOSED_FULL_350_ACCOUNTING.csv", proposed_rows, fieldnames)
    write_json(out_root / "PROPOSED_FULL_350_ACCOUNTING.json", proposed_rows)
    write_csv(
        out_root / "PROPOSED_STRICT_ACCEPTED_350.csv",
        [row for row in proposed_rows if row.get("current_outcome") == "strict_success"],
        fieldnames,
    )
    write_csv(
        out_root / "PROPOSED_TYPED_RESIDUAL_350.csv",
        [row for row in proposed_rows if row.get("current_outcome") == "typed_residual"],
        fieldnames,
    )
    provenance_fieldnames = list(provenance_rows[0].keys()) if provenance_rows else []
    write_csv(out_root / "PROPOSED_FULL_350_PROVENANCE.csv", provenance_rows, provenance_fieldnames)
    write_json(out_root / "PROPOSED_FULL_350_PROVENANCE.json", provenance_rows)
    write_csv(
        out_root / "STRICT_MERGE_ACCOUNTING_AUDIT.csv",
        merge_audit_rows,
        [
            "paper_spec",
            "previous_outcome",
            "previous_failure_type",
            "previous_final_CG",
            "previous_final_REA",
            "candidate_label",
            "candidate_graph",
            "candidate_eval_dir",
            "candidate_fresh_eval_results",
            "candidate_fresh_eval_results_sha256",
            "proposed_final_CG",
            "proposed_final_REA",
            "merge_source",
        ],
    )
    write_csv(
        out_root / "STRICT_MERGE_CANDIDATE_VERIFICATION.csv",
        candidate_audit_rows,
        [
            "paper_spec",
            "candidate_label",
            "candidate_CG",
            "candidate_REA",
            "graph_exists",
            "eval_dir_exists",
            "fresh_eval_exists",
            "fresh_eval_hash_matches",
            "mergeable_verified",
        ],
    )

    summary = summarize(proposed_rows)
    summary.update(
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "out_root": rel(out_root),
            "source_accounting": rel(accounting_path),
            "source_provenance": rel(provenance_path),
            "source_merge_candidates": rel(candidates_path),
            "verified_merge_candidate_count": len(verified_candidates),
            "merged_row_count": len(merge_audit_rows),
            "canonical_accounting_write": False,
        }
    )
    write_json(out_root / "PROPOSED_FULL_350_SUMMARY.json", summary)
    write_json(
        out_root / "STRICT_MERGE_PROPOSAL_AUDIT.json",
        {
            "created_at": summary["created_at"],
            "canonical_accounting_write": False,
            "candidate_verification_rows": candidate_audit_rows,
            "merge_audit_rows": merge_audit_rows,
            "summary": summary,
        },
    )
    lines = [
        "# Strict Merge Accounting Proposal",
        "",
        f"Created: {summary['created_at']}",
        f"Canonical accounting write: `{summary['canonical_accounting_write']}`",
        f"Verified candidates merged in proposal: `{len(merge_audit_rows)}`",
        f"Proposed strict/residual: `{summary['strict_success_rows']}` / `{summary['typed_residual_rows']}`",
        "",
        "| paper_spec | candidate | previous | proposed |",
        "|---|---|---|---|",
    ]
    for row in merge_audit_rows:
        lines.append(
            f"| {row['paper_spec']} | {row['candidate_label']} | "
            f"{row['previous_final_CG']}/{row['previous_final_REA']} | "
            f"{row['proposed_final_CG']}/{row['proposed_final_REA']} |"
        )
    write_text(out_root / "STRICT_MERGE_PROPOSAL.md", "\n".join(lines) + "\n")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounting", default=str(DEFAULT_ACCOUNTING))
    parser.add_argument("--provenance", default=str(DEFAULT_PROVENANCE))
    parser.add_argument("--merge-candidates", default=str(DEFAULT_MERGE_CANDIDATES))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


def main() -> int:
    summary = build(parse_args())
    print(
        json.dumps(
            {
                "out_root": summary["out_root"],
                "merged_row_count": summary["merged_row_count"],
                "strict_success_rows": summary["strict_success_rows"],
                "typed_residual_rows": summary["typed_residual_rows"],
                "canonical_accounting_write": summary["canonical_accounting_write"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
