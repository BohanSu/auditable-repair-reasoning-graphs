#!/usr/bin/env python3
"""Materialize a compact source-seed candidate for qwen56795.

The existing source-leaf candidate has full entity coverage but one failed
reasoning step: an abduction target asserts demonstrated cracking actuation from
a motivation premise. This provider-free candidate rebuilds the graph as one
source-grounded induction over the directly relevant result sentences.
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


DEFAULT_PAPER_SPEC = "qwen3_5_397b_a17b:s41467-025-56795-w"
DEFAULT_LABEL = "qwen56795-compact-source-seed-v1"
DEFAULT_PACKET = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "packets"
    / "030_qwen3_5_397b_a17b__s41467-025-56795-w__20260319_035254"
    / "packet.json"
)
DEFAULT_INPUT_DATA = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "source_leaf_bridge_after327_20260606"
    / "staged"
    / "qwen3_5_397b_a17b_s41467-025-56795-w"
    / "after327-56795-grok-support-risk-pruned-v1"
    / "input_data.json"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "compact_source_seed"
    / "20260606_qwen56795_compact_source_seed_v1"
)
DEFAULT_ROW_ANS_FLOOR = "0.984375"


ROOT_TEXT = (
    "The paper reports a new single-crystal ruthenium-tetraammine [RuSO2] complex, "
    "trans-[Ru(SO2)(NH3)4(4-bromopyridine)](tosylate)2, that exhibits thermally "
    "reversible macroscopic cracking optical actuation and 100% SO2 linkage "
    "photoisomerisation to temperature-controlled metastable eta1-OSO and eta2-(OS)O "
    "photoisomers, enabling a visible-light- and temperature-controlled ternary "
    "molecular switch."
)

SOURCE_LEAVES = [
    {
        "id": "E8",
        "source": [8, 0, 0],
        "text": "The authors sought a new single-crystal optical actuator by developing ruthenium-tetraammine complexes that undergo SO2-linkage photoisomerisation.",
        "edge_type": "induction-case",
    },
    {
        "id": "E17",
        "source": [17, 0, 0],
        "text": "The paper reports a new [RuSO2] complex, trans-[Ru(SO2)(NH3)4(4-bromopyridine)]tosylate2, that exhibits thermally reversible cracking single-crystal optical actuation.",
        "edge_type": "induction-common",
    },
    {
        "id": "E18",
        "source": [18, 0, 0],
        "text": "The eta1-SO2 dark-state crystal structure undergoes 100% SO2-linkage photoisomerisation to create a pure eta1-OSO photoisomer at 90 K or a pure eta2-(OS)O photoisomer at 100 K.",
        "edge_type": "induction-case",
    },
    {
        "id": "E19",
        "source": [19, 0, 0],
        "text": "Each photoisomer is metastable at its stated temperature and the crystal strain diminishes as the complex thermally recovers its dark-state crystal structure.",
        "edge_type": "induction-case",
    },
    {
        "id": "E20",
        "source": [20, 0, 0],
        "text": "Ternary switching arises from photostructural changes caused by 100% SO2 linkage photoisomerisation under 505 nm light at low temperature.",
        "edge_type": "induction-case",
    },
    {
        "id": "E23",
        "source": [23, 0, 0],
        "text": "The three distinct 100%-converted SO2-linkage isomeric species reveal the possibility of high-purity ternary photonic signatures in single-crystal optical actuators.",
        "edge_type": "induction-case",
    },
    {
        "id": "E25",
        "source": [25, 0, 0],
        "text": "The findings show that ternary molecular switching can be controlled by temperature and visible light.",
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
            "mode": "qwen56795_compact_source_seed",
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
        "failure_type": "preflight:no_anchor_regenerate",
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
