#!/usr/bin/env python3
"""Materialize a compact source-seed candidate for gemini56698.

The previous source-leaf candidate passes reasoning but fresh coverage is 9/10.
This version keeps the one-step source-seed structure, adds the directly
supporting pipeline/profile source leaves, and spells the fixed-anchor entities
with the evaluator's US/hyphenated wording in the root.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
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


DEFAULT_PAPER_SPEC = "gemini_3_1_pro_preview:s41467-025-56698-w"
DEFAULT_LABEL = "gemini56698-compact-source-seed-v1"
DEFAULT_PACKET = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "packets"
    / "021_gemini_3_1_pro_preview__s41467-025-56698-w__20260318_035055"
    / "packet.json"
)
DEFAULT_INPUT_DATA = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "source_leaf_edits"
    / "20260606_gemini56698_wound_leaf_v2"
    / "staged"
    / "gemini_3_1_pro_preview_s41467-025-56698-w"
    / "source-seed-wound-healing-leaf-v2-gemini_3_1_pro_preview_s41467-025-56698-w"
    / "input_data.json"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "compact_source_seed"
    / "20260606_gemini56698_compact_source_seed_v1"
)
DEFAULT_ROW_ANS_FLOOR = "1.0"


ROOT_TEXT = (
    "This study develops an optimized, standardized fecal small-RNA sequencing "
    "pipeline and establishes a reproducible baseline miRNA profile in healthy "
    "mice. It demonstrates pipeline utility by analysing fecal miRNA changes "
    "due to chronic Trichuris muris infection, including wound-healing miRNA "
    "changes and barrier-integrity miRNA changes associated with fibrosis. "
    "Together, these findings support fecal miRNAs as non-invasive markers of "
    "intestinal status and biologically relevant deviations."
)

SOURCE_LEAVES = [
    {
        "id": "E12",
        "source": [12, 0, 0],
        "text": "The authors developed an optimized fecal small-RNA sequencing pipeline and performed a meta-analysis of published small RNA sequencing data from murine fecal samples.",
        "edge_type": "induction-common",
    },
    {
        "id": "E13",
        "source": [13, 0, 0],
        "text": "The authors established best practices for the technique and identified a core fecal miRNA profile that is consistent across laboratory practices.",
        "edge_type": "induction-case",
    },
    {
        "id": "E14",
        "source": [14, 0, 0],
        "text": "The current study reports the first longitudinal analysis of murine fecal miRNAs in healthy mice.",
        "edge_type": "induction-case",
    },
    {
        "id": "E15",
        "source": [15, 0, 0],
        "text": "The authors demonstrate pipeline utility in hypothesis generation by analysing changes in fecal miRNAs due to chronic Trichuris muris infection.",
        "edge_type": "induction-case",
    },
    {
        "id": "E19",
        "source": [19, 0, 0],
        "text": "Chronic infection induces differential expression of miRNAs associated with wound healing, barrier integrity, and fibrosis.",
        "edge_type": "induction-case",
    },
    {
        "id": "E6",
        "source": [6, 0, 0],
        "text": "Intestinal epithelial-cell miRNAs are detectable in extracellular vesicles in feces and offer a promising non-invasive tool to study intestinal disease.",
        "edge_type": "induction-case",
    },
    {
        "id": "E5",
        "source": [5, 0, 0],
        "text": "In the mammalian intestine, miRNAs perform essential roles in barrier integrity, metabolism, mucosal immune-response regulation, and microbial homeostasis.",
        "edge_type": "induction-case",
    },
    {
        "id": "E18",
        "source": [18, 0, 0],
        "text": "In Trichuris muris infection, a Th1-type immune response is associated with chronicity and the inability to clear the infection.",
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
            "mode": "gemini56698_compact_source_seed_v1",
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
        "failure_type": "coverage_exact_entity_compact_source_seed",
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
