#!/usr/bin/env python3
"""Build a provider-free graph_spec attempt by pruning a known failed unit."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def safe_slug(value: str, limit: int = 140) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    out = re.sub(r"_+", "_", out).strip("_")
    return (out or "item")[:limit]


def attempt_dir_for(row: dict[str, str], model: str) -> Path:
    priority = f"{int(row.get('priority') or 999999):03d}"
    return Path(f"{priority}_{safe_slug(row.get('paper_spec') or 'paper')}") / safe_slug(model)


def prune_target(spec: dict[str, Any], target_node: str) -> dict[str, Any]:
    out = dict(spec)
    nodes = spec.get("nodes") or []
    edges = spec.get("edges") or []
    out["nodes"] = [
        node for node in nodes if not (isinstance(node, dict) and str(node.get("id") or "") == target_node)
    ]
    out["edges"] = [
        edge
        for edge in edges
        if not (
            isinstance(edge, dict)
            and (str(edge.get("source") or "") == target_node or str(edge.get("target") or "") == target_node)
        )
    ]
    return out


def build(args: argparse.Namespace) -> dict[str, Any]:
    attempt_rows = read_csv(resolve(args.attempt_index))
    source_row = next((row for row in attempt_rows if row.get("paper_spec") == args.paper_spec), None)
    if source_row is None:
        raise RuntimeError(f"paper_spec not found in attempt index: {args.paper_spec}")
    source_spec_path = resolve(source_row["graph_spec"])
    source_spec = read_json(source_spec_path)
    pruned = prune_target(source_spec, args.target_node)

    out_root = resolve(args.out_attempts_root)
    out_dir = out_root / attempt_dir_for(source_row, args.model)
    response_path = out_dir / "response.json"
    payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "paper_spec": args.paper_spec,
        "model": args.model,
        "candidate_label": args.candidate_label,
        "source_attempt_index": rel(resolve(args.attempt_index)),
        "source_graph_spec": rel(source_spec_path),
        "repair_action": {
            "type": "prune_failed_reasoning_target",
            "target_node": args.target_node,
            "rationale": args.rationale,
        },
        "response_json": pruned,
        "content": json.dumps(pruned, ensure_ascii=False),
    }
    write_json(response_path, payload)
    manifest = {
        "created_at": payload["created_at"],
        "provider_calls": False,
        "canonical_accounting_write": False,
        "paper_spec": args.paper_spec,
        "target_node": args.target_node,
        "source_graph_spec": rel(source_spec_path),
        "response_json": rel(response_path),
        "out_attempts_root": rel(out_root),
    }
    write_json(out_root / "TARGETED_PRUNE_MANIFEST.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-index", required=True)
    parser.add_argument("--out-attempts-root", required=True)
    parser.add_argument("--paper-spec", required=True)
    parser.add_argument("--target-node", required=True)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--candidate-label", default="targeted_unit_prune_v1")
    parser.add_argument("--rationale", default="remove a fresh-judge rejected non-essential bridge while preserving coverage")
    return parser.parse_args()


def main() -> int:
    build(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
