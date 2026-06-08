#!/usr/bin/env python3
"""Materialize a compact source-seed candidate for grok56819.

The prior source-leaf candidate covers all anchor entities but fresh evaluation
rejects one reasoning step that infers current training demand from generic
machine-learning premises. This provider-free candidate rebuilds the row as a
small directly sourced induction graph to avoid unsupported template claims.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
CODE_DIR = Path(__file__).resolve().parent
FRAMEWORK_DIR = PROJECT_ROOT / "operation_records" / "restructured"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(FRAMEWORK_DIR))

from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from run_evidence_bound_graph_spec_regeneration import preflight_graph_spec  # type: ignore  # noqa: E402
from build_ans_claims_for_strict_merge_candidates import build_candidate_packet  # type: ignore  # noqa: E402


DEFAULT_PAPER_SPEC = "grok_4_1_thinking:s41467-025-56819-5"
DEFAULT_LABEL = "grok56819-compact-source-seed-v1"
DEFAULT_PACKET = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "packets"
    / "050_grok_4_1_thinking__s41467-025-56819-5__20260319_022319"
    / "packet.json"
)
DEFAULT_INPUT_DATA = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "source_leaf_bridge_after327_20260606"
    / "staged"
    / "grok_4_1_thinking_s41467-025-56819-5"
    / "after327-56819-qwen-support-risk-pruned-v3"
    / "input_data.json"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "compact_source_seed"
    / "20260606_grok56819_compact_source_seed_v1"
)
DEFAULT_ROW_ANS_FLOOR = "0.921875"


ROOT_TEXT = (
    "The paper proposes and demonstrates an optical floating-gate transistor "
    "(O-FGT) that replaces the conventional metal control gate with a dielectric "
    "2D photoconductive (NH4)BiI3 photosensitive control-gate layer. The "
    "material has high density of states, low exciton binding energy, and long "
    "exciton lifetime, allowing light-driven write and erase operations without "
    "gate-voltage assistance. The resulting O-FGT uses ultra-short, ultra-dim, "
    "low-energy light pulses to generate and nonvolatilely store many resistive "
    "synaptic weight states, combines low-power optical training with electrical "
    "inference, and supports ANN and YOLOv8 device-array demonstrations for "
    "neuromorphic computing."
)

SOURCE_LEAVES = [
    {
        "id": "E4",
        "source": [4, 0, 0],
        "text": "Using optical signals for training and converting them into non-volatile electrical signals for inference combines the strengths of both signal types.",
        "edge_type": "induction-case",
    },
    {
        "id": "E6",
        "source": [6, 0, 0],
        "text": "Non-volatile optoelectronic memories are expected to modulate many non-volatile resistive states using ultra-short and ultra-dim light pulses with high accuracy, fast computing speed, and low energy consumption.",
        "edge_type": "induction-case",
    },
    {
        "id": "E8",
        "source": [8, 0, 0],
        "text": "A floating-gate transistor is an ideal device structure for non-volatile optoelectronic memory.",
        "edge_type": "induction-case",
    },
    {
        "id": "E14",
        "source": [14, 0, 0],
        "text": "The authors propose using a 2D photoconductive material as the photosensitive control gate, instead of the commonly used metal control gate, to construct a floating-gate transistor.",
        "edge_type": "induction-common",
    },
    {
        "id": "E15",
        "source": [15, 0, 0],
        "text": "In the proposed device, light plays a role similar to gate voltage in traditional floating-gate transistors, so the device is called an optical floating-gate transistor.",
        "edge_type": "induction-case",
    },
    {
        "id": "E17",
        "source": [17, 0, 0],
        "text": "Low exciton binding energy and long exciton lifetime allow exciton separation inside the photosensitive control gate without additional gate voltage and allow writing into or erasing out of the floating-gate layer.",
        "edge_type": "induction-case",
    },
    {
        "id": "E20",
        "source": [20, 0, 0],
        "text": "The authors synthesize dielectric 2D photoconductive (NH4)BiI3 with high density of states, low exciton binding energy, and long exciton lifetime.",
        "edge_type": "induction-case",
    },
    {
        "id": "E21",
        "source": [21, 0, 0],
        "text": "The O-FGT constructed with (NH4)BiI3 as the photosensitive control gate exhibits adjustable synaptic weights using light without gate-voltage assistance and achieves non-volatile storage, meeting training and inference demands.",
        "edge_type": "induction-case",
    },
    {
        "id": "E22",
        "source": [22, 0, 0],
        "text": "The O-FGT shows ultra-low training energy consumption of about 5.25 fJ to generate a non-volatile state and provides up to 360 resistive states.",
        "edge_type": "induction-case",
    },
    {
        "id": "E23",
        "source": [23, 0, 0],
        "text": "An artificial neural network implemented by on-chip trained weights of a one-transistor-one-memory device array achieves about 99% accuracy in nonlinear localization tasks.",
        "edge_type": "induction-case",
    },
    {
        "id": "E24",
        "source": [24, 0, 0],
        "text": "The device array shows comparable performance to a graphics processing unit in YOLOv8 while greatly reducing energy consumption.",
        "edge_type": "induction-case",
    },
]


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def safe_slug(value: str, *, limit: int = 140) -> str:
    out = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in str(value).strip())
    while "__" in out:
        out = out.replace("__", "_")
    return (out.strip("_") or "item")[:limit]


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


def infer_model_and_paper(paper_spec: str) -> tuple[str, str]:
    if ":" in paper_spec:
        model, paper = paper_spec.split(":", 1)
        return model, paper
    return "", paper_spec


def build_graph_spec(paper_spec: str) -> dict[str, Any]:
    nodes = [{"id": "NROOT", "source": [0, 0, 0], "text": ROOT_TEXT}]
    edges = []
    for leaf in SOURCE_LEAVES:
        nodes.append({"id": leaf["id"], "source": leaf["source"], "text": leaf["text"]})
        edges.append({"source": leaf["id"], "target": "NROOT", "type": leaf["edge_type"]})
    return {"paper_id": paper_spec, "root": "NROOT", "nodes": nodes, "edges": edges}


def claim_args() -> argparse.Namespace:
    return argparse.Namespace(
        window=1,
        root_window=None,
        traversal_depth=2,
        max_evidence_sentences=10,
        max_evidence_chars=1800,
        include_viewpoints=True,
        graph_ordered_evidence=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-spec", default=DEFAULT_PAPER_SPEC)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--packet", default=str(DEFAULT_PACKET))
    parser.add_argument("--input-data", default=str(DEFAULT_INPUT_DATA))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--row-ans-floor", default=DEFAULT_ROW_ANS_FLOOR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paper_spec = args.paper_spec
    model, paper = infer_model_and_paper(paper_spec)
    label = args.label
    packet_path = resolve_path(args.packet)
    input_data = resolve_path(args.input_data)
    out_root = resolve_path(args.out_root)
    candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(label)
    staged_dir = out_root / "staged" / safe_slug(paper_spec) / safe_slug(label)
    claims_dir = out_root / "ans_claims" / safe_slug(label)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    staged_dir.mkdir(parents=True, exist_ok=True)
    claims_dir.mkdir(parents=True, exist_ok=True)

    packet = read_json(packet_path)
    spec = build_graph_spec(paper_spec)
    preflight = preflight_graph_spec(spec, packet)

    graph_spec_path = candidate_dir / "graph_spec.json"
    dot_path = candidate_dir / "final_clean_graph.dot"
    preflight_path = candidate_dir / "preflight_report.json"
    write_json(graph_spec_path, spec)
    write_text(dot_path, graph_spec_to_dot(spec))
    write_json(
        preflight_path,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "grok56819_compact_source_seed",
            "paper_spec": paper_spec,
            "model": model,
            "paper": paper,
            "label": label,
            "packet": rel(packet_path),
            "input_data": rel(input_data),
            "root_text": ROOT_TEXT,
            "source_leaves": SOURCE_LEAVES,
            **preflight,
        },
    )

    shutil.copy2(graph_spec_path, staged_dir / "graph_spec.json")
    shutil.copy2(dot_path, staged_dir / "final_clean_graph.dot")
    shutil.copy2(input_data, staged_dir / "input_data.json")
    shutil.copy2(input_data, candidate_dir / "input_data.json")
    write_json(
        staged_dir / "evidence_bound_packet_pointer.json",
        {"packet": rel(packet_path), "source": label, "preflight_report": rel(preflight_path)},
    )

    candidate_row = {
        "paper_spec": paper_spec,
        "model": model,
        "paper": paper,
        "candidate_label": label,
        "candidate_graph": rel(dot_path),
        "candidate_eval_dir": rel(staged_dir),
        "candidate_fresh_eval_results": "",
        "mergeable": True,
        "candidate_CG": "",
        "candidate_REA": "",
    }
    packet_for_ans = build_candidate_packet(candidate_row, claim_args())
    claims_input = claims_dir / "claims_input.jsonl"
    with claims_input.open("w", encoding="utf-8") as handle:
        for claim in packet_for_ans.get("claims") or []:
            handle.write(json.dumps({k: v for k, v in claim.items() if k != "evidence_items"}, ensure_ascii=False) + "\n")
    write_json(claims_dir / "claim_packets.json", [packet_for_ans])

    row = {
        "priority": 1,
        "paper_spec": paper_spec,
        "lane": "compact_source_seed",
        "model": model,
        "paper": paper,
        "failure_type": "after327_judge_reason_targeted_compact_source_seed",
        "candidate_label": label,
        "passed_local_preflight": preflight.get("passed_local_preflight"),
        "covered_entities_local": (preflight.get("entity_coverage") or {}).get("covered_entities", ""),
        "total_entities_local": (preflight.get("entity_coverage") or {}).get("total_entities", ""),
        "premise_support_high_risk_count": (preflight.get("immediate_premise_support") or {}).get("high_risk_count", ""),
        "row_ans_floor": args.row_ans_floor,
        "fresh_eval_candidate": bool(preflight.get("passed_local_preflight")),
        "graph_spec": rel(graph_spec_path),
        "dot": rel(dot_path),
        "staged_run_dir": rel(staged_dir),
        "preflight_report": rel(preflight_path),
        "claims_input": rel(claims_input),
        "final_clean_graph_sha256": sha256_file(dot_path),
        "input_data": rel(staged_dir / "input_data.json"),
        "packet": rel(packet_path),
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
        "claims_input",
        "final_clean_graph_sha256",
        "input_data",
        "packet",
    ]
    write_csv(out_root / "ATTEMPT_INDEX.csv", [row], fieldnames)
    write_json(out_root / "ATTEMPT_INDEX.json", {"rows": [row]})
    write_json(
        out_root / "COMPACT_SOURCE_SEED_SUMMARY.json",
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "provider_calls": False,
            "canonical_accounting_write": False,
            "out_root": rel(out_root),
            "attempt_index": rel(out_root / "ATTEMPT_INDEX.csv"),
            "row": row,
            "preflight": preflight,
            "ans_claims": {
                "claims_input": rel(claims_input),
                "claim_rows": len(packet_for_ans.get("claims") or []),
            },
        },
    )
    print(json.dumps({"attempt_index": rel(out_root / "ATTEMPT_INDEX.csv"), "row": row}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
