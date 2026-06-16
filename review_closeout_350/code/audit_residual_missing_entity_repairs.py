#!/usr/bin/env python3
"""Audit hard-tail residual rows for missing-entity repair planning.

The script is intentionally provider-free. It does not edit graphs and does not
rewrite canonical accounting. It combines the latest proposed residual CSV,
fresh evaluation outputs, terminal DOT labels, and evidence-bound regeneration
packets to produce a source-sensitive repair ledger.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
    / "10_standard_flow_350_subset_package"
)
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_PROPOSED_ROOT = (
    RESIDUAL_ROOT
    / "proposed_accounting_merges"
    / "20260604_combined_guarded_v5_plus_anchor_v3_plus_same_paper_v2_plus_ans_surface_v4_plus_coverage_v5"
)
DEFAULT_RESIDUAL_CSV = DEFAULT_PROPOSED_ROOT / "PROPOSED_TYPED_RESIDUAL_350.csv"
DEFAULT_PACKET_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "packets"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "missing_entity_audit"

TOKEN_RE = re.compile(r"[^\W_]+", re.IGNORECASE | re.UNICODE)
NODE_RE = re.compile(r'^\s*"(?P<id>[^"]+)"\s+\[(?P<attrs>.+)\];\s*$')
EDGE_RE = re.compile(r'^\s*"(?P<src>[^"]+)"\s*->\s*"(?P<tgt>[^"]+)"\s+\[(?P<attrs>.+)\];\s*$')
LABEL_RE = re.compile(r'label\s*=\s*"(?P<label>(?:[^"\\]|\\.)*)"')
SOURCE_RE = re.compile(r"^\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)\s*(.*)$", re.DOTALL)
STANDARD_EDGE_TYPES = {
    "deduction-rule",
    "deduction-case",
    "induction-case",
    "induction-common",
    "abduction-phenomenon",
    "abduction-knowledge",
}
GREEK_NAME_ALIASES = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "ε": "epsilon",
    "θ": "theta",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "mu",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "τ": "tau",
    "ϕ": "phi",
    "φ": "phi",
    "χ": "chi",
    "ω": "omega",
}
STOP_WORDS = {
    "the",
    "and",
    "are",
    "for",
    "with",
    "that",
    "this",
    "can",
    "may",
    "will",
    "has",
    "have",
    "been",
    "more",
    "such",
    "also",
    "used",
    "use",
    "than",
    "these",
    "they",
    "from",
    "into",
    "over",
    "under",
    "their",
    "there",
    "where",
    "when",
    "what",
    "which",
    "while",
    "through",
    "but",
    "not",
    "all",
    "any",
    "both",
    "each",
    "few",
    "most",
    "other",
    "some",
    "only",
    "own",
    "same",
    "then",
    "very",
    "just",
    "now",
    "how",
    "its",
    "our",
    "out",
    "way",
    "many",
    "could",
    "would",
    "should",
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


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def compact_text(text: str, *, max_chars: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def normalize_match_text(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).lower()


def tokens(text: str) -> List[str]:
    normalized = normalize_match_text(text)
    return [tok.lower() for tok in TOKEN_RE.findall(normalized) if tok]


def token_variants(token: str) -> set[str]:
    tok = normalize_match_text(token).strip()
    if not tok:
        return set()
    variants = {tok}
    if len(tok) > 3 and tok.endswith("s"):
        variants.add(tok[:-1])
    if len(tok) > 4 and tok.endswith("ies"):
        variants.add(tok[:-3] + "y")
    if len(tok) > 5 and tok.endswith("ices"):
        variants.add(tok[:-4] + "ex")
        variants.add(tok[:-3] + "x")
    if len(tok) > 6 and tok.endswith("ness"):
        variants.add(tok[:-4])
    if len(tok) > 7 and tok.endswith("ically"):
        variants.add(tok[:-2])
        variants.add(tok[:-4])
    if len(tok) > 5 and tok.endswith("ly"):
        variants.add(tok[:-2])
    if len(tok) > 7 and tok.endswith("ation"):
        variants.add(tok[:-5] + "e")
        variants.add(tok[:-3])
    if len(tok) > 8 and tok.endswith("ations"):
        variants.add(tok[:-6] + "e")
        variants.add(tok[:-4])
    if len(tok) > 8 and tok.endswith("ession"):
        variants.add(tok[:-3])
    if len(tok) > 7 and tok.endswith("ing"):
        variants.add(tok[:-3])
        variants.add(tok[:-3] + "e")
    if len(tok) > 6 and tok.endswith("ed"):
        variants.add(tok[:-2])
        variants.add(tok[:-1])
    for symbol, name in GREEK_NAME_ALIASES.items():
        if symbol in tok:
            variants.add(tok.replace(symbol, name))
            variants.add(tok.replace(symbol, f"{name} "))
    mixed = re.match(r"^([^\W_a-z0-9]+)([a-z0-9]+)$", tok, flags=re.IGNORECASE | re.UNICODE)
    if mixed:
        variants.add(mixed.group(1))
        variants.add(mixed.group(2))
        symbol_alias = "".join(GREEK_NAME_ALIASES.get(ch, ch) for ch in mixed.group(1))
        if symbol_alias:
            variants.add(symbol_alias)
            variants.add(f"{symbol_alias} {mixed.group(2)}")
    return {item.strip() for item in variants if item.strip()}


def token_variant_index(text: str) -> set[str]:
    out: set[str] = set()
    for token in tokens(text):
        out.update(token_variants(token))
    return out


def compact_match_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_match_text(text))


def normalize_text(text: str) -> str:
    return " ".join(tokens(text))


def entity_match(entity: str, text: str) -> Dict[str, Any]:
    entity_norm = normalize_text(entity)
    text_norm = normalize_text(text)
    entity_raw = normalize_match_text(entity)
    text_raw = normalize_match_text(text)
    entity_compact = compact_match_text(entity)
    text_compact = compact_match_text(text)
    entity_tokens = token_variant_index(entity)
    text_tokens = token_variant_index(text)
    matched_tokens = sorted(entity_tokens & text_tokens)
    exact = bool(entity_norm and entity_norm in text_norm)
    if not exact:
        variants = {entity_raw}
        if entity_raw.endswith("s"):
            variants.add(entity_raw[:-1])
        exact = any(variant and variant in text_raw for variant in variants)
    if not exact:
        exact = bool(entity_compact and len(entity_compact) >= 6 and entity_compact in text_compact)
    token_coverage = len(matched_tokens) / max(1, len(entity_tokens))
    return {
        "covered": bool(exact or token_coverage >= 0.67),
        "exact_phrase_match": exact,
        "token_coverage": round(token_coverage, 4),
        "matched_tokens": matched_tokens,
    }


def parse_attrs(attrs: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    label_match = LABEL_RE.search(attrs)
    if label_match:
        out["label"] = bytes(label_match.group("label"), "utf-8").decode("unicode_escape")
    return out


def split_source_label(label: str) -> Tuple[List[int], str]:
    match = SOURCE_RE.match(str(label or "").strip())
    if not match:
        return [0, 0, 0], str(label or "").strip()
    return [int(match.group(1)), int(match.group(2)), int(match.group(3))], match.group(4).strip()


def parse_dot(dot_path: Path) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, str]] = []
    try:
        lines = dot_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return nodes, edges
    for line in lines:
        node_match = NODE_RE.match(line)
        if node_match and "->" not in line:
            node_id = node_match.group("id")
            attrs = parse_attrs(node_match.group("attrs"))
            label = attrs.get("label", "")
            source, text = split_source_label(label)
            nodes[node_id] = {"id": node_id, "label": label, "source": source, "text": text}
            continue
        edge_match = EDGE_RE.match(line)
        if edge_match:
            attrs = parse_attrs(edge_match.group("attrs"))
            edges.append(
                {
                    "source": edge_match.group("src"),
                    "target": edge_match.group("tgt"),
                    "type": attrs.get("label", ""),
                }
            )
    return nodes, edges


def vote_files(eval_dir: Path) -> List[Path]:
    return sorted((eval_dir / "responses").glob("reasoning_validation_*_vote_result.json"))


def correct_vote_texts(eval_dir: Path) -> Tuple[List[Dict[str, Any]], str]:
    votes: List[Dict[str, Any]] = []
    text_parts: List[str] = []
    for path in vote_files(eval_dir):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        result = str(payload.get("final_result") or "").lower()
        payload["_vote_file"] = rel(path)
        votes.append(payload)
        if result != "correct":
            continue
        text_parts.append(str(payload.get("target_content") or ""))
        for source_text in payload.get("source_contents") or []:
            text_parts.append(str(source_text or ""))
    return votes, " ".join(text_parts)


def source_content(input_data: Dict[str, Any], source: Any) -> str:
    if not isinstance(source, list) or len(source) != 3:
        return ""
    source_x, source_y, source_z = source
    if not all(isinstance(item, int) for item in source):
        return ""
    if source_x == 0 and source_y == 0 and source_z == 0:
        return ""
    intro = input_data.get("introduction") if isinstance(input_data.get("introduction"), dict) else {}
    sentences = intro.get("sentences") if isinstance(intro.get("sentences"), list) else []
    sentence_data = next((item for item in sentences if isinstance(item, dict) and item.get("idx") == source_x), None)
    if not sentence_data:
        return ""
    if source_z == 0 and source_y == 0:
        return str(sentence_data.get("sentence") or "")
    if source_z == 0 and source_y != 0:
        viewpoints = sentence_data.get("viewpoints") if isinstance(sentence_data.get("viewpoints"), list) else []
        if 1 <= source_y <= len(viewpoints):
            return remove_reasoning_prefixes(str(viewpoints[source_y - 1] or ""))
        return ""
    references = sentence_data.get("references") if isinstance(sentence_data.get("references"), dict) else {}
    ref_keys = list(references.keys())
    if 1 <= source_y <= len(ref_keys):
        viewpoints = references.get(ref_keys[source_y - 1])
        if isinstance(viewpoints, list) and 1 <= source_z <= len(viewpoints):
            return remove_reasoning_prefixes(str(viewpoints[source_z - 1] or ""))
    return ""


def remove_reasoning_prefixes(text: str) -> str:
    return re.sub(
        r"^\s*(deduction|induction|abduction|reasoning|premise|conclusion)\s*[:：-]\s*",
        "",
        str(text or ""),
        flags=re.IGNORECASE,
    ).strip()


def evaluator_terms_from_source_text(text: str) -> List[str]:
    single_words = re.findall(r"\b[A-Za-z]+\b", str(text or ""))
    compound_terms = re.findall(r"\b[A-Za-z]+(?:[-\s][A-Za-z]+)+\b", str(text or ""))
    terms = []
    for word in single_words + compound_terms:
        normalized = word.strip()
        if len(normalized) < 2:
            continue
        if normalized.lower() in STOP_WORDS:
            continue
        terms.append(normalized)
    return terms


def correct_step_node_ids(votes: Sequence[Dict[str, Any]]) -> set[str]:
    node_ids: set[str] = set()
    for vote in votes:
        if str(vote.get("final_result") or "").lower() != "correct":
            continue
        target = str(vote.get("target_node") or "").strip()
        if target:
            node_ids.add(target)
        for source in vote.get("source_nodes") or []:
            source_id = str(source or "").strip()
            if source_id:
                node_ids.add(source_id)
    return node_ids


def official_correct_step_node_ids(
    *,
    edges: Sequence[Dict[str, str]],
    eval_payload: Dict[str, Any],
    votes: Sequence[Dict[str, Any]],
) -> set[str]:
    accuracy = eval_payload.get("accuracy") if isinstance(eval_payload.get("accuracy"), dict) else {}
    details = accuracy.get("details") if isinstance(accuracy.get("details"), dict) else {}
    if not details:
        return correct_step_node_ids(votes)
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    target_order: List[str] = []
    for edge in edges:
        if edge.get("type") not in STANDARD_EDGE_TYPES:
            continue
        target = str(edge.get("target") or "")
        if target not in grouped:
            target_order.append(target)
        grouped[target].append(edge)
    node_ids: set[str] = set()
    for step_id, target in enumerate(target_order, start=1):
        result = str(details.get(str(step_id), details.get(step_id, ""))).lower()
        if result != "correct":
            continue
        node_ids.add(target)
        for edge in grouped.get(target, []):
            source = str(edge.get("source") or "")
            if source:
                node_ids.add(source)
    return node_ids


def official_source_coverage(
    *,
    entities: Sequence[str],
    nodes: Dict[str, Dict[str, Any]],
    edges: Sequence[Dict[str, str]],
    votes: Sequence[Dict[str, Any]],
    eval_payload: Dict[str, Any],
    input_data: Dict[str, Any],
) -> Dict[str, Any]:
    correct_nodes = official_correct_step_node_ids(edges=edges, eval_payload=eval_payload, votes=votes)
    source_rows: List[Dict[str, Any]] = []
    reasoning_entities: set[str] = set()
    for node_id in sorted(correct_nodes):
        node = nodes.get(node_id)
        if not node:
            continue
        source = node.get("source")
        content = source_content(input_data, source)
        if not content or content.startswith("["):
            continue
        terms = evaluator_terms_from_source_text(content)
        reasoning_entities.update(terms)
        source_rows.append(
            {
                "node_id": node_id,
                "source": source_key(source),
                "term_count": len(terms),
                "content": compact_text(content, max_chars=360),
            }
        )
    reasoning_lower = [term.lower() for term in reasoning_entities]
    entity_rows: List[Dict[str, Any]] = []
    covered: List[str] = []
    missing: List[str] = []
    for entity in entities:
        core = str(entity).lower()
        matched = ""
        for reasoning_entity in reasoning_lower:
            if core in reasoning_entity or reasoning_entity in core:
                matched = reasoning_entity
                break
        if matched:
            covered.append(str(entity))
        else:
            missing.append(str(entity))
        entity_rows.append({"entity": entity, "covered": bool(matched), "matched_reasoning_entity": matched})
    return {
        "covered": covered,
        "missing": missing,
        "entity_rows": entity_rows,
        "reasoning_entity_count": len(reasoning_entities),
        "source_rows": source_rows,
    }


def packet_index(packet_root: Path) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    if not packet_root.exists():
        return out
    for path in packet_root.glob("*/packet.json"):
        payload = read_json(path, {})
        if isinstance(payload, dict) and payload.get("paper_spec"):
            out[str(payload["paper_spec"])] = path
    return out


def entity_evidence_map(packet: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    evidence = packet.get("evidence") if isinstance(packet.get("evidence"), dict) else {}
    rows = evidence.get("entity_evidence") if isinstance(evidence.get("entity_evidence"), list) else []
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        entity = str(row.get("entity") or "").strip()
        if not entity:
            continue
        top_sentences = row.get("top_sentences") if isinstance(row.get("top_sentences"), list) else []
        out[entity] = [item for item in top_sentences if isinstance(item, dict)]
    return out


def source_key(source: Any) -> str:
    if isinstance(source, list) and len(source) == 3:
        return f"{source[0]},{source[1]},{source[2]}"
    return ""


def graph_risk(nodes: Dict[str, Dict[str, Any]], edges: Sequence[Dict[str, str]], votes: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    incoming: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    outgoing: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for edge in edges:
        incoming[edge["target"]].append(edge)
        outgoing[edge["source"]].append(edge)
    correct_targets = {str(vote.get("target_node") or "") for vote in votes if str(vote.get("final_result") or "").lower() == "correct"}
    correct_sources = {
        str(source)
        for vote in votes
        if str(vote.get("final_result") or "").lower() == "correct"
        for source in (vote.get("source_nodes") or [])
    }
    out: Dict[str, Dict[str, Any]] = {}
    for node_id in nodes:
        is_correct_target = node_id in correct_targets
        is_correct_source = node_id in correct_sources
        is_leaf = not incoming.get(node_id)
        is_rootish = node_id.upper() in {"NROOT", "ROOT"} or "ROOT" in node_id.upper()
        if is_rootish or is_correct_target:
            risk = "high"
            reason = "fresh-judged target/root"
        elif is_correct_source:
            risk = "high"
            reason = "premise used by a correct fresh-judged step"
        elif is_leaf:
            risk = "low"
            reason = "leaf/non-judged support node"
        else:
            risk = "medium"
            reason = "intermediate or unjudged connective node"
        out[node_id] = {
            "risk": risk,
            "reason": reason,
            "incoming": len(incoming.get(node_id, [])),
            "outgoing": len(outgoing.get(node_id, [])),
            "source": source_key(nodes[node_id].get("source")),
        }
    return out


def best_node_matches(entity: str, nodes: Dict[str, Dict[str, Any]], risk: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for node_id, node in nodes.items():
        match = entity_match(entity, str(node.get("text") or ""))
        if not match["matched_tokens"] and not match["exact_phrase_match"]:
            continue
        rows.append(
            {
                "node_id": node_id,
                "source": source_key(node.get("source")),
                "risk": risk.get(node_id, {}).get("risk", ""),
                "risk_reason": risk.get(node_id, {}).get("reason", ""),
                "exact_phrase_match": match["exact_phrase_match"],
                "token_coverage": match["token_coverage"],
                "matched_tokens": ";".join(match["matched_tokens"]),
                "text": compact_text(str(node.get("text") or ""), max_chars=300),
            }
        )
    return sorted(rows, key=lambda row: (row["exact_phrase_match"], row["token_coverage"]), reverse=True)


def source_already_used(nodes: Dict[str, Dict[str, Any]], source: Any) -> List[str]:
    key = source_key(source)
    if not key:
        return []
    return [node_id for node_id, node in nodes.items() if source_key(node.get("source")) == key]


def choose_patch_lane(*, missing_count: int, best_nodes: List[Dict[str, Any]], top_evidence: List[Dict[str, Any]]) -> Tuple[str, str, str]:
    if not top_evidence:
        return (
            "anchor_bootstrap_evidence_search",
            "packet has no strong source sentence for this entity; run paper-local retrieval before graph edits",
            "high",
        )
    if not best_nodes:
        return (
            "source_backed_leaf_addition",
            "add one source-backed leaf for the missing entity, then connect it with a minimal bridge while preserving current correct reasoning",
            "medium",
        )
    if best_nodes[0].get("risk") == "high":
        return (
            "leaf_addition_not_high_risk_rewrite",
            "entity is only partially present in high-risk/root or judged nodes; avoid direct rewrite and add source-backed support below the existing reasoning",
            "medium",
        )
    if missing_count == 1:
        return (
            "low_risk_surface_calibration",
            "existing low/medium-risk node already partially matches; try a minimal text calibration with unchanged topology/source if fresh REA remains stable",
            "low",
        )
    return (
        "multi_entity_bridge_repair",
        "several entities are missing; repair as a small evidence-backed bridge rather than independent root rewrites",
        "medium",
    )


def select_rows(rows: Sequence[Dict[str, str]], failure_types: set[str], specs: set[str]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for row in rows:
        if failure_types and row.get("failure_type") not in failure_types:
            continue
        if specs and row.get("paper_spec") not in specs:
            continue
        out.append(row)
    return out


def audit_row(row: Dict[str, str], packet_by_spec: Dict[str, Path]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    paper_spec = row.get("paper_spec", "")
    final_graph = resolve_path(row.get("final_graph", "")) if row.get("final_graph") else Path("")
    final_eval_dir = resolve_path(row.get("final_eval_dir", "")) if row.get("final_eval_dir") else Path("")
    eval_payload = read_json(final_eval_dir / "evaluation_results.json", {}) if final_eval_dir else {}
    summary = eval_payload.get("evaluation_summary") if isinstance(eval_payload.get("evaluation_summary"), dict) else {}
    entities = list(eval_payload.get("entities") or [])
    packet_path = packet_by_spec.get(paper_spec)
    packet = read_json(packet_path, {}) if packet_path else {}
    if not entities and isinstance(packet.get("paper_anchor"), dict):
        entities = list(packet.get("paper_anchor", {}).get("entities") or [])

    nodes, edges = parse_dot(final_graph) if final_graph else ({}, [])
    votes, correct_text = correct_vote_texts(final_eval_dir) if final_eval_dir else ([], "")
    risk = graph_risk(nodes, edges, votes)
    all_node_text = " ".join(str(node.get("text") or "") for node in nodes.values())
    evidence = entity_evidence_map(packet) if isinstance(packet, dict) else {}
    input_path = ""
    if isinstance(packet, dict):
        source_paths = packet.get("source_paths") if isinstance(packet.get("source_paths"), dict) else {}
        input_path = str(source_paths.get("input_data") or "")
    input_data = read_json(resolve_path(input_path), {}) if input_path else {}
    official_cov = official_source_coverage(
        entities=entities,
        nodes=nodes,
        edges=edges,
        votes=votes,
        eval_payload=eval_payload if isinstance(eval_payload, dict) else {},
        input_data=input_data,
    )
    official_by_entity = {
        str(item.get("entity")): item
        for item in official_cov.get("entity_rows", [])
        if isinstance(item, dict)
    }

    entity_rows: List[Dict[str, Any]] = []
    missing_entities: List[str] = list(official_cov.get("missing", []))
    covered_entities: List[str] = list(official_cov.get("covered", []))
    patch_tasks: List[Dict[str, Any]] = []
    for entity in entities:
        graph_match = entity_match(entity, all_node_text)
        correct_match = entity_match(entity, correct_text)
        official_entity = official_by_entity.get(str(entity), {})
        covered = bool(official_entity.get("covered"))
        best_nodes = best_node_matches(entity, nodes, risk)[:5]
        top_evidence = evidence.get(entity, [])[:5]
        lane, recommendation, risk_level = choose_patch_lane(
            missing_count=0,
            best_nodes=best_nodes,
            top_evidence=top_evidence,
        )
        top_source = top_evidence[0].get("source") if top_evidence else []
        entity_row = {
            "paper_spec": paper_spec,
            "failure_type": row.get("failure_type", ""),
            "model": row.get("model", ""),
            "paper": row.get("paper", ""),
            "entity": entity,
            "covered_by_correct_vote_text": correct_match["covered"],
            "covered_by_graph_text": graph_match["covered"],
            "covered_by_official_source_terms": covered,
            "official_matched_reasoning_entity": official_entity.get("matched_reasoning_entity", ""),
            "computed_covered": covered,
            "graph_token_coverage": graph_match["token_coverage"],
            "correct_token_coverage": correct_match["token_coverage"],
            "graph_matched_tokens": ";".join(graph_match["matched_tokens"]),
            "correct_matched_tokens": ";".join(correct_match["matched_tokens"]),
            "best_node": best_nodes[0].get("node_id", "") if best_nodes else "",
            "best_node_source": best_nodes[0].get("source", "") if best_nodes else "",
            "best_node_risk": best_nodes[0].get("risk", "") if best_nodes else "",
            "top_evidence_source": source_key(top_source),
            "top_evidence_score": top_evidence[0].get("score", "") if top_evidence else "",
            "top_evidence_exact": top_evidence[0].get("exact_phrase_match", "") if top_evidence else "",
            "top_evidence_sentence": compact_text(top_evidence[0].get("sentence", ""), max_chars=360) if top_evidence else "",
            "top_evidence_used_by_nodes": ";".join(source_already_used(nodes, top_source)) if top_evidence else "",
            "candidate_lane": lane if not covered else "",
            "risk_level": risk_level if not covered else "",
            "recommendation": recommendation if not covered else "",
        }
        entity_rows.append(entity_row)
        if not covered:
            patch_tasks.append(
                {
                    **entity_row,
                    "final_CG": row.get("final_CG", summary.get("entity_coverage_score", "")),
                    "final_REA": row.get("final_REA", summary.get("accuracy_score", "")),
                    "final_graph": rel(final_graph) if final_graph else "",
                    "final_eval_dir": rel(final_eval_dir) if final_eval_dir else "",
                    "packet": rel(packet_path) if packet_path else "",
                    "input_data": rel(resolve_path(input_path)) if input_path else "",
                    "safe_edit_boundary": (
                        "Do not rewrite NROOT or judged premises first. Prefer source-backed leaf/bridge; fresh-evaluate CG/REA, then run ANS guard."
                    ),
                }
            )

    cg = to_float(row.get("final_CG", summary.get("entity_coverage_score", 0.0)))
    rea = to_float(row.get("final_REA", summary.get("accuracy_score", 0.0)))
    vote_counts = Counter(str(vote.get("final_result") or "").lower() for vote in votes)
    row_audit = {
        "paper_spec": paper_spec,
        "model": row.get("model", ""),
        "paper": row.get("paper", ""),
        "failure_type": row.get("failure_type", ""),
        "final_CG": cg,
        "final_REA": rea,
        "reported_CG": row.get("final_CG", ""),
        "computed_missing_count": len(missing_entities),
        "computed_missing_entities": "; ".join(missing_entities),
        "computed_covered_count": len(covered_entities),
        "computed_covered_entities": "; ".join(covered_entities),
        "anchor_entity_count": len(entities),
        "official_reasoning_entity_count": official_cov.get("reasoning_entity_count", 0),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "vote_correct": vote_counts.get("correct", 0),
        "vote_wrong": vote_counts.get("wrong", 0),
        "vote_error": vote_counts.get("error", 0),
        "packet": rel(packet_path) if packet_path else "",
        "input_data": rel(resolve_path(input_path)) if input_path else "",
        "final_graph": rel(final_graph) if final_graph else "",
        "final_eval_dir": rel(final_eval_dir) if final_eval_dir else "",
    }
    details = {
        "row": row_audit,
        "entities": entity_rows,
        "nodes": nodes,
        "edges": edges,
        "node_risk": risk,
        "official_source_coverage": official_cov,
    }
    return row_audit, patch_tasks, details


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--residual-csv", default=str(DEFAULT_RESIDUAL_CSV))
    parser.add_argument("--packet-root", default=str(DEFAULT_PACKET_ROOT))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument(
        "--failure-type",
        action="append",
        default=["final_metric_gate_failed"],
        help="Residual failure_type to audit. Repeatable. Use empty string with --all-failure-types to disable.",
    )
    parser.add_argument("--paper-spec", action="append", default=[], help="Specific paper_spec to audit.")
    parser.add_argument("--all-failure-types", action="store_true")
    parser.add_argument("--label", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    residual_csv = resolve_path(args.residual_csv)
    packet_root = resolve_path(args.packet_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    label = safe_slug(args.label or f"missing_entity_audit_{timestamp}")
    out_root = resolve_path(args.out_root) / label

    rows = read_csv(residual_csv)
    failure_types = set() if args.all_failure_types else {item for item in args.failure_type if item}
    specs = {item for item in args.paper_spec if item}
    selected = select_rows(rows, failure_types, specs)
    packets = packet_index(packet_root)

    row_audits: List[Dict[str, Any]] = []
    patch_tasks: List[Dict[str, Any]] = []
    detail_payload: Dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "residual_csv": rel(residual_csv),
        "packet_root": rel(packet_root),
        "failure_types": sorted(failure_types) if failure_types else "all",
        "paper_specs": sorted(specs),
        "rows": {},
    }
    for row in selected:
        row_audit, row_tasks, details = audit_row(row, packets)
        row_audits.append(row_audit)
        patch_tasks.extend(row_tasks)
        detail_payload["rows"][row_audit["paper_spec"]] = details

    summary = {
        "created_at": detail_payload["created_at"],
        "residual_csv": rel(residual_csv),
        "selected_rows": len(selected),
        "rows_with_missing_entities": sum(1 for row in row_audits if row.get("computed_missing_count")),
        "patch_task_count": len(patch_tasks),
        "failure_type_counts": dict(Counter(row.get("failure_type", "") for row in row_audits)),
        "patch_lane_counts": dict(Counter(task.get("candidate_lane", "") for task in patch_tasks)),
        "out_root": rel(out_root),
    }
    row_fields = [
        "paper_spec",
        "model",
        "paper",
        "failure_type",
        "final_CG",
        "final_REA",
        "computed_missing_count",
        "computed_missing_entities",
        "computed_covered_count",
        "anchor_entity_count",
        "official_reasoning_entity_count",
        "node_count",
        "edge_count",
        "vote_correct",
        "vote_wrong",
        "vote_error",
        "packet",
        "input_data",
        "final_graph",
        "final_eval_dir",
    ]
    task_fields = [
        "paper_spec",
        "failure_type",
        "model",
        "paper",
        "entity",
        "final_CG",
        "final_REA",
        "candidate_lane",
        "risk_level",
        "recommendation",
        "top_evidence_source",
        "top_evidence_score",
        "top_evidence_exact",
        "top_evidence_sentence",
        "top_evidence_used_by_nodes",
        "best_node",
        "best_node_source",
        "best_node_risk",
        "graph_token_coverage",
        "correct_token_coverage",
        "graph_matched_tokens",
        "correct_matched_tokens",
        "safe_edit_boundary",
        "packet",
        "input_data",
        "final_graph",
        "final_eval_dir",
    ]
    write_json(out_root / "SUMMARY.json", summary)
    write_csv(out_root / "ROW_AUDIT.csv", row_audits, row_fields)
    write_csv(out_root / "MISSING_ENTITY_PATCH_TASKS.csv", patch_tasks, task_fields)
    write_json(out_root / "DETAILS.json", detail_payload)
    write_text(
        out_root / "README.md",
        "\n".join(
            [
                "# Missing-Entity Repair Audit",
                "",
                "Provider-free audit for current-version residual repair. This package does not edit graphs,",
                "does not call judges, and does not rewrite canonical accounting.",
                "",
                f"- Selected rows: {summary['selected_rows']}",
                f"- Rows with computed missing entities: {summary['rows_with_missing_entities']}",
                f"- Patch tasks: {summary['patch_task_count']}",
                "",
                "Use `MISSING_ENTITY_PATCH_TASKS.csv` as the repair queue. Each task must still",
                "pass fresh `CG=1.0 / REA=1.0` and ANS non-regression before merge.",
            ]
        )
        + "\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
