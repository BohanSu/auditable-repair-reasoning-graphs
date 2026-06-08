#!/usr/bin/env python3
"""Materialize a compact source-seed candidate for grok56882.

The introduction sentence list omits figure-caption evidence for the
nonreciprocal next-nearest-neighbor circuit implementation. The full content
field contains that caption, so this materializer appends those exact caption
sentences to the staged input_data and points source leaves at them.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from materialize_gemini56283_compact_source_seed_candidate import (
    RESIDUAL_ROOT,
    build_candidate_packet,
    claim_args,
    graph_spec_to_dot,
    infer_model_and_paper,
    preflight_graph_spec,
    read_json,
    rel,
    resolve_path,
    safe_slug,
    sha256_file,
    write_csv,
    write_json,
    write_text,
)


DEFAULT_PAPER_SPEC = "grok_4_1_thinking:s41467-025-56882-y"
DEFAULT_LABEL = "grok56882-compact-source-seed-v1"
DEFAULT_PACKET = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "packets"
    / "027_grok_4_1_thinking__s41467-025-56882-y__20260318_053024"
    / "packet.json"
)
DEFAULT_INPUT_DATA = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "root_minimal_edits"
    / "20260606_grok56882_root_truncation_v1"
    / "staged"
    / "grok_4_1_thinking_s41467-025-56882-y"
    / "after327-56882-root-truncation-repair-v1"
    / "input_data.json"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "compact_source_seed"
    / "20260606_grok56882_compact_source_seed_v1"
)
DEFAULT_ROW_ANS_FLOOR = "0.7407407407407407"


ROOT_TEXT = (
    "This work engineers and experimentally realizes a non-Hermitian "
    "graphene-like circuit lattice whose designed circuit implements "
    "nonreciprocal next-nearest-neighbor couplings through nonreciprocal "
    "capacitance. The lattice creates a time-reversal-related "
    "complex-conjugate pair of intrinsically non-Hermitian Dirac cones "
    "governed by valley-dependent imaginary potentials. The resulting "
    "lifetime contrast yields long-lived single-valley Dirac quasiparticles "
    "and unidirectional, lifetime-asymmetric valley kink states, revealing "
    "non-Hermitian Dirac physics beyond Hermitian counterparts."
)

SUPPLEMENTAL_SENTENCES = [
    {
        "idx": 25,
        "sentence": "In this work, a complex conjugate pair of Dirac cones at the K and K' valleys are related to one another by time-reversal symmetry, with colors denoting the imaginary parts of the eigenvalues.",
        "viewpoints": [
            "A complex conjugate pair of Dirac cones at K and K' valleys are related by time-reversal symmetry.",
            "The imaginary parts of the eigenvalues distinguish the pair.",
        ],
    },
    {
        "idx": 26,
        "sentence": "In the long time limit, only states at the K valley survive due to the sharp contrast in lifetime between the two valleys, leading to effective single Dirac cone behavior over a large energy range.",
        "viewpoints": [
            "Sharp lifetime contrast between valleys leaves only K-valley states in the long-time limit.",
            "The system shows effective single Dirac cone behavior over a large energy range.",
        ],
    },
    {
        "idx": 27,
        "sentence": "The schematic diagram of the designed circuit realizes the hexagonal unit cell, with next-neighbor couplings achieved through capacitors C.",
        "viewpoints": [
            "The designed circuit realizes a hexagonal unit cell.",
            "Next-neighbor couplings are achieved through capacitors C.",
        ],
    },
    {
        "idx": 28,
        "sentence": "The nonreciprocal next-nearest-neighbor couplings are achieved via nonreciprocal capacitance C1 plus or minus C2.",
        "viewpoints": [
            "Nonreciprocal next-nearest-neighbor couplings are achieved via nonreciprocal capacitance.",
        ],
    },
    {
        "idx": 29,
        "sentence": "A calculated bulk dispersion of the circuit lattice uses colors to denote the imaginary part of the eigenfrequencies.",
        "viewpoints": [
            "The circuit lattice has a calculated bulk dispersion.",
            "The imaginary part of the eigenfrequencies is encoded in the colors.",
        ],
    },
]

SOURCE_LEAVES = [
    {
        "id": "E12",
        "source": [12, 0, 0],
        "text": "The work designs and experimentally realizes a non-Hermitian extension of the graphene lattice with a complex conjugate pair of Dirac cones.",
        "edge_type": "induction-common",
    },
    {
        "id": "E13",
        "source": [13, 0, 0],
        "text": "The Dirac cones in the study are intrinsically non-Hermitian.",
        "edge_type": "induction-case",
    },
    {
        "id": "E14",
        "source": [14, 0, 0],
        "text": "The Dirac quasiparticles are governed by non-Hermitian Dirac Hamiltonians with a valley-dependent background imaginary term.",
        "edge_type": "induction-case",
    },
    {
        "id": "E17",
        "source": [17, 0, 0],
        "text": "The authors fabricate a circuit lattice to implement the theoretical model.",
        "edge_type": "induction-case",
    },
    {
        "id": "E18",
        "source": [18, 0, 0],
        "text": "A sharp imaginary eigenvalue contrast wipes out the shorter-lifetime Dirac cone in the long-time limit, making the system effectively exhibit single Dirac cone physics.",
        "edge_type": "induction-case",
    },
    {
        "id": "E22",
        "source": [22, 0, 0],
        "text": "Valley kink states inherit non-Hermitian bulk properties and have valley-dependent lifetimes.",
        "edge_type": "induction-case",
    },
    {
        "id": "E23",
        "source": [23, 0, 0],
        "text": "Valley kink-state propagation is effectively unidirectional and valley flipping is suppressed by imaginary-eigenvalue differences between valleys.",
        "edge_type": "induction-case",
    },
    {
        "id": "E24",
        "source": [24, 0, 0],
        "text": "Dirac cones can be induced in non-Hermitian lattices and have non-Hermitian physics beyond Hermitian counterparts.",
        "edge_type": "induction-case",
    },
    {
        "id": "E25",
        "source": [25, 0, 0],
        "text": "A complex conjugate pair of Dirac cones at the K and K' valleys are related by time-reversal symmetry and carry imaginary eigenvalue parts.",
        "edge_type": "induction-case",
    },
    {
        "id": "E26",
        "source": [26, 0, 0],
        "text": "In the long-time limit, lifetime contrast leaves only K-valley states, giving effective single Dirac cone behavior over a large energy range.",
        "edge_type": "induction-case",
    },
    {
        "id": "E27",
        "source": [27, 0, 0],
        "text": "The designed circuit realizes the hexagonal unit cell and implements nearest-neighbor couplings through capacitors.",
        "edge_type": "induction-case",
    },
    {
        "id": "E28",
        "source": [28, 0, 0],
        "text": "The designed circuit achieves nonreciprocal next-nearest-neighbor couplings through nonreciprocal capacitance.",
        "edge_type": "induction-case",
    },
    {
        "id": "E29",
        "source": [29, 0, 0],
        "text": "The circuit lattice bulk dispersion uses imaginary eigenfrequency parts, matching the non-Hermitian circuit implementation.",
        "edge_type": "induction-case",
    },
]


def augment_input_data(input_data: dict[str, Any]) -> dict[str, Any]:
    augmented = deepcopy(input_data)
    intro = augmented.setdefault("introduction", {})
    sentences = intro.setdefault("sentences", [])
    existing = {int(item.get("idx", pos + 1)) for pos, item in enumerate(sentences) if isinstance(item, dict)}
    for item in SUPPLEMENTAL_SENTENCES:
        if item["idx"] in existing:
            continue
        sentences.append({"idx": item["idx"], "sentence": item["sentence"], "references": {}, "viewpoints": item["viewpoints"]})
    return augmented


def build_graph_spec(paper_spec: str) -> dict[str, Any]:
    nodes = [{"id": "NROOT", "source": [0, 0, 0], "text": ROOT_TEXT}]
    edges = []
    for leaf in SOURCE_LEAVES:
        nodes.append({"id": leaf["id"], "source": leaf["source"], "text": leaf["text"]})
        edges.append({"source": leaf["id"], "target": "NROOT", "type": leaf["edge_type"]})
    return {"paper_id": paper_spec, "root": "NROOT", "nodes": nodes, "edges": edges}


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
    input_data_path = resolve_path(args.input_data)
    out_root = resolve_path(args.out_root)
    candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(label)
    staged_dir = out_root / "staged" / safe_slug(paper_spec) / safe_slug(label)
    claims_dir = out_root / "ans_claims" / safe_slug(label)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    staged_dir.mkdir(parents=True, exist_ok=True)
    claims_dir.mkdir(parents=True, exist_ok=True)

    packet = read_json(packet_path)
    original_input_data = read_json(input_data_path)
    staged_input_data = augment_input_data(original_input_data)
    spec = build_graph_spec(paper_spec)
    preflight = preflight_graph_spec(spec, packet)

    graph_spec_path = candidate_dir / "graph_spec.json"
    dot_path = candidate_dir / "final_clean_graph.dot"
    preflight_path = candidate_dir / "preflight_report.json"
    write_json(graph_spec_path, spec)
    write_text(dot_path, graph_spec_to_dot(spec))
    write_json(candidate_dir / "input_data.json", staged_input_data)
    write_json(
        preflight_path,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "grok56882_compact_source_seed_v1",
            "paper_spec": paper_spec,
            "model": model,
            "paper": paper,
            "label": label,
            "packet": rel(packet_path),
            "input_data": rel(input_data_path),
            "augmented_source_sentence_count": len(SUPPLEMENTAL_SENTENCES),
            "root_text": ROOT_TEXT,
            "source_leaves": SOURCE_LEAVES,
            **preflight,
        },
    )

    write_json(staged_dir / "graph_spec.json", spec)
    write_text(staged_dir / "final_clean_graph.dot", graph_spec_to_dot(spec))
    write_json(staged_dir / "input_data.json", staged_input_data)
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
        "failure_type": "reasoning_chain_compact_source_seed_augmented_caption_sources",
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
            "augmented_source_sentences": SUPPLEMENTAL_SENTENCES,
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
