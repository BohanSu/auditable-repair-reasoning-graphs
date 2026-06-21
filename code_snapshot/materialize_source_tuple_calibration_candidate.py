#!/usr/bin/env python3
"""Materialize a provider-free source-tuple calibration candidate.

This helper edits existing graph_spec nodes without changing topology. It is
intended for residual rows where the graph text is semantically right, but the
fresh CG scorer extracts terms from a too-narrow source tuple. It does not call
providers and does not write canonical accounting.
"""

from __future__ import annotations

import argparse
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


DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "source_tuple_calibration"


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
        return tuple(paper_spec.split(":", 1))  # type: ignore[return-value]
    return "", paper_spec


def parse_source(value: str) -> list[int]:
    parts = [part.strip() for part in str(value).split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"source must be x,y,z: {value}")
    return [int(part) for part in parts]


def parse_edit(value: str) -> dict[str, Any]:
    parts = str(value).split("|", 2)
    if len(parts) < 2:
        raise argparse.ArgumentTypeError("edit must be NODE_ID|x,y,z or NODE_ID|x,y,z|text")
    edit = {"node_id": parts[0].strip(), "source": parse_source(parts[1])}
    if len(parts) == 3:
        edit["text"] = parts[2].strip()
    if not edit["node_id"]:
        raise argparse.ArgumentTypeError(f"missing node id in edit: {value}")
    return edit


def apply_edits(spec: dict[str, Any], edits: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    edited = deepcopy(spec)
    nodes = {str(node.get("id")): node for node in edited.get("nodes") or [] if isinstance(node, dict)}
    audit: list[dict[str, Any]] = []
    for edit in edits:
        node_id = str(edit["node_id"])
        node = nodes.get(node_id)
        if node is None:
            raise RuntimeError(f"node not found: {node_id}")
        before = {"id": node_id, "source": list(node.get("source") or []), "text": str(node.get("text") or "")}
        node["source"] = list(edit["source"])
        if "text" in edit:
            node["text"] = str(edit["text"])
        audit.append({"before": before, "after": {"id": node_id, "source": node["source"], "text": node.get("text", "")}})
    return edited, audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-staged-dir", required=True)
    parser.add_argument("--base-graph-spec", default="")
    parser.add_argument("--input-data", default="")
    parser.add_argument("--packet", required=True)
    parser.add_argument("--paper-spec", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--edit", action="append", required=True, help="NODE_ID|x,y,z or NODE_ID|x,y,z|new text")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--row-ans-floor", default="")
    parser.add_argument("--failure-type", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_staged = resolve_path(args.base_staged_dir)
    base_graph_spec = resolve_path(args.base_graph_spec) if args.base_graph_spec else base_staged / "graph_spec.json"
    if not base_graph_spec.exists():
        for fallback_name in (
            "gpt55_semantic_root_graph.json",
            "final_clean_graph.json",
            "deterministic_vote_kept_graph.json",
        ):
            fallback = base_staged / fallback_name
            if fallback.exists():
                base_graph_spec = fallback
                break
    input_data = base_staged / "input_data.json"
    if not input_data.exists() and args.input_data:
        input_data = resolve_path(args.input_data)
    if not base_graph_spec.exists():
        raise FileNotFoundError(f"base graph spec not found: {base_graph_spec}")
    if not input_data.exists():
        raise FileNotFoundError(f"input_data.json not found: {input_data}")
    packet_path = resolve_path(args.packet)
    out_root = resolve_path(args.out_root)
    paper_spec = args.paper_spec
    model, paper = infer_model_and_paper(paper_spec)
    label = args.label
    edits = [parse_edit(item) for item in args.edit]

    base_spec = read_json(base_graph_spec)
    packet = read_json(packet_path)
    edited_spec, edit_audit = apply_edits(base_spec, edits)
    preflight = preflight_graph_spec(edited_spec, packet)

    candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(label)
    staged_dir = out_root / "staged" / safe_slug(paper_spec) / safe_slug(label)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    staged_dir.mkdir(parents=True, exist_ok=True)

    graph_spec_path = candidate_dir / "graph_spec.json"
    dot_path = candidate_dir / "final_clean_graph.dot"
    preflight_path = candidate_dir / "preflight_report.json"
    write_json(graph_spec_path, edited_spec)
    write_text(dot_path, graph_spec_to_dot(edited_spec))
    write_json(
        preflight_path,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "source_tuple_calibration",
            "paper_spec": paper_spec,
            "model": model,
            "paper": paper,
            "label": label,
            "base_staged_dir": rel(base_staged),
            "packet": rel(packet_path),
            "edits": edit_audit,
            **preflight,
        },
    )

    shutil.copy2(graph_spec_path, staged_dir / "graph_spec.json")
    shutil.copy2(dot_path, staged_dir / "final_clean_graph.dot")
    shutil.copy2(input_data, staged_dir / "input_data.json")
    write_json(
        staged_dir / "evidence_bound_packet_pointer.json",
        {
            "packet": rel(packet_path),
            "source": label,
            "base_staged_dir": rel(base_staged),
            "base_graph_spec": rel(base_graph_spec),
            "preflight_report": rel(preflight_path),
        },
    )

    row = {
        "priority": 1,
        "paper_spec": paper_spec,
        "lane": "source_tuple_calibration",
        "model": model,
        "paper": paper,
        "failure_type": args.failure_type,
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
        "final_clean_graph_sha256",
        "input_data",
        "packet",
    ]
    write_csv(out_root / "ATTEMPT_INDEX.csv", [row], fieldnames)
    write_json(out_root / "ATTEMPT_INDEX.json", {"rows": [row]})
    write_json(
        out_root / "SOURCE_TUPLE_CALIBRATION_SUMMARY.json",
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "out_root": rel(out_root),
            "attempt_index": rel(out_root / "ATTEMPT_INDEX.csv"),
            "row": row,
            "preflight": preflight,
            "edits": edit_audit,
        },
    )
    print(json.dumps({"attempt_index": rel(out_root / "ATTEMPT_INDEX.csv"), "row": row}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
