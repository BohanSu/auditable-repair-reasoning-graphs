#!/usr/bin/env python3
"""Materialize same-paper graph transfer candidates for current-version residual repair.

This is a provider-free staging utility. It takes a verified graph_spec for one
paper and re-stages the same graph against other residual rows for the same
paper, using each target row's own fixed anchor packet. Fresh EC/REA evaluation
is still required after this local preflight.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
    / "10_standard_flow_350_subset_package"
)
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_PACKET_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "packets"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "same_paper_graph_transfer"

FRAMEWORK_DIR = PROJECT_ROOT / "code" / "framework"
RESTRUCTURED_DIR = PROJECT_ROOT / "operation_records" / "restructured"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(FRAMEWORK_DIR))
sys.path.insert(0, str(RESTRUCTURED_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from graph_spec_validator import validate_graph_spec  # type: ignore  # noqa: E402
from run_evidence_bound_graph_spec_regeneration import preflight_graph_spec  # type: ignore  # noqa: E402


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def safe_slug(value: str, *, limit: int = 140) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    out = re.sub(r"_+", "_", out).strip("_")
    return (out or "item")[:limit]


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


def read_packet(packet_path: Path) -> Dict[str, Any]:
    if packet_path.is_dir():
        packet_path = packet_path / "packet.json"
    packet = read_json(packet_path, {})
    if not isinstance(packet, dict) or not packet.get("paper_spec"):
        raise RuntimeError(f"bad target packet: {packet_path}")
    return packet


def find_packet_for_spec(packet_root: Path, paper_spec: str) -> Path:
    matches: List[Path] = []
    for packet_file in sorted(packet_root.glob("*/packet.json")):
        packet = read_json(packet_file, {})
        if isinstance(packet, dict) and packet.get("paper_spec") == paper_spec:
            matches.append(packet_file)
    if not matches:
        raise RuntimeError(f"no packet found for paper_spec={paper_spec}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple packets found for paper_spec={paper_spec}: {[rel(p) for p in matches]}")
    return matches[0]


def parse_targets(packet_root: Path, target_packets: Sequence[str], target_specs: Sequence[str]) -> List[Path]:
    targets: List[Path] = []
    for value in target_packets:
        if value.strip():
            targets.append(resolve_path(value.strip()))
    for spec in target_specs:
        spec = spec.strip()
        if spec:
            targets.append(find_packet_for_spec(packet_root, spec))
    out: List[Path] = []
    seen: set[Path] = set()
    for target in targets:
        packet_file = target / "packet.json" if target.is_dir() else target
        resolved = packet_file.resolve()
        if resolved not in seen:
            seen.add(resolved)
            out.append(packet_file)
    return out


def materialize_one(
    *,
    graph_spec: Dict[str, Any],
    source_graph_spec: Path,
    source_stage_dir: Path,
    target_packet_path: Path,
    out_dir: Path,
    label: str,
    model: str,
    copy_staging: bool,
) -> Dict[str, Any]:
    target_packet = read_packet(target_packet_path)
    paper_spec = str(target_packet["paper_spec"])
    attempt_dir = out_dir / safe_slug(paper_spec) / safe_slug(model)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    strict_valid, strict_issues = validate_graph_spec(graph_spec, mode="strict")
    preflight = preflight_graph_spec(graph_spec, target_packet)
    graph_spec_path = attempt_dir / "graph_spec.json"
    dot_path = attempt_dir / "final_clean_graph.dot"
    preflight_path = attempt_dir / "preflight_report.json"
    write_json(graph_spec_path, graph_spec)
    write_text(dot_path, graph_spec_to_dot(graph_spec))
    write_json(
        preflight_path,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "same_paper_graph_transfer",
            "paper_spec": paper_spec,
            "candidate_label": label,
            "model": model,
            "source_graph_spec": rel(source_graph_spec),
            "target_packet": rel(target_packet_path),
            "strict_validator": {"valid": strict_valid, "issues": strict_issues},
            **preflight,
        },
    )

    staged_dir = ""
    if copy_staging and strict_valid and preflight.get("passed_local_preflight") is True:
        stage_dir = out_dir / "strict_gate_staging" / safe_slug(paper_spec) / safe_slug(model)
        stage_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dot_path, stage_dir / "final_clean_graph.dot")
        input_data = source_stage_dir / "input_data.json"
        if not input_data.exists():
            raise RuntimeError(f"missing source input_data.json: {input_data}")
        shutil.copy2(input_data, stage_dir / "input_data.json")
        write_json(
            stage_dir / "evidence_bound_packet_pointer.json",
            {
                "packet": rel(target_packet_path),
                "transfer_preflight_report": rel(preflight_path),
                "source_graph_spec": rel(source_graph_spec),
                "source_stage_dir": rel(source_stage_dir),
                "transfer_policy": "same paper only; target packet anchors are used for fresh evaluation",
            },
        )
        staged_dir = rel(stage_dir)

    entity_cov = preflight.get("entity_coverage") if isinstance(preflight.get("entity_coverage"), dict) else {}
    reasoning = preflight.get("reasoning_units") if isinstance(preflight.get("reasoning_units"), dict) else {}
    connectivity = preflight.get("connectivity") if isinstance(preflight.get("connectivity"), dict) else {}
    return {
        "priority": 1,
        "paper_spec": paper_spec,
        "lane": str(target_packet.get("lane") or "same_paper_graph_transfer"),
        "model": model,
        "candidate_label": label,
        "attempt_path": rel(preflight_path),
        "graph_spec": rel(graph_spec_path),
        "dot": rel(dot_path),
        "preflight_report": rel(preflight_path),
        "staged_run_dir": staged_dir,
        "transfer_source_graph_spec": rel(source_graph_spec),
        "target_packet": rel(target_packet_path),
        "passed_local_preflight": preflight.get("passed_local_preflight") is True,
        "strict_validator_valid": strict_valid,
        "unit_invalid_count": reasoning.get("invalid_target_count", ""),
        "entity_coverage_local": entity_cov.get("coverage_rate_local", ""),
        "covered_entities_local": entity_cov.get("covered_entities", ""),
        "total_entities": entity_cov.get("total_entities", ""),
        "stranded_node_count": len(connectivity.get("stranded_nodes") or []),
        "fresh_gate_required": True,
        "status": "",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-graph-spec", required=True)
    parser.add_argument("--source-stage-dir", required=True)
    parser.add_argument("--target-packets", nargs="*", default=[])
    parser.add_argument("--target-specs", nargs="*", default=[])
    parser.add_argument("--packet-root", default=str(DEFAULT_PACKET_ROOT))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--label", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--copy-staging", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_graph_spec = resolve_path(args.source_graph_spec)
    source_stage_dir = resolve_path(args.source_stage_dir)
    packet_root = resolve_path(args.packet_root)
    target_packets = parse_targets(packet_root, args.target_packets, args.target_specs)
    if not target_packets:
        raise RuntimeError("no target packets or specs supplied")

    graph_spec = read_json(source_graph_spec, {})
    if not isinstance(graph_spec, dict) or not graph_spec.get("nodes"):
        raise RuntimeError(f"bad source graph_spec: {source_graph_spec}")
    label = args.label
    model = args.model or label
    out_dir = resolve_path(args.out_root) / safe_slug(label)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        materialize_one(
            graph_spec=graph_spec,
            source_graph_spec=source_graph_spec,
            source_stage_dir=source_stage_dir,
            target_packet_path=target_packet,
            out_dir=out_dir,
            label=label,
            model=model,
            copy_staging=args.copy_staging,
        )
        for target_packet in target_packets
    ]
    fieldnames = [
        "priority",
        "paper_spec",
        "lane",
        "model",
        "candidate_label",
        "attempt_path",
        "graph_spec",
        "dot",
        "preflight_report",
        "staged_run_dir",
        "transfer_source_graph_spec",
        "target_packet",
        "passed_local_preflight",
        "strict_validator_valid",
        "unit_invalid_count",
        "entity_coverage_local",
        "covered_entities_local",
        "total_entities",
        "stranded_node_count",
        "fresh_gate_required",
        "status",
    ]
    write_csv(out_dir / "SAME_PAPER_TRANSFER_ATTEMPT_INDEX.csv", rows, fieldnames)
    write_json(out_dir / "SAME_PAPER_TRANSFER_ATTEMPT_INDEX.json", {"rows": rows})
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "same_paper_graph_transfer",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "out_root": rel(out_dir),
        "source_graph_spec": rel(source_graph_spec),
        "source_stage_dir": rel(source_stage_dir),
        "candidate_label": label,
        "attempt_rows": len(rows),
        "local_preflight_passed": sum(1 for row in rows if row.get("passed_local_preflight") is True),
        "strict_validator_valid": sum(1 for row in rows if row.get("strict_validator_valid") is True),
        "by_lane": dict(Counter(row.get("lane", "") for row in rows)),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
    }
    write_json(out_dir / "SAME_PAPER_TRANSFER_ATTEMPT_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
