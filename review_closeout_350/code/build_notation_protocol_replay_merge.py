#!/usr/bin/env python3
"""Materialize notation-protocol replay candidates and a proposed 350 merge.

This is a provider-free closeout step. It does not change graphs, does not call
LLM providers, and does not rewrite canonical accounting. It only promotes rows
whose saved evaluator judgments reach CG=1.0 after the patched notation-aware
coverage protocol and whose existing ANS score does not regress against raw,
step2, or the current PEARL terminal graph.
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
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
ACCOUNTING_ROOT = PACKAGE_ROOT / "01_final_accounting"
DESIGN_ROOT = RESIDUAL_ROOT / "closeout_design" / "20260604_evidence_bound_claim_reconstruction"
BASE_LABEL = "20260604_combined_guarded_v5_plus_anchor_v3_plus_same_paper_v2_plus_ans_surface_v4_plus_coverage_v5_plus_rub15_v1"
OUT_LABEL = f"{BASE_LABEL}_plus_notation_replay_v1"

DEFAULT_BASE_ACCOUNTING = (
    RESIDUAL_ROOT
    / "proposed_accounting_merges"
    / BASE_LABEL
    / "PROPOSED_FULL_350_ACCOUNTING.csv"
)
DEFAULT_BASE_PROVENANCE = (
    RESIDUAL_ROOT
    / "proposed_accounting_merges"
    / BASE_LABEL
    / "PROPOSED_FULL_350_PROVENANCE.csv"
)
DEFAULT_NOTATION_REPLAY = DESIGN_ROOT / "notation_batch_replay_20260604" / "NOTATION_BATCH_REPLAY.json"
DEFAULT_ANS_NODE_RESULTS = (
    PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_node_results.jsonl"
)
DEFAULT_CANDIDATE_ROOT = RESIDUAL_ROOT / "strict_merge_candidates" / "20260604_notation_protocol_replay_v1"
DEFAULT_PROPOSAL_ROOT = RESIDUAL_ROOT / "proposed_accounting_merges" / OUT_LABEL
DEFAULT_CANONICAL_SUMMARY = ACCOUNTING_ROOT / "FULL_350_SUMMARY.json"

EXCLUDED_ANS_UNIT_TYPES = {"root_common_bridge", "graph_node"}
ANS_STAGES = [
    "raw_step1_extraction",
    "llm_step2_self_fix_final_clean",
    "pearl_terminal_graph",
]


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def safe_spec(spec: str) -> str:
    return spec.replace(":", "__").replace("/", "_")


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
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp.replace(path)


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


def avg(values: Any) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "")].append(row)
    return grouped


def load_ans_by_spec(path: Path, wanted_specs: set[str]) -> dict[str, dict[str, Any]]:
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            spec = str(row.get("paper_spec") or "")
            if spec not in wanted_specs:
                continue
            if row.get("unit_type") in EXCLUDED_ANS_UNIT_TYPES:
                continue
            stage = str(row.get("stage") or "")
            if stage not in ANS_STAGES:
                continue
            item = stats.setdefault(
                (spec, stage),
                {"atomic_fact_count": 0, "supported_fact_count": 0, "node_count": 0},
            )
            item["atomic_fact_count"] += int(row.get("atomic_fact_count") or 0)
            item["supported_fact_count"] += int(row.get("supported_fact_count") or 0)
            item["node_count"] += 1

    out: dict[str, dict[str, Any]] = {}
    for spec in wanted_specs:
        per_stage: dict[str, Any] = {}
        for stage in ANS_STAGES:
            item = stats.get((spec, stage), {"atomic_fact_count": 0, "supported_fact_count": 0, "node_count": 0})
            total = item["atomic_fact_count"]
            supported = item["supported_fact_count"]
            per_stage[stage] = {
                "atomic_fact_count": total,
                "supported_fact_count": supported,
                "node_count": item["node_count"],
                "ans": (supported / total) if total else None,
            }
        stage_scores = [
            per_stage[stage]["ans"]
            for stage in ANS_STAGES
            if per_stage[stage]["ans"] is not None
        ]
        candidate_ans = per_stage["pearl_terminal_graph"]["ans"]
        guard_threshold = max(stage_scores) if stage_scores else None
        out[spec] = {
            "stages": per_stage,
            "candidate_stage": "pearl_terminal_graph",
            "candidate_main_factual_ans": candidate_ans,
            "guard_threshold": guard_threshold,
            "ans_guard_passed": (
                candidate_ans is not None
                and guard_threshold is not None
                and candidate_ans + 1e-12 >= guard_threshold
            ),
        }
    return out


def ensure_unique_specs(rows: list[dict[str, Any]], label: str) -> None:
    counts = Counter(str(row.get("paper_spec") or "") for row in rows)
    dupes = sorted(spec for spec, count in counts.items() if spec and count > 1)
    if dupes:
        raise RuntimeError(f"{label} has duplicate paper_spec rows: {dupes[:10]}")


def materialize_replay_eval(
    *,
    out_dir: Path,
    replay_row: dict[str, Any],
    ans_guard: dict[str, Any],
) -> Path:
    original_eval = resolve(str(replay_row["eval_json"]))
    original_payload = read_json(original_eval)
    replay_payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "candidate_label": "notation_protocol_replay_v1",
        "paper_spec": replay_row["paper_spec"],
        "protocol_update_required": True,
        "protocol_update": {
            "coverage_protocol": "notation_normalized_saved_judgment_replay",
            "graph_changed": False,
            "judgments_changed": False,
            "coverage_tokenization_changed": True,
            "reason": "Scientific notation, unicode, subscript, superscript, and alphanumeric entity normalization replay.",
        },
        "source_original_eval_json": rel(original_eval),
        "source_original_eval_json_sha256": sha256_file(original_eval),
        "graph_file": replay_row["graph_file"],
        "input_data": replay_row["input_data"],
        "judgment_source": replay_row.get("judgment_source", ""),
        "entities": original_payload.get("entities", []),
        "old_coverage": {
            "CG": replay_row.get("old_CG"),
            "covered_entities": replay_row.get("old_covered_entities"),
            "total_entities": replay_row.get("old_total_entities"),
        },
        "coverage": {
            "coverage_rate": replay_row.get("replayed_CG"),
            "covered_entities": replay_row.get("replayed_covered_entities"),
            "total_entities": replay_row.get("replayed_total_entities"),
        },
        "accuracy": original_payload.get("accuracy", {}),
        "evaluation_summary": original_payload.get("evaluation_summary", {}),
        "replayed_metrics": {
            "CG": replay_row.get("replayed_CG"),
            "REA": to_float(replay_row.get("final_REA")),
            "strict_gate_passed": bool(replay_row.get("strict_gate_by_replay")),
        },
        "ans_guard": ans_guard,
    }
    out_path = out_dir / "REPLAY_EVALUATION_RESULTS.json"
    write_json(out_path, replay_payload)
    return out_path


def build_candidate_package(
    *,
    notation_replay: dict[str, Any],
    accounting_by_spec: dict[str, dict[str, str]],
    ans_by_spec: dict[str, dict[str, Any]],
    out_root: Path,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    all_replay_rows = notation_replay.get("rows") if isinstance(notation_replay, dict) else []
    if not isinstance(all_replay_rows, list):
        raise RuntimeError("notation replay package has no rows")

    for replay_row in all_replay_rows:
        if not isinstance(replay_row, dict) or not replay_row.get("strict_gate_by_replay"):
            continue
        spec = str(replay_row["paper_spec"])
        source = accounting_by_spec.get(spec, {})
        ans_guard = ans_by_spec.get(spec, {})
        row_out_dir = out_root / "per_row_replay_eval" / safe_spec(spec)
        replay_eval_json = materialize_replay_eval(
            out_dir=row_out_dir,
            replay_row=replay_row,
            ans_guard=ans_guard,
        )
        graph = resolve(str(replay_row["graph_file"]))
        mergeable = (
            source.get("current_outcome") == "typed_residual"
            and bool(ans_guard.get("ans_guard_passed"))
            and to_float(replay_row.get("replayed_CG")) >= 1.0
            and to_float(replay_row.get("final_REA")) >= 1.0
            and graph.exists()
            and replay_eval_json.exists()
        )
        accuracy = read_json(resolve(str(replay_row["eval_json"]))).get("accuracy") or {}
        rows.append(
            {
                "paper_spec": spec,
                "model": replay_row.get("model", ""),
                "paper": replay_row.get("paper", ""),
                "candidate_label": "notation_protocol_replay_v1",
                "source_current_outcome": source.get("current_outcome", ""),
                "source_failure_type": source.get("failure_type", ""),
                "source_final_CG": source.get("final_CG", ""),
                "source_final_REA": source.get("final_REA", ""),
                "candidate_CG": replay_row.get("replayed_CG", ""),
                "candidate_REA": replay_row.get("final_REA", ""),
                "candidate_valid_reasoning_steps": accuracy.get("valid_steps", ""),
                "candidate_total_reasoning_steps": accuracy.get("total_steps", ""),
                "candidate_covered_entities": replay_row.get("replayed_covered_entities", ""),
                "candidate_total_entities": replay_row.get("replayed_total_entities", ""),
                "candidate_graph": rel(graph),
                "candidate_eval_dir": rel(row_out_dir),
                "candidate_fresh_eval_results": rel(replay_eval_json),
                "candidate_fresh_eval_results_sha256": sha256_file(replay_eval_json),
                "candidate_preflight_report": "",
                "candidate_patch": "",
                "raw_main_factual_ans": ans_guard.get("stages", {}).get("raw_step1_extraction", {}).get("ans"),
                "step2_main_factual_ans": ans_guard.get("stages", {}).get("llm_step2_self_fix_final_clean", {}).get("ans"),
                "current_best_main_factual_ans": ans_guard.get("guard_threshold"),
                "candidate_main_factual_ans": ans_guard.get("candidate_main_factual_ans"),
                "ans_guard_passed": bool(ans_guard.get("ans_guard_passed")),
                "protocol_update_required": True,
                "graph_changed": False,
                "judgments_changed": False,
                "coverage_tokenization_changed": True,
                "mergeable": mergeable,
                "canonical_write_executed": False,
            }
        )

    fieldnames = [
        "paper_spec",
        "model",
        "paper",
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
        "candidate_main_factual_ans",
        "ans_guard_passed",
        "protocol_update_required",
        "graph_changed",
        "judgments_changed",
        "coverage_tokenization_changed",
        "mergeable",
        "canonical_write_executed",
    ]
    out_root.mkdir(parents=True, exist_ok=True)
    write_csv(out_root / "STRICT_MERGE_CANDIDATES.csv", rows, fieldnames)
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "candidate_label": "notation_protocol_replay_v1",
        "out_root": rel(out_root),
        "source_notation_replay": notation_replay.get("summary", {}).get("out_root", ""),
        "candidate_rows": len(rows),
        "mergeable_rows": sum(1 for row in rows if row["mergeable"] is True),
        "non_mergeable_rows": sum(1 for row in rows if row["mergeable"] is not True),
        "merge_policy": {
            "requires_strict_gate_by_replay": True,
            "requires_final_CG": 1.0,
            "requires_final_REA": 1.0,
            "requires_ans_non_regression": True,
            "requires_existing_graph": True,
            "requires_replay_eval_json_hash": True,
            "requires_no_provider_calls": True,
        },
        "rows": rows,
    }
    write_json(out_root / "STRICT_MERGE_CANDIDATES.json", report)
    write_text(out_root / "STRICT_MERGE_CANDIDATES.md", render_candidate_markdown(report))
    return report


def load_verified_mergeable_candidates(candidate_package: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    verified: dict[str, dict[str, Any]] = {}
    audit_rows: list[dict[str, Any]] = []
    for row in candidate_package.get("rows", []):
        if not isinstance(row, dict):
            continue
        spec = str(row.get("paper_spec") or "")
        graph = resolve(str(row.get("candidate_graph") or ""))
        eval_dir = resolve(str(row.get("candidate_eval_dir") or ""))
        fresh_eval = resolve(str(row.get("candidate_fresh_eval_results") or ""))
        expected_hash = str(row.get("candidate_fresh_eval_results_sha256") or "")
        actual_hash = sha256_file(fresh_eval) if fresh_eval.exists() else ""
        mergeable_verified = (
            bool(spec)
            and row.get("mergeable") is True
            and row.get("ans_guard_passed") is True
            and row.get("graph_changed") is False
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
                "candidate_main_factual_ans": row.get("candidate_main_factual_ans", ""),
                "current_best_main_factual_ans": row.get("current_best_main_factual_ans", ""),
                "ans_guard_passed": row.get("ans_guard_passed", ""),
                "graph_exists": graph.exists(),
                "eval_dir_exists": eval_dir.exists(),
                "fresh_eval_exists": fresh_eval.exists(),
                "fresh_eval_hash_matches": expected_hash == actual_hash,
                "mergeable_verified": mergeable_verified,
            }
        )
        if mergeable_verified:
            if spec in verified:
                raise RuntimeError(f"multiple verified notation replay candidates for {spec}")
            verified[spec] = row
    return verified, audit_rows


def merged_accounting_row(source: dict[str, str], candidate: dict[str, Any]) -> dict[str, Any]:
    original_cg = to_float(source.get("original_CG"))
    original_rea = to_float(source.get("original_REA"))
    candidate_cg = to_float(candidate.get("candidate_CG"))
    candidate_rea = to_float(candidate.get("candidate_REA"))
    out: dict[str, Any] = dict(source)
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
            "quality_tier": "protocol_replay_closeout",
            "protocol": f"{source.get('protocol', '').strip()} + notation_protocol_replay_v1".strip(" +"),
            "final_graph": candidate.get("candidate_graph", ""),
            "final_eval_dir": candidate.get("candidate_eval_dir", ""),
            "error_summary": "",
            "terminal_metric_policy": "notation_normalized_saved_judgment_replay",
            "terminal_metric_eval_dir": candidate.get("candidate_eval_dir", ""),
            "terminal_graph_policy": "current_terminal_final_graph_protocol_replay",
            "metric_fallback_applied": "false",
            "terminal_metric_notes": (
                "Proposed notation protocol replay merge: graph unchanged; saved judgments unchanged; "
                "patched notation-aware coverage yields final CG=1.0 and REA=1.0; ANS non-regression guard passed; "
                "canonical accounting not overwritten."
            ),
            "result_scope": "proposed_350_closeout_excluding_gpt54_gpt55_notation_replay_v1",
        }
    )
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    strict = [row for row in rows if row.get("current_outcome") == "strict_success"]
    residual = [row for row in rows if row.get("current_outcome") == "typed_residual"]
    by_model: dict[str, dict[str, Any]] = {}
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


def build_proposal(
    *,
    base_accounting_rows: list[dict[str, str]],
    provenance_rows: list[dict[str, str]],
    candidate_package: dict[str, Any],
    out_root: Path,
    base_accounting: Path,
    base_provenance: Path,
    canonical_summary: Path,
) -> dict[str, Any]:
    verified_candidates, candidate_audit_rows = load_verified_mergeable_candidates(candidate_package)
    accounting_by_spec = {row["paper_spec"]: row for row in base_accounting_rows}
    merge_audit_rows: list[dict[str, Any]] = []
    proposed_rows: list[dict[str, Any]] = []
    for row in base_accounting_rows:
        spec = row.get("paper_spec", "")
        candidate = verified_candidates.get(spec)
        if not candidate:
            proposed_rows.append(dict(row))
            continue
        if row.get("current_outcome") != "typed_residual":
            raise RuntimeError(f"candidate target is not a typed residual in base accounting: {spec}")
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
                "candidate_main_factual_ans": candidate.get("candidate_main_factual_ans", ""),
                "current_best_main_factual_ans": candidate.get("current_best_main_factual_ans", ""),
                "proposed_final_CG": merged.get("final_CG", ""),
                "proposed_final_REA": merged.get("final_REA", ""),
                "merge_source": "notation_protocol_replay_v1",
            }
        )

    unknown_specs = sorted(set(verified_candidates) - set(accounting_by_spec))
    if unknown_specs:
        raise RuntimeError(f"candidate specs missing from base accounting: {unknown_specs}")

    out_root.mkdir(parents=True, exist_ok=True)
    accounting_fieldnames = list(base_accounting_rows[0].keys()) if base_accounting_rows else []
    provenance_fieldnames = list(provenance_rows[0].keys()) if provenance_rows else []
    write_csv(out_root / "PROPOSED_FULL_350_ACCOUNTING.csv", proposed_rows, accounting_fieldnames)
    write_json(out_root / "PROPOSED_FULL_350_ACCOUNTING.json", proposed_rows)
    write_csv(
        out_root / "PROPOSED_STRICT_ACCEPTED_350.csv",
        [row for row in proposed_rows if row.get("current_outcome") == "strict_success"],
        accounting_fieldnames,
    )
    write_csv(
        out_root / "PROPOSED_TYPED_RESIDUAL_350.csv",
        [row for row in proposed_rows if row.get("current_outcome") == "typed_residual"],
        accounting_fieldnames,
    )
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
            "candidate_main_factual_ans",
            "current_best_main_factual_ans",
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
            "candidate_main_factual_ans",
            "current_best_main_factual_ans",
            "ans_guard_passed",
            "graph_exists",
            "eval_dir_exists",
            "fresh_eval_exists",
            "fresh_eval_hash_matches",
            "mergeable_verified",
        ],
    )
    summary = summarize(proposed_rows)
    canonical = read_json(canonical_summary) if canonical_summary.exists() else {}
    summary.update(
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "out_root": rel(out_root),
            "source_base_accounting": rel(base_accounting),
            "source_base_provenance": rel(base_provenance),
            "source_merge_candidates": candidate_package.get("out_root", ""),
            "canonical_baseline": {
                "source_summary": rel(canonical_summary),
                "strict_success_rows": canonical.get("strict_success_rows"),
                "typed_residual_rows": canonical.get("typed_residual_rows"),
            },
            "base_proposed_baseline": {
                "strict_success_rows": sum(1 for row in base_accounting_rows if row.get("current_outcome") == "strict_success"),
                "typed_residual_rows": sum(1 for row in base_accounting_rows if row.get("current_outcome") == "typed_residual"),
            },
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
    write_text(out_root / "STRICT_MERGE_PROPOSAL.md", render_proposal_markdown(summary, merge_audit_rows))
    return summary


def render_candidate_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Notation Protocol Replay Strict Merge Candidates",
        "",
        f"Created: `{report['created_at']}`",
        "",
        "This package is provider-free and does not write canonical accounting.",
        "Graphs and saved judgments are unchanged; only notation-aware CG replay is applied.",
        "",
        f"- Candidate rows: `{report['candidate_rows']}`",
        f"- Mergeable rows: `{report['mergeable_rows']}`",
        f"- Non-mergeable rows: `{report['non_mergeable_rows']}`",
        "",
        "| paper_spec | CG | REA | candidate ANS | guard threshold | ANS guard | mergeable |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in report["rows"]:
        lines.append(
            "| `{paper_spec}` | `{candidate_CG}` | `{candidate_REA}` | `{candidate_main_factual_ans}` | `{current_best_main_factual_ans}` | `{ans_guard_passed}` | `{mergeable}` |".format(
                **row
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_proposal_markdown(summary: dict[str, Any], merge_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Notation Protocol Replay Accounting Proposal",
        "",
        f"Created: `{summary['created_at']}`",
        f"Canonical accounting write: `{summary['canonical_accounting_write']}`",
        f"Base proposed strict/residual: `{summary['base_proposed_baseline']['strict_success_rows']}` / `{summary['base_proposed_baseline']['typed_residual_rows']}`",
        f"New proposed strict/residual: `{summary['strict_success_rows']}` / `{summary['typed_residual_rows']}`",
        f"Verified notation replay merges: `{len(merge_rows)}`",
        "",
        "| paper_spec | previous | proposed | candidate ANS | guard threshold |",
        "|---|---|---|---:|---:|",
    ]
    for row in merge_rows:
        lines.append(
            "| `{paper_spec}` | `{previous_final_CG}`/`{previous_final_REA}` | `{proposed_final_CG}`/`{proposed_final_REA}` | `{candidate_main_factual_ans}` | `{current_best_main_factual_ans}` |".format(
                **row
            )
        )
    lines.append("")
    return "\n".join(lines)


def build(args: argparse.Namespace) -> dict[str, Any]:
    base_accounting = resolve(args.base_accounting)
    base_provenance = resolve(args.base_provenance)
    notation_replay_path = resolve(args.notation_replay)
    ans_node_results = resolve(args.ans_node_results)
    candidate_root = resolve(args.candidate_root)
    proposal_root = resolve(args.proposal_root)
    canonical_summary = resolve(args.canonical_summary)

    base_rows = read_csv(base_accounting)
    provenance_rows = read_csv(base_provenance)
    ensure_unique_specs(base_rows, "base accounting")
    ensure_unique_specs(provenance_rows, "base provenance")
    accounting_by_spec = {row["paper_spec"]: row for row in base_rows}
    notation_replay = read_json(notation_replay_path)
    wanted = {
        str(row.get("paper_spec") or "")
        for row in notation_replay.get("rows", [])
        if isinstance(row, dict) and row.get("strict_gate_by_replay")
    }
    ans_by_spec = load_ans_by_spec(ans_node_results, wanted)
    candidate_package = build_candidate_package(
        notation_replay=notation_replay,
        accounting_by_spec=accounting_by_spec,
        ans_by_spec=ans_by_spec,
        out_root=candidate_root,
    )
    summary = build_proposal(
        base_accounting_rows=base_rows,
        provenance_rows=provenance_rows,
        candidate_package=candidate_package,
        out_root=proposal_root,
        base_accounting=base_accounting,
        base_provenance=base_provenance,
        canonical_summary=canonical_summary,
    )
    return {
        "candidate_package": candidate_package,
        "proposal_summary": summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-accounting", default=str(DEFAULT_BASE_ACCOUNTING))
    parser.add_argument("--base-provenance", default=str(DEFAULT_BASE_PROVENANCE))
    parser.add_argument("--notation-replay", default=str(DEFAULT_NOTATION_REPLAY))
    parser.add_argument("--ans-node-results", default=str(DEFAULT_ANS_NODE_RESULTS))
    parser.add_argument("--candidate-root", default=str(DEFAULT_CANDIDATE_ROOT))
    parser.add_argument("--proposal-root", default=str(DEFAULT_PROPOSAL_ROOT))
    parser.add_argument("--canonical-summary", default=str(DEFAULT_CANONICAL_SUMMARY))
    return parser.parse_args()


def main() -> int:
    report = build(parse_args())
    summary = report["proposal_summary"]
    print(
        json.dumps(
            {
                "candidate_out_root": report["candidate_package"]["out_root"],
                "candidate_rows": report["candidate_package"]["candidate_rows"],
                "mergeable_rows": report["candidate_package"]["mergeable_rows"],
                "proposal_out_root": summary["out_root"],
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
