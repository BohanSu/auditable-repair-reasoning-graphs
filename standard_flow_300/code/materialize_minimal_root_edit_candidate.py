#!/usr/bin/env python3
"""Materialize a staged candidate by replacing only NROOT text.

This is a narrow provider-free helper for ANS-safe residual closeout cases:
start from an already staged graph_spec, apply a minimal root wording edit,
rerun local preflight, and emit a fresh-eval-ready ATTEMPT_INDEX.csv.
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


DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "root_minimal_edits"


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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def infer_model_and_paper(paper_spec: str) -> tuple[str, str]:
    if ":" in paper_spec:
        model, paper = paper_spec.split(":", 1)
        return model, paper
    return "", paper_spec


def replace_root_text(spec: dict[str, Any], root_text: str) -> tuple[dict[str, Any], str]:
    edited = deepcopy(spec)
    root_id = str(edited.get("root") or "NROOT")
    old_text = ""
    for node in edited.get("nodes") or []:
        if isinstance(node, dict) and str(node.get("id") or "") == root_id:
            old_text = str(node.get("text") or "")
            node["text"] = root_text
            return edited, old_text
    raise RuntimeError(f"root node not found: {root_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-staged-dir", required=True)
    parser.add_argument("--packet", default="")
    parser.add_argument("--paper-spec", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--root-text", required=True)
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--row-ans-floor", default="1.0")
    parser.add_argument("--failure-type", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_staged = resolve_path(args.base_staged_dir)
    base_spec_path = base_staged / "graph_spec.json"
    if not base_spec_path.exists():
        raise RuntimeError(f"missing base graph_spec: {base_spec_path}")

    pointer = read_json(base_staged / "evidence_bound_packet_pointer.json") if (base_staged / "evidence_bound_packet_pointer.json").exists() else {}
    packet_path = resolve_path(args.packet or pointer.get("packet", ""))
    packet = read_json(packet_path)
    paper_spec = args.paper_spec
    model, paper = infer_model_and_paper(paper_spec)
    label = args.label
    out_root = resolve_path(args.out_root)
    candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(label)
    staged_dir = out_root / "staged" / safe_slug(paper_spec) / safe_slug(label)

    base_spec = read_json(base_spec_path)
    edited_spec, old_root_text = replace_root_text(base_spec, args.root_text)
    preflight = preflight_graph_spec(edited_spec, packet)

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
            "mode": "minimal_root_text_edit",
            "paper_spec": paper_spec,
            "model": model,
            "paper": paper,
            "label": label,
            "base_staged_dir": rel(base_staged),
            "packet": rel(packet_path),
            "root_edit": {
                "node_id": edited_spec.get("root") or "NROOT",
                "old_text": old_root_text,
                "new_text": args.root_text,
            },
            **preflight,
        },
    )

    shutil.copy2(graph_spec_path, staged_dir / "graph_spec.json")
    shutil.copy2(dot_path, staged_dir / "final_clean_graph.dot")
    shutil.copy2(base_staged / "input_data.json", staged_dir / "input_data.json")
    write_json(
        staged_dir / "evidence_bound_packet_pointer.json",
        {
            "packet": rel(packet_path),
            "source": label,
            "base_staged_dir": rel(base_staged),
            "preflight_report": rel(preflight_path),
        },
    )

    row = {
        "priority": 1,
        "paper_spec": paper_spec,
        "lane": "minimal_root_text_edit",
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
        out_root / "ROOT_MINIMAL_EDIT_SUMMARY.json",
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "out_root": rel(out_root),
            "attempt_index": rel(out_root / "ATTEMPT_INDEX.csv"),
            "row": row,
            "preflight": preflight,
        },
    )
    print(json.dumps({"attempt_index": rel(out_root / "ATTEMPT_INDEX.csv"), "row": row}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
