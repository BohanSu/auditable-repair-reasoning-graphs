#!/usr/bin/env python3
"""Build provider-free ANS-safe hybrid candidates for residual closeout rows.

This batch generator is a conservative expansion of the single-row
`materialize_ans_safe_step2_hybrid_candidate.py` pilot. It reads the current
proposed accounting, residual queue, V2 ledger, terminal graphs, step2 graphs,
and paper input data. It then emits candidate graphs plus local preflight and
cached ANS estimates only. Fresh CG/REA evaluation remains a separate gate.
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
from evaluate_ans_factscore_style_350 import (  # type: ignore  # noqa: E402
    clean_text,
    eval_key,
    evidence_corpus,
    load_input_data,
    retrieve_evidence,
)


DEFAULT_ACCOUNTING = (
    RESIDUAL_ROOT
    / "proposed_accounting_merges"
    / "20260606_after_315_gemini56657_entity_complete_anssafe"
    / "PROPOSED_FULL_350_ACCOUNTING.csv"
)
DEFAULT_QUEUE = RESIDUAL_ROOT / "RESIDUAL_50_CLOSEOUT_QUEUE.csv"
DEFAULT_V2_LEDGER = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_residual_closeout_v2"
    / "V2_RESIDUAL_LEDGER.csv"
)
DEFAULT_ANS_NODE_RESULTS = PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_node_results.jsonl"
DEFAULT_ANS_CACHE = PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_eval_cache.jsonl"
DEFAULT_OUT_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "ans_safe_hybrid_batch"
    / "20260606_source_seed_state_machine_v1"
)
TARGET_FAILURES = {"final_metric_gate_failed", "metric_regression", "preflight:no_anchor_regenerate"}
SOURCE_UNIT_TYPES = {"explicit_source_node"}


def load_local_build_candidate_packet() -> Any:
    helper_path = CODE_DIR / "build_ans_claims_for_strict_merge_candidates.py"
    spec = importlib.util.spec_from_file_location("local_build_ans_claims_for_strict_merge_candidates", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_candidate_packet


build_candidate_packet = load_local_build_candidate_packet()


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


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def graph_nodes(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(node.get("id")): node for node in spec.get("nodes") or [] if isinstance(node, dict) and node.get("id")}


def graph_edges(spec: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (str(edge.get("source")), str(edge.get("target")), str(edge.get("type")))
        for edge in spec.get("edges") or []
        if isinstance(edge, dict)
    }


def graph_json_from_dot(dot_path: Path) -> Path:
    sibling = dot_path.with_suffix(".json")
    if sibling.exists():
        return sibling
    graph_spec = dot_path.parent / "graph_spec.json"
    if graph_spec.exists():
        return graph_spec
    raise FileNotFoundError(f"graph json not found for {dot_path}")


def terminal_graph_path(row: dict[str, str]) -> Path:
    for key in ["final_graph", "terminal_graph", "generation_final_clean_graph"]:
        value = row.get(key)
        if value:
            path = resolve_path(value)
            if path.exists():
                return path
    model = row.get("model") or row.get("paper_spec", "").split(":", 1)[0]
    paper = row.get("paper") or row.get("paper_spec", "").split(":", 1)[-1]
    run_id = row.get("run_id") or ""
    source_eval = row.get("source_eval_dir") or row.get("terminal_metric_eval_dir") or row.get("final_eval_dir") or ""
    if not run_id:
        match = re.search(rf"/{re.escape(model)}/{re.escape(paper)}/([^/]+)/", source_eval)
        if match:
            run_id = match.group(1)
    if run_id:
        for stage, filename in [
            ("llm_step2_self_fix_final_clean", "llm_step2_self_fix_final_clean_graph.dot"),
            ("raw_step1_extraction", "raw_step1_extraction_graph.dot"),
        ]:
            candidate = (
                PACKAGE_ROOT.parent
                / "08_minicheck"
                / "baselines"
                / "materialized_graphs"
                / stage
                / model
                / paper
                / run_id
                / filename
            )
            if candidate.exists():
                return candidate
    if source_eval:
        eval_path = resolve_path(source_eval)
        candidates = [
            eval_path.parent.parent / "final_clean_graph.dot",
            eval_path.parent / "final_clean_graph.dot",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
    raise FileNotFoundError(f"terminal graph not found for {row.get('paper_spec')}")


def run_id_from_graph_path(graph: Path, model: str, paper: str) -> str:
    parts = list(graph.parts)
    for idx in range(0, max(0, len(parts) - 2)):
        if parts[idx] == model and parts[idx + 1] == paper:
            return parts[idx + 2]
    return ""


def input_data_path(row: dict[str, str], ledger_row: dict[str, str]) -> Path:
    graph = terminal_graph_path(row)
    candidate = graph.parent / "input_data.json"
    if candidate.exists():
        return candidate
    model = row.get("model") or row.get("paper_spec", "").split(":", 1)[0]
    paper = row.get("paper") or row.get("paper_spec", "").split(":", 1)[-1]
    run_id = row.get("run_id") or run_id_from_graph_path(graph, model, paper)
    if run_id:
        candidate = PACKAGE_ROOT.parent / "01_teacher_pool_inputs" / model / paper / run_id / "input_data.json"
        if candidate.exists():
            return candidate
    prompt = ledger_row.get("packet_prompt") or ""
    if prompt:
        packet_run = Path(prompt).parent.name
        candidate = PACKAGE_ROOT.parent / "01_teacher_pool_inputs" / model / paper / packet_run / "input_data.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"input_data.json not found for {row.get('paper_spec')}")


def step2_graph_path(row: dict[str, str]) -> Path | None:
    model = row.get("model") or row.get("paper_spec", "").split(":", 1)[0]
    paper = row.get("paper") or row.get("paper_spec", "").split(":", 1)[-1]
    run_id = row.get("run_id") or run_id_from_graph_path(terminal_graph_path(row), model, paper)
    base = PACKAGE_ROOT.parent / "08_minicheck" / "baselines" / "materialized_graphs" / "llm_step2_self_fix_final_clean" / model / paper
    if run_id:
        candidate = base / run_id / "llm_step2_self_fix_final_clean_graph.json"
        if candidate.exists():
            return candidate
    matches = sorted(base.glob("*/llm_step2_self_fix_final_clean_graph.json"))
    return matches[0] if matches else None


def packet_path(ledger_row: dict[str, str]) -> Path:
    path = resolve_path(ledger_row.get("packet_json") or "")
    if not path.exists():
        raise FileNotFoundError(f"packet not found: {path}")
    return path


def load_ans_stage_rows(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") != "ok":
                continue
            spec = str(row.get("paper_spec") or "")
            stage = str(row.get("stage") or "")
            node_id = str(row.get("node_id") or "")
            if not spec or not stage or not node_id:
                continue
            out.setdefault(spec, {}).setdefault(stage, {})[node_id] = row
    return out


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


def missing_entities(packet: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    entities = [str(entity) for entity in (packet.get("paper_anchor") or {}).get("entities") or [] if str(entity)]
    coverage = entity_coverage_audit(spec, entities)
    return [str(row.get("entity")) for row in coverage.get("entities") or [] if not row.get("covered")]


def overlap_score(text: str, entities: list[str]) -> float:
    text_tokens = token_variant_index(text)
    if not text_tokens:
        return 0.0
    score = 0.0
    text_lower = text.lower()
    for entity in entities:
        entity_text = str(entity)
        if not entity_text:
            continue
        entity_tokens = token_variant_index(entity_text)
        if entity_text.lower() in text_lower:
            score += 2.0
        if entity_tokens:
            score += len(text_tokens & entity_tokens) / len(entity_tokens)
    return score


def entity_match_score(text: str, entity: str) -> float:
    text_tokens = token_variant_index(text)
    entity_text = str(entity or "")
    entity_tokens = token_variant_index(entity_text)
    if not entity_text or not entity_tokens:
        return 0.0
    score = len(text_tokens & entity_tokens) / len(entity_tokens)
    if entity_text.lower() in text.lower():
        score += 2.0
    return score


def supported_ratio(ans_row: dict[str, Any] | None) -> float:
    if not ans_row:
        return 0.0
    atomic = int(ans_row.get("atomic_fact_count") or 0)
    supported = int(ans_row.get("supported_fact_count") or 0)
    return supported / atomic if atomic else 0.0


def select_step2_nodes(
    *,
    paper_spec: str,
    step2: dict[str, Any],
    ans_rows: dict[str, dict[str, dict[str, Any]]],
    missing: list[str],
    max_nodes: int,
    min_ratio: float,
) -> list[dict[str, Any]]:
    stage_rows = ans_rows.get(paper_spec, {}).get("llm_step2_self_fix_final_clean", {})
    ranked = []
    for node in step2.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        row = stage_rows.get(node_id)
        if not row or row.get("unit_type") not in SOURCE_UNIT_TYPES:
            continue
        atomic = int(row.get("atomic_fact_count") or 0)
        supported = int(row.get("supported_fact_count") or 0)
        if atomic <= 0 or supported / atomic < min_ratio:
            continue
        text = str(node.get("text") or "")
        ranked.append(
            {
                "node_id": node_id,
                "text": text,
                "source": node.get("source", [0, 0, 0]),
                "support": f"{supported}/{atomic}",
                "overlap": overlap_score(text, missing),
                "atomic": atomic,
                "supported": supported,
            }
        )
    ranked.sort(key=lambda row: (-float(row["overlap"]), -int(row["supported"]), int(row["atomic"]), str(row["node_id"])))
    return ranked[:max_nodes]


def sentence_text(sentence: dict[str, Any]) -> str:
    sent = str(sentence.get("sentence") or "").strip()
    viewpoints = [str(v).strip() for v in sentence.get("viewpoints") or [] if str(v).strip()]
    if viewpoints:
        return f"{sent} Viewpoints: {' | '.join(viewpoints[:4])}".strip()
    return sent


def compact_text(value: str, *, max_chars: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"\[[^\]]+\]", "", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def select_anchor_sentences(input_data: dict[str, Any], missing: list[str], max_nodes: int) -> list[dict[str, Any]]:
    ranked = []
    for sentence in (input_data.get("introduction") or {}).get("sentences") or []:
        if not isinstance(sentence, dict):
            continue
        text = sentence_text(sentence)
        score = overlap_score(text, missing)
        if score <= 0:
            continue
        idx = int(sentence.get("idx") or 0)
        ranked.append({"idx": idx, "source": [idx, 0, 0], "text": f"Currently, {str(sentence.get('sentence') or '').strip()}", "overlap": score})
    ranked.sort(key=lambda row: (-float(row["overlap"]), int(row["idx"])))
    return ranked[:max_nodes]


def select_entity_complete_anchor_sentences(
    input_data: dict[str, Any],
    missing: list[str],
    max_nodes: int,
    *,
    min_entity_score: float,
) -> list[dict[str, Any]]:
    sentence_rows = []
    for sentence in (input_data.get("introduction") or {}).get("sentences") or []:
        if not isinstance(sentence, dict):
            continue
        idx = int(sentence.get("idx") or 0)
        raw_sentence = str(sentence.get("sentence") or "").strip()
        if not idx or not raw_sentence:
            continue
        text = sentence_text(sentence)
        sentence_rows.append(
            {
                "idx": idx,
                "source": [idx, 0, 0],
                "text": f"Currently, {raw_sentence}",
                "overlap": overlap_score(text, missing),
                "raw_text": text,
            }
        )

    selected: dict[int, dict[str, Any]] = {}
    entity_to_idx: dict[str, int] = {}
    for entity in missing:
        ranked = []
        for row in sentence_rows:
            score = entity_match_score(str(row["raw_text"]), entity)
            if score >= min_entity_score:
                ranked.append((score, len(str(row["text"])), int(row["idx"]), row))
        if not ranked:
            continue
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        best = dict(ranked[0][3])
        best["matched_entity"] = entity
        best["entity_match_score"] = ranked[0][0]
        selected[int(best["idx"])] = best
        entity_to_idx[entity] = int(best["idx"])

    if len(selected) < max_nodes:
        fillers = sorted(
            [row for row in sentence_rows if int(row["idx"]) not in selected and float(row["overlap"]) > 0],
            key=lambda row: (-float(row["overlap"]), int(row["idx"])),
        )
        for row in fillers:
            if len(selected) >= max_nodes:
                break
            selected[int(row["idx"])] = row

    out = sorted(selected.values(), key=lambda row: (-float(row.get("overlap") or 0), int(row["idx"])))
    for row in out:
        row.pop("raw_text", None)
    return out[:max_nodes]


def compact_entity_bridge_text(entity: str, evidence_text: str) -> str:
    evidence = compact_text(evidence_text, max_chars=220)
    entity_text = str(entity or "").strip()
    if entity_text and entity_text.lower() not in evidence.lower():
        return f"Currently, the paper supports the anchor entity {entity_text}: {evidence}"
    return f"Currently, {evidence}"


def source_tuple(value: Any) -> list[int]:
    if isinstance(value, list):
        padded = list(value) + [0, 0, 0]
        out = []
        for item in padded[:3]:
            try:
                out.append(max(0, int(item)))
            except (TypeError, ValueError):
                out.append(0)
        return out
    return [0, 0, 0]


def top_entity_evidence(packet: dict[str, Any], *, max_entities: int, max_per_entity: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_sources: set[tuple[int, int, int]] = set()
    for item in ((packet.get("evidence") or {}).get("entity_evidence") or [])[:max_entities]:
        if not isinstance(item, dict):
            continue
        entity = str(item.get("entity") or "").strip()
        ranked = sorted(
            [row for row in item.get("top_sentences") or [] if isinstance(row, dict)],
            key=lambda row: (
                not bool(row.get("exact_phrase_match")),
                -float(row.get("ans_source_support") or 0.0),
                -float(row.get("score") or 0.0),
                int(row.get("idx") or 999999),
            ),
        )
        picked_for_entity = 0
        for sentence in ranked:
            text = compact_text(sentence.get("sentence") or "", max_chars=260)
            if not text:
                continue
            src = source_tuple(sentence.get("source") or [sentence.get("idx") or 0, 0, 0])
            key = tuple(src)
            if key in used_sources:
                continue
            selected.append(
                {
                    "entity": entity,
                    "source": src,
                    "text": text.rstrip(".; ") + ".",
                    "score": sentence.get("score", ""),
                    "exact_phrase_match": sentence.get("exact_phrase_match", ""),
                }
            )
            used_sources.add(key)
            picked_for_entity += 1
            if picked_for_entity >= max_per_entity:
                break
    return selected


def select_common_evidence_leaf(packet: dict[str, Any], leaves: list[dict[str, Any]]) -> dict[str, Any] | None:
    core = compact_text((packet.get("paper_anchor") or {}).get("core_idea") or "", max_chars=320)
    anchor_entities = [str(entity) for entity in (packet.get("paper_anchor") or {}).get("entities") or [] if str(entity)]
    if leaves:
        ranked = sorted(
            leaves,
            key=lambda row: (
                -overlap_score(str(row.get("text") or ""), anchor_entities),
                len(str(row.get("text") or "")),
                str(row.get("entity") or ""),
            ),
        )
        best = dict(ranked[0])
        entity = str(best.get("entity") or "").strip()
        text = str(best.get("text") or "").strip()
        if entity and entity.lower() not in text.lower():
            text = f"{entity}: {text}"
        best["text"] = compact_text(text, max_chars=280).rstrip(".; ") + "."
        return best
    if core:
        return {"entity": "core_idea", "source": [0, 0, 0], "text": core.rstrip(".; ") + "."}
    return None


def build_source_seed_graph(
    *,
    paper_spec: str,
    packet: dict[str, Any],
    evidence_limit: int,
    root_mode: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    leaves = top_entity_evidence(packet, max_entities=evidence_limit, max_per_entity=1)
    if not leaves:
        return None, []
    if root_mode == "inventory_priority":
        def priority_key(leaf: dict[str, Any]) -> tuple[int, int, str]:
            combined = f"{leaf.get('entity', '')} {leaf.get('text', '')}".lower()
            if "free-standing" in combined or "membrane" in combined:
                tier = 0
            elif "record" in combined:
                tier = 1
            elif "gas-separation" in combined or "separation performance" in combined:
                tier = 2
            else:
                tier = 3
            source_idx = source_tuple(leaf.get("source"))[0]
            return (tier, source_idx, str(leaf.get("entity") or ""))

        leaves = sorted(leaves, key=priority_key)
    common_leaf = select_common_evidence_leaf(packet, leaves)
    anchor_core = compact_text((packet.get("paper_anchor") or {}).get("core_idea") or "", max_chars=420)
    core = anchor_core
    if not core:
        core = "; ".join(compact_text(leaf.get("text") or "", max_chars=140).rstrip(".; ") for leaf in leaves[:4])
    if root_mode in {"inventory", "inventory_priority"}:
        root_parts = []
        for leaf in leaves[: min(len(leaves), evidence_limit)]:
            entity = compact_text(str(leaf.get("entity") or ""), max_chars=70)
            text = compact_text(str(leaf.get("text") or ""), max_chars=150).rstrip(".; ")
            if entity and entity.lower() not in text.lower():
                root_parts.append(f"{entity}: {text}")
            elif text:
                root_parts.append(text)
        if root_parts:
            core = "; ".join(root_parts)
    elif root_mode == "compact":
        # Keep the semantic root short enough for the fixed ANS evidence window.
        # Long-tail facts remain in source leaves for CG/REA coverage.
        split = re.split(
            r"\b(?:yielding|thereby|thus exhibit|and thus exhibit|leading to)\b",
            anchor_core,
            maxsplit=1,
            flags=re.IGNORECASE,
        )
        compact_core = split[0].strip(" ,;")
        if len(compact_core) < 80:
            compact_core = "; ".join(
                compact_text(leaf.get("text") or "", max_chars=120).rstrip(".; ") for leaf in leaves[:3]
            )
        core = compact_text(compact_core, max_chars=260)
    nodes: list[dict[str, Any]] = [{"id": "NROOT", "source": [0, 0, 0], "text": core.rstrip(".; ") + "."}]
    edges: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    common_source_key: tuple[int, int, int] | None = tuple(source_tuple(common_leaf.get("source"))) if common_leaf else None
    common_node_id = ""
    for idx, leaf in enumerate(leaves, start=1):
        node_id = f"E{idx}"
        nodes.append({"id": node_id, "source": source_tuple(leaf.get("source")), "text": str(leaf.get("text") or "")})
        edge_type = "induction-case"
        if common_source_key and tuple(source_tuple(leaf.get("source"))) == common_source_key and not common_node_id:
            edge_type = "induction-common"
            common_node_id = node_id
        edges.append({"source": node_id, "target": "NROOT", "type": edge_type})
        actions.append(
            {
                "action": "source_seed_entity_evidence_node",
                "node_id": node_id,
                "entity": leaf.get("entity", ""),
                "source_tuple": source_tuple(leaf.get("source")),
                "edge_type": edge_type,
                "root_mode": root_mode,
                "candidate_label": "source_seed",
            }
        )
    if not common_node_id:
        common_node_id = f"E{len(nodes)}"
        common_text = str((common_leaf or {}).get("text") or core).strip()
        nodes.append({"id": common_node_id, "source": source_tuple((common_leaf or {}).get("source")), "text": common_text})
        edges.append({"source": common_node_id, "target": "NROOT", "type": "induction-common"})
        actions.append(
            {
                "action": "source_seed_common_evidence_node",
                "node_id": common_node_id,
                "entity": (common_leaf or {}).get("entity", ""),
                "source_tuple": source_tuple((common_leaf or {}).get("source")),
                "edge_type": "induction-common",
                "root_mode": root_mode,
                "candidate_label": "source_seed",
            }
        )
    spec = {"root": "NROOT", "nodes": nodes, "edges": edges, "paper_id": paper_spec}
    return spec, actions


def select_compact_entity_bridge_sentences(
    input_data: dict[str, Any],
    missing: list[str],
    max_nodes: int,
    *,
    min_entity_score: float,
) -> list[dict[str, Any]]:
    sentence_rows = []
    for sentence in (input_data.get("introduction") or {}).get("sentences") or []:
        if not isinstance(sentence, dict):
            continue
        idx = int(sentence.get("idx") or 0)
        raw_sentence = str(sentence.get("sentence") or "").strip()
        if idx and raw_sentence:
            sentence_rows.append({"idx": idx, "source": [idx, 0, 0], "raw_sentence": raw_sentence, "raw_text": sentence_text(sentence)})

    selected: dict[tuple[str, int], dict[str, Any]] = {}
    for entity in missing:
        ranked = []
        for row in sentence_rows:
            score = entity_match_score(str(row["raw_text"]), entity)
            if score >= min_entity_score:
                ranked.append((score, len(str(row["raw_sentence"])), int(row["idx"]), row))
        if not ranked:
            continue
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        score, _length, _idx, best = ranked[0]
        selected[(entity, int(best["idx"]))] = {
            "idx": int(best["idx"]),
            "source": best["source"],
            "text": compact_entity_bridge_text(entity, str(best["raw_sentence"])),
            "overlap": overlap_score(str(best["raw_text"]), missing),
            "matched_entity": entity,
            "entity_match_score": score,
        }
        if len(selected) >= max_nodes:
            break
    return list(selected.values())[:max_nodes]


def next_node_id(existing: set[str], prefix: str, start: int = 1) -> str:
    idx = start
    while f"{prefix}{idx}" in existing:
        idx += 1
    return f"{prefix}{idx}"


def add_leaf_nodes(
    terminal: dict[str, Any],
    leaves: list[dict[str, Any]],
    *,
    label: str,
    source_kind: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    out = deepcopy(terminal)
    nodes = graph_nodes(out)
    existing_ids = set(nodes)
    edges = graph_edges(out)
    actions = []
    for ordinal, leaf in enumerate(leaves, 1):
        source_id = str(leaf.get("node_id") or "")
        if source_kind == "step2_source_leaf" and source_id and source_id not in existing_ids:
            node_id = source_id
        else:
            node_id = next_node_id(existing_ids, "ANS_SRC_")
        existing_ids.add(node_id)
        text = str(leaf.get("text") or "").strip()
        out.setdefault("nodes", []).append({"id": node_id, "source": leaf.get("source", [0, 0, 0]), "text": text})
        edge_key = (node_id, "NROOT", "induction-case")
        if edge_key not in edges:
            out.setdefault("edges", []).append({"source": node_id, "target": "NROOT", "type": "induction-case"})
            edges.add(edge_key)
        actions.append(
            {
                "action": "add_source_leaf_to_root",
                "source_kind": source_kind,
                "node_id": node_id,
                "source_node_id": source_id,
                "source_tuple": leaf.get("source", [0, 0, 0]),
                "new_text": text,
                "overlap": leaf.get("overlap", ""),
                "support": leaf.get("support", ""),
                "ordinal": ordinal,
                "candidate_label": label,
            }
        )
    return out, actions


def build_claim_packet(candidate_row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    claim_args = argparse.Namespace(
        window=args.window,
        traversal_depth=args.traversal_depth,
        max_evidence_sentences=args.max_evidence_sentences,
        max_evidence_chars=args.max_evidence_chars,
        include_viewpoints=args.include_viewpoints,
        graph_ordered_evidence=args.graph_ordered_evidence,
    )
    return build_candidate_packet(candidate_row, claim_args)


def estimate_cached_ans(claim_packet: dict[str, Any], cache: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
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
        key = eval_key(eval_row)
        facts = cache.get(key)
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


def candidate_base_row(row: dict[str, str], *, graph_path: Path, eval_dir: Path, label: str) -> dict[str, Any]:
    return {
        "paper_spec": row.get("paper_spec"),
        "model": row.get("model"),
        "candidate_label": label,
        "candidate_graph": rel(graph_path),
        "candidate_eval_dir": rel(eval_dir),
        "candidate_fresh_eval_results": "",
        "mergeable": True,
        "candidate_CG": "",
        "candidate_REA": "",
    }


def write_candidate_artifacts(
    *,
    out_root: Path,
    row: dict[str, str],
    packet_path_value: Path,
    spec: dict[str, Any],
    label: str,
    actions: list[dict[str, Any]],
    preflight: dict[str, Any],
    cached_ans: dict[str, Any],
    row_floor: float,
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
    write_json(candidate_dir / "preflight_report.json", preflight)
    shutil.copy2(graph_json, staged_dir / "graph_spec.json")
    shutil.copy2(graph_dot, staged_dir / "final_clean_graph.dot")
    shutil.copy2(resolve_path(row["input_data_path_for_candidate"]), staged_dir / "input_data.json")
    write_json(staged_dir / "evidence_bound_packet_pointer.json", {"packet": rel(packet_path_value), "source": label})

    candidate_row = candidate_base_row(row, graph_path=graph_dot, eval_dir=staged_dir, label=label)
    claim_packet = build_claim_packet(candidate_row, args)
    claims_dir = out_root / "ans_claims" / safe_slug(label)
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
    cached_value = cached_ans.get("cached_ans")
    cache_misses = int(cached_ans.get("cache_misses") or 0)
    cached_passes = bool(cached_value is not None and cached_value + 1e-12 >= row_floor)
    preflight_passes = bool(preflight.get("passed_local_preflight"))
    if cached_value is None:
        ans_gate_status = "formal_ans_required_no_cached_hits"
    elif cached_passes:
        ans_gate_status = "cached_ans_passes_row_floor"
    elif cache_misses > 0:
        ans_gate_status = "formal_ans_required_cache_incomplete"
    else:
        ans_gate_status = "cached_ans_fails_row_floor"
    return {
        "paper_spec": paper_spec,
        "model": row.get("model"),
        "paper": row.get("paper"),
        "failure_type": row.get("failure_type"),
        "candidate_label": label,
        "graph_spec": rel(graph_json),
        "dot": rel(graph_dot),
        "staged_run_dir": rel(staged_dir),
        "preflight_report": rel(candidate_dir / "preflight_report.json"),
        "claims_input": rel(claims_path),
        "final_clean_graph_sha256": sha256_file(graph_dot),
        "passed_local_preflight": preflight_passes,
        "covered_entities_local": preflight.get("entity_coverage", {}).get("covered_entities", ""),
        "total_entities_local": preflight.get("entity_coverage", {}).get("total_entities", ""),
        "premise_support_high_risk_count": preflight.get("immediate_premise_support", {}).get("high_risk_count", ""),
        "row_ans_floor": row_floor,
        "cached_ans": cached_value,
        "cached_ans_passes_row_floor": cached_passes,
        "ans_gate_status": ans_gate_status,
        "formal_ans_required": ans_gate_status.startswith("formal_ans_required"),
        "cache_hits": cached_ans.get("cache_hits"),
        "cache_misses": cache_misses,
        "actions": actions,
        "fresh_eval_candidate": bool(preflight_passes and (cached_passes or cache_misses > 0)),
    }


def build_for_row(
    row: dict[str, str],
    *,
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
    terminal_dot = terminal_graph_path(row)
    terminal_json = graph_json_from_dot(terminal_dot)
    terminal = read_json(terminal_json, {})
    input_path = input_data_path(row, ledger_row)
    input_data = read_json(input_path, {})
    row = dict(row)
    row["input_data_path_for_candidate"] = rel(input_path)
    row_floor = to_float(queue_row.get("current_best_main_factual_ans") or ledger_row.get("current_best_main_factual_ans"))
    missing = missing_entities(packet, terminal)
    if not missing:
        return []

    summaries: list[dict[str, Any]] = []
    if row.get("failure_type") == "preflight:no_anchor_regenerate" and args.source_seed_no_anchor:
        for root_mode in ["core", "inventory", "compact", "inventory_priority"]:
            seed_spec, seed_actions = build_source_seed_graph(
                paper_spec=paper_spec,
                packet=packet,
                evidence_limit=args.max_source_seed_entities,
                root_mode=root_mode,
            )
            if seed_spec:
                if root_mode == "core":
                    label_kind = "source-seed-anchor-contract"
                elif root_mode == "inventory":
                    label_kind = "source-seed-root-inventory"
                elif root_mode == "inventory_priority":
                    label_kind = "source-seed-root-inventory-priority"
                else:
                    label_kind = "source-seed-root-compact"
                label = f"batch-{label_kind}-v1-{safe_slug(paper_spec, limit=60)}"
                for action in seed_actions:
                    action["candidate_label"] = label
                preflight = preflight_graph_spec(seed_spec, packet)
                candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(label)
                candidate_dir.mkdir(parents=True, exist_ok=True)
                temp_dot = candidate_dir / "final_clean_graph.dot"
                temp_json = candidate_dir / "graph_spec.json"
                write_json(temp_json, seed_spec)
                write_text(temp_dot, graph_spec_to_dot(seed_spec))
                candidate_row = candidate_base_row(row, graph_path=temp_dot, eval_dir=candidate_dir, label=label)
                shutil.copy2(input_path, candidate_dir / "input_data.json")
                claim_packet = build_claim_packet(candidate_row, args)
                cached_ans = estimate_cached_ans(claim_packet, cache)
                summaries.append(
                    write_candidate_artifacts(
                        out_root=out_root,
                        row=row,
                        packet_path_value=packet_file,
                        spec=seed_spec,
                        label=label,
                        actions=seed_actions,
                        preflight=preflight,
                        cached_ans=cached_ans,
                        row_floor=row_floor,
                        args=args,
                    )
                )

    step2_path = step2_graph_path(row)
    if step2_path and step2_path.exists():
        step2 = read_json(step2_path, {})
        leaves = select_step2_nodes(
            paper_spec=paper_spec,
            step2=step2,
            ans_rows=ans_rows,
            missing=missing,
            max_nodes=args.max_leaf_nodes,
            min_ratio=args.min_step2_support_ratio,
        )
        if leaves:
            label = f"batch-step2-leaf-v1-{safe_slug(paper_spec, limit=60)}"
            candidate, actions = add_leaf_nodes(terminal, leaves, label=label, source_kind="step2_source_leaf")
            preflight = preflight_graph_spec(candidate, packet)
            candidate_row = candidate_base_row(row, graph_path=out_root / "tmp.dot", eval_dir=out_root, label=label)
            candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(label)
            candidate_dir.mkdir(parents=True, exist_ok=True)
            temp_dot = candidate_dir / "final_clean_graph.dot"
            temp_json = candidate_dir / "graph_spec.json"
            write_json(temp_json, candidate)
            write_text(temp_dot, graph_spec_to_dot(candidate))
            candidate_row["candidate_graph"] = rel(temp_dot)
            candidate_row["candidate_eval_dir"] = rel(candidate_dir)
            shutil.copy2(input_path, candidate_dir / "input_data.json")
            claim_packet = build_claim_packet(candidate_row, args)
            cached_ans = estimate_cached_ans(claim_packet, cache)
            summaries.append(
                write_candidate_artifacts(
                    out_root=out_root,
                    row=row,
                    packet_path_value=packet_file,
                    spec=candidate,
                    label=label,
                    actions=actions,
                    preflight=preflight,
                    cached_ans=cached_ans,
                    row_floor=row_floor,
                    args=args,
                )
            )

    if args.entity_complete_anchor:
        anchor_leaves = select_entity_complete_anchor_sentences(
            input_data,
            missing,
            args.max_leaf_nodes,
            min_entity_score=args.min_anchor_entity_score,
        )
        anchor_label_kind = "entity-complete-anchor-evidence-leaf"
    else:
        anchor_leaves = select_anchor_sentences(input_data, missing, args.max_leaf_nodes)
        anchor_label_kind = "anchor-evidence-leaf"
    if anchor_leaves:
        label = f"batch-{anchor_label_kind}-v1-{safe_slug(paper_spec, limit=60)}"
        candidate, actions = add_leaf_nodes(terminal, anchor_leaves, label=label, source_kind="anchor_evidence_leaf")
        preflight = preflight_graph_spec(candidate, packet)
        candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(label)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        temp_dot = candidate_dir / "final_clean_graph.dot"
        temp_json = candidate_dir / "graph_spec.json"
        write_json(temp_json, candidate)
        write_text(temp_dot, graph_spec_to_dot(candidate))
        candidate_row = candidate_base_row(row, graph_path=temp_dot, eval_dir=candidate_dir, label=label)
        shutil.copy2(input_path, candidate_dir / "input_data.json")
        claim_packet = build_claim_packet(candidate_row, args)
        cached_ans = estimate_cached_ans(claim_packet, cache)
        summaries.append(
            write_candidate_artifacts(
                out_root=out_root,
                row=row,
                packet_path_value=packet_file,
                spec=candidate,
                label=label,
                actions=actions,
                preflight=preflight,
                cached_ans=cached_ans,
                row_floor=row_floor,
                args=args,
            )
        )
    bridge_leaves = select_compact_entity_bridge_sentences(
        input_data,
        missing,
        args.max_bridge_nodes,
        min_entity_score=args.min_anchor_entity_score,
    )
    if bridge_leaves:
        label = f"batch-compact-entity-bridge-v1-{safe_slug(paper_spec, limit=60)}"
        candidate, actions = add_leaf_nodes(terminal, bridge_leaves, label=label, source_kind="compact_entity_bridge")
        preflight = preflight_graph_spec(candidate, packet)
        candidate_dir = out_root / "candidates" / safe_slug(paper_spec) / safe_slug(label)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        temp_dot = candidate_dir / "final_clean_graph.dot"
        temp_json = candidate_dir / "graph_spec.json"
        write_json(temp_json, candidate)
        write_text(temp_dot, graph_spec_to_dot(candidate))
        candidate_row = candidate_base_row(row, graph_path=temp_dot, eval_dir=candidate_dir, label=label)
        shutil.copy2(input_path, candidate_dir / "input_data.json")
        claim_packet = build_claim_packet(candidate_row, args)
        cached_ans = estimate_cached_ans(claim_packet, cache)
        summaries.append(
            write_candidate_artifacts(
                out_root=out_root,
                row=row,
                packet_path_value=packet_file,
                spec=candidate,
                label=label,
                actions=actions,
                preflight=preflight,
                cached_ans=cached_ans,
                row_floor=row_floor,
                args=args,
            )
        )
    for summary in summaries:
        summary["missing_entities"] = "; ".join(missing)
        summary["terminal_graph"] = rel(terminal_dot)
        summary["terminal_graph_json"] = rel(terminal_json)
        summary["step2_graph"] = rel(step2_path) if step2_path else ""
        summary["input_data"] = rel(input_path)
        summary["packet"] = rel(packet_file)
    return summaries


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ANS-Safe Hybrid Batch Candidates",
        "",
        f"Created: `{report['created_at']}`",
        f"Provider calls: `{report['provider_calls']}`",
        f"Canonical accounting write: `{report['canonical_accounting_write']}`",
        f"Rows considered: `{report['rows_considered']}`",
        f"Candidates built: `{report['candidate_count']}`",
        f"Fresh-eval ready candidates: `{report['fresh_eval_candidate_count']}`",
        "",
        "| paper_spec | candidate | preflight | cached ANS | row floor | cache misses | fresh-eval candidate |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in report["rows"]:
        lines.append(
            "| {paper_spec} | {candidate_label} | `{passed_local_preflight}` | "
            "`{cached_ans}` | `{row_ans_floor}` | `{cache_misses}` | `{fresh_eval_candidate}` |".format(**row)
        )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounting", default=str(DEFAULT_ACCOUNTING))
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--v2-ledger", default=str(DEFAULT_V2_LEDGER))
    parser.add_argument("--ans-node-results", default=str(DEFAULT_ANS_NODE_RESULTS))
    parser.add_argument("--ans-cache", default=str(DEFAULT_ANS_CACHE))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--paper-spec", action="append", default=[])
    parser.add_argument("--failure-type", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-leaf-nodes", type=int, default=4)
    parser.add_argument("--max-bridge-nodes", type=int, default=6)
    parser.add_argument("--source-seed-no-anchor", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-source-seed-entities", type=int, default=12)
    parser.add_argument("--min-step2-support-ratio", type=float, default=1.0)
    parser.add_argument("--entity-complete-anchor", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-anchor-entity-score", type=float, default=0.67)
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
    accounting = read_csv(resolve_path(args.accounting))
    queue = {row.get("paper_spec", ""): row for row in read_csv(resolve_path(args.queue))}
    ledger = {row.get("paper_spec", ""): row for row in read_csv(resolve_path(args.v2_ledger))}
    ans_rows = load_ans_stage_rows(resolve_path(args.ans_node_results))
    cache = read_eval_cache(resolve_path(args.ans_cache))
    target_failures = set(args.failure_type or []) or TARGET_FAILURES
    target_specs = set(args.paper_spec or [])
    residuals = [
        row
        for row in accounting
        if row.get("current_outcome") == "typed_residual"
        and row.get("failure_type") in target_failures
        and (not target_specs or row.get("paper_spec") in target_specs)
    ]
    if args.limit and args.limit > 0:
        residuals = residuals[: args.limit]

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in residuals:
        spec = row.get("paper_spec", "")
        try:
            if spec not in queue:
                raise RuntimeError("missing queue row")
            if spec not in ledger:
                raise RuntimeError("missing V2 ledger row")
            rows.extend(
                build_for_row(
                    row,
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
        "paper_spec",
        "model",
        "paper",
        "failure_type",
        "candidate_label",
        "passed_local_preflight",
        "covered_entities_local",
        "total_entities_local",
        "premise_support_high_risk_count",
        "row_ans_floor",
        "cached_ans",
        "cached_ans_passes_row_floor",
        "ans_gate_status",
        "formal_ans_required",
        "cache_hits",
        "cache_misses",
        "fresh_eval_candidate",
        "missing_entities",
        "graph_spec",
        "dot",
        "staged_run_dir",
        "preflight_report",
        "claims_input",
        "final_clean_graph_sha256",
        "terminal_graph",
        "step2_graph",
        "input_data",
        "packet",
    ]
    write_csv(out_root / "ATTEMPT_INDEX.csv", rows, fieldnames)
    write_json(out_root / "ATTEMPT_INDEX.json", {"rows": rows})
    write_csv(
        out_root / "FRESH_EVAL_READY_QUEUE.csv",
        [row for row in rows if row.get("fresh_eval_candidate") is True],
        fieldnames,
    )
    write_csv(out_root / "SKIPPED_ROWS.csv", skipped, ["paper_spec", "reason"])
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_accounting": rel(resolve_path(args.accounting)),
        "out_root": rel(out_root),
        "rows_considered": len(residuals),
        "candidate_count": len(rows),
        "fresh_eval_candidate_count": sum(1 for row in rows if row.get("fresh_eval_candidate") is True),
        "skipped_count": len(skipped),
        "rows": rows,
        "skipped": skipped,
    }
    write_json(out_root / "ANS_SAFE_HYBRID_BATCH_SUMMARY.json", report)
    write_text(out_root / "ANS_SAFE_HYBRID_BATCH_SUMMARY.md", markdown(report))
    print(
        json.dumps(
            {
                "out_root": report["out_root"],
                "rows_considered": report["rows_considered"],
                "candidate_count": report["candidate_count"],
                "fresh_eval_candidate_count": report["fresh_eval_candidate_count"],
                "skipped_count": report["skipped_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
