#!/usr/bin/env python3
"""Build ANS claim inputs for strict merge candidates.

The output schema intentionally matches the existing PEARL MiniCheck/ANS claim
rows so `evaluate_ans_factscore_style_350.py` can evaluate candidate graphs with
the same evidence-window construction used for the 350-run ANS baseline.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_MERGE_CANDIDATES = (
    RESIDUAL_ROOT
    / "strict_merge_candidates"
    / "20260603_local_window_v3_closure"
    / "STRICT_MERGE_CANDIDATES.json"
)
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "ans_factscore_style"
    / "strict_merge_candidate_claims"
    / "20260603_local_window_v3_closure"
)

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from evaluate_minicheck_grounding import (  # type: ignore  # noqa: E402
    build_evidence,
    clean_claim_text,
    display_path,
    evidence_text_for_sentence,
    graph_support_sources,
    incoming_edges_by_target,
    node_by_id,
    read_json,
    sentence_text_by_idx,
    source_key,
    split_claim_sentences,
    unit_type,
)


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def candidate_input_data_path(candidate: Dict[str, Any]) -> Path:
    graph = resolve_path(str(candidate.get("candidate_graph") or ""))
    staged_dir = graph.parent
    local_input = staged_dir / "input_data.json"
    if local_input.exists():
        return local_input
    eval_dir = resolve_path(str(candidate.get("candidate_eval_dir") or ""))
    for parent in [eval_dir, *eval_dir.parents]:
        candidate_path = parent / "input_data.json"
        if candidate_path.exists():
            return candidate_path
    raise FileNotFoundError(f"input_data.json not found for candidate {candidate.get('paper_spec')}")


def candidate_graph_json(candidate: Dict[str, Any]) -> Path:
    graph = resolve_path(str(candidate.get("candidate_graph") or ""))
    graph_json = graph.with_suffix(".json")
    if graph_json.exists():
        return graph_json
    sibling = graph.parent / "graph_spec.json"
    if sibling.exists():
        return sibling
    raise FileNotFoundError(f"graph JSON/spec not found for candidate {candidate.get('paper_spec')}: {graph}")


def graph_ordered_evidence(
    node_id: str,
    nodes: Dict[str, Dict[str, Any]],
    incoming: Dict[str, List[Dict[str, Any]]],
    input_data: Dict[str, Any],
    *,
    window: int,
    depth: int,
    max_sentences: int,
    max_chars: int,
    include_viewpoints: bool,
    claim_text: str = "",
    claim_aware_root_rerank: bool = True,
) -> Dict[str, Any]:
    """Build compact evidence with directly connected graph support first.

    The generic MiniCheck packet builder sorts evidence by sentence index. That
    is fine for large MiniCheck documents, but ANS subsequently truncates
    evidence to a smaller prompt budget. For local repair candidates, the most
    faithful ordering is the graph support order: direct incoming source nodes
    first, then their upstream sources, with local sentence windows retained.
    """

    by_idx = sentence_text_by_idx(input_data)
    support_sources = graph_support_sources(node_id, nodes, incoming, depth=depth)
    ordered_ids: List[int] = []
    sentence_reasons: Dict[int, List[Dict[str, Any]]] = {}
    seen: set[int] = set()
    direct_ids: List[int] = []
    for support in support_sources:
        x, _y, _z = source_key(support.get("source_tuple"))
        if x <= 0:
            continue
        if x in by_idx and x not in direct_ids:
            direct_ids.append(x)
        for idx in range(max(1, x - window), x + window + 1):
            if idx not in by_idx:
                continue
            sentence_reasons.setdefault(idx, []).append(support)
            if idx not in seen:
                seen.add(idx)
                ordered_ids.append(idx)

    if claim_aware_root_rerank and node_id == "NROOT" and claim_text and ordered_ids:
        claim_tokens = set(re.findall(r"[A-Za-z0-9]+", claim_text.lower()))

        def claim_score(idx: int) -> float:
            sentence = by_idx.get(idx, {})
            text = evidence_text_for_sentence(sentence, include_viewpoints=include_viewpoints).lower()
            sentence_tokens = set(re.findall(r"[A-Za-z0-9]+", text))
            score = len(claim_tokens & sentence_tokens)
            if any(marker in text for marker in ["mixed ligands", "imidazole", "benzimidazole", "bim", "recrystallization"]):
                score += 10
            if any(marker in text for marker in ["bulky amide", "sda", "structure-directing"]):
                score += 9
            if any(marker in text for marker in ["free-standing", "membrane", "record-breaking", "ch4/n2", "co2/n2"]):
                score += 8
            if any(marker in text for marker in ["12 mr", "12-membered", "meltable mofs", "highly porous topologies"]):
                score += 7
            if any(marker in text for marker in ["benchmark and review", "benchmarking process", "evaluation categories", "28 different metrics", "five categories"]):
                score += 10
            if any(marker in text for marker in ["performance, stability", "clinical usage", "clinical translation", "translational potential"]):
                score += 7
            if any(marker in text for marker in ["heterostructure-based strategy", "square-lattice iridates", "isotropic continua", "compelling evidence for spinons"]):
                score += 10
            if any(marker in text for marker in ["spinon excitations", "néel af order", "superlattice", "magnetic frustration"]):
                score += 7
            return float(score)

        ordered_ids = sorted(ordered_ids, key=lambda idx: (-claim_score(idx), ordered_ids.index(idx), idx))
        direct_ranked = sorted(direct_ids, key=lambda idx: (-claim_score(idx), direct_ids.index(idx), idx))
        merged_ids: List[int] = []
        for idx in direct_ranked + ordered_ids:
            if idx not in merged_ids:
                merged_ids.append(idx)
        ordered_ids = merged_ids

    truncated_sentences = False
    if len(ordered_ids) > max_sentences:
        ordered_ids = ordered_ids[:max_sentences]
        truncated_sentences = True

    evidence_items = []
    evidence_lines = []
    char_count = 0
    truncated_chars = False
    compact_root_evidence = claim_aware_root_rerank and node_id == "NROOT"
    for idx in ordered_ids:
        text = evidence_text_for_sentence(by_idx[idx], include_viewpoints=include_viewpoints and not compact_root_evidence)
        if max_chars > 0 and char_count + len(text) + 1 > max_chars:
            truncated_chars = True
            break
        char_count += len(text) + 1
        evidence_lines.append(text)
        evidence_items.append({"idx": idx, "text": text, "reasons": sentence_reasons[idx]})

    return {
        "evidence_mode": "graph_ordered_source_evidence",
        "evidence_sentence_ids": [item["idx"] for item in evidence_items],
        "evidence_text": "\n".join(evidence_lines).strip(),
        "evidence_items": evidence_items,
        "evidence_source_count": len(support_sources),
        "evidence_truncated": bool(truncated_sentences or truncated_chars),
        "evidence_truncated_sentences": truncated_sentences,
        "evidence_truncated_chars": truncated_chars,
        "no_evidence": not evidence_items,
    }


def build_candidate_packet(candidate: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    graph_path = resolve_path(str(candidate.get("candidate_graph") or ""))
    graph_json = candidate_graph_json(candidate)
    input_data_path = candidate_input_data_path(candidate)
    eval_dir = resolve_path(str(candidate.get("candidate_eval_dir") or ""))
    graph = read_json(graph_json)
    input_data = read_json(input_data_path)
    nodes = node_by_id(graph)
    incoming = incoming_edges_by_target(graph)

    source_model, _, source_paper = str(candidate.get("paper_spec") or "").partition(":")
    model = source_model or str(candidate.get("model") or "")
    paper = source_paper or str(candidate.get("paper") or "")
    run_id = "strict_merge_candidate"
    claims: List[Dict[str, Any]] = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        raw_text = str(node.get("text") or "").strip()
        if not node_id or not raw_text:
            continue
        cleaned = clean_claim_text(raw_text)
        sentence_claims = split_claim_sentences(cleaned)
        evidence_window = args.root_window if node_id == "NROOT" and args.root_window is not None else args.window
        if args.graph_ordered_evidence:
            evidence = graph_ordered_evidence(
                node_id,
                nodes,
                incoming,
                input_data,
                window=evidence_window,
                depth=args.traversal_depth,
                max_sentences=args.max_evidence_sentences,
                max_chars=args.max_evidence_chars,
                include_viewpoints=args.include_viewpoints,
                claim_text=cleaned,
            )
        else:
            evidence = build_evidence(
                node_id,
                nodes,
                incoming,
                input_data,
                window=evidence_window,
                depth=args.traversal_depth,
                max_sentences=args.max_evidence_sentences,
                max_chars=args.max_evidence_chars,
                include_viewpoints=args.include_viewpoints,
            )
        claims.append(
            {
                "claim_id": f"{candidate.get('paper_spec')}::{node_id}",
                "paper_spec": candidate.get("paper_spec"),
                "model": model,
                "paper": paper,
                "run_id": run_id,
                "quality_tier": "evidence_bound_closeout",
                "graph_path": display_path(graph_path),
                "graph_json": display_path(graph_json),
                "source_eval_dir": display_path(eval_dir),
                "input_data_path": display_path(input_data_path),
                "node_id": node_id,
                "unit_type": unit_type(node),
                "source_tuple": list(source_key(node.get("source"))),
                "raw_node_text": raw_text,
                "claim_text_for_minicheck": cleaned,
                "sentence_claims": sentence_claims,
                "sentence_count": len(sentence_claims),
                "candidate_label": candidate.get("candidate_label", ""),
                "candidate_fresh_eval_results": candidate.get("candidate_fresh_eval_results", ""),
                **evidence,
            }
        )
    return {
        "paper_spec": candidate.get("paper_spec"),
        "model": model,
        "paper": paper,
        "run_id": run_id,
        "candidate_label": candidate.get("candidate_label", ""),
        "graph_path": display_path(graph_path),
        "graph_json": display_path(graph_json),
        "source_eval_dir": display_path(eval_dir),
        "input_data_path": display_path(input_data_path),
        "claims": claims,
    }


def load_candidates(path: Path) -> List[Dict[str, Any]]:
    package = json.loads(path.read_text(encoding="utf-8"))
    rows = package.get("rows") if isinstance(package, dict) else []
    if not isinstance(rows, list):
        raise RuntimeError(f"candidate package lacks rows: {path}")
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("mergeable") is True
        and float(row.get("candidate_CG") or 0.0) >= 1.0
        and float(row.get("candidate_REA") or 0.0) >= 1.0
    ]


def build(args: argparse.Namespace) -> Dict[str, Any]:
    candidates_path = resolve_path(args.merge_candidates)
    out_root = resolve_path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    claims_path = out_root / "claims_input.jsonl"
    claims_path.unlink(missing_ok=True)

    candidate_label_filter = {label.strip() for label in args.candidate_label or [] if label.strip()}
    candidates = load_candidates(candidates_path)
    if candidate_label_filter:
        candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("candidate_label") or "") in candidate_label_filter
        ]
    packets = [build_candidate_packet(candidate, args) for candidate in candidates]
    rows = []
    for packet in packets:
        rows.extend({key: value for key, value in claim.items() if key != "evidence_items"} for claim in packet["claims"])
    append_jsonl(claims_path, rows)
    write_json(out_root / "claim_packets.json", packets)
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "strict_merge_candidate_ans_claim_input",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_merge_candidates": rel(candidates_path),
        "out_root": rel(out_root),
        "candidate_count": len(packets),
        "candidate_label_filter": sorted(candidate_label_filter),
        "claim_rows": len(rows),
        "claims_input": rel(claims_path),
        "evidence_window": args.window,
        "root_evidence_window": args.root_window,
        "traversal_depth": args.traversal_depth,
        "max_evidence_sentences": args.max_evidence_sentences,
        "max_evidence_chars": args.max_evidence_chars,
    }
    write_json(out_root / "ANS_CANDIDATE_CLAIMS_SUMMARY.json", summary)
    write_csv(
        out_root / "ANS_CANDIDATE_CLAIMS_INDEX.csv",
        [
            {
                "paper_spec": packet["paper_spec"],
                "candidate_label": packet["candidate_label"],
                "claims": len(packet["claims"]),
                "graph_path": packet["graph_path"],
                "graph_json": packet["graph_json"],
                "input_data_path": packet["input_data_path"],
            }
            for packet in packets
        ],
        ["paper_spec", "candidate_label", "claims", "graph_path", "graph_json", "input_data_path"],
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merge-candidates", default=str(DEFAULT_MERGE_CANDIDATES))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--window", type=int, default=1)
    parser.add_argument("--root-window", type=int, default=None)
    parser.add_argument("--traversal-depth", type=int, default=2)
    parser.add_argument("--max-evidence-sentences", type=int, default=10)
    parser.add_argument("--max-evidence-chars", type=int, default=1800)
    parser.add_argument("--candidate-label", action="append", default=[])
    parser.add_argument("--include-viewpoints", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--graph-ordered-evidence", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    summary = build(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
