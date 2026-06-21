#!/usr/bin/env python3
"""Build a provider-free batch ANS guard for a proposed accounting package.

The row-level strict merge guard proves that each newly merged candidate does
not regress that row's main-factual ANS. This script adds the batch-level
check: combine current full-run PEARL ANS rows with candidate ANS rows for the
proposal's strict specs and compare the resulting strict-set ANS against the
canonical 300-row strict floor.

It does not call providers and does not write canonical accounting.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_ANS_NODE_RESULTS = PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_node_results.jsonl"
DEFAULT_CANONICAL_ACCOUNTING = PACKAGE_ROOT / "01_final_accounting" / "FULL_350_ACCOUNTING.csv"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "ans_factscore_style" / "proposal_batch_ans_guard"
EXCLUDED_UNIT_TYPES = {"root_common_bridge", "graph_node"}
DEFAULT_STAGE = "pearl_terminal_graph"


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
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
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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


def is_main_factual(row: dict[str, Any]) -> bool:
    return str(row.get("unit_type") or "") not in EXCLUDED_UNIT_TYPES


def summarize_rows(rows: list[dict[str, Any]], specs: set[str], stage: str) -> dict[str, Any]:
    supported = 0
    total = 0
    nodes = 0
    specs_with_ans: set[str] = set()
    for row in rows:
        spec = str(row.get("paper_spec") or "")
        if spec not in specs or str(row.get("stage") or "") != stage or not is_main_factual(row):
            continue
        supported += int(row.get("supported_fact_count") or 0)
        fact_count = int(row.get("atomic_fact_count") or 0)
        total += fact_count
        nodes += 1
        if fact_count > 0:
            specs_with_ans.add(spec)
    return {
        "stage": stage,
        "specs_requested": len(specs),
        "specs_with_ans": len(specs_with_ans),
        "nodes": nodes,
        "supported": supported,
        "total": total,
        "ans": supported / total if total else None,
    }


def summarize_by_spec(rows: list[dict[str, Any]], specs: set[str], stage: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"nodes": 0, "supported": 0, "total": 0})
    for row in rows:
        spec = str(row.get("paper_spec") or "")
        if spec not in specs or str(row.get("stage") or "") != stage or not is_main_factual(row):
            continue
        fact_count = int(row.get("atomic_fact_count") or 0)
        if fact_count <= 0:
            continue
        out[spec]["nodes"] += 1
        out[spec]["supported"] += int(row.get("supported_fact_count") or 0)
        out[spec]["total"] += fact_count
    return {
        spec: {
            **values,
            "ans": values["supported"] / values["total"] if values["total"] else None,
        }
        for spec, values in out.items()
    }


def strict_specs(accounting_rows: list[dict[str, str]]) -> set[str]:
    return {row.get("paper_spec", "") for row in accounting_rows if row.get("current_outcome") == "strict_success"}


def discover_candidate_ans_paths(proposal_root: Path) -> list[Path]:
    audit = read_json(proposal_root / "STRICT_MERGE_PROPOSAL_AUDIT.json", {})
    summary = audit.get("summary") if isinstance(audit, dict) else {}
    paths: list[Path] = []
    merge_candidates_text = str((summary or {}).get("source_merge_candidates") or "")
    if merge_candidates_text:
        merge_candidates_path = resolve_path(merge_candidates_text)
        merge_package = read_json(merge_candidates_path, {})
        guard_texts = []
        source_ans_guards = (merge_package or {}).get("source_ans_guards")
        if isinstance(source_ans_guards, list):
            guard_texts.extend(str(item or "").strip() for item in source_ans_guards)
        guard_text = str((merge_package or {}).get("source_ans_guard") or "").strip()
        if guard_text:
            guard_texts.append(guard_text)
        for guard_text in guard_texts:
            if not guard_text:
                continue
            guard = read_json(resolve_path(guard_text), {})
            summary_text = str((guard or {}).get("source_ans_summary") or "")
            if summary_text:
                node_path = resolve_path(summary_text).parent / "ans_node_results.jsonl"
                if node_path.exists():
                    paths.append(node_path)
    return sorted({path.resolve() for path in paths})


def candidate_rows_for_specs(paths: list[Path], specs: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for row in read_jsonl(path):
            if str(row.get("paper_spec") or "") in specs:
                copied = dict(row)
                copied["_source_ans_node_results"] = rel(path)
                rows.append(copied)
    return rows


def normalize_candidate_rows(rows: list[dict[str, Any]], stage: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        copied["_original_stage"] = copied.get("stage", "")
        copied["stage"] = stage
        copied["stage_label"] = "PEARL semantic repair terminal graph (candidate replacement)"
        normalized.append(copied)
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal-root", required=True)
    parser.add_argument("--canonical-accounting", default=str(DEFAULT_CANONICAL_ACCOUNTING))
    parser.add_argument("--base-ans-node-results", default=str(DEFAULT_ANS_NODE_RESULTS))
    parser.add_argument("--stage", default=DEFAULT_STAGE)
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    return parser.parse_args()


def build(args: argparse.Namespace) -> dict[str, Any]:
    proposal_root = resolve_path(args.proposal_root)
    proposal_accounting = proposal_root / "PROPOSED_FULL_350_ACCOUNTING.csv"
    proposal_summary = read_json(proposal_root / "PROPOSED_FULL_350_SUMMARY.json", {})
    canonical_accounting = resolve_path(args.canonical_accounting)
    base_ans_path = resolve_path(args.base_ans_node_results)
    out_root = resolve_path(args.out_root)

    proposal_specs = strict_specs(read_csv(proposal_accounting))
    canonical_specs = strict_specs(read_csv(canonical_accounting))
    base_rows = read_jsonl(base_ans_path)
    candidate_paths = discover_candidate_ans_paths(proposal_root)
    raw_candidate_rows = candidate_rows_for_specs(candidate_paths, proposal_specs)
    candidate_rows = normalize_candidate_rows(raw_candidate_rows, args.stage)
    candidate_specs = {str(row.get("paper_spec") or "") for row in candidate_rows}

    combined_rows = [row for row in base_rows if str(row.get("paper_spec") or "") not in candidate_specs]
    combined_rows.extend(candidate_rows)

    canonical_floor = summarize_rows(base_rows, canonical_specs, args.stage)
    proposal_on_base = summarize_rows(base_rows, proposal_specs, args.stage)
    proposal_combined = summarize_rows(combined_rows, proposal_specs, args.stage)
    candidate_by_spec = summarize_by_spec(candidate_rows, candidate_specs, str(candidate_rows[0].get("stage") or args.stage) if candidate_rows else args.stage)

    floor_value = canonical_floor.get("ans")
    combined_value = proposal_combined.get("ans")
    passed = combined_value is not None and floor_value is not None and combined_value >= floor_value
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "proposal_root": rel(proposal_root),
        "proposal_summary_counts": {
            "strict_success_rows": proposal_summary.get("strict_success_rows"),
            "typed_residual_rows": proposal_summary.get("typed_residual_rows"),
        },
        "source_base_ans_node_results": rel(base_ans_path),
        "source_candidate_ans_node_results": [rel(path) for path in candidate_paths],
        "candidate_specs_replaced": sorted(candidate_specs),
        "canonical_300_floor": canonical_floor,
        "proposal_strict_on_base_ans_artifacts": proposal_on_base,
        "proposal_strict_with_candidate_ans_replacements": proposal_combined,
        "batch_ans_guard_passed": passed,
        "batch_ans_guard_margin": (combined_value - floor_value) if combined_value is not None and floor_value is not None else None,
        "candidate_by_spec_main_factual_ans": candidate_by_spec,
    }

    out_root.mkdir(parents=True, exist_ok=True)
    write_json(out_root / "PROPOSAL_BATCH_ANS_GUARD.json", report)
    write_csv(
        out_root / "PROPOSAL_BATCH_ANS_GUARD.csv",
        [
            {"metric": "canonical_300_floor", **canonical_floor},
            {"metric": "proposal_strict_on_base_ans_artifacts", **proposal_on_base},
            {"metric": "proposal_strict_with_candidate_ans_replacements", **proposal_combined},
        ],
        ["metric", "stage", "specs_requested", "specs_with_ans", "nodes", "supported", "total", "ans"],
    )
    lines = [
        "# Proposal Batch ANS Guard",
        "",
        f"Created: `{report['created_at']}`",
        f"Canonical accounting write: `{report['canonical_accounting_write']}`",
        f"Batch guard passed: `{report['batch_ans_guard_passed']}`",
        f"Batch guard margin: `{report['batch_ans_guard_margin']}`",
        "",
        "| metric | specs with ANS | supported | total | ANS |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, item in [
        ("canonical_300_floor", canonical_floor),
        ("proposal_strict_on_base_ans_artifacts", proposal_on_base),
        ("proposal_strict_with_candidate_ans_replacements", proposal_combined),
    ]:
        lines.append(
            "| {label} | {specs_with_ans}/{specs_requested} | {supported} | {total} | {ans} |".format(
                label=label,
                specs_with_ans=item.get("specs_with_ans"),
                specs_requested=item.get("specs_requested"),
                supported=item.get("supported"),
                total=item.get("total"),
                ans=item.get("ans"),
            )
        )
    write_text(out_root / "PROPOSAL_BATCH_ANS_GUARD.md", "\n".join(lines) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    report = build(parse_args())
    return 0 if report.get("batch_ans_guard_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
