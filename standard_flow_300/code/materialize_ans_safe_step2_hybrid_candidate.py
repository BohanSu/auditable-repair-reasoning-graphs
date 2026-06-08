#!/usr/bin/env python3
"""Materialize a provider-free step2 source-leaf hybrid candidate.

This pilot targets residual rows where notation replay already reaches CG/REA
but ANS misses the row floor by a tiny margin. It restores selected source
nodes from the high-ANS step2 graph into the terminal graph, attaches them to
NROOT as additional induction cases, and estimates ANS using the existing ANS
eval cache before any provider-backed fresh evaluation.
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

from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from run_evidence_bound_graph_spec_regeneration import preflight_graph_spec  # type: ignore  # noqa: E402
from evaluate_ans_factscore_style_350 import (  # type: ignore  # noqa: E402
    clean_text,
    eval_key,
    evidence_corpus,
    load_input_data,
    retrieve_evidence,
)


def load_local_build_candidate_packet() -> Any:
    helper_path = CODE_DIR / "build_ans_claims_for_strict_merge_candidates.py"
    spec = importlib.util.spec_from_file_location("local_build_ans_claims_for_strict_merge_candidates", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load local helper: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_candidate_packet


build_candidate_packet = load_local_build_candidate_packet()


DEFAULT_PAPER_SPEC = "gpt_5_2:s41467-025-56921-8"
DEFAULT_NODE_IDS = "N18"
DEFAULT_CANDIDATES = (
    RESIDUAL_ROOT
    / "strict_merge_candidates"
    / "20260606_notation_protocol_replay_v2_from_312base"
    / "STRICT_MERGE_CANDIDATES.json"
)
DEFAULT_V2_LEDGER = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_residual_closeout_v2"
    / "V2_RESIDUAL_LEDGER.csv"
)
DEFAULT_STEP2_GRAPH = (
    PACKAGE_ROOT.parent
    / "08_minicheck"
    / "baselines"
    / "materialized_graphs"
    / "llm_step2_self_fix_final_clean"
    / "gpt_5_2"
    / "s41467-025-56921-8"
    / "20260315_203122"
    / "llm_step2_self_fix_final_clean_graph.json"
)
DEFAULT_ANS_CACHE = (
    PACKAGE_ROOT
    / "08_ans_factscore_style"
    / "03_current_full_run"
    / "ans_eval_cache.jsonl"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "ans_safe_step2_hybrids"
    / "20260606_gpt52_56921_step2_source_leaf_v1"
)
DEFAULT_LABEL = "step2-source-leaf-N18-to-NROOT-v1"
N26_NARROW_TEXT = (
    "Currently, for MAX phases, ex situ irradiation experiments cannot directly observe evolving atomic "
    "structure across damage accumulation, and initial-phase-focused analyses rarely study intermediate "
    "phases; therefore the complete multi-stage transformation pathway is not adequately explained."
)


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


def load_candidate(path: Path, paper_spec: str) -> dict[str, Any]:
    payload = read_json(path, {})
    for row in payload.get("rows") or []:
        if isinstance(row, dict) and row.get("paper_spec") == paper_spec:
            return row
    raise RuntimeError(f"paper_spec not found in candidates: {paper_spec}")


def load_ledger_row(path: Path, paper_spec: str) -> dict[str, str]:
    for row in read_csv(path):
        if row.get("paper_spec") == paper_spec:
            return row
    return {}


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


def graph_nodes(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(node.get("id")): node for node in spec.get("nodes") or [] if isinstance(node, dict) and node.get("id")}


def parse_node_ids(value: str) -> list[str]:
    out = []
    seen = set()
    for part in value.split(","):
        node_id = part.strip()
        if node_id and node_id not in seen:
            seen.add(node_id)
            out.append(node_id)
    return out


def terminal_spec_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    graph_dot = resolve_path(str(candidate.get("candidate_graph") or ""))
    graph_json = graph_dot.with_suffix(".json")
    if not graph_json.exists():
        raise FileNotFoundError(f"candidate graph JSON not found: {graph_json}")
    return read_json(graph_json, {})


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
    replay = resolve_path(str(candidate.get("candidate_fresh_eval_results") or ""))
    payload = read_json(replay, {})
    input_path = payload.get("input_data")
    if input_path:
        resolved = resolve_path(str(input_path))
        if resolved.exists():
            return resolved
    raise FileNotFoundError(f"input_data.json not found for {candidate.get('paper_spec')}")


def packet_from_ledger(ledger_row: dict[str, str]) -> tuple[Path, dict[str, Any]]:
    packet_path = resolve_path(ledger_row.get("packet_json") or "")
    if not packet_path.exists():
        raise FileNotFoundError(f"packet_json not found: {packet_path}")
    return packet_path, read_json(packet_path, {})


def materialize_spec(
    *,
    terminal: dict[str, Any],
    step2: dict[str, Any],
    node_ids: list[str],
    label: str,
    narrow_n26: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    out = json.loads(json.dumps(terminal, ensure_ascii=False))
    nodes = graph_nodes(out)
    step2_nodes = graph_nodes(step2)
    actions: list[dict[str, Any]] = []
    existing_edges = {
        (str(edge.get("source")), str(edge.get("target")), str(edge.get("type")))
        for edge in out.get("edges") or []
        if isinstance(edge, dict)
    }
    for node_id in node_ids:
        if node_id not in step2_nodes:
            raise RuntimeError(f"step2 node missing: {node_id}")
        source_node = step2_nodes[node_id]
        if node_id in nodes:
            old_text = nodes[node_id].get("text", "")
            old_source = nodes[node_id].get("source", [])
            nodes[node_id]["text"] = source_node.get("text", "")
            nodes[node_id]["source"] = source_node.get("source", [0, 0, 0])
            action = "replace_existing_node_from_step2"
        else:
            out.setdefault("nodes", []).append(
                {
                    "id": node_id,
                    "source": source_node.get("source", [0, 0, 0]),
                    "text": source_node.get("text", ""),
                }
            )
            old_text = ""
            old_source = []
            action = "add_step2_source_node"
        edge_key = (node_id, "NROOT", "induction-case")
        if edge_key not in existing_edges:
            out.setdefault("edges", []).append({"source": node_id, "target": "NROOT", "type": "induction-case"})
            existing_edges.add(edge_key)
        actions.append(
            {
                "action": action,
                "node_id": node_id,
                "old_text": old_text,
                "old_source": old_source,
                "new_text": source_node.get("text", ""),
                "new_source": source_node.get("source", [0, 0, 0]),
                "added_edge": {"source": node_id, "target": "NROOT", "type": "induction-case"},
                "candidate_label": label,
            }
        )
    if narrow_n26:
        if "N26" not in nodes:
            raise RuntimeError("N26 missing from terminal graph")
        old_text = str(nodes["N26"].get("text") or "")
        nodes["N26"]["text"] = N26_NARROW_TEXT
        actions.append(
            {
                "action": "narrow_existing_reasoning_node",
                "node_id": "N26",
                "old_text": old_text,
                "old_source": nodes["N26"].get("source", []),
                "new_text": N26_NARROW_TEXT,
                "new_source": nodes["N26"].get("source", []),
                "added_edge": {},
                "candidate_label": label,
            }
        )
    return out, actions


def estimate_cached_ans(candidate_package: dict[str, Any], cache: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    rows = candidate_package.get("claims") or []
    atomic = 0
    supported = 0
    cache_hits = 0
    cache_misses = 0
    rows_out = []
    for row in rows:
        ans_evidence = row.get("evidence_text") or ""
        input_path = str(row.get("input_data_path") or "")
        if input_path:
            input_data = load_input_data(input_path)
            ans_evidence = retrieve_evidence(
                row,
                evidence_corpus(input_data),
                top_k=4,
                max_chars=1800,
            )
        eval_row = dict(row)
        eval_row["ans_evidence"] = ans_evidence
        key = eval_key(eval_row)
        facts = cache.get(key)
        if facts is None:
            cache_misses += 1
            rows_out.append(
                {
                    "node_id": row.get("node_id"),
                    "unit_type": row.get("unit_type"),
                    "cache_hit": False,
                    "atomic_fact_count": "",
                    "supported_fact_count": "",
                    "node_text": clean_text(row.get("claim_text_for_minicheck") or row.get("raw_node_text") or ""),
                }
            )
            continue
        cache_hits += 1
        fact_count = len(facts)
        support_count = sum(1 for fact in facts if fact.get("supported"))
        atomic += fact_count
        supported += support_count
        rows_out.append(
            {
                "node_id": row.get("node_id"),
                "unit_type": row.get("unit_type"),
                "cache_hit": True,
                "atomic_fact_count": fact_count,
                "supported_fact_count": support_count,
                "node_text": clean_text(row.get("claim_text_for_minicheck") or row.get("raw_node_text") or ""),
            }
        )
    return {
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cached_atomic_fact_count": atomic,
        "cached_supported_fact_count": supported,
        "cached_ans": supported / atomic if atomic else None,
        "rows": rows_out,
    }


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# ANS-Safe Step2 Hybrid Candidate",
        "",
        f"Created: `{summary['created_at']}`",
        f"Paper spec: `{summary['paper_spec']}`",
        f"Candidate label: `{summary['candidate_label']}`",
        "",
        "## Gates",
        "",
        f"- Local preflight passed: `{summary['preflight_passed']}`",
        f"- Row ANS floor: `{summary['row_ans_floor']}`",
        f"- Cached ANS estimate: `{summary['cached_ans_estimate'].get('cached_ans')}`",
        f"- Cache hits/misses: `{summary['cached_ans_estimate'].get('cache_hits')}` / `{summary['cached_ans_estimate'].get('cache_misses')}`",
        f"- Fresh eval required: `{summary['fresh_eval_required']}`",
        "",
        "## Actions",
        "",
    ]
    for action in summary["actions"]:
        lines.append(f"- `{action['action']}` `{action['node_id']}`: {action['new_text']}")
    lines.append("")
    return "\n".join(lines)


def build(args: argparse.Namespace) -> dict[str, Any]:
    paper_spec = args.paper_spec
    node_ids = parse_node_ids(args.node_ids)
    out_root = resolve_path(args.out_root)
    candidate = load_candidate(resolve_path(args.strict_candidates), paper_spec)
    ledger_row = load_ledger_row(resolve_path(args.v2_ledger), paper_spec)
    packet_path, packet = packet_from_ledger(ledger_row)
    terminal = terminal_spec_from_candidate(candidate)
    step2 = read_json(resolve_path(args.step2_graph), {})
    if not step2:
        raise RuntimeError("empty step2 graph")
    hybrid, actions = materialize_spec(
        terminal=terminal,
        step2=step2,
        node_ids=node_ids,
        label=args.label,
        narrow_n26=args.narrow_n26,
    )
    preflight = preflight_graph_spec(hybrid, packet)

    candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(args.label)
    staged_dir = out_root / "staged" / safe_slug(paper_spec) / safe_slug(args.label)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    staged_dir.mkdir(parents=True, exist_ok=True)
    write_json(candidate_dir / "graph_spec.json", hybrid)
    write_text(candidate_dir / "final_clean_graph.dot", graph_spec_to_dot(hybrid))
    write_json(candidate_dir / "preflight_report.json", preflight)

    input_path = input_data_from_candidate(candidate)
    shutil.copy2(candidate_dir / "graph_spec.json", staged_dir / "graph_spec.json")
    shutil.copy2(candidate_dir / "final_clean_graph.dot", staged_dir / "final_clean_graph.dot")
    shutil.copy2(input_path, staged_dir / "input_data.json")
    write_json(
        staged_dir / "evidence_bound_packet_pointer.json",
        {"packet": rel(packet_path), "source": "ans_safe_step2_hybrid_candidate"},
    )

    candidate_for_claims = {
        **candidate,
        "paper_spec": paper_spec,
        "candidate_label": args.label,
        "candidate_graph": rel(candidate_dir / "final_clean_graph.dot"),
        "candidate_eval_dir": rel(staged_dir),
    }
    # build_candidate_packet takes (candidate, args); keep the call explicit to
    # avoid hiding the imported helper's argument order.
    claim_args = argparse.Namespace(
        window=args.window,
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
        out_root / "CACHED_ANS_NODE_ESTIMATE.csv",
        cached_estimate["rows"],
        ["node_id", "unit_type", "cache_hit", "atomic_fact_count", "supported_fact_count", "node_text"],
    )

    row_ans_floor = float(candidate.get("current_best_main_factual_ans") or 0.0)
    cached_ans = cached_estimate.get("cached_ans")
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "paper_spec": paper_spec,
        "candidate_label": args.label,
        "node_ids": node_ids,
        "source_strict_candidates": rel(resolve_path(args.strict_candidates)),
        "source_step2_graph": rel(resolve_path(args.step2_graph)),
        "source_terminal_graph": candidate.get("candidate_graph"),
        "source_packet": rel(packet_path),
        "candidate_dir": rel(candidate_dir),
        "staged_dir": rel(staged_dir),
        "claims_input": rel(claims_path),
        "graph_spec": rel(candidate_dir / "graph_spec.json"),
        "final_clean_graph": rel(candidate_dir / "final_clean_graph.dot"),
        "final_clean_graph_sha256": sha256_file(candidate_dir / "final_clean_graph.dot"),
        "preflight_report": rel(candidate_dir / "preflight_report.json"),
        "preflight_passed": bool(preflight.get("passed_local_preflight")),
        "row_ans_floor": row_ans_floor,
        "cached_ans_estimate": cached_estimate,
        "cached_ans_passes_row_floor": bool(cached_ans is not None and cached_ans + 1e-12 >= row_ans_floor),
        "fresh_eval_required": True,
        "actions": actions,
    }
    write_json(out_root / "ANS_SAFE_STEP2_HYBRID_SUMMARY.json", summary)
    write_text(out_root / "ANS_SAFE_STEP2_HYBRID_SUMMARY.md", markdown(summary))
    write_json(out_root / "ATTEMPT_INDEX.json", {"rows": [summary]})
    write_csv(
        out_root / "ATTEMPT_INDEX.csv",
        [
            {
                "paper_spec": paper_spec,
                "candidate_label": args.label,
                "passed_local_preflight": summary["preflight_passed"],
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
    print(json.dumps({k: summary[k] for k in ["paper_spec", "candidate_label", "preflight_passed", "row_ans_floor", "cached_ans_passes_row_floor"]} | {"cached_ans": cached_estimate.get("cached_ans"), "out_root": rel(out_root)}, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-spec", default=DEFAULT_PAPER_SPEC)
    parser.add_argument("--node-ids", default=DEFAULT_NODE_IDS)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--strict-candidates", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--v2-ledger", default=str(DEFAULT_V2_LEDGER))
    parser.add_argument("--step2-graph", default=str(DEFAULT_STEP2_GRAPH))
    parser.add_argument("--ans-cache", default=str(DEFAULT_ANS_CACHE))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--window", type=int, default=1)
    parser.add_argument("--traversal-depth", type=int, default=2)
    parser.add_argument("--max-evidence-sentences", type=int, default=10)
    parser.add_argument("--max-evidence-chars", type=int, default=1800)
    parser.add_argument("--include-viewpoints", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--graph-ordered-evidence", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--narrow-n26", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
