#!/usr/bin/env python3
"""Materialize a compact source-seed candidate for grok56611.

The previous galloping-bubble graph had full entity coverage, but retained
background bridge nodes about historical bubble paradoxes and application
chains. This candidate keeps only direct source sentences that support the
fixed-anchor galloping-bubble mechanism.
"""

from __future__ import annotations

import argparse
import json
import shutil
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


DEFAULT_PAPER_SPEC = "grok_4_1_thinking:s41467-025-56611-5"
DEFAULT_LABEL = "grok56611-compact-source-seed-v1"
DEFAULT_PACKET = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "packets"
    / "037_grok_4_1_thinking__s41467-025-56611-5__20260318_015214"
    / "packet.json"
)
DEFAULT_INPUT_DATA = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "source_leaf_bridge"
    / "20260606_grok56611_gallop_v1"
    / "staged"
    / "grok_4_1_thinking_s41467-025-56611-5"
    / "20260606_gallop_source_leaf_v1"
    / "input_data.json"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "compact_source_seed"
    / "20260606_grok56611_compact_source_seed_v1"
)
DEFAULT_ROW_ANS_FLOOR = "0.8795180722891566"


ROOT_TEXT = (
    "This work demonstrates a bubble confined beneath the upper wall of a "
    "vertically vibrated fluid chamber that can spontaneously gallop and "
    "self-propel through resonant coupling of vibration and shape oscillation "
    "modes. Adjusting vibration amplitude, vibration frequency, and bubble "
    "size tunes rectilinear, orbital, and run-and-tumble trajectories. The "
    "galloping bubble leverages inertial fluid forces in inviscid environments, "
    "providing a tunable inertia-based mechanism for bubble transport and "
    "bubble manipulation."
)

SOURCE_LEAVES = [
    {
        "id": "E9",
        "source": [9, 0, 0],
        "text": "A bubble inside a vertically vibrated fluid chamber may gallop along the upper wall, self-propelled through resonant interaction between its vibration modes.",
        "edge_type": "induction-common",
    },
    {
        "id": "E10",
        "source": [10, 0, 0],
        "text": "Adjusting vibrational forcing tunes galloping bubbles between rectilinear, orbital, and run-and-tumble domain exploration modes.",
        "edge_type": "induction-case",
    },
    {
        "id": "E11",
        "source": [11, 0, 0],
        "text": "Galloping bubbles leverage inertial fluid forces to advance and propel in inviscid flows where viscous traction is not possible.",
        "edge_type": "induction-case",
    },
    {
        "id": "E12",
        "source": [12, 0, 0],
        "text": "A time sequence illustrates a self-propelling bubble under the upper boundary of a vertically vibrating fluid chamber with shape oscillations reminiscent of galloping motion.",
        "edge_type": "induction-case",
    },
    {
        "id": "E13",
        "source": [13, 0, 0],
        "text": "The setup uses oscillation period T = 2*pi/omega.",
        "edge_type": "induction-case",
    },
    {
        "id": "E15",
        "source": [15, 0, 0],
        "text": "Different bubble sizes and vibrational forcings produce diverse domain exploration modes.",
        "edge_type": "induction-case",
    },
    {
        "id": "E16",
        "source": [16, 0, 0],
        "text": "A bubble may gallop in steady rectilinear motion in an infinite bath.",
        "edge_type": "induction-case",
    },
    {
        "id": "E18",
        "source": [18, 0, 0],
        "text": "Depending on bubble volume, increasing forcing amplitude can curve the bubble trajectory into orbital states or jagged run-and-tumble motion.",
        "edge_type": "induction-case",
    },
    {
        "id": "E19",
        "source": [19, 0, 0],
        "text": "A phase map at f = 40 Hz illustrates dependence of bubble dynamics on driving acceleration and bubble volume.",
        "edge_type": "induction-case",
    },
    {
        "id": "E24",
        "source": [24, 0, 0],
        "text": "Manipulating bubble motion is valuable in boiling-based heat transfer where unremoved bubbles near the heat source reduce heat transfer efficiency.",
        "edge_type": "induction-case",
    },
    {
        "id": "E26",
        "source": [26, 0, 0],
        "text": "Proof-of-concept experiments showcase galloping dynamics and inform methods for producing bubbles with tunable size, sorting and removal of bubbles, navigating fluid networks, and cleaning surfaces.",
        "edge_type": "induction-case",
    },
]


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
            "mode": "grok56611_compact_source_seed_v1",
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
        "failure_type": "reasoning_chain_compact_source_seed",
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
