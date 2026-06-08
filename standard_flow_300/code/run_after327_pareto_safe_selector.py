#!/usr/bin/env python3
"""Build provider-free Pareto-safe rollback/hybrid candidates for after327 rows.

The after327 metric-regression tail is not a broad regeneration problem:
terminal graphs already preserve REA, while larger rollback/frontier graphs
often improve CG at the cost of REA. This selector builds deterministic
candidate portfolios from source evidence, terminal graphs, and input-data
anchors, then keeps only locally preflightable and ANS-non-regressing rows for
fresh evaluation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
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
FRAMEWORK_DIR = PROJECT_ROOT / "operation_records" / "restructured"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(FRAMEWORK_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from run_evidence_bound_graph_spec_regeneration import (  # type: ignore  # noqa: E402
    entity_coverage_audit,
    preflight_graph_spec,
    token_variant_index,
)
from run_ans_safe_hybrid_batch_candidates import (  # type: ignore  # noqa: E402
    add_leaf_nodes,
    build_source_seed_graph,
    graph_json_from_dot,
    input_data_path,
    load_ans_stage_rows,
    missing_entities,
    read_eval_cache,
    select_compact_entity_bridge_sentences,
    select_step2_nodes,
    step2_graph_path,
    terminal_graph_path,
)


DEFAULT_CONTROLLER_QUEUE = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_after327_failure_typed_lit_diagnostic"
    / "controller_queue_v1"
    / "AFTER327_CONTROLLER_QUEUE.json"
)
DEFAULT_ACCOUNTING = (
    RESIDUAL_ROOT
    / "proposed_accounting_merges"
    / "20260606_after_327_gemini56921_node5repair_anssafe"
    / "PROPOSED_TYPED_RESIDUAL_350.csv"
)
DEFAULT_V2_LEDGER = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_residual_closeout_v2"
    / "V2_RESIDUAL_LEDGER.csv"
)
DEFAULT_QUEUE = RESIDUAL_ROOT / "RESIDUAL_50_CLOSEOUT_QUEUE.csv"
DEFAULT_ANS_NODE_RESULTS = PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_node_results.jsonl"
DEFAULT_ANS_CACHE = PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_eval_cache.jsonl"
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "pareto_safe_after327_selector_20260606"
)

LITERATURE_DESIGN_MAP = [
    {
        "source": "FActScore",
        "url": "https://arxiv.org/abs/2305.14251",
        "selector_rule": "Keep repair decisions at atomic claim/source-leaf granularity.",
    },
    {
        "source": "SAFE / LongFact",
        "url": "https://arxiv.org/abs/2403.18802",
        "selector_rule": "Treat local source support as a filter; fresh CG/REA remains authoritative.",
    },
    {
        "source": "RefChecker",
        "url": "https://arxiv.org/abs/2405.14486",
        "selector_rule": "Prefer structured claim/entity support over whole-graph rewrites.",
    },
    {
        "source": "RAGChecker / RAGAS",
        "url": "https://arxiv.org/abs/2408.08067",
        "selector_rule": "Separate coverage/retrieval gaps from reasoning-generation regressions.",
    },
    {
        "source": "RARR / FAVA",
        "url": "https://arxiv.org/abs/2210.08726",
        "selector_rule": "Revise unsupported or missing content minimally while preserving REA-safe topology.",
    },
    {
        "source": "Importance-aware factual recall",
        "url": "https://arxiv.org/abs/2604.03141",
        "selector_rule": "Do not accept factual precision if important anchor entities are omitted.",
    },
    {
        "source": "Cited but Not Verified",
        "url": "https://arxiv.org/abs/2605.06635",
        "selector_rule": "A source tuple must support the exact commitment, not merely be topical.",
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
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    out = re.sub(r"_+", "_", out).strip("_")
    return (out or "item")[:limit]


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


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def compact_text(value: Any, *, max_chars: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"\[[^\]]+\]", "", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def source_tuple(value: Any) -> list[int]:
    if isinstance(value, list):
        padded = list(value) + [0, 0, 0]
        out: list[int] = []
        for item in padded[:3]:
            try:
                out.append(max(0, int(item)))
            except (TypeError, ValueError):
                out.append(0)
        return out
    return [0, 0, 0]


def entity_match_score(text: str, entity: str) -> float:
    text_tokens = token_variant_index(text)
    entity_text = str(entity or "")
    entity_tokens = token_variant_index(entity_text)
    if not entity_text or not entity_tokens:
        return 0.0
    score = len(text_tokens & entity_tokens) / len(entity_tokens)
    # Morphological families such as irradiation/irradiating share the same
    # long stem but can have several generated variants. A stem hit is a strong
    # retrieval cue even when exact token recall looks artificially low.
    stem_hits = 0
    for entity_token in entity_tokens:
        if len(entity_token) < 6:
            continue
        if any(
            entity_token.startswith(text_token[:6]) or text_token.startswith(entity_token[:6])
            for text_token in text_tokens
            if len(text_token) >= 6
        ):
            stem_hits += 1
    if stem_hits:
        score += min(1.0, stem_hits / max(1, len(entity_tokens)))
    if entity_text.lower() in text.lower():
        score += 2.0
    return score


def overlap_score(text: str, entities: list[str]) -> float:
    text_tokens = token_variant_index(text)
    if not text_tokens:
        return 0.0
    score = 0.0
    text_lower = text.lower()
    for entity in entities:
        entity_tokens = token_variant_index(entity)
        if str(entity).lower() in text_lower:
            score += 2.0
        if entity_tokens:
            score += len(text_tokens & entity_tokens) / len(entity_tokens)
    return score


def sentence_text(sentence: dict[str, Any]) -> str:
    sent = str(sentence.get("sentence") or "").strip()
    viewpoints = [str(v).strip() for v in sentence.get("viewpoints") or [] if str(v).strip()]
    if viewpoints:
        return f"{sent} Viewpoints: {' | '.join(viewpoints[:4])}".strip()
    return sent


def source_text_from_input(input_data: dict[str, Any], source: list[int]) -> str:
    x, y, z = source_tuple(source)
    for sentence in (input_data.get("introduction") or {}).get("sentences") or []:
        if not isinstance(sentence, dict) or int(sentence.get("idx") or 0) != x:
            continue
        if y == 0 and z == 0:
            return str(sentence.get("sentence") or "")
        viewpoints = sentence.get("viewpoints") or []
        if z == 0 and y > 0 and len(viewpoints) >= y:
            return str(viewpoints[y - 1])
    return ""


def infer_model_paper(paper_spec: str) -> tuple[str, str]:
    if ":" in paper_spec:
        return tuple(paper_spec.split(":", 1))  # type: ignore[return-value]
    return "", paper_spec


def load_local_build_candidate_packet() -> Any:
    helper_path = CODE_DIR / "build_ans_claims_for_strict_merge_candidates.py"
    spec = importlib.util.spec_from_file_location("local_build_ans_claims_for_strict_merge_candidates", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_candidate_packet


def estimate_cached_ans(claim_packet: dict[str, Any], cache: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    from evaluate_ans_factscore_style_350 import (  # type: ignore  # noqa: PLC0415
        clean_text,
        eval_key,
        evidence_corpus,
        load_input_data,
        retrieve_evidence,
    )

    atomic = 0
    supported = 0
    cache_hits = 0
    cache_misses = 0
    rows = []
    for row in claim_packet.get("claims") or []:
        ans_evidence = row.get("evidence_text") or ""
        input_path = str(row.get("input_data_path") or "")
        if input_path:
            input_data = load_input_data(input_path)
            ans_evidence = retrieve_evidence(row, evidence_corpus(input_data), top_k=4, max_chars=1800)
        eval_row = dict(row)
        eval_row["ans_evidence"] = ans_evidence
        facts = cache.get(eval_key(eval_row))
        if facts is None:
            cache_misses += 1
            rows.append(
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
        rows.append(
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
        "rows": rows,
    }


build_candidate_packet_for_ans = load_local_build_candidate_packet()


def ans_claim_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        window=args.window,
        root_window=args.root_window,
        traversal_depth=args.traversal_depth,
        max_evidence_sentences=args.max_evidence_sentences,
        max_evidence_chars=args.max_evidence_chars,
        include_viewpoints=args.include_viewpoints,
        graph_ordered_evidence=args.graph_ordered_evidence,
    )


def candidate_ans_packet(
    *,
    row: dict[str, Any],
    graph_dot: Path,
    staged_dir: Path,
    label: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    candidate_row = {
        "paper_spec": row.get("paper_spec"),
        "model": row.get("model"),
        "candidate_label": label,
        "candidate_graph": rel(graph_dot),
        "candidate_eval_dir": rel(staged_dir),
        "candidate_fresh_eval_results": "",
    }
    return build_candidate_packet_for_ans(candidate_row, ans_claim_args(args))


def packet_path(ledger_row: dict[str, str]) -> Path:
    path = resolve_path(ledger_row.get("packet_json") or "")
    if not path.exists():
        raise FileNotFoundError(f"packet not found: {path}")
    return path


def accounting_row_input(row: dict[str, str], ledger_row: dict[str, str]) -> Path:
    candidate = input_data_path(row, ledger_row)
    if candidate.exists():
        return candidate
    value = ledger_row.get("input_data") or ""
    if value:
        resolved = resolve_path(value)
        if resolved.exists():
            return resolved
    raise FileNotFoundError(f"input_data not found for {row.get('paper_spec')}")


def dedupe_leaves(leaves: list[dict[str, Any]], *, by_entity: bool) -> list[dict[str, Any]]:
    out = []
    seen: set[tuple[Any, ...]] = set()
    for leaf in leaves:
        source = tuple(source_tuple(leaf.get("source")))
        entity = str(leaf.get("entity") or leaf.get("matched_entity") or "")
        key = (entity, source) if by_entity else source
        if key in seen:
            continue
        seen.add(key)
        out.append(leaf)
    return out


def entity_inventory_leaves(
    packet: dict[str, Any],
    input_data: dict[str, Any],
    *,
    max_per_entity: int,
    min_entity_score: float,
) -> list[dict[str, Any]]:
    entities = [str(entity) for entity in (packet.get("paper_anchor") or {}).get("entities") or [] if str(entity)]
    evidence_by_entity: dict[str, list[dict[str, Any]]] = {}
    for item in (packet.get("evidence") or {}).get("entity_evidence") or []:
        if isinstance(item, dict) and item.get("entity"):
            evidence_by_entity[str(item["entity"])] = [row for row in item.get("top_sentences") or [] if isinstance(row, dict)]

    sentence_rows = []
    for sentence in (input_data.get("introduction") or {}).get("sentences") or []:
        if not isinstance(sentence, dict):
            continue
        idx = int(sentence.get("idx") or 0)
        if not idx:
            continue
        sentence_rows.append({"idx": idx, "source": [idx, 0, 0], "text": sentence_text(sentence)})

    leaves: list[dict[str, Any]] = []
    for entity in entities:
        ranked = []
        for row in evidence_by_entity.get(entity, []):
            src = source_tuple(row.get("source") or [row.get("idx") or 0, 0, 0])
            source_text = source_text_from_input(input_data, src) or str(row.get("sentence") or "")
            score = entity_match_score(source_text, entity)
            if score <= 0:
                continue
            ranked.append(
                (
                    score + float(row.get("score") or 0.0) / 10.0 + (1.0 if row.get("exact_phrase_match") else 0.0),
                    src[0],
                    {
                        "entity": entity,
                        "source": src,
                        "text": compact_text(f"{entity}: {source_text}", max_chars=300).rstrip(".; ") + ".",
                        "score": score,
                        "source_kind": "packet_entity_evidence",
                    },
                )
            )
        for sentence in sentence_rows:
            score = entity_match_score(str(sentence["text"]), entity)
            if score >= min_entity_score:
                # Sentence-level exact matches are often better fresh-CG anchors
                # than broad packet matches whose citation is topical but does
                # not contain the committed entity.
                ranked.append(
                    (
                        score + 0.25,
                        int(sentence["idx"]),
                        {
                            "entity": entity,
                            "source": sentence["source"],
                            "text": compact_text(f"{entity}: {sentence['text']}", max_chars=300).rstrip(".; ") + ".",
                            "score": score,
                            "source_kind": "input_sentence_entity_match",
                        },
                    )
                )
        ranked.sort(key=lambda item: (-float(item[0]), int(item[1])))
        for _score, _idx, leaf in ranked[:max_per_entity]:
            leaves.append(leaf)
    return dedupe_leaves(leaves, by_entity=True)


def rank_candidate(row: dict[str, Any]) -> tuple[int, int, int, float, int, str]:
    def number(value: Any, default: float = 0.0) -> float:
        try:
            if value in ("", None):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    source_kind = str(row.get("source_kind") or "")
    if "step2" in source_kind:
        source_rank = 0
    elif source_kind.startswith("source_inventory"):
        source_rank = 1
    elif "source_seed" in source_kind:
        source_rank = 2
    else:
        source_rank = 3
    return (
        0 if row.get("passed_local_preflight") is True else 1,
        0 if row.get("cached_ans_passes_row_floor") is True else 1,
        int(row.get("cache_misses") or 999999),
        -number(row.get("cached_ans"), -1.0),
        source_rank,
        str(row.get("candidate_label") or ""),
    )


def top_ready_rows(rows: list[dict[str, Any]], *, per_paper: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    by_spec: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("fresh_eval_candidate") is not True:
            continue
        by_spec.setdefault(str(row.get("paper_spec") or ""), []).append(row)
    for spec in sorted(by_spec):
        ranked = sorted(by_spec[spec], key=rank_candidate)
        for rank, row in enumerate(ranked[:per_paper], start=1):
            out = dict(row)
            out["selector_rank"] = rank
            out["selector_rank_reason"] = "preflight,cached_ans,row_floor,cache_misses,source_kind"
            selected.append(out)
    return selected


def attach_selector_ranks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_spec: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_spec.setdefault(str(row.get("paper_spec") or ""), []).append(row)
    ranked_lookup: dict[tuple[str, str], int] = {}
    for spec, items in by_spec.items():
        for rank, row in enumerate(sorted(items, key=rank_candidate), start=1):
            ranked_lookup[(spec, str(row.get("candidate_label") or ""))] = rank
    out = []
    for row in rows:
        copy = dict(row)
        copy["selector_rank"] = ranked_lookup.get((str(row.get("paper_spec") or ""), str(row.get("candidate_label") or "")), "")
        copy["selector_rank_reason"] = "preflight,cached_ans,row_floor,cache_misses,source_kind"
        out.append(copy)
    return out


def build_inventory_graph(
    *,
    paper_spec: str,
    packet: dict[str, Any],
    input_data: dict[str, Any],
    root_mode: str,
    max_per_entity: int,
    min_entity_score: float,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    leaves = entity_inventory_leaves(
        packet,
        input_data,
        max_per_entity=max_per_entity,
        min_entity_score=min_entity_score,
    )
    if not leaves:
        return None, []
    entities = [str(entity) for entity in (packet.get("paper_anchor") or {}).get("entities") or [] if str(entity)]
    if root_mode == "core":
        root_text = compact_text((packet.get("paper_anchor") or {}).get("core_idea") or "", max_chars=520)
    elif root_mode == "compact":
        root_text = "; ".join(compact_text(leaf.get("text") or "", max_chars=95).rstrip(".; ") for leaf in leaves[: min(5, len(leaves))])
    else:
        ordered = sorted(leaves, key=lambda leaf: (entities.index(str(leaf.get("entity"))) if str(leaf.get("entity")) in entities else 999))
        root_text = "; ".join(compact_text(leaf.get("text") or "", max_chars=130).rstrip(".; ") for leaf in ordered[: len(entities)])
    root_text = root_text.rstrip(".; ") + "."

    nodes: list[dict[str, Any]] = [{"id": "NROOT", "source": [0, 0, 0], "text": root_text}]
    edges: list[dict[str, str]] = []
    actions: list[dict[str, Any]] = []
    for idx, leaf in enumerate(leaves, start=1):
        node_id = f"PARETO_SRC_{idx}"
        nodes.append({"id": node_id, "source": source_tuple(leaf.get("source")), "text": str(leaf.get("text") or "")})
        edge_type = "induction-common" if idx == 1 else "induction-case"
        edges.append({"source": node_id, "target": "NROOT", "type": edge_type})
        actions.append(
            {
                "action": "inventory_source_leaf",
                "node_id": node_id,
                "entity": leaf.get("entity", ""),
                "source_tuple": source_tuple(leaf.get("source")),
                "source_kind": leaf.get("source_kind", ""),
                "edge_type": edge_type,
                "root_mode": root_mode,
            }
        )
    return {"root": "NROOT", "paper_id": paper_spec, "nodes": nodes, "edges": edges}, actions


def build_terminal_plus_inventory(
    *,
    terminal: dict[str, Any],
    packet: dict[str, Any],
    input_data: dict[str, Any],
    label: str,
    max_per_entity: int,
    min_entity_score: float,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    leaves = entity_inventory_leaves(
        packet,
        input_data,
        max_per_entity=max_per_entity,
        min_entity_score=min_entity_score,
    )
    if not leaves:
        return None, []
    candidate, actions = add_leaf_nodes(terminal, leaves, label=label, source_kind="pareto_inventory_leaf")
    return candidate, actions


def clone_candidate_graph(label: str, source_graph: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = deepcopy(source_graph)
    return spec, [{"action": "clone_graph_as_candidate", "candidate_label": label}]


def load_graph_for_dot(dot_path: Path) -> dict[str, Any]:
    return read_json(graph_json_from_dot(dot_path), {})


def write_candidate(
    *,
    out_root: Path,
    row: dict[str, Any],
    packet_file: Path,
    input_file: Path,
    spec: dict[str, Any],
    label: str,
    actions: list[dict[str, Any]],
    preflight: dict[str, Any],
    cache: dict[str, list[dict[str, Any]]],
    row_floor: float,
    source_kind: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    paper_spec = str(row.get("paper_spec") or "")
    candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(label)
    staged_dir = out_root / "staged" / safe_slug(paper_spec) / safe_slug(label)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    staged_dir.mkdir(parents=True, exist_ok=True)
    graph_json = candidate_dir / "graph_spec.json"
    graph_dot = candidate_dir / "final_clean_graph.dot"
    write_json(graph_json, spec)
    write_text(graph_dot, graph_spec_to_dot(spec))
    shutil.copy2(graph_json, staged_dir / "graph_spec.json")
    shutil.copy2(graph_dot, staged_dir / "final_clean_graph.dot")
    shutil.copy2(input_file, staged_dir / "input_data.json")
    write_json(
        staged_dir / "evidence_bound_packet_pointer.json",
        {"packet": rel(packet_file), "source": label, "source_kind": source_kind},
    )

    claim_packet = candidate_ans_packet(row=row, graph_dot=graph_dot, staged_dir=staged_dir, label=label, args=args)
    cached_ans = estimate_cached_ans(claim_packet, cache)
    claims_dir = out_root / "ans_claims" / safe_slug(paper_spec) / safe_slug(label)
    claims_dir.mkdir(parents=True, exist_ok=True)
    claims_path = claims_dir / "claims_input.jsonl"
    with claims_path.open("w", encoding="utf-8") as handle:
        for claim in claim_packet.get("claims") or []:
            handle.write(json.dumps({k: v for k, v in claim.items() if k != "evidence_items"}, ensure_ascii=False) + "\n")
    write_json(claims_dir / "claim_packets.json", [claim_packet])
    write_csv(
        candidate_dir / "CACHED_ANS_NODE_ESTIMATE.csv",
        cached_ans["rows"],
        ["node_id", "unit_type", "cache_hit", "atomic_fact_count", "supported_fact_count", "node_text"],
    )

    preflight_path = candidate_dir / "preflight_report.json"
    write_json(
        preflight_path,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "after327_pareto_safe_selector",
            "paper_spec": paper_spec,
            "candidate_label": label,
            "source_kind": source_kind,
            "actions": actions,
            **preflight,
        },
    )

    cached_value = cached_ans.get("cached_ans")
    cache_misses = int(cached_ans.get("cache_misses") or 0)
    cached_passes = bool(cached_value is not None and cached_value + 1e-12 >= row_floor)
    if cached_value is None:
        ans_gate_status = "formal_ans_required_no_cached_hits"
    elif cached_passes:
        ans_gate_status = "cached_ans_passes_row_floor"
    elif cache_misses > 0:
        ans_gate_status = "formal_ans_required_cache_incomplete"
    else:
        ans_gate_status = "cached_ans_fails_row_floor"

    coverage = preflight.get("entity_coverage") or {}
    support = preflight.get("immediate_premise_support") or {}
    preflight_passed = bool(preflight.get("passed_local_preflight"))
    fresh_candidate = bool(preflight_passed and (cached_passes or cache_misses > 0))
    return {
        "priority": row.get("priority", ""),
        "paper_spec": paper_spec,
        "model": row.get("model"),
        "paper": row.get("paper"),
        "failure_type": row.get("failure_type"),
        "selector_route": "pareto_safe_rollback_or_hybrid_selector",
        "source_kind": source_kind,
        "candidate_label": label,
        "passed_local_preflight": preflight_passed,
        "covered_entities_local": coverage.get("covered_entities", ""),
        "total_entities_local": coverage.get("total_entities", ""),
        "coverage_rate_local": coverage.get("coverage_rate_local", ""),
        "premise_support_high_risk_count": support.get("high_risk_count", ""),
        "row_ans_floor": row_floor,
        "cached_ans": cached_value,
        "cached_ans_passes_row_floor": cached_passes,
        "ans_gate_status": ans_gate_status,
        "formal_ans_required": ans_gate_status.startswith("formal_ans_required"),
        "cache_hits": cached_ans.get("cache_hits"),
        "cache_misses": cache_misses,
        "fresh_eval_candidate": fresh_candidate,
        "graph_spec": rel(graph_json),
        "dot": rel(graph_dot),
        "staged_run_dir": rel(staged_dir),
        "preflight_report": rel(preflight_path),
        "claims_input": rel(claims_path),
        "final_clean_graph_sha256": sha256_file(graph_dot),
        "input_data": rel(staged_dir / "input_data.json"),
        "packet": rel(packet_file),
        "actions": actions,
    }


def build_candidates_for_row(
    *,
    row: dict[str, Any],
    accounting_row: dict[str, str],
    ledger_row: dict[str, str],
    queue_row: dict[str, str],
    ans_rows: dict[str, dict[str, dict[str, Any]]],
    cache: dict[str, list[dict[str, Any]]],
    out_root: Path,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    paper_spec = str(row.get("paper_spec") or "")
    packet_file = packet_path(ledger_row)
    packet = read_json(packet_file, {})
    terminal_dot = terminal_graph_path(accounting_row)
    terminal = load_graph_for_dot(terminal_dot)
    input_file = accounting_row_input(accounting_row, ledger_row)
    input_data = read_json(input_file, {})
    row_floor = to_float(queue_row.get("current_best_main_factual_ans") or ledger_row.get("current_best_main_factual_ans"))

    work_row = dict(accounting_row)
    work_row.update({"paper_spec": paper_spec, "model": row.get("model"), "paper": row.get("paper")})

    raw_candidates: list[tuple[str, str, dict[str, Any], list[dict[str, Any]]]] = []
    for mode in args.inventory_root_mode:
        spec, actions = build_inventory_graph(
            paper_spec=paper_spec,
            packet=packet,
            input_data=input_data,
            root_mode=mode,
            max_per_entity=args.max_per_entity,
            min_entity_score=args.min_entity_score,
        )
        if spec:
            label = f"after327-pareto-inventory-{mode}-v1-{safe_slug(paper_spec, limit=60)}"
            for action in actions:
                action["candidate_label"] = label
            raw_candidates.append((label, f"source_inventory_{mode}", spec, actions))

    label = f"after327-pareto-terminal-plus-inventory-v1-{safe_slug(paper_spec, limit=60)}"
    spec, actions = build_terminal_plus_inventory(
        terminal=terminal,
        packet=packet,
        input_data=input_data,
        label=label,
        max_per_entity=args.max_per_entity,
        min_entity_score=args.min_entity_score,
    )
    if spec:
        raw_candidates.append((label, "terminal_plus_inventory", spec, actions))

    step2 = step2_graph_path(accounting_row)
    if step2 and step2.exists():
        leaves = select_step2_nodes(
            paper_spec=paper_spec,
            step2=read_json(step2, {}),
            ans_rows=ans_rows,
            missing=missing_entities(packet, terminal)
            or [str(entity) for entity in (packet.get("paper_anchor") or {}).get("entities") or []],
            max_nodes=args.max_step2_nodes,
            min_ratio=args.min_step2_support_ratio,
        )
        if leaves:
            label = f"after327-pareto-terminal-plus-step2-v1-{safe_slug(paper_spec, limit=60)}"
            spec, actions = add_leaf_nodes(terminal, leaves, label=label, source_kind="step2_source_leaf")
            raw_candidates.append((label, "terminal_plus_step2", spec, actions))

    bridge_leaves = select_compact_entity_bridge_sentences(
        input_data,
        missing_entities(packet, terminal)
        or [str(entity) for entity in (packet.get("paper_anchor") or {}).get("entities") or []],
        args.max_bridge_nodes,
        min_entity_score=args.min_entity_score,
    )
    if bridge_leaves:
        label = f"after327-pareto-terminal-plus-compact-bridge-v1-{safe_slug(paper_spec, limit=60)}"
        spec, actions = add_leaf_nodes(terminal, bridge_leaves, label=label, source_kind="compact_entity_bridge")
        raw_candidates.append((label, "terminal_plus_compact_bridge", spec, actions))

    if args.include_source_seed:
        for mode in ["inventory", "inventory_priority"]:
            spec, actions = build_source_seed_graph(
                paper_spec=paper_spec,
                packet=packet,
                evidence_limit=args.max_source_seed_entities,
                root_mode=mode,
            )
            if spec:
                label = f"after327-pareto-source-seed-{mode}-v1-{safe_slug(paper_spec, limit=60)}"
                for action in actions:
                    action["candidate_label"] = label
                raw_candidates.append((label, f"legacy_source_seed_{mode}", spec, actions))

    rows: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for label, source_kind, spec, actions in raw_candidates:
        preflight = preflight_graph_spec(spec, packet)
        fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if fingerprint in seen_hashes:
            continue
        seen_hashes.add(fingerprint)
        rows.append(
            write_candidate(
                out_root=out_root,
                row=work_row,
                packet_file=packet_file,
                input_file=input_file,
                spec=spec,
                label=label,
                actions=actions,
                preflight=preflight,
                cache=cache,
                row_floor=row_floor,
                source_kind=source_kind,
                args=args,
            )
        )
    return rows


def controller_targets(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path, {})
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return [
        row
        for row in rows or []
        if isinstance(row, dict)
        and row.get("controller_route") == "pareto_safe_rollback_or_hybrid_selector"
        and row.get("controller_status") == "needs_candidate_generation"
    ]


def metric_regression_targets(accounting: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec, row in sorted(accounting.items()):
        if row.get("current_outcome") != "typed_residual":
            continue
        if row.get("failure_type") != "metric_regression":
            continue
        rows.append(
            {
                "paper_spec": spec,
                "model": row.get("model", ""),
                "paper": row.get("paper", ""),
                "failure_type": row.get("failure_type", ""),
                "priority": row.get("priority", ""),
            }
        )
    return rows


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# After327 Pareto-Safe Selector",
        "",
        f"Created: `{report['created_at']}`",
        f"Provider calls: `{report['provider_calls']}`",
        f"Canonical accounting write: `{report['canonical_accounting_write']}`",
        f"Rows considered: `{report['rows_considered']}`",
        f"Candidates built: `{report['candidate_count']}`",
        f"Fresh-eval ready: `{report['fresh_eval_candidate_count']}`",
        f"Fresh-eval top queue: `{report.get('fresh_eval_top_candidate_count', 0)}`",
        "",
        "| paper_spec | candidate | source | preflight | coverage | cached ANS | row floor | fresh-ready |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in report["rows"]:
        lines.append(
            "| {paper_spec} | {candidate_label} | {source_kind} | `{passed_local_preflight}` | "
            "`{covered_entities_local}/{total_entities_local}` | `{cached_ans}` | `{row_ans_floor}` | `{fresh_eval_candidate}` |".format(
                **row
            )
        )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-queue", default=str(DEFAULT_CONTROLLER_QUEUE))
    parser.add_argument("--accounting", default=str(DEFAULT_ACCOUNTING))
    parser.add_argument("--v2-ledger", default=str(DEFAULT_V2_LEDGER))
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--ans-node-results", default=str(DEFAULT_ANS_NODE_RESULTS))
    parser.add_argument("--ans-cache", default=str(DEFAULT_ANS_CACHE))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--paper-spec", action="append", default=[])
    parser.add_argument("--inventory-root-mode", action="append", default=["inventory", "core"])
    parser.add_argument("--max-per-entity", type=int, default=1)
    parser.add_argument("--min-entity-score", type=float, default=0.55)
    parser.add_argument("--max-step2-nodes", type=int, default=8)
    parser.add_argument("--min-step2-support-ratio", type=float, default=1.0)
    parser.add_argument("--max-bridge-nodes", type=int, default=12)
    parser.add_argument("--include-source-seed", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-source-seed-entities", type=int, default=14)
    parser.add_argument("--top-per-paper", type=int, default=2)
    parser.add_argument("--window", type=int, default=1)
    parser.add_argument("--root-window", type=int, default=None)
    parser.add_argument("--traversal-depth", type=int, default=2)
    parser.add_argument("--max-evidence-sentences", type=int, default=10)
    parser.add_argument("--max-evidence-chars", type=int, default=1800)
    parser.add_argument("--include-viewpoints", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--graph-ordered-evidence", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_root = resolve_path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    accounting = {row.get("paper_spec", ""): row for row in read_csv(resolve_path(args.accounting))}
    targets = metric_regression_targets(accounting)
    if not targets:
        targets = controller_targets(resolve_path(args.controller_queue))
    paper_specs = set(args.paper_spec or [])
    if paper_specs:
        targets = [row for row in targets if row.get("paper_spec") in paper_specs]

    ledger = {row.get("paper_spec", ""): row for row in read_csv(resolve_path(args.v2_ledger))}
    queue = {row.get("paper_spec", ""): row for row in read_csv(resolve_path(args.queue))}
    ans_rows = load_ans_stage_rows(resolve_path(args.ans_node_results))
    cache = read_eval_cache(resolve_path(args.ans_cache))

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in targets:
        spec = str(row.get("paper_spec") or "")
        try:
            if spec not in accounting:
                raise RuntimeError("missing accounting row")
            if spec not in ledger:
                raise RuntimeError("missing V2 ledger row")
            if spec not in queue:
                raise RuntimeError("missing residual queue row")
            rows.extend(
                build_candidates_for_row(
                    row=row,
                    accounting_row=accounting[spec],
                    ledger_row=ledger[spec],
                    queue_row=queue[spec],
                    ans_rows=ans_rows,
                    cache=cache,
                    out_root=out_root,
                    args=args,
                )
            )
        except Exception as exc:  # noqa: BLE001
            skipped.append({"paper_spec": spec, "reason": f"{type(exc).__name__}: {exc}"})

    fieldnames = [
        "selector_rank",
        "selector_rank_reason",
        "priority",
        "paper_spec",
        "model",
        "paper",
        "failure_type",
        "selector_route",
        "source_kind",
        "candidate_label",
        "passed_local_preflight",
        "covered_entities_local",
        "total_entities_local",
        "coverage_rate_local",
        "premise_support_high_risk_count",
        "row_ans_floor",
        "cached_ans",
        "cached_ans_passes_row_floor",
        "ans_gate_status",
        "formal_ans_required",
        "cache_hits",
        "cache_misses",
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
    rows = attach_selector_ranks(rows)
    ready_rows = [row for row in rows if row.get("fresh_eval_candidate") is True]
    top_rows = top_ready_rows(rows, per_paper=args.top_per_paper)
    write_csv(out_root / "ATTEMPT_INDEX.csv", rows, fieldnames)
    write_json(out_root / "ATTEMPT_INDEX.json", {"rows": rows})
    write_csv(out_root / "FRESH_EVAL_READY_QUEUE.csv", ready_rows, fieldnames)
    write_csv(out_root / "FRESH_EVAL_TOP_QUEUE.csv", top_rows, fieldnames)
    write_csv(out_root / "SKIPPED_ROWS.csv", skipped, ["paper_spec", "reason"])
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "after327_pareto_safe_selector",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_controller_queue": rel(resolve_path(args.controller_queue)),
        "source_accounting": rel(resolve_path(args.accounting)),
        "out_root": rel(out_root),
        "rows_considered": len(targets),
        "candidate_count": len(rows),
        "fresh_eval_candidate_count": len(ready_rows),
        "fresh_eval_top_candidate_count": len(top_rows),
        "skipped_count": len(skipped),
        "literature_design_map": LITERATURE_DESIGN_MAP,
        "acceptance_contract": {
            "fresh_CG": 1.0,
            "fresh_REA": 1.0,
            "judge_provider_error": False,
            "row_ANS": "non_regression",
            "proposal_batch_ANS": "at_or_above_current_300_floor",
        },
        "rows": rows,
        "top_rows": top_rows,
        "skipped": skipped,
    }
    write_json(out_root / "PARETO_SAFE_SELECTOR_SUMMARY.json", report)
    write_text(out_root / "PARETO_SAFE_SELECTOR_SUMMARY.md", markdown(report))
    print(
        json.dumps(
            {
                "out_root": report["out_root"],
                "rows_considered": report["rows_considered"],
                "candidate_count": report["candidate_count"],
                "fresh_eval_candidate_count": report["fresh_eval_candidate_count"],
                "fresh_eval_top_candidate_count": report["fresh_eval_top_candidate_count"],
                "skipped_count": report["skipped_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
