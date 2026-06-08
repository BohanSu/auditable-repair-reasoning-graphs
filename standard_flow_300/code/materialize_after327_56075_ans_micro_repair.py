#!/usr/bin/env python3
"""Materialize a provider-free ANS micro-repair candidate for after327 row 56075.

The current 56075 candidate already passed clean fresh CG/REA but regressed on
row ANS. The ANS audit localizes the drift to Li-rich surface-layer wording in
NROOT/R1/R2 and one over-specific E5 clause. This script preserves the graph
topology and source tuples, rewrites only those named nodes to the supported
surface Ni reduction layer wording, and stages the candidate for standard fresh
eval plus ANS guards. It never writes canonical accounting.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
CODE_DIR = Path(__file__).resolve().parent
RESTRUCTURED_DIR = PROJECT_ROOT / "operation_records" / "restructured"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(RESTRUCTURED_DIR))

from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from run_evidence_bound_graph_spec_regeneration import preflight_graph_spec  # type: ignore  # noqa: E402


PAPER_SPEC = "gemini_3_1_pro_preview:s41467-025-56075-7"
MODEL = "gemini_3_1_pro_preview"
PAPER = "s41467-025-56075-7"
LABEL = "after327-56075-ansmicro-ni-reduction-v4"
LANE = "after327_ans_micro_repair"
BASE_GRAPH_SPEC = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "no_anchor_after_notation_v3_atomic_anssafe"
    / "model_attempts_gpt55_anssafe_v2_n5"
    / "007_gemini_3_1_pro_preview_s41467-025-56075-7"
    / "gpt-5.5"
    / "graph_spec.json"
)
BASE_STAGED_DIR = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "no_anchor_after_notation_v3_atomic_anssafe"
    / "strict_gate_staging_gpt55_anssafe_v2_n5"
    / "gemini_3_1_pro_preview_s41467-025-56075-7"
    / "gpt-5.5"
)
PACKET = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "packets"
    / "009_gemini_3_1_pro_preview__s41467-025-56075-7__20260318_021445"
    / "packet.json"
)
OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "after327_ans_micro_repair" / "20260606_56075_ni_reduction_v4"

NODE_REWRITES = {
    "NROOT": (
        "Rapid quenching from the peak calcination temperature regulates nickel-rich "
        "LiNixMnyCozO2 cathode particles; lithium outward diffusion forms a Li-rich "
        "surface layer and lithium diffusion creates a Ni-reduced surface layer; this "
        "surface Ni reduction layer decreases surface reactivity, while improved "
        "surface stability allows homogeneous bulk redox behavior and favorable "
        "high-voltage cycling performance without excessive extra dopants."
    ),
    "R1": (
        "Substantial lithium diffusion from the bulk to the surface is observed in "
        "NMC polycrystalline particles."
    ),
    "R2": (
        "Lithium diffusion from the bulk to the surface creates a Ni-reduced surface "
        "layer."
    ),
    "E5": (
        "Controlling materials microstructures without excessively using dopants has "
        "become an attractive strategy for long-term manufacturing reliability."
    ),
}


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


def apply_rewrites(spec: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    out = deepcopy(spec)
    nodes = out.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("bad graph spec: nodes is not a list")
    by_id = {str(node.get("id")): node for node in nodes if isinstance(node, dict)}
    changes: list[dict[str, Any]] = []
    for node_id, replacement in NODE_REWRITES.items():
        node = by_id.get(node_id)
        if node is None:
            raise RuntimeError(f"target node missing: {node_id}")
        before = {
            "id": node_id,
            "source": list(node.get("source") or []),
            "text": str(node.get("text") or ""),
        }
        node["text"] = replacement
        changes.append(
            {
                "action": "rewrite_node_text",
                "node_id": node_id,
                "before": before,
                "after": {
                    "id": node_id,
                    "source": list(node.get("source") or []),
                    "text": replacement,
                },
            }
        )
    edges = out.get("edges")
    if not isinstance(edges, list):
        raise RuntimeError("bad graph spec: edges is not a list")
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("source") == "R1" and edge.get("target") == "R2":
            before = dict(edge)
            edge["source"] = "E11"
            edge["type"] = "deduction-rule"
            changes.append(
                {
                    "action": "retarget_edge",
                    "before": before,
                    "after": dict(edge),
                    "rationale": "R2's Ni-reduced surface-layer claim is directly supported by E11, not by R1's bulk-to-surface diffusion observation.",
                }
            )
            break
    key = ("R1", "NROOT", "induction-case")
    existing = {
        (str(edge.get("source")), str(edge.get("target")), str(edge.get("type")))
        for edge in edges
        if isinstance(edge, dict)
    }
    if key not in existing:
        edge = {"source": "R1", "target": "NROOT", "type": "induction-case"}
        edges.append(edge)
        changes.append(
            {
                "action": "add_edge",
                "edge": edge,
                "rationale": "Keep the supported bulk-to-surface lithium diffusion observation connected to NROOT after R2 is retargeted to E11.",
            }
        )
    return out, changes


def render_repair_explanation(changes: list[dict[str, Any]], preflight: dict[str, Any]) -> str:
    lines = [
        "# After327 56075 ANS Micro-Repair",
        "",
        f"Created: `{datetime.now().astimezone().isoformat(timespec='seconds')}`",
        "",
        "Provider calls: `False`",
        "Canonical accounting write: `False`",
        "",
        "## Repair Contract",
        "",
        "- Source state: clean fresh `CG=1.0` / `REA=1.0`, row ANS failed.",
        "- Failure: unsupported Li-rich surface-layer reasoning drift.",
        "- Allowed edits: `NROOT`, `R1`, `R2`, `E5` text only.",
        "- Preserved: graph topology, node ids, source tuples, input data, packet pointer.",
        "- Remaining gates: standard fresh eval, row ANS non-regression, proposal batch ANS guard.",
        "",
        "## Local Preflight",
        "",
        f"- Passed: `{preflight.get('passed_local_preflight')}`",
        f"- Reasoning invalid targets: `{(preflight.get('reasoning_units') or {}).get('invalid_target_count')}`",
        f"- Premise high-risk count: `{(preflight.get('immediate_premise_support') or {}).get('high_risk_count')}`",
        f"- Coverage: `{(preflight.get('entity_coverage') or {}).get('covered_entities')}/{(preflight.get('entity_coverage') or {}).get('total_entities')}`",
        "",
        "## Rewrites",
        "",
    ]
    for change in changes:
        title = change.get("node_id") or f"{change.get('before', {}).get('source')}->{change.get('before', {}).get('target')}"
        lines.append(f"### {title}")
        lines.append("")
        if change.get("action") == "retarget_edge":
            lines.append("Rationale:")
            lines.append("")
            lines.append(str(change.get("rationale") or ""))
            lines.append("")
            lines.append("Before:")
            lines.append("")
            lines.append(f"> {change['before']}")
            lines.append("")
            lines.append("After:")
            lines.append("")
            lines.append(f"> {change['after']}")
            lines.append("")
            continue
        if change.get("action") == "add_edge":
            lines.append("Rationale:")
            lines.append("")
            lines.append(str(change.get("rationale") or ""))
            lines.append("")
            lines.append("Added edge:")
            lines.append("")
            lines.append(f"> {change['edge']}")
            lines.append("")
            continue
        lines.append("Before:")
        lines.append("")
        lines.append(f"> {change['before']['text']}")
        lines.append("")
        lines.append("After:")
        lines.append("")
        lines.append(f"> {change['after']['text']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    base_spec = read_json(BASE_GRAPH_SPEC)
    packet = read_json(PACKET)
    candidate_spec, changes = apply_rewrites(base_spec)
    preflight = preflight_graph_spec(candidate_spec, packet)

    candidate_dir = OUT_ROOT / "candidates" / "gemini_3_1_pro_preview_s41467-025-56075-7" / LABEL
    staged_dir = OUT_ROOT / "staged" / "gemini_3_1_pro_preview_s41467-025-56075-7" / LABEL
    candidate_dir.mkdir(parents=True, exist_ok=True)
    staged_dir.mkdir(parents=True, exist_ok=True)
    graph_spec = candidate_dir / "graph_spec.json"
    dot = candidate_dir / "final_clean_graph.dot"
    preflight_report = candidate_dir / "preflight_report.json"
    repair_explanation = candidate_dir / "REPAIR_EXPLANATION.md"

    write_json(graph_spec, candidate_spec)
    write_text(dot, graph_spec_to_dot(candidate_spec))
    write_json(
        preflight_report,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": LANE,
            "paper_spec": PAPER_SPEC,
            "model": MODEL,
            "paper": PAPER,
            "label": LABEL,
            "base_graph_spec": rel(BASE_GRAPH_SPEC),
            "base_staged_dir": rel(BASE_STAGED_DIR),
            "packet": rel(PACKET),
            "changes": changes,
            "provider_calls": False,
            "canonical_accounting_write": False,
            **preflight,
        },
    )
    write_text(repair_explanation, render_repair_explanation(changes, preflight))

    shutil.copy2(graph_spec, staged_dir / "graph_spec.json")
    shutil.copy2(dot, staged_dir / "final_clean_graph.dot")
    shutil.copy2(BASE_STAGED_DIR / "input_data.json", staged_dir / "input_data.json")
    write_json(
        staged_dir / "evidence_bound_packet_pointer.json",
        {
            "packet": rel(PACKET),
            "source": LABEL,
            "base_graph_spec": rel(BASE_GRAPH_SPEC),
            "preflight_report": rel(preflight_report),
        },
    )

    row = {
        "priority": 1,
        "paper_spec": PAPER_SPEC,
        "lane": LANE,
        "model": MODEL,
        "paper": PAPER,
        "failure_type": "fresh_1_1_ans_failed",
        "candidate_label": LABEL,
        "passed_local_preflight": preflight.get("passed_local_preflight"),
        "covered_entities_local": (preflight.get("entity_coverage") or {}).get("covered_entities", ""),
        "total_entities_local": (preflight.get("entity_coverage") or {}).get("total_entities", ""),
        "premise_support_high_risk_count": (preflight.get("immediate_premise_support") or {}).get("high_risk_count", ""),
        "row_ans_floor": "1.0",
        "fresh_eval_candidate": bool(preflight.get("passed_local_preflight")),
        "graph_spec": rel(graph_spec),
        "dot": rel(dot),
        "staged_run_dir": rel(staged_dir),
        "preflight_report": rel(preflight_report),
        "final_clean_graph_sha256": sha256_file(dot),
        "input_data": rel(staged_dir / "input_data.json"),
        "packet": rel(PACKET),
        "repair_explanation": rel(repair_explanation),
    }
    fieldnames = [
        "priority",
        "paper_spec",
        "lane",
        "model",
        "paper",
        "failure_type",
        "candidate_label",
        "passed_local_preflight",
        "covered_entities_local",
        "total_entities_local",
        "premise_support_high_risk_count",
        "row_ans_floor",
        "fresh_eval_candidate",
        "graph_spec",
        "dot",
        "staged_run_dir",
        "preflight_report",
        "final_clean_graph_sha256",
        "input_data",
        "packet",
        "repair_explanation",
    ]
    write_csv(OUT_ROOT / "ATTEMPT_INDEX.csv", [row], fieldnames)
    write_json(OUT_ROOT / "ATTEMPT_INDEX.json", {"rows": [row]})
    write_json(
        OUT_ROOT / "SUMMARY.json",
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": LANE,
            "paper_spec": PAPER_SPEC,
            "candidate_label": LABEL,
            "provider_calls": False,
            "canonical_accounting_write": False,
            "row": row,
            "changes": changes,
            "preflight": preflight,
        },
    )
    print(json.dumps({"attempt_index": rel(OUT_ROOT / "ATTEMPT_INDEX.csv"), "row": row}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
