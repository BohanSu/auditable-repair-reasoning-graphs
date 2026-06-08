#!/usr/bin/env python3
"""Materialize a provider-free ANS micro-edit candidate for qwen56635.

The candidate preserves the fresh-1/1 topology from the compact bridge graph and
only narrows ANS-visible text that the atomic support audit marked as overbroad.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
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
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(FRAMEWORK_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from evaluate_ans_factscore_style_350 import eval_key  # type: ignore  # noqa: E402
from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from run_evidence_bound_graph_spec_regeneration import preflight_graph_spec  # type: ignore  # noqa: E402


def load_local_build_candidate_packet() -> Any:
    helper_path = CODE_DIR / "build_ans_claims_for_strict_merge_candidates.py"
    spec = importlib.util.spec_from_file_location("local_build_ans_claims_for_strict_merge_candidates", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load local helper: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_candidate_packet


build_candidate_packet = load_local_build_candidate_packet()

DEFAULT_PAPER_SPEC = "qwen3_5_397b_a17b:s41467-025-56635-x"
DEFAULT_LABEL = "qwen56635-ans-microedit-v1"
DEFAULT_STRICT_CANDIDATES = (
    RESIDUAL_ROOT
    / "strict_merge_candidates"
    / "after327_pareto_top_clean_fresh_20260606"
    / "STRICT_MERGE_CANDIDATES.json"
)
DEFAULT_V2_LEDGER = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_after326_failure_typed_lit_diagnostic"
    / "V2_RESIDUAL_LEDGER.csv"
)
DEFAULT_SOURCE_LABEL = "after327-pareto-terminal-plus-compact-bridge-v1-qwen3_5_397b_a17b_s41467-025-56635-x"
DEFAULT_ANS_CACHE = PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_eval_cache.jsonl"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "ans_micro_edits" / "20260606_qwen56635_ans_microedit_v1"

TEXT_PATCHES = {
    "BG_Conc2": (
        "Deduction-reasoning: Given a quasi-2D square-lattice antiferromagnet research connection to HTSC "
        "and a (pi, 0) anomaly attributed to spinon excitations, the paper motivates studying spinon-related "
        "continuum scattering in square-lattice antiferromagnets."
    ),
    "BG_Premise2": (
        "Induction: The paper links quasi-2D square-lattice Heisenberg antiferromagnets to HTSC research and "
        "reports that a (pi, 0) anomaly has been attributed to spinon excitations."
    ),
    "BG_Rule2": (
        "If a paper links quasi-2D square-lattice antiferromagnets to HTSC research and separately attributes "
        "a (pi, 0) anomaly to spinon excitations, then spinon-related continuum scattering is a relevant "
        "study target within that square-lattice antiferromagnet setting."
    ),
    "Meth_Conc3": "Abduction-reasoning: Given isotropic continua (Phenomenon) and evidence rule (Knowledge), spinons are evidenced.",
    "Strat_Rule1": (
        "If exchange interactions in iridates allow heterointerfacing different types of magnetism and such "
        "heterointerfacing may induce magnetic frustration, then a heterostructure-based strategy can frustrate "
        "Neel antiferromagnetic order in square-lattice iridates."
    ),
    "Strat_Conc1": (
        "Given heterointerfacing in iridate superlattices and possible magnetic frustration through proximity "
        "coupling, heterostructures can frustrate Neel antiferromagnetic order in square-lattice iridates."
    ),
    "NROOT": (
        "Use heterostructured square-lattice iridate superlattices to frustrate Neel antiferromagnetic order, "
        "measure quantum fluctuations with time-resolved RXD, and use polarization-resolved high-resolution "
        "RIXS to test whether conventional magnons are absent while broadband continua provide evidence for "
        "spinons in a quasi-two-dimensional Heisenberg antiferromagnet setting."
    ),
    "NROOT_COMMON": "The graph combines supported iridate heterostructure, RXD, RIXS, magnon, continuum, and spinon evidence.",
}

ROOT_ONLY_TEXT_PATCHES = {
    "NROOT": (
        "Use heterostructured square-lattice iridate superlattices to frustrate Neel antiferromagnetic order, "
        "measure quantum fluctuations with time-resolved RXD, and use polarization-resolved high-resolution "
        "RIXS to examine the absence of conventional magnons and broadband continua that provide evidence for "
        "spinons in quasi-two-dimensional square-lattice Heisenberg antiferromagnets."
    )
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
    out = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value))
    while "__" in out:
        out = out.replace("__", "_")
    return (out.strip("_") or "item")[:limit]


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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp.replace(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_candidate(path: Path, paper_spec: str, source_label: str) -> dict[str, Any]:
    payload = read_json(path, {})
    for row in payload.get("rows") or []:
        if (
            isinstance(row, dict)
            and row.get("paper_spec") == paper_spec
            and row.get("candidate_label") == source_label
        ):
            return row
    raise RuntimeError(f"candidate not found: {paper_spec} / {source_label}")


def load_ledger_row(path: Path, paper_spec: str) -> dict[str, str]:
    for row in read_csv(path):
        if row.get("paper_spec") == paper_spec:
            return row
    return {}


def input_data_from_candidate(candidate: dict[str, Any]) -> Path:
    graph_dot = resolve_path(str(candidate.get("candidate_graph") or ""))
    local = graph_dot.parent / "input_data.json"
    if local.exists():
        return local
    eval_dir = resolve_path(str(candidate.get("candidate_eval_dir") or ""))
    for parent in [eval_dir, *eval_dir.parents]:
        path = parent / "input_data.json"
        if path.exists():
            return path
    raise FileNotFoundError(f"input_data.json not found for {candidate.get('paper_spec')}")


def packet_from_ledger(ledger_row: dict[str, str]) -> tuple[Path, dict[str, Any]]:
    packet_path = resolve_path(ledger_row.get("packet_json") or "")
    if not packet_path.exists():
        raise FileNotFoundError(f"packet_json not found: {packet_path}")
    return packet_path, read_json(packet_path, {})


def read_eval_cache(path: Path) -> dict[str, list[dict[str, Any]]]:
    cache: dict[str, list[dict[str, Any]]] = {}
    if not path.exists():
        return cache
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = row.get("ans_eval_key")
            facts = row.get("atomic_facts")
            if isinstance(key, str) and isinstance(facts, list):
                cache[key] = facts
    return cache


def estimate_cached_ans(claim_packet: dict[str, Any], cache: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    rows_out: list[dict[str, Any]] = []
    atomic = 0
    supported = 0
    misses = 0
    for claim in claim_packet.get("claims") or []:
        row = {k: v for k, v in claim.items() if k != "evidence_items"}
        row["ans_evidence"] = row.get("evidence_text") or ""
        key = eval_key(row)
        facts = cache.get(key)
        hit = facts is not None
        if not hit:
            misses += 1
            facts = []
        a = len(facts)
        s = sum(1 for fact in facts if fact.get("supported"))
        atomic += a
        supported += s
        rows_out.append(
            {
                "node_id": row.get("node_id"),
                "unit_type": row.get("unit_type"),
                "cache_hit": hit,
                "atomic_fact_count": a,
                "supported_fact_count": s,
                "node_text": row.get("claim_text_for_minicheck") or row.get("raw_node_text"),
            }
        )
    return {
        "cache_hits": len(rows_out) - misses,
        "cache_misses": misses,
        "cached_atomic_fact_count": atomic,
        "cached_supported_fact_count": supported,
        "cached_ans": supported / atomic if atomic else None,
        "rows": rows_out,
    }


def materialize_spec(source_spec: dict[str, Any], *, label: str, patch_set: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    patches = ROOT_ONLY_TEXT_PATCHES if patch_set == "root_only" else TEXT_PATCHES
    spec = json.loads(json.dumps(source_spec, ensure_ascii=False))
    actions: list[dict[str, Any]] = []
    for node in spec.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if node_id not in patches:
            continue
        old_text = str(node.get("text") or "")
        new_text = patches[node_id]
        node["text"] = new_text
        actions.append({"action": "ans_microedit_text", "node_id": node_id, "old_text": old_text, "new_text": new_text})
    if len(actions) != len(patches):
        seen = {action["node_id"] for action in actions}
        missing = sorted(set(patches) - seen)
        raise RuntimeError(f"micro-edit nodes not found: {missing}")
    curation = dict(spec.get("curation") or {})
    curation["ans_microedit"] = {
        "label": label,
        "patch_set": patch_set,
        "patched_nodes": sorted(patches),
        "rule": "provider_free_near_extractiveness_for_ans_regression",
    }
    spec["curation"] = curation
    return spec, actions


def build(args: argparse.Namespace) -> dict[str, Any]:
    paper_spec = args.paper_spec
    source_candidates = resolve_path(args.strict_candidates)
    candidate = load_candidate(source_candidates, paper_spec, args.source_label)
    ledger_row = load_ledger_row(resolve_path(args.v2_ledger), paper_spec)
    packet_path, packet = packet_from_ledger(ledger_row)
    source_dot = resolve_path(str(candidate.get("candidate_graph") or ""))
    source_graph = source_dot.parent / "graph_spec.json"
    if not source_graph.exists():
        source_graph = source_dot.with_suffix(".json")
    source_spec = read_json(source_graph, {})
    if not source_spec:
        raise RuntimeError(f"empty source graph: {source_graph}")
    micro_spec, actions = materialize_spec(source_spec, label=args.label, patch_set=args.patch_set)
    preflight = preflight_graph_spec(micro_spec, packet)

    out_root = resolve_path(args.out_root)
    candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(args.label)
    staged_dir = out_root / "staged" / safe_slug(paper_spec) / safe_slug(args.label)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    staged_dir.mkdir(parents=True, exist_ok=True)
    graph_json = candidate_dir / "graph_spec.json"
    graph_dot = candidate_dir / "final_clean_graph.dot"
    write_json(graph_json, micro_spec)
    write_text(graph_dot, graph_spec_to_dot(micro_spec))
    write_json(candidate_dir / "preflight_report.json", preflight)

    input_path = input_data_from_candidate(candidate)
    shutil.copy2(graph_json, staged_dir / "graph_spec.json")
    shutil.copy2(graph_dot, staged_dir / "final_clean_graph.dot")
    shutil.copy2(input_path, staged_dir / "input_data.json")
    write_json(
        staged_dir / "evidence_bound_packet_pointer.json",
        {"packet": rel(packet_path), "source": args.label, "source_kind": "ans_microedit_text"},
    )

    candidate_for_claims = {
        **candidate,
        "candidate_label": args.label,
        "candidate_graph": rel(graph_dot),
        "candidate_eval_dir": rel(staged_dir),
    }
    claim_args = argparse.Namespace(
        window=args.window,
        root_window=args.root_window,
        traversal_depth=args.traversal_depth,
        max_evidence_sentences=args.max_evidence_sentences,
        max_evidence_chars=args.max_evidence_chars,
        include_viewpoints=args.include_viewpoints,
        graph_ordered_evidence=args.graph_ordered_evidence,
    )
    claim_packet = build_candidate_packet(candidate_for_claims, claim_args)
    claims_rows = [{k: v for k, v in row.items() if k != "evidence_items"} for row in claim_packet["claims"]]
    claims_dir = out_root / "ans_claims" / safe_slug(args.label)
    claims_dir.mkdir(parents=True, exist_ok=True)
    claims_path = claims_dir / "claims_input.jsonl"
    with claims_path.open("w", encoding="utf-8") as handle:
        for row in claims_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_json(claims_dir / "claim_packets.json", [claim_packet])

    cached_estimate = estimate_cached_ans(claim_packet, read_eval_cache(resolve_path(args.ans_cache)))
    write_csv(
        candidate_dir / "CACHED_ANS_NODE_ESTIMATE.csv",
        cached_estimate["rows"],
        ["node_id", "unit_type", "cache_hit", "atomic_fact_count", "supported_fact_count", "node_text"],
    )

    row_ans_floor = float(candidate.get("current_best_main_factual_ans") or 0.0)
    cached_ans = cached_estimate.get("cached_ans")
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "qwen56635_ans_microedit_candidate",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "paper_spec": paper_spec,
        "candidate_label": args.label,
        "source_label": args.source_label,
        "patch_set": args.patch_set,
        "source_strict_candidates": rel(source_candidates),
        "source_graph": rel(source_graph),
        "source_packet": rel(packet_path),
        "candidate_dir": rel(candidate_dir),
        "staged_dir": rel(staged_dir),
        "claims_input": rel(claims_path),
        "graph_spec": rel(graph_json),
        "final_clean_graph": rel(graph_dot),
        "final_clean_graph_sha256": sha256_file(graph_dot),
        "preflight_report": rel(candidate_dir / "preflight_report.json"),
        "passed_local_preflight": bool(preflight.get("passed_local_preflight")),
        "row_ans_floor": row_ans_floor,
        "cached_ans_estimate": cached_estimate,
        "cached_ans_passes_row_floor": bool(cached_ans is not None and cached_ans + 1e-12 >= row_ans_floor),
        "fresh_eval_required": True,
        "actions": actions,
    }
    write_json(out_root / "ANS_MICROEDIT_SUMMARY.json", summary)
    write_json(out_root / "ATTEMPT_INDEX.json", {"rows": [summary]})
    write_csv(
        out_root / "ATTEMPT_INDEX.csv",
        [
            {
                "paper_spec": paper_spec,
                "candidate_label": args.label,
                "passed_local_preflight": summary["passed_local_preflight"],
                "cached_ans": cached_estimate.get("cached_ans"),
                "cached_ans_passes_row_floor": summary["cached_ans_passes_row_floor"],
                "graph_spec": summary["graph_spec"],
                "dot": summary["final_clean_graph"],
                "staged_run_dir": summary["staged_dir"],
                "preflight_report": summary["preflight_report"],
            }
        ],
        [
            "paper_spec",
            "candidate_label",
            "passed_local_preflight",
            "cached_ans",
            "cached_ans_passes_row_floor",
            "graph_spec",
            "dot",
            "staged_run_dir",
            "preflight_report",
        ],
    )
    print(
        json.dumps(
            {
                "paper_spec": summary["paper_spec"],
                "candidate_label": summary["candidate_label"],
                "passed_local_preflight": summary["passed_local_preflight"],
                "row_ans_floor": summary["row_ans_floor"],
                "cached_ans": cached_estimate.get("cached_ans"),
                "cached_ans_passes_row_floor": summary["cached_ans_passes_row_floor"],
                "out_root": rel(out_root),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-spec", default=DEFAULT_PAPER_SPEC)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--source-label", default=DEFAULT_SOURCE_LABEL)
    parser.add_argument("--patch-set", choices=["full", "root_only"], default="full")
    parser.add_argument("--strict-candidates", default=str(DEFAULT_STRICT_CANDIDATES))
    parser.add_argument("--v2-ledger", default=str(DEFAULT_V2_LEDGER))
    parser.add_argument("--ans-cache", default=str(DEFAULT_ANS_CACHE))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--window", type=int, default=1)
    parser.add_argument("--root-window", type=int, default=None)
    parser.add_argument("--traversal-depth", type=int, default=2)
    parser.add_argument("--max-evidence-sentences", type=int, default=10)
    parser.add_argument("--max-evidence-chars", type=int, default=1800)
    parser.add_argument("--include-viewpoints", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--graph-ordered-evidence", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
