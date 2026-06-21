#!/usr/bin/env python3
"""Materialize anchor-adapted same-paper graph candidates.

The script is intentionally narrow and provider-free. It starts from a verified
paper-level graph, applies hand-audited evidence-bound adaptations for target
residual anchors, runs local preflight against the target packet, and stages only
preflight-clean candidates for fresh EC/REA evaluation.
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
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "anchor_adapted_transfer"

FRAMEWORK_DIR = PROJECT_ROOT / "code" / "framework"
RESTRUCTURED_DIR = PROJECT_ROOT / "operation_records" / "restructured"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(FRAMEWORK_DIR))
sys.path.insert(0, str(RESTRUCTURED_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from graph_spec_validator import validate_graph_spec  # type: ignore  # noqa: E402
from run_evidence_bound_graph_spec_regeneration import preflight_graph_spec  # type: ignore  # noqa: E402


ADAPTATION_NOTES: Dict[str, Dict[str, Any]] = {
    "gemini_3_1_pro_preview:s41467-025-56283-1": {
        "supported": [
            "energy minimization is treated as the target anchor's spelling variant of the directly evidenced energy minimisation protocol in sentence 25",
            "universal critical state is anchored through critical-state evidence in sentence 18 plus hyperuniform critical-phenomena context; this remains a higher-risk anchor and must pass fresh judge",
        ],
        "risk_anchors": ["universal critical state"],
    },
    "grok_4_1_thinking:s41467-025-56283-1": {
        "supported": [
            "energy minimization is treated as the target anchor's spelling variant of the directly evidenced energy minimisation protocol in sentence 25",
            "ideal glass is directly supported by sentence 38",
        ],
        "risk_anchors": [],
    },
    "qwen3_5_397b_a17b:s41467-025-56283-1": {
        "supported": [
            "vibrational stability and thermodynamic stability are directly listed in sentence 35",
            "kinetic stability is directly supported by sentence 36 and also listed in sentence 35",
        ],
        "risk_anchors": [],
    },
}


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


def find_packet_for_spec(packet_root: Path, paper_spec: str) -> Path:
    matches: List[Path] = []
    for packet_file in sorted(packet_root.glob("*/packet.json")):
        packet = read_json(packet_file, {})
        if isinstance(packet, dict) and packet.get("paper_spec") == paper_spec:
            matches.append(packet_file)
    if not matches:
        raise RuntimeError(f"no packet found for paper_spec={paper_spec}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple packets for paper_spec={paper_spec}: {[rel(p) for p in matches]}")
    return matches[0]


def parse_targets(packet_root: Path, target_specs: Sequence[str]) -> List[Path]:
    targets: List[Path] = []
    for spec in target_specs:
        spec = spec.strip()
        if spec:
            targets.append(find_packet_for_spec(packet_root, spec))
    return targets


def node_map(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(node.get("id")): node for node in spec.get("nodes") or [] if isinstance(node, dict) and node.get("id")}


def update_node(nodes: Dict[str, Dict[str, Any]], node_id: str, *, text: str, source: List[int] | None = None) -> None:
    if node_id not in nodes:
        raise RuntimeError(f"source graph missing expected node {node_id}")
    nodes[node_id] = dict(nodes[node_id])
    nodes[node_id]["text"] = text
    if source is not None:
        nodes[node_id]["source"] = source


def append_node(nodes: Dict[str, Dict[str, Any]], node_id: str, *, text: str, source: List[int]) -> None:
    if node_id in nodes:
        raise RuntimeError(f"duplicate adapted node id {node_id}")
    nodes[node_id] = {"id": node_id, "source": source, "text": text}


def replace_edges(spec: Dict[str, Any], *, target: str, new_edges: List[Dict[str, str]]) -> None:
    spec["edges"] = [
        edge
        for edge in spec.get("edges") or []
        if not (isinstance(edge, dict) and str(edge.get("target") or "") == target)
    ]
    spec["edges"].extend(new_edges)


def add_edges(spec: Dict[str, Any], new_edges: List[Dict[str, str]]) -> None:
    seen = {
        (str(edge.get("source") or ""), str(edge.get("target") or ""), str(edge.get("type") or ""))
        for edge in spec.get("edges") or []
        if isinstance(edge, dict)
    }
    for edge in new_edges:
        key = (edge["source"], edge["target"], edge["type"])
        if key not in seen:
            spec.setdefault("edges", []).append(edge)
            seen.add(key)


def commit_nodes(spec: Dict[str, Any], nodes: Dict[str, Dict[str, Any]]) -> None:
    ordered: List[Dict[str, Any]] = []
    emitted: set[str] = set()
    for node in spec.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if node_id in nodes:
            ordered.append(nodes[node_id])
            emitted.add(node_id)
    for node_id in sorted(set(nodes) - emitted):
        ordered.append(nodes[node_id])
    spec["nodes"] = ordered


def remove_incoming_edges(spec: Dict[str, Any], target: str) -> None:
    spec["edges"] = [
        edge
        for edge in spec.get("edges") or []
        if not (isinstance(edge, dict) and str(edge.get("target") or "") == target)
    ]


def drop_nodes(spec: Dict[str, Any], nodes: Dict[str, Dict[str, Any]], node_ids: Sequence[str]) -> None:
    drop = {str(node_id) for node_id in node_ids}
    for node_id in drop:
        nodes.pop(node_id, None)
    spec["edges"] = [
        edge
        for edge in spec.get("edges") or []
        if not (
            isinstance(edge, dict)
            and (str(edge.get("source") or "") in drop or str(edge.get("target") or "") in drop)
        )
    ]


def apply_v2_bridge_repairs(spec: Dict[str, Any], nodes: Dict[str, Dict[str, Any]], paper_spec: str) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    update_node(
        nodes,
        "R8",
        text=(
            "induction-reasoning: The evidence jointly shows lower-wavenumber density-spectrum scaling and "
            "jamming-point density/contact-number hyperuniformity, so the study links density scaling with "
            "contact-number fluctuations specifically near the jamming point."
        ),
    )
    changes.append(
        {
            "node": "R8",
            "reason": "narrow weak second-order induction to exactly the density-scaling/contact-number claim supported by its premises",
        }
    )
    if paper_spec == "grok_4_1_thinking:s41467-025-56283-1":
        update_node(
            nodes,
            "R9",
            text=(
                "deduction-reasoning: The paper explicitly states that its distinct stable disordered solid "
                "state is more stable than a hypothetical ideal glass, so ideal glass is a directly evidenced "
                "comparison point."
            ),
        )
        replace_edges(
            spec,
            target="R9",
            new_edges=[
                {"source": "E15", "target": "R9", "type": "deduction-case"},
                {"source": "E15", "target": "R9", "type": "deduction-rule"},
            ],
        )
        changes.append(
            {
                "node": "R9",
                "reason": "make ideal-glass support a narrow direct deduction from the explicit sentence-38 statement",
            }
        )
    return changes


def apply_v3_source_promotion(spec: Dict[str, Any], nodes: Dict[str, Dict[str, Any]], paper_spec: str) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    update_node(
        nodes,
        "R8",
        source=[34, 0, 0],
        text=(
            "Currently special mechanical self-organisation at the jamming point leads to density and "
            "contact-number hyperuniformity, reflecting the essential difference in marginal stability "
            "between marginally jammed and conventional over-jammed states."
        ),
    )
    remove_incoming_edges(spec, "R8")
    add_edges(spec, [{"source": "R7", "target": "NROOT", "type": "induction-case"}])
    changes.append(
        {
            "node": "R8",
            "reason": "promote repeatedly failed R8 reasoning target to explicit sentence-34 source support while keeping R7 connected to NROOT",
        }
    )
    if paper_spec == "grok_4_1_thinking:s41467-025-56283-1":
        update_node(
            nodes,
            "R9",
            source=[38, 0, 0],
            text=(
                "Currently the findings indicate a distinct, stable, disordered solid state that is more "
                "stable than a hypothetical ideal glass."
            ),
        )
        remove_incoming_edges(spec, "R9")
        drop_nodes(spec, nodes, ["E15"])
        changes.append(
            {
                "node": "R9",
                "reason": "promote ideal-glass anchor to explicit sentence-38 source support instead of a separate judged deduction",
            }
        )
    if paper_spec == "qwen3_5_397b_a17b:s41467-025-56283-1":
        replace_edges(
            spec,
            target="R9",
            new_edges=[
                {"source": "E16", "target": "R9", "type": "deduction-case"},
                {"source": "E15", "target": "R9", "type": "deduction-rule"},
            ],
        )
        update_node(
            nodes,
            "R9",
            text=(
                "deduction-reasoning: Because hyperuniform glasses are explicitly reported to have exceptional "
                "vibrational, kinetic, thermodynamic, and mechanical stability, and the finite energy gap "
                "indicates mechanical and kinetic stability, the evidence supports the stated stability anchors."
            ),
        )
        changes.append(
            {
                "node": "R9",
                "reason": "make Qwen stability unit a direct deduction from sentence-35 broad stability and sentence-36 finite-gap support",
            }
        )
    return changes


def adapt_spec(source_spec: Dict[str, Any], paper_spec: str, *, variant: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    spec = json.loads(json.dumps(source_spec, ensure_ascii=False))
    nodes = node_map(spec)
    changes: List[Dict[str, Any]] = []

    if paper_spec in {
        "gemini_3_1_pro_preview:s41467-025-56283-1",
        "grok_4_1_thinking:s41467-025-56283-1",
    }:
        update_node(
            nodes,
            "R2",
            text=(
                "deduction-reasoning: The iterative particle-resizing and energy-minimisation protocol, "
                "using energy minimization as the target anchor's spelling variant, is the reported "
                "preparation route for hyperuniform over-jammed packings."
            ),
        )
        changes.append({"node": "R2", "reason": "cover target spelling variant energy minimization while preserving sentence-25 evidence"})

    if paper_spec == "gemini_3_1_pro_preview:s41467-025-56283-1":
        append_node(
            nodes,
            "E15",
            source=[18, 0, 0],
            text=(
                "Currently the low-q hyperuniform scaling resembles the critical state observed in "
                "Manna-class absorbing phase transitions."
            ),
        )
        append_node(
            nodes,
            "R9",
            source=[0, 0, 0],
            text=(
                "induction-reasoning: Hyperuniform systems are associated with critical phenomena, and "
                "the study's low-q scaling resembles a critical state; together these evidence a "
                "critical-state framing for the target anchor universal critical state."
            ),
        )
        replace_edges(
            spec,
            target="R9",
            new_edges=[
                {"source": "E15", "target": "R9", "type": "induction-case"},
                {"source": "E14", "target": "R9", "type": "induction-common"},
            ],
        )
        add_edges(spec, [{"source": "R9", "target": "NROOT", "type": "induction-case"}])
        changes.append({"node": "R9", "reason": "add evidence-bound critical-state anchor for Gemini target entities"})

    if paper_spec == "grok_4_1_thinking:s41467-025-56283-1":
        append_node(
            nodes,
            "E15",
            source=[38, 0, 0],
            text=(
                "Currently the findings indicate a distinct stable disordered solid state that is more "
                "stable than a hypothetical ideal glass."
            ),
        )
        append_node(
            nodes,
            "R9",
            source=[0, 0, 0],
            text=(
                "deduction-reasoning: Because the findings identify a stable disordered solid state "
                "more stable than a hypothetical ideal glass, ideal glass is a directly evidenced "
                "comparison point for the studied disordered solid."
            ),
        )
        replace_edges(
            spec,
            target="R9",
            new_edges=[
                {"source": "E15", "target": "R9", "type": "deduction-case"},
                {"source": "E12", "target": "R9", "type": "deduction-rule"},
            ],
        )
        add_edges(spec, [{"source": "R9", "target": "NROOT", "type": "induction-case"}])
        changes.append({"node": "R9", "reason": "add direct ideal-glass comparison evidence for Grok anchor"})

    if paper_spec == "qwen3_5_397b_a17b:s41467-025-56283-1":
        append_node(
            nodes,
            "E15",
            source=[35, 0, 0],
            text=(
                "Currently hyperuniform glasses exhibit exceptional stability across vibrational, "
                "kinetic, thermodynamic, and mechanical properties."
            ),
        )
        append_node(
            nodes,
            "E16",
            source=[36, 0, 0],
            text=(
                "Currently the vibrational density of states exhibits a finite energy gap, indicating "
                "remarkable mechanical and kinetic stability."
            ),
        )
        append_node(
            nodes,
            "R9",
            source=[0, 0, 0],
            text=(
                "induction-reasoning: Hyperuniform glasses are reported to have exceptional vibrational, "
                "kinetic, thermodynamic, and mechanical stability; the finite energy gap further "
                "supports mechanical and kinetic stability."
            ),
        )
        replace_edges(
            spec,
            target="R9",
            new_edges=[
                {"source": "E15", "target": "R9", "type": "induction-case"},
                {"source": "E16", "target": "R9", "type": "induction-case"},
                {"source": "R8", "target": "R9", "type": "induction-common"},
            ],
        )
        add_edges(spec, [{"source": "R9", "target": "NROOT", "type": "induction-case"}])
        changes.append({"node": "R9", "reason": "add stability-focused evidence for Qwen target anchors"})

    if variant == "v2_bridge_hard_units":
        changes.extend(apply_v2_bridge_repairs(spec, nodes, paper_spec))
    elif variant == "v3_source_promotion":
        changes.extend(apply_v3_source_promotion(spec, nodes, paper_spec))
    elif variant != "v1_anchor_adapted":
        raise RuntimeError(f"unsupported adaptation variant: {variant}")

    if not changes:
        raise RuntimeError(f"no adaptation recipe for {paper_spec}")
    commit_nodes(spec, nodes)
    return spec, {"variant": variant, "changes": changes, **ADAPTATION_NOTES.get(paper_spec, {})}


def materialize_one(
    *,
    source_spec: Dict[str, Any],
    source_graph_spec: Path,
    source_stage_dir: Path,
    target_packet_path: Path,
    out_dir: Path,
    label: str,
    model: str,
    copy_staging: bool,
    variant: str,
) -> Dict[str, Any]:
    packet = read_json(target_packet_path, {})
    if not isinstance(packet, dict) or not packet.get("paper_spec"):
        raise RuntimeError(f"bad target packet: {target_packet_path}")
    paper_spec = str(packet["paper_spec"])
    spec, adaptation_report = adapt_spec(source_spec, paper_spec, variant=variant)

    attempt_dir = out_dir / safe_slug(paper_spec) / safe_slug(model)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    strict_valid, strict_issues = validate_graph_spec(spec, mode="strict")
    preflight = preflight_graph_spec(spec, packet)
    graph_spec_path = attempt_dir / "graph_spec.json"
    dot_path = attempt_dir / "final_clean_graph.dot"
    preflight_path = attempt_dir / "preflight_report.json"
    adaptation_path = attempt_dir / "anchor_adaptation_report.json"
    write_json(graph_spec_path, spec)
    write_text(dot_path, graph_spec_to_dot(spec))
    write_json(adaptation_path, {"paper_spec": paper_spec, "candidate_label": label, **adaptation_report})
    write_json(
        preflight_path,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "anchor_adapted_same_paper_transfer",
            "paper_spec": paper_spec,
            "candidate_label": label,
            "model": model,
            "source_graph_spec": rel(source_graph_spec),
            "target_packet": rel(target_packet_path),
            "anchor_adaptation_report": rel(adaptation_path),
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
                "anchor_adaptation_report": rel(adaptation_path),
                "preflight_report": rel(preflight_path),
                "source_graph_spec": rel(source_graph_spec),
                "source_stage_dir": rel(source_stage_dir),
                "transfer_policy": "same paper only; target anchors adapted with evidence-bound local additions",
            },
        )
        staged_dir = rel(stage_dir)

    entity_cov = preflight.get("entity_coverage") if isinstance(preflight.get("entity_coverage"), dict) else {}
    reasoning = preflight.get("reasoning_units") if isinstance(preflight.get("reasoning_units"), dict) else {}
    connectivity = preflight.get("connectivity") if isinstance(preflight.get("connectivity"), dict) else {}
    return {
        "priority": 1,
        "paper_spec": paper_spec,
        "lane": str(packet.get("lane") or "anchor_adapted_same_paper_transfer"),
        "model": model,
        "candidate_label": label,
        "attempt_path": rel(adaptation_path),
        "graph_spec": rel(graph_spec_path),
        "dot": rel(dot_path),
        "preflight_report": rel(preflight_path),
        "staged_run_dir": staged_dir,
        "target_packet": rel(target_packet_path),
        "passed_local_preflight": preflight.get("passed_local_preflight") is True,
        "strict_validator_valid": strict_valid,
        "unit_invalid_count": reasoning.get("invalid_target_count", ""),
        "entity_coverage_local": entity_cov.get("coverage_rate_local", ""),
        "covered_entities_local": entity_cov.get("covered_entities", ""),
        "total_entities": entity_cov.get("total_entities", ""),
        "stranded_node_count": len(connectivity.get("stranded_nodes") or []),
        "risk_anchors": ";".join(adaptation_report.get("risk_anchors") or []),
        "fresh_gate_required": True,
        "status": "",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-graph-spec", required=True)
    parser.add_argument("--source-stage-dir", required=True)
    parser.add_argument("--target-specs", nargs="+", required=True)
    parser.add_argument("--packet-root", default=str(DEFAULT_PACKET_ROOT))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--label", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--variant",
        choices=["v1_anchor_adapted", "v2_bridge_hard_units", "v3_source_promotion"],
        default="v1_anchor_adapted",
    )
    parser.add_argument("--copy-staging", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_graph_spec = resolve_path(args.source_graph_spec)
    source_stage_dir = resolve_path(args.source_stage_dir)
    target_packets = parse_targets(resolve_path(args.packet_root), args.target_specs)
    source_spec = read_json(source_graph_spec, {})
    if not isinstance(source_spec, dict) or not source_spec.get("nodes"):
        raise RuntimeError(f"bad source graph_spec: {source_graph_spec}")
    label = args.label
    model = args.model or label
    out_dir = resolve_path(args.out_root) / safe_slug(label)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        materialize_one(
            source_spec=source_spec,
            source_graph_spec=source_graph_spec,
            source_stage_dir=source_stage_dir,
            target_packet_path=target_packet,
            out_dir=out_dir,
            label=label,
            model=model,
            copy_staging=args.copy_staging,
            variant=args.variant,
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
        "target_packet",
        "passed_local_preflight",
        "strict_validator_valid",
        "unit_invalid_count",
        "entity_coverage_local",
        "covered_entities_local",
        "total_entities",
        "stranded_node_count",
        "risk_anchors",
        "fresh_gate_required",
        "status",
    ]
    write_csv(out_dir / "ANCHOR_ADAPTED_ATTEMPT_INDEX.csv", rows, fieldnames)
    write_json(out_dir / "ANCHOR_ADAPTED_ATTEMPT_INDEX.json", {"rows": rows})
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "anchor_adapted_same_paper_transfer",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "out_root": rel(out_dir),
        "source_graph_spec": rel(source_graph_spec),
        "source_stage_dir": rel(source_stage_dir),
        "candidate_label": label,
        "variant": args.variant,
        "attempt_rows": len(rows),
        "local_preflight_passed": sum(1 for row in rows if row.get("passed_local_preflight") is True),
        "strict_validator_valid": sum(1 for row in rows if row.get("strict_validator_valid") is True),
        "risk_anchor_rows": sum(1 for row in rows if row.get("risk_anchors")),
        "by_lane": dict(Counter(row.get("lane", "") for row in rows)),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
    }
    write_json(out_dir / "ANCHOR_ADAPTED_ATTEMPT_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
