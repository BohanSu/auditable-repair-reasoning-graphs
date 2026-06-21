#!/usr/bin/env python3
"""Build a provider-free claim/evidence closure solver for after327 residuals.

This diagnostic layer is intentionally offline. It aligns each hard residual's
root claim, packet entity evidence, current graph source leaves, and fresh judge
reason into repair instructions. It does not write canonical accounting, call
providers, or accept any candidate.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DIAGNOSTIC_ROOT = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_after327_failure_typed_lit_diagnostic"
)
DEFAULT_CONTROLLER_QUEUE = DIAGNOSTIC_ROOT / "controller_queue_v1" / "AFTER327_CONTROLLER_QUEUE.json"
DEFAULT_OUT_ROOT = DIAGNOSTIC_ROOT / "claim_evidence_closure_solver_v1"
DEFAULT_PACKET_ROOTS = [
    RESIDUAL_ROOT / "evidence_bound_regeneration" / "packets",
    RESIDUAL_ROOT / "evidence_bound_regeneration" / "no_anchor_after_notation_v3_atomic_anssafe" / "packets",
    RESIDUAL_ROOT / "evidence_bound_regeneration" / "no_anchor_after_notation_v4_ansaware" / "packets",
    RESIDUAL_ROOT / "evidence_bound_regeneration" / "no_anchor_after_notation_v2_atomic_support" / "packets",
    RESIDUAL_ROOT / "evidence_bound_regeneration" / "no_anchor_after_notation_v1" / "packets",
]

TARGET_ROUTES = {
    "route_specific_candidate_revision",
    "claim_graph_reconstruction_from_source_inventory",
    "judge_reason_targeted_reconstruction",
}

METHOD_RULES = [
    {
        "source": "FActScore",
        "url": "https://arxiv.org/abs/2305.14251",
        "solver_rule": "Split root commitments into atomic entity/support checks instead of judging the whole graph.",
    },
    {
        "source": "SAFE",
        "url": "https://arxiv.org/abs/2403.18802",
        "solver_rule": "Require independently source-backed evidence for every factual commitment before fresh eval.",
    },
    {
        "source": "RefChecker",
        "url": "https://arxiv.org/abs/2405.14486",
        "solver_rule": "Classify support at the structured claim/entity level so repair actions are typed.",
    },
    {
        "source": "RAGChecker",
        "url": "https://arxiv.org/abs/2408.08067",
        "solver_rule": "Separate evidence retrieval gaps from generation/reasoning gaps.",
    },
    {
        "source": "RARR",
        "url": "https://arxiv.org/abs/2210.08726",
        "solver_rule": "Revise only unsupported content while preserving supported claims and topology.",
    },
    {
        "source": "Cited but Not Verified",
        "url": "https://arxiv.org/abs/2605.06635",
        "solver_rule": "Reject merely topical source leaves unless they support the exact root commitment.",
    },
    {
        "source": "Importance-aware factual recall",
        "url": "https://arxiv.org/abs/2604.03141",
        "solver_rule": "Keep high-importance anchor entities in the claim/evidence closure target even when precision is already high.",
    },
    {
        "source": "LLM-as-judge bias",
        "url": "https://arxiv.org/abs/2305.17926",
        "solver_rule": "Provider and judge-noise lanes remain rerun-only; do not edit content for provider errors.",
    },
]

STOP_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "via",
    "with",
}

BACKGROUND_MARKERS = [
    "previous",
    "recent progress",
    "currently",
    "limited research",
    "for comparison",
    "available data suggest",
    "requires refining",
    "challenge lies",
]

OVERCLAIM_MARKERS = [
    "fails to",
    "unless",
    "thereby",
    "specifically",
    "prevents",
    "sidesteps",
    "demonstrates",
    "current techniques",
    "up to",
]


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z0-9]+", str(text).lower())
        if len(token) > 1 and token not in STOP_TOKENS
    ]


def token_set(text: str) -> set[str]:
    return set(tokenize(text))


def overlap_score(query: str, text: str) -> float:
    q = token_set(query)
    if not q:
        return 0.0
    t = token_set(text)
    return len(q & t) / len(q)


def slug_variants(paper_spec: str) -> list[str]:
    model, _, paper = paper_spec.partition(":")
    values = [
        paper_spec.replace(":", "_"),
        paper_spec.replace(":", "__"),
        f"{model}_{paper}",
        f"{model}__{paper}",
        paper,
    ]
    return [value.replace("/", "_").replace(" ", "_") for value in values if value.strip("_")]


def load_controller_rows(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = read_json(path, {})
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return payload if isinstance(payload, dict) else {}, [row for row in rows if isinstance(row, dict)]


def graph_from_row(row: dict[str, Any]) -> dict[str, Any]:
    graph_path = str(row.get("graph_spec") or "")
    if not graph_path:
        return {}
    payload = read_json(resolve(graph_path), {})
    if not isinstance(payload, dict):
        return {}
    root_id = str(payload.get("root") or "NROOT")
    source_nodes = []
    root_text = ""
    node_by_id = {}
    for node in payload.get("nodes", []) if isinstance(payload.get("nodes"), list) else []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        node_by_id[node_id] = node
        if node_id == root_id:
            root_text = str(node.get("text") or "")
        source = node.get("source")
        if isinstance(source, list) and source and source[0] not in (0, "0", None):
            source_nodes.append(
                {
                    "node_id": node_id,
                    "source": source,
                    "source_key": source_key(source),
                    "text": str(node.get("text") or ""),
                }
            )
    return {
        "graph_spec": rel(resolve(graph_path)),
        "root_id": root_id,
        "root_text": root_text,
        "source_nodes": source_nodes,
        "node_by_id": node_by_id,
    }


def source_key(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def packet_from_row(row: dict[str, Any], packet_roots: list[Path]) -> tuple[Path | None, dict[str, Any]]:
    staged = str(row.get("staged_run_dir") or "")
    if staged:
        pointer_path = resolve(staged) / "evidence_bound_packet_pointer.json"
        pointer = read_json(pointer_path, {})
        packet_value = pointer.get("packet") if isinstance(pointer, dict) else ""
        if packet_value:
            packet_path = resolve(str(packet_value))
            packet = read_json(packet_path, {})
            if isinstance(packet, dict):
                return packet_path, packet

    attempt_index_csv = str(row.get("attempt_index_csv") or "")
    if attempt_index_csv:
        attempt_json = resolve(attempt_index_csv).with_suffix(".json")
        payload = read_json(attempt_json, {})
        for attempt in payload.get("rows", []) if isinstance(payload, dict) else []:
            if not isinstance(attempt, dict):
                continue
            if str(attempt.get("paper_spec") or "") != str(row.get("paper_spec") or ""):
                continue
            packet_value = str(attempt.get("packet") or "")
            if packet_value:
                packet_path = resolve(packet_value)
                packet = read_json(packet_path, {})
                if isinstance(packet, dict):
                    return packet_path, packet

    variants = slug_variants(str(row.get("paper_spec") or ""))
    for root in packet_roots:
        if not root.exists():
            continue
        for packet_path in sorted(root.rglob("packet.json")):
            path_text = str(packet_path)
            if any(variant in path_text for variant in variants):
                packet = read_json(packet_path, {})
                if isinstance(packet, dict):
                    return packet_path, packet
    return None, {}


def paper_anchor(packet: dict[str, Any]) -> dict[str, Any]:
    anchor = packet.get("paper_anchor") if isinstance(packet, dict) else {}
    if isinstance(anchor, dict):
        return anchor
    return {}


def packet_entities(packet: dict[str, Any]) -> list[str]:
    anchor = paper_anchor(packet)
    entities = anchor.get("entities")
    if isinstance(entities, list):
        return [str(entity) for entity in entities if str(entity).strip()]
    return []


def packet_core_idea(packet: dict[str, Any]) -> str:
    anchor = paper_anchor(packet)
    return str(anchor.get("core_idea") or "")


def packet_entity_evidence(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence = packet.get("evidence") if isinstance(packet, dict) else {}
    rows = evidence.get("entity_evidence") if isinstance(evidence, dict) else []
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("entity"):
            out[str(row["entity"])] = row
    return out


def packet_prompt_sentences(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence = packet.get("evidence") if isinstance(packet, dict) else {}
    rows = evidence.get("prompt_sentences") if isinstance(evidence, dict) else []
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        source = row.get("source")
        if isinstance(source, list):
            out[source_key(source)] = row
        if row.get("idx") not in (None, ""):
            out[str(row.get("idx"))] = row
    return out


def fresh_detail(row: dict[str, Any]) -> dict[str, Any]:
    fresh_path = str(row.get("fresh_eval_results") or "")
    fresh_row = {}
    if fresh_path:
        payload = read_json(resolve(fresh_path), {})
        for candidate in payload.get("rows", []) if isinstance(payload, dict) else []:
            if isinstance(candidate, dict) and str(candidate.get("paper_spec") or "") == str(row.get("paper_spec") or ""):
                label = str(row.get("candidate_label") or "")
                if not label or str(candidate.get("candidate_label") or "") == label:
                    fresh_row = candidate
                    break
    eval_detail = {}
    vote_detail = {}
    eval_dir = fresh_row.get("eval_dir") if isinstance(fresh_row, dict) else ""
    if eval_dir:
        eval_root = resolve(str(eval_dir))
        eval_detail = read_json(eval_root / "evaluation_results.json", {})
        vote_detail = read_json(eval_root / "responses" / "reasoning_validation_001_vote_result.json", {})
    return {
        "fresh_row": fresh_row,
        "eval_detail": eval_detail if isinstance(eval_detail, dict) else {},
        "vote_detail": vote_detail if isinstance(vote_detail, dict) else {},
    }


def missing_entities_from_row(row: dict[str, Any], eval_detail: dict[str, Any], entities: list[str]) -> list[str]:
    value = row.get("missing_entities")
    if value:
        return [item.strip() for item in str(value).split(";") if item.strip()]
    covered = int(as_float((eval_detail.get("coverage") or {}).get("covered_entities"), 0))
    total = int(as_float((eval_detail.get("coverage") or {}).get("total_entities"), 0))
    if total and covered == 0:
        return list(entities)
    return []


def entity_graph_support(entity: str, source_nodes: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    scored = []
    for node in source_nodes:
        score = overlap_score(entity, str(node.get("text") or ""))
        if score > 0:
            scored.append(
                {
                    "node_id": node.get("node_id", ""),
                    "source": node.get("source", []),
                    "text": node.get("text", ""),
                    "overlap": round(score, 4),
                }
            )
    scored.sort(key=lambda item: (-float(item["overlap"]), str(item["node_id"])))
    return (float(scored[0]["overlap"]) if scored else 0.0, scored[:3])


def entity_packet_support(entity: str, entity_rows: dict[str, dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    row = entity_rows.get(entity)
    if not row:
        return 0.0, []
    out = []
    for item in row.get("top_sentences", []) if isinstance(row.get("top_sentences"), list) else []:
        if not isinstance(item, dict):
            continue
        score = as_float(item.get("score"), 0.0)
        out.append(
            {
                "idx": item.get("idx", ""),
                "source": item.get("source", []),
                "score": score,
                "exact_phrase_match": item.get("exact_phrase_match", item.get("exact", "")),
                "sentence": item.get("sentence", ""),
                "viewpoints": item.get("viewpoints", []),
            }
        )
    out.sort(key=lambda item: (-as_float(item.get("score")), str(item.get("idx"))))
    return (as_float(out[0].get("score")) if out else 0.0, out[:5])


def judge_reasons(vote_detail: dict[str, Any]) -> list[str]:
    reasons = []
    model_responses = vote_detail.get("model_responses") if isinstance(vote_detail, dict) else {}
    for key, response in sorted(model_responses.items()) if isinstance(model_responses, dict) else []:
        if not isinstance(response, dict):
            continue
        if str(response.get("result") or "").lower() == "wrong":
            reasons.append(f"{key}: {str(response.get('reason') or '').strip()}")
    return reasons


def classify_entity(
    entity: str,
    graph_score: float,
    packet_score: float,
    graph_matches: list[dict[str, Any]],
    packet_matches: list[dict[str, Any]],
) -> str:
    if not packet_matches:
        return "packet_support_missing"
    if graph_score <= 0.0 and packet_score > 0.0:
        return "source_leaf_missing"
    if graph_score < 0.5 and packet_score > graph_score:
        return "weak_graph_leaf"
    top_graph_source = source_key(graph_matches[0].get("source")) if graph_matches else ""
    top_packet_source = source_key(packet_matches[0].get("source")) if packet_matches else ""
    if top_graph_source and top_packet_source and top_graph_source != top_packet_source and packet_score > graph_score:
        return "source_tuple_mismatch"
    return "graph_entity_present"


def closure_action(row: dict[str, Any], entity_rows: list[dict[str, Any]], vote_reasons: list[str], root_text: str) -> str:
    route = str(row.get("controller_route") or "")
    fresh_cg = as_float(row.get("fresh_CG"), -1.0)
    fresh_rea = as_float(row.get("fresh_REA"), -1.0)
    if route == "claim_graph_reconstruction_from_source_inventory" and not row.get("graph_spec"):
        return "build_source_inventory_claim_graph_candidate"
    if fresh_cg == 0.0 and fresh_rea == 0.0 and vote_reasons:
        if any("contradict" in reason.lower() or "inconsistent" in reason.lower() for reason in vote_reasons):
            return "prune_conflicting_background_leaf_then_rebuild_root"
        if any("mechanistic" in reason.lower() or "causal" in reason.lower() or "link" in reason.lower() for reason in vote_reasons):
            return "add_mechanistic_bridge_or_prune_causal_root"
        return "claim_graph_reconstruction_from_candidate_inventory"
    if any(item["entity_status"] in {"source_leaf_missing", "weak_graph_leaf", "source_tuple_mismatch"} for item in entity_rows):
        return "source_leaf_bridge_from_packet_top_sentence"
    if text_has_any(root_text, OVERCLAIM_MARKERS):
        return "minimal_root_commitment_prune"
    return "claim_graph_reconstruction_from_source_inventory"


def text_has_any(text: str, markers: list[str]) -> list[str]:
    lower = str(text).lower()
    return [marker for marker in markers if marker in lower]


def build_row(row: dict[str, Any], packet_roots: list[Path]) -> dict[str, Any]:
    graph = graph_from_row(row)
    packet_path, packet = packet_from_row(row, packet_roots)
    entities = packet_entities(packet)
    core_idea = packet_core_idea(packet)
    entity_support = packet_entity_evidence(packet)
    fresh = fresh_detail(row)
    eval_detail = fresh["eval_detail"]
    vote_detail = fresh["vote_detail"]
    missing = missing_entities_from_row(row, eval_detail, entities)
    source_nodes = graph.get("source_nodes") if isinstance(graph, dict) else []
    if not isinstance(source_nodes, list):
        source_nodes = []
    root_text = str(graph.get("root_text") or core_idea or "")
    judge_wrong_reasons = judge_reasons(vote_detail)

    entity_rows = []
    for entity in entities:
        graph_score, graph_matches = entity_graph_support(entity, source_nodes if isinstance(source_nodes, list) else [])
        packet_score, packet_matches = entity_packet_support(entity, entity_support)
        entity_rows.append(
            {
                "entity": entity,
                "is_fresh_missing": entity in missing or (bool(missing) and not row.get("missing_entities")),
                "graph_overlap": round(graph_score, 4),
                "packet_support_score": round(packet_score, 4),
                "entity_status": classify_entity(entity, graph_score, packet_score, graph_matches, packet_matches),
                "top_graph_matches": graph_matches,
                "top_packet_sentences": packet_matches,
            }
        )

    missing_status_counts = Counter(item["entity_status"] for item in entity_rows if item["is_fresh_missing"])
    all_status_counts = Counter(item["entity_status"] for item in entity_rows)
    action = closure_action(row, entity_rows, judge_wrong_reasons, root_text)
    root_markers = text_has_any(root_text, OVERCLAIM_MARKERS)
    background_leaf_count = sum(1 for node in source_nodes if text_has_any(str(node.get("text") or ""), BACKGROUND_MARKERS))
    candidate_sources = []
    for item in entity_rows:
        if item["entity_status"] in {"source_leaf_missing", "weak_graph_leaf", "source_tuple_mismatch", "packet_support_missing"}:
            for support in item["top_packet_sentences"][:2]:
                candidate_sources.append(
                    {
                        "entity": item["entity"],
                        "source": support.get("source", []),
                        "idx": support.get("idx", ""),
                        "score": support.get("score", 0.0),
                        "sentence": support.get("sentence", ""),
                        "viewpoints": support.get("viewpoints", []),
                    }
                )

    return {
        "paper_spec": row.get("paper_spec", ""),
        "model": row.get("model", ""),
        "paper": row.get("paper", ""),
        "controller_route": row.get("controller_route", ""),
        "controller_status": row.get("controller_status", ""),
        "candidate_label": row.get("candidate_label", ""),
        "fresh_CG": row.get("fresh_CG", ""),
        "fresh_REA": row.get("fresh_REA", ""),
        "fresh_judge_provider_error": row.get("fresh_judge_provider_error", ""),
        "passed_local_preflight": row.get("passed_local_preflight", ""),
        "row_ans_floor": row.get("row_ans_floor", ""),
        "packet": rel(packet_path) if packet_path else "",
        "graph_spec": graph.get("graph_spec", row.get("graph_spec", "")),
        "core_idea": core_idea,
        "root_text": root_text,
        "root_overclaim_markers": root_markers,
        "background_leaf_count": background_leaf_count,
        "entity_count": len(entities),
        "fresh_missing_entities": missing,
        "entity_status_counts": dict(all_status_counts),
        "missing_entity_status_counts": dict(missing_status_counts),
        "judge_wrong_reasons": judge_wrong_reasons,
        "recommended_action": action,
        "candidate_source_suggestions": candidate_sources[:12],
        "entity_rows": entity_rows,
        "acceptance_gates": {
            "fresh_CG": 1.0,
            "fresh_REA": 1.0,
            "judge_provider_error": False,
            "row_ANS": "non-regression",
            "batch_ANS": "at_or_above_current_300_floor",
        },
    }


def csv_projection(row: dict[str, Any]) -> dict[str, Any]:
    missing = row.get("fresh_missing_entities") or []
    source_suggestions = row.get("candidate_source_suggestions") or []
    return {
        "paper_spec": row.get("paper_spec", ""),
        "controller_route": row.get("controller_route", ""),
        "controller_status": row.get("controller_status", ""),
        "candidate_label": row.get("candidate_label", ""),
        "fresh_CG": row.get("fresh_CG", ""),
        "fresh_REA": row.get("fresh_REA", ""),
        "passed_local_preflight": row.get("passed_local_preflight", ""),
        "entity_count": row.get("entity_count", ""),
        "fresh_missing_entities": "; ".join(str(item) for item in missing),
        "entity_status_counts": json.dumps(row.get("entity_status_counts", {}), ensure_ascii=False, sort_keys=True),
        "missing_entity_status_counts": json.dumps(row.get("missing_entity_status_counts", {}), ensure_ascii=False, sort_keys=True),
        "root_overclaim_markers": "; ".join(str(item) for item in row.get("root_overclaim_markers", [])),
        "background_leaf_count": row.get("background_leaf_count", ""),
        "judge_wrong_reason_count": len(row.get("judge_wrong_reasons", [])),
        "recommended_action": row.get("recommended_action", ""),
        "candidate_source_suggestions": " | ".join(
            f"{item.get('entity')}->{source_key(item.get('source'))}" for item in source_suggestions[:6]
        ),
        "packet": row.get("packet", ""),
        "graph_spec": row.get("graph_spec", ""),
    }


def write_markdown(path: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# After327 Claim-Evidence Closure Solver",
        "",
        f"Created: `{summary['created_at']}`",
        "",
        "Provider-free diagnostic only. It does not call judges, write canonical accounting, or accept candidates.",
        "",
        "## Route Counts",
        "",
    ]
    for key, value in sorted(summary["route_counts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Recommended Actions", ""])
    for key, value in sorted(summary["recommended_action_counts"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Repair Queue", ""])
    lines.append("| Paper | Fresh | Route | Missing Entities | Recommended Action | Source Suggestions |")
    lines.append("|---|---:|---|---|---|---|")
    for row in rows:
        suggestions = ", ".join(
            f"{item.get('entity')}->{source_key(item.get('source'))}"
            for item in (row.get("candidate_source_suggestions") or [])[:4]
        )
        missing = ", ".join(row.get("fresh_missing_entities") or [])[:160]
        lines.append(
            f"| `{row['paper_spec']}` | `{row.get('fresh_CG')}/{row.get('fresh_REA')}` | "
            f"`{row.get('controller_route')}` | {missing} | `{row.get('recommended_action')}` | {suggestions} |"
        )
    lines.extend(["", "## Method Rules", ""])
    for item in METHOD_RULES:
        lines.append(f"- `{item['source']}`: {item['solver_rule']} {item['url']}")
    write_text(path, "\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-queue", default=str(DEFAULT_CONTROLLER_QUEUE))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--include-routes", default=",".join(sorted(TARGET_ROUTES)))
    parser.add_argument("--packet-root", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    controller_queue = resolve(args.controller_queue)
    out_root = resolve(args.out_root)
    packet_roots = [resolve(item) for item in args.packet_root] if args.packet_root else DEFAULT_PACKET_ROOTS
    controller_payload, controller_rows = load_controller_rows(controller_queue)
    include_routes = {item.strip() for item in str(args.include_routes).split(",") if item.strip()}
    # The solver is a candidate-generation queue, not a fresh-eval queue. Once a
    # row has a locally passing candidate, the controller owns it until fresh
    # eval/ANS guard returns.
    selected_rows = [
        row
        for row in controller_rows
        if str(row.get("controller_route") or "") in include_routes
        and str(row.get("controller_status") or "") == "needs_candidate_generation"
    ]
    rows = [build_row(row, packet_roots) for row in selected_rows]
    rows.sort(key=lambda item: (str(item.get("recommended_action")), str(item.get("paper_spec"))))
    summary_source = controller_payload.get("summary") if isinstance(controller_payload, dict) else {}
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "after327_claim_evidence_closure_solver",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_controller_queue": rel(controller_queue),
        "out_root": rel(out_root),
        "input_row_count": len(controller_rows),
        "solver_row_count": len(rows),
        "current_strict_success_rows": (summary_source or {}).get("current_strict_success_rows", ""),
        "current_typed_residual_rows": (summary_source or {}).get("current_typed_residual_rows", ""),
        "route_counts": dict(Counter(str(row.get("controller_route") or "") for row in rows)),
        "recommended_action_counts": dict(Counter(str(row.get("recommended_action") or "") for row in rows)),
        "method_rules": METHOD_RULES,
        "acceptance_contract": {
            "fresh_CG": 1.0,
            "fresh_REA": 1.0,
            "judge_provider_error": False,
            "row_ANS": "non-regression",
            "proposal_batch_ANS": "at_or_above_current_300_floor",
            "canonical_accounting_write": False,
        },
    }
    payload = {"summary": summary, "rows": rows}
    json_path = out_root / "AFTER327_CLAIM_EVIDENCE_CLOSURE_SOLVER.json"
    csv_path = out_root / "AFTER327_CLAIM_EVIDENCE_CLOSURE_SOLVER.csv"
    md_path = out_root / "AFTER327_CLAIM_EVIDENCE_CLOSURE_SOLVER.md"
    write_json(json_path, payload)
    fieldnames = [
        "paper_spec",
        "controller_route",
        "controller_status",
        "candidate_label",
        "fresh_CG",
        "fresh_REA",
        "passed_local_preflight",
        "entity_count",
        "fresh_missing_entities",
        "entity_status_counts",
        "missing_entity_status_counts",
        "root_overclaim_markers",
        "background_leaf_count",
        "judge_wrong_reason_count",
        "recommended_action",
        "candidate_source_suggestions",
        "packet",
        "graph_spec",
    ]
    write_csv(csv_path, [csv_projection(row) for row in rows], fieldnames)
    write_markdown(md_path, rows, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
