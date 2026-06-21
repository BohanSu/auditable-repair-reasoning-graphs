#!/usr/bin/env python3
"""Build evidence-bound regeneration packets for the residual-50 closeout.

This script does not call model providers and does not rewrite 350-row
accounting. It creates a clean, auditable input layer for the next closeout
attempt: each residual row gets a compact paper-evidence packet, a strict
graph_spec prompt, quality guards, and any audited frontier information that
should be preserved.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


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
DEFAULT_QUEUE = RESIDUAL_ROOT / "RESIDUAL_50_CLOSEOUT_QUEUE.csv"
DEFAULT_FRONTIER = RESIDUAL_ROOT / "runs" / "CANDIDATE_FRONTIER.csv"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration"

STANDARD_EDGE_TYPES = [
    "deduction-rule",
    "deduction-case",
    "abduction-phenomenon",
    "abduction-knowledge",
    "induction-case",
    "induction-common",
]

LANE_ORDER = {
    "anchor_bootstrap_then_semantic_repair": 1,
    "entity_coverage_targeted_repair": 2,
    "non_regression_selection_or_merge": 3,
    "bounded_final_judge_feedback_repair": 4,
}

TOKEN_RE = re.compile(r"[^\W_]+", re.IGNORECASE | re.UNICODE)
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


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def safe_slug(value: str, *, limit: int = 120) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
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


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_match_text(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).lower()


def tokens(text: str) -> List[str]:
    normalized = normalize_match_text(text)
    return [tok.lower() for tok in TOKEN_RE.findall(normalized) if tok]


def token_variants(token: str) -> set[str]:
    """Return conservative lexical variants for sentence-level evidence search.

    This is not a semantic synonym expander. It only handles normalization
    failures observed in residual packets: Unicode forms, plurals, common
    derivational suffixes, and Greek-symbol names used in scientific notation.
    """
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


def compact_text(text: str, *, max_chars: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def split_semicolon_list(value: str) -> List[str]:
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def parse_specs(values: Sequence[str], specs_file: str = "") -> List[str]:
    specs = [str(value).strip() for value in values if str(value).strip()]
    if specs_file:
        path = resolve_path(specs_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                specs.append(line)
    out: List[str] = []
    seen: set[str] = set()
    for spec in specs:
        if spec not in seen:
            seen.add(spec)
            out.append(spec)
    return out


def select_rows(
    rows: List[Dict[str, str]],
    *,
    lanes: Iterable[str],
    paper_specs: Iterable[str],
    limit: int,
) -> List[Dict[str, str]]:
    lane_set = set(lanes)
    spec_set = set(paper_specs)
    selected = [
        row
        for row in rows
        if (not lane_set or row.get("lane") in lane_set)
        and (not spec_set or row.get("paper_spec") in spec_set)
    ]
    selected.sort(
        key=lambda row: (
            LANE_ORDER.get(row.get("lane", ""), 99),
            int(row.get("priority") or 999999),
            row.get("paper_spec", ""),
        )
    )
    return selected[:limit] if limit > 0 else selected


def latest_eval_dir(path: Path) -> Optional[Path]:
    if (path / "evaluation_results.json").exists():
        return path
    root = path if path.name == "evaluation_outputs" else path / "evaluation_outputs"
    if not root.exists():
        return None
    candidates = sorted(
        [item for item in root.iterdir() if item.is_dir() and (item / "evaluation_results.json").exists()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_eval_results(path_text: str) -> tuple[Optional[Path], Dict[str, Any]]:
    if not path_text:
        return None, {}
    path = resolve_path(path_text)
    eval_dir = latest_eval_dir(path)
    if not eval_dir:
        return None, {}
    data = read_json(eval_dir / "evaluation_results.json", {})
    return eval_dir, data if isinstance(data, dict) else {}


def find_input_data(row: Dict[str, str]) -> Optional[Path]:
    candidates: List[Path] = []
    for key in ("generation_final_clean_graph", "terminal_graph"):
        value = str(row.get(key) or "").strip()
        if value:
            graph_path = resolve_path(value)
            candidates.append(graph_path.parent / "input_data.json")
    source_eval_dir = str(row.get("source_eval_dir") or "").strip()
    if source_eval_dir:
        source_path = resolve_path(source_eval_dir)
        if source_path.name == "evaluation_outputs":
            candidates.append(source_path.parent / "input_data.json")
        elif source_path.parent.name == "evaluation_outputs":
            candidates.append(source_path.parent.parent / "input_data.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    model = str(row.get("model") or row.get("paper_spec", "").split(":", 1)[0])
    paper = str(row.get("paper") or row.get("paper_spec", "").split(":", 1)[-1])
    search_root = PROJECT_ROOT / "data" / "teacher_pool" / "model_outputs" / model / paper
    if search_root.exists():
        matches = sorted(search_root.glob("20*/input_data.json"))
        if matches:
            return matches[-1]
    return None


def sentence_inventory(input_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sent in (input_data.get("introduction") or {}).get("sentences") or []:
        if not isinstance(sent, dict):
            continue
        idx = to_int(sent.get("idx"))
        if idx is None:
            continue
        sentence = str(sent.get("sentence") or "").strip()
        viewpoints = [str(item).strip() for item in sent.get("viewpoints") or [] if str(item).strip()]
        ref_items: List[Dict[str, Any]] = []
        refs = sent.get("references") if isinstance(sent.get("references"), dict) else {}
        for ref_i, (ref_id, ref_payload) in enumerate(refs.items(), start=1):
            if isinstance(ref_payload, dict):
                opinions = [str(v).strip() for _k, v in ref_payload.items() if str(v).strip()]
            elif isinstance(ref_payload, list):
                opinions = [str(v).strip() for v in ref_payload if str(v).strip()]
            else:
                opinions = []
            for opinion_i, opinion in enumerate(opinions, start=1):
                ref_items.append(
                    {
                        "source": [idx, ref_i, opinion_i],
                        "ref_id": ref_id,
                        "text": opinion,
                    }
                )
        combined_parts = [sentence]
        combined_parts.extend(viewpoints)
        combined_parts.extend(item["text"] for item in ref_items)
        rows.append(
            {
                "idx": idx,
                "source": [idx, 0, 0],
                "sentence": sentence,
                "viewpoints": viewpoints,
                "references": ref_items,
                "text_for_matching": " ".join(part for part in combined_parts if part),
                "tokens": token_variant_index(" ".join(part for part in combined_parts if part)),
            }
        )
    rows.sort(key=lambda item: item["idx"])
    return rows


def score_entity_sentence(entity: str, sent: Dict[str, Any]) -> tuple[float, List[str], bool]:
    entity_norm = normalize_text(entity)
    text_norm = normalize_text(str(sent.get("text_for_matching") or ""))
    entity_tokens = token_variant_index(entity)
    sentence_tokens = sent.get("tokens") if isinstance(sent.get("tokens"), set) else set(tokens(text_norm))
    overlap_tokens = sorted(entity_tokens & sentence_tokens)
    exact = bool(entity_norm and entity_norm in text_norm)
    if not exact:
        entity_raw = normalize_match_text(entity)
        text_raw = normalize_match_text(str(sent.get("text_for_matching") or ""))
        entity_phrase_variants = {entity_raw}
        if entity_raw.endswith("s"):
            entity_phrase_variants.add(entity_raw[:-1])
        exact = any(variant and variant in text_raw for variant in entity_phrase_variants)
    compact_entity = compact_match_text(entity)
    compact_text_value = compact_match_text(str(sent.get("text_for_matching") or ""))
    compact_exact = bool(
        compact_entity
        and len(compact_entity) >= 6
        and compact_entity in compact_text_value
    )
    if compact_exact:
        exact = True
    if not entity_tokens:
        return 0.0, [], False
    coverage = len(overlap_tokens) / max(1, len(entity_tokens))
    score = coverage * 4.0 + len(overlap_tokens) * 0.45
    if exact:
        score += 6.0
    if len(entity_tokens) >= 2 and len(overlap_tokens) >= 2:
        score += 1.5
    return score, overlap_tokens, exact


def entity_evidence_map(
    entities: List[str],
    sentences: List[Dict[str, Any]],
    *,
    top_k: int,
    min_score: float,
) -> List[Dict[str, Any]]:
    mapped: List[Dict[str, Any]] = []
    for entity in entities:
        scored: List[Dict[str, Any]] = []
        for sent in sentences:
            score, matched_tokens, exact = score_entity_sentence(entity, sent)
            if score <= 0:
                continue
            scored.append(
                {
                    "idx": sent["idx"],
                    "source": sent["source"],
                    "score": round(score, 4),
                    "exact_phrase_match": exact,
                    "matched_tokens": matched_tokens,
                    "sentence": compact_text(sent.get("sentence", ""), max_chars=500),
                    "viewpoints": [compact_text(v, max_chars=280) for v in sent.get("viewpoints", [])[:3]],
                }
            )
        scored.sort(key=lambda item: (-float(item["score"]), item["idx"]))
        top = scored[:top_k]
        mapped.append(
            {
                "entity": entity,
                "status": "mapped" if top and float(top[0]["score"]) >= min_score else "weak_or_missing",
                "top_sentences": top,
            }
        )
    return mapped


def load_candidate_frontier(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    frontier: Dict[str, Dict[str, Any]] = {}
    for row in read_csv(path):
        paper_spec = str(row.get("paper_spec") or "").strip()
        graph_text = str(row.get("candidate_graph") or "").strip()
        if not paper_spec or not graph_text:
            continue
        graph_path = resolve_path(graph_text)
        if not graph_path.exists():
            continue
        cg = to_float(row.get("CG")) or 0.0
        rea = to_float(row.get("REA")) or 0.0
        covered = to_int(row.get("covered_entities")) or 0
        total = to_int(row.get("total_entities")) or 0
        bad_votes = to_int(row.get("noncorrect_vote_count")) or 0
        candidate = {
            "run": row.get("run", ""),
            "paper_spec": paper_spec,
            "eval_name": row.get("eval_name", ""),
            "CG": cg,
            "REA": rea,
            "covered_entities": covered,
            "total_entities": total,
            "valid_reasoning_steps": to_int(row.get("valid_reasoning_steps")),
            "total_reasoning_steps": to_int(row.get("total_reasoning_steps")),
            "noncorrect_targets": row.get("noncorrect_targets", ""),
            "noncorrect_vote_count": bad_votes,
            "candidate_graph": rel(graph_path),
            "eval_dir": rel(resolve_path(str(row.get("eval_dir") or ""))) if row.get("eval_dir") else "",
        }
        score = (int(cg >= 1.0 - 1e-9), covered, rea, -bad_votes, total)
        previous = frontier.get(paper_spec)
        if previous is None or score > previous["_score"]:
            candidate["_score"] = score
            frontier[paper_spec] = candidate
    for candidate in frontier.values():
        candidate.pop("_score", None)
    return frontier


def frontier_eval_payload(frontier: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not frontier:
        return {}
    eval_dir_text = str(frontier.get("eval_dir") or "")
    _eval_dir, results = load_eval_results(eval_dir_text)
    if not results:
        return {}
    correct_targets: List[str] = []
    for item in results.get("reused_votes") or []:
        if isinstance(item, dict) and str(item.get("source_result", "")).lower() == "correct":
            correct_targets.append(str(item.get("target_node", "")))
    for item in results.get("judged_votes") or []:
        if isinstance(item, dict) and str(item.get("final_result", "")).lower() == "correct":
            correct_targets.append(str(item.get("target_node", "")))
    failed_vote = results.get("first_failed_vote") if isinstance(results.get("first_failed_vote"), dict) else {}
    return {
        "clean": results.get("clean"),
        "metric_bearing": results.get("metric_bearing"),
        "accepted_by_repair_root_judge": results.get("accepted_by_repair_root_judge"),
        "correct_targets": [target for target in correct_targets if target],
        "first_failed_vote": {
            "reasoning_id": failed_vote.get("reasoning_id"),
            "target_node": failed_vote.get("target_node"),
            "reasoning_type": failed_vote.get("reasoning_type"),
            "target_content": failed_vote.get("target_content"),
            "source_contents": failed_vote.get("source_contents"),
            "model_results": failed_vote.get("model_results"),
            "majority_reason": failed_vote.get("vote_breakdown", {}).get("decision")
            if isinstance(failed_vote.get("vote_breakdown"), dict)
            else "",
        }
        if failed_vote
        else {},
    }


def anchor_from_row(row: Dict[str, str]) -> tuple[str, List[str], Dict[str, Any]]:
    eval_candidates = [row.get("terminal_eval_dir", ""), row.get("source_eval_dir", "")]
    for candidate in eval_candidates:
        _eval_dir, results = load_eval_results(str(candidate or ""))
        if not results:
            continue
        core_idea = str(results.get("core_idea") or "").strip()
        entities = [str(item).strip() for item in results.get("entities") or [] if str(item).strip()]
        if core_idea or entities:
            return core_idea, entities, {"source": candidate, "evaluation_summary": results.get("evaluation_summary", {})}
    entities = split_semicolon_list(row.get("final_anchor_entities", ""))
    return "", entities, {"source": "RESIDUAL_50_CLOSEOUT_QUEUE.final_anchor_entities"}


def evidence_sentence_union(
    entity_map: List[Dict[str, Any]],
    sentences: List[Dict[str, Any]],
    *,
    max_sentences: int,
) -> List[Dict[str, Any]]:
    by_idx = {int(item["idx"]): item for item in sentences}
    selected: Dict[int, float] = {}
    for entity in entity_map:
        for rank, sent in enumerate(entity.get("top_sentences") or []):
            idx = to_int(sent.get("idx"))
            if idx is None:
                continue
            selected[idx] = max(selected.get(idx, 0.0), float(sent.get("score") or 0.0) - rank * 0.01)
    ordered = sorted(selected, key=lambda idx: (-selected[idx], idx))
    if len(ordered) > max_sentences:
        ordered = ordered[:max_sentences]
    compact: List[Dict[str, Any]] = []
    for idx in sorted(ordered):
        sent = by_idx.get(idx)
        if not sent:
            continue
        compact.append(
            {
                "idx": idx,
                "source": [idx, 0, 0],
                "sentence": compact_text(sent.get("sentence", ""), max_chars=700),
                "viewpoints": [compact_text(v, max_chars=320) for v in sent.get("viewpoints", [])[:3]],
            }
        )
    return compact


def missing_entity_names(entity_map: List[Dict[str, Any]]) -> List[str]:
    return [str(item.get("entity")) for item in entity_map if item.get("status") != "mapped"]


def build_prompt(packet: Dict[str, Any]) -> str:
    anchor = packet["paper_anchor"]
    evidence = packet["evidence"]
    frontier = packet.get("audited_frontier") or {}
    failed = (frontier.get("evaluation_payload") or {}).get("first_failed_vote") or packet.get("failed_vote") or {}
    quality = packet.get("quality_guard") or {}
    required_entities = anchor.get("entities") or []

    evidence_lines: List[str] = []
    for sent in evidence.get("prompt_sentences") or []:
        evidence_lines.append(f"[S{sent['idx']}] {sent['sentence']}")
        for vp_i, viewpoint in enumerate(sent.get("viewpoints") or [], start=1):
            evidence_lines.append(f"  [S{sent['idx']}.V{vp_i}] {viewpoint}")

    entity_lines: List[str] = []
    entity_map = evidence.get("entity_evidence") or []
    for item in entity_map:
        refs = [f"S{sent.get('idx')}" for sent in item.get("top_sentences") or []]
        entity_lines.append(f"- {item.get('entity')}: {', '.join(refs[:4]) or 'NO_STRONG_MATCH'}")

    frontier_block = "No audited frontier is available for this row."
    if frontier:
        frontier_block = (
            f"Best audited frontier: CG={frontier.get('CG')}, REA={frontier.get('REA')}, "
            f"coverage={frontier.get('covered_entities')}/{frontier.get('total_entities')}, "
            f"failed_targets={frontier.get('noncorrect_targets') or 'none'}.\n"
            "Preserve the frontier's coverage-bearing content unless replacing an explicitly failed target with a better source-grounded unit."
        )

    failed_block = "No explicit failed vote payload is available."
    if failed:
        failed_block = (
            f"Failed target: {failed.get('target_node')} ({failed.get('reasoning_type')}).\n"
            f"Rejected conclusion: {failed.get('target_content')}\n"
            f"Judge decision: {failed.get('majority_reason') or failed.get('model_results')}\n"
            "Do not repeat the rejected logical leap unchanged."
        )

    return f"""You are constructing one evidence-bound PEARL graph_spec candidate for a residual scientific reasoning graph.

Return ONLY one JSON object. Do not output DOT, markdown, prose, or comments.

OUTPUT SCHEMA:
{{
  "r": "NROOT",
  "n": [
    {{"i": "E1", "p": [1, 0, 0], "x": "Currently ..."}},
    {{"i": "R1", "p": [0, 0, 0], "x": "deduction-reasoning: ..."}}
  ],
  "e": [
    {{"u": "E1", "v": "R1", "y": "deduction-case"}},
    {{"u": "E2", "v": "R1", "y": "deduction-rule"}}
  ]
}}

HARD STRUCTURAL RULES:
- Use root id exactly "NROOT".
- Use node ids E1, E2, ... for direct evidence nodes; R1, R2, ... for intermediate reasoning nodes; NROOT for the final paper-level conclusion.
- Every node must have exactly one source tuple `p=[X,Y,Z]`.
- Direct sentence evidence must use `[sentence_id,0,0]`; original viewpoints may use `[sentence_id,viewpoint_number,0]`; use `[0,0,0]` only for concise bridge/rule/reasoning nodes.
- Every reasoning conclusion must have exactly one complete incoming reasoning unit:
  - deduction: one `deduction-rule` edge and one `deduction-case` edge.
  - abduction: one `abduction-knowledge` edge and one `abduction-phenomenon` edge.
  - induction: one `induction-common` edge and one or more `induction-case` edges.
- Do not mix reasoning families into the same target node.
- No self-loops, no isolated nodes, and no outgoing edges from NROOT.
- Edge labels must be exactly one of: {", ".join(STANDARD_EDGE_TYPES)}.

HARD SEMANTIC RULES:
- Use only the evidence sentences/viewpoints listed below plus minimal domain-common bridge rules.
- Do not invent study findings, methods, materials, or mechanisms that are not supported by the listed evidence.
- Cover every required entity at least once in a node text and connect that content to NROOT.
- Keep the graph compact: prefer 14-24 nodes and 7-12 reasoning targets unless the evidence requires more.
- The graph must be acceptable for fresh PEARL evaluation: final CG=1.0 and final REA=1.0.
- Preserve source grounding. The current best main-factual ANS guard is {quality.get("current_best_main_factual_ans")}; do not add unsupported claims to chase coverage.

PAPER ANCHOR:
Core idea: {anchor.get("core_idea") or "(use the required entities and evidence to synthesize a paper-level idea)"}

Required entities:
{json.dumps(required_entities, ensure_ascii=False)}

Entity-to-evidence map:
{chr(10).join(entity_lines)}

AUDITED FRONTIER:
{frontier_block}

FAILED / RISKY TARGET:
{failed_block}

EVIDENCE SENTENCES:
{chr(10).join(evidence_lines)}
"""


def build_packet(
    row: Dict[str, str],
    *,
    frontier_by_spec: Dict[str, Dict[str, Any]],
    top_k: int,
    min_entity_score: float,
    max_prompt_sentences: int,
) -> Dict[str, Any]:
    input_path = find_input_data(row)
    if not input_path:
        raise FileNotFoundError(f"input_data.json not found for {row.get('paper_spec')}")
    input_data = read_json(input_path, {})
    if not isinstance(input_data, dict):
        raise ValueError(f"input_data.json is not an object: {input_path}")

    core_idea, entities, anchor_source = anchor_from_row(row)
    if not entities:
        raise ValueError(f"no anchor entities available for {row.get('paper_spec')}")

    sentences = sentence_inventory(input_data)
    entity_map = entity_evidence_map(entities, sentences, top_k=top_k, min_score=min_entity_score)
    prompt_sentences = evidence_sentence_union(entity_map, sentences, max_sentences=max_prompt_sentences)
    frontier = frontier_by_spec.get(str(row.get("paper_spec") or ""))
    frontier_payload = dict(frontier or {})
    if frontier_payload:
        frontier_payload["evaluation_payload"] = frontier_eval_payload(frontier_payload)

    terminal_eval_dir, terminal_eval = load_eval_results(row.get("terminal_eval_dir", ""))
    failed_vote = {}
    if isinstance(terminal_eval.get("first_failed_vote"), dict):
        failed_vote = terminal_eval["first_failed_vote"]

    packet = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "evidence_bound_regeneration_packet_v1",
        "paper_spec": row.get("paper_spec", ""),
        "model": row.get("model", ""),
        "paper": row.get("paper", ""),
        "run_id": row.get("run_id", ""),
        "lane": row.get("lane", ""),
        "failure_type": row.get("failure_type", ""),
        "source_paths": {
            "input_data": rel(input_path),
            "step1_raw_output": row.get("step1_raw_output", ""),
            "step2_autonomous_repair_output": row.get("step2_autonomous_repair_output", ""),
            "generation_final_clean_graph": row.get("generation_final_clean_graph", ""),
            "source_eval_dir": row.get("source_eval_dir", ""),
            "terminal_graph": row.get("terminal_graph", ""),
            "terminal_eval_dir": row.get("terminal_eval_dir", ""),
            "terminal_eval_resolved": rel(terminal_eval_dir) if terminal_eval_dir else "",
        },
        "paper_anchor": {
            "core_idea": core_idea,
            "entities": entities,
            "anchor_source": anchor_source,
        },
        "current_metrics": {
            "original_CG": to_float(row.get("current_original_CG")),
            "original_REA": to_float(row.get("current_original_REA")),
            "final_CG": to_float(row.get("current_final_CG")),
            "final_REA": to_float(row.get("current_final_REA")),
            "final_covered_entities": to_int(row.get("final_covered_entities")),
            "final_total_entities": to_int(row.get("final_total_entities")),
            "final_missing_entities": to_int(row.get("final_missing_entities")),
        },
        "quality_guard": {
            "row_text": row.get("quality_guard", ""),
            "raw_main_factual_ans": to_float(row.get("raw_main_factual_ans")),
            "step2_main_factual_ans": to_float(row.get("step2_main_factual_ans")),
            "pearl_main_factual_ans": to_float(row.get("pearl_main_factual_ans")),
            "current_best_main_factual_ans": to_float(row.get("current_best_main_factual_ans")),
            "acceptance_gate": {"final_CG": 1.0, "final_REA": 1.0},
        },
        "evidence": {
            "sentence_count": len(sentences),
            "prompt_sentence_count": len(prompt_sentences),
            "prompt_sentences": prompt_sentences,
            "entity_evidence": entity_map,
            "weak_or_missing_entities": missing_entity_names(entity_map),
        },
        "audited_frontier": frontier_payload,
        "failed_vote": failed_vote,
        "regeneration_contract": {
            "output_protocol": "graph_spec_minimal_v2",
            "root_id": "NROOT",
            "node_id_policy": "E* for direct evidence, R* for intermediate reasoning, NROOT for final semantic root",
            "fresh_gate_required": True,
            "do_not_report_until": "fresh final CG=1.0 and REA=1.0",
            "provider_error_policy": "provider errors are retained as execution failures, not semantic closure",
        },
    }
    packet["generation_prompt"] = build_prompt(packet)
    return packet


def packet_dir_for(row: Dict[str, str], priority: int) -> Path:
    model = safe_slug(str(row.get("model") or row.get("paper_spec", "").split(":", 1)[0]))
    paper = safe_slug(str(row.get("paper") or row.get("paper_spec", "").split(":", 1)[-1]))
    run_id = safe_slug(str(row.get("run_id") or "run"))
    return Path(f"{priority:03d}_{model}__{paper}__{run_id}")


def build_readme(out_root: Path, summary: Dict[str, Any]) -> str:
    lane_lines = [f"- `{lane}`: {count}" for lane, count in sorted(summary["by_lane"].items())]
    return "\n".join(
        [
            "# Evidence-Bound Residual Regeneration",
            "",
            f"Created: {summary['created_at']}",
            "",
            "This directory is a clean staging area for the residual-50 closeout attempt.",
            "It does not modify the current 350-row accounting and it does not claim closure.",
            "",
            "## Contents",
            "",
            "- `packets/`: one evidence-bound JSON packet and prompt per residual row.",
            "- `PACKET_INDEX.csv/json`: compact index of packet paths, lanes, evidence status, and guards.",
            "- `DESIGN_NOTE.md`: rationale and execution boundary.",
            "- `model_attempts/`: reserved for optional provider-backed graph_spec attempts.",
            "",
            "## Lane Counts",
            "",
            *lane_lines,
            "",
            "## Acceptance Boundary",
            "",
            "A packet or generated graph is not a success. A row can only be merged after the existing PEARL fresh evaluation reports final `CG=1.0` and `REA=1.0`, with source-grounding guards preserved.",
            "",
        ]
    )


def build_design_note(summary: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Evidence-Bound Regeneration Design",
            "",
            "## Why This Module Exists",
            "",
            "The residual-50 smoke runs showed that prompt-level frontier guards can trade entity coverage against reasoning correctness. The next candidate source should therefore be evidence-bound before it enters PEARL, rather than asking a model to freely redraw DOT.",
            "",
            "## Contract",
            "",
            "1. Start from `input_data.json`, the fixed paper anchor, and audited frontier metrics when available.",
            "2. Bind every required entity to sentence-level evidence before generation.",
            "3. Ask the model for JSON `graph_spec` only, not free DOT.",
            "4. Locally preflight the graph_spec for source tuples, legal paired reasoning units, single root, and entity coverage.",
            "5. Enter the unchanged PEARL fresh evaluation gate; only final `CG=1.0` and `REA=1.0` can close a row.",
            "",
            "## Non-Claims",
            "",
            "- This package does not claim that any of the 50 residual rows are solved.",
            "- ANS/FActScore-style support remains a grounding diagnostic, not a replacement for CG/REA.",
            "- Provider failures are execution artifacts and must not be reported as semantic residual closure.",
            "",
            "## Current Packet Summary",
            "",
            f"- Packets: {summary['packet_count']}",
            f"- Weak/missing entity mappings: {summary['weak_or_missing_entity_rows']}",
            f"- Frontier-guarded packets: {summary['frontier_guarded_packets']}",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--candidate-frontier", default=str(DEFAULT_FRONTIER))
    parser.add_argument("--report-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--lanes", nargs="+", default=[])
    parser.add_argument("--paper-specs", nargs="+", default=[])
    parser.add_argument("--paper-specs-file", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--top-k-evidence", type=int, default=4)
    parser.add_argument("--min-entity-score", type=float, default=1.2)
    parser.add_argument("--max-prompt-sentences", type=int, default=32)
    parser.add_argument("--copy-input-data", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--overwrite", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue_path = resolve_path(args.queue)
    out_root = resolve_path(args.report_root)
    packets_root = out_root / "packets"
    attempts_root = out_root / "model_attempts"
    out_root.mkdir(parents=True, exist_ok=True)
    packets_root.mkdir(parents=True, exist_ok=True)
    attempts_root.mkdir(parents=True, exist_ok=True)

    frontier = load_candidate_frontier(resolve_path(args.candidate_frontier))
    rows = select_rows(
        read_csv(queue_path),
        lanes=args.lanes,
        paper_specs=parse_specs(args.paper_specs, args.paper_specs_file),
        limit=args.limit,
    )

    index_rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        rel_dir = packet_dir_for(row, int(row.get("priority") or idx))
        out_dir = packets_root / rel_dir
        if out_dir.exists() and not args.overwrite:
            continue
        try:
            packet = build_packet(
                row,
                frontier_by_spec=frontier,
                top_k=args.top_k_evidence,
                min_entity_score=args.min_entity_score,
                max_prompt_sentences=args.max_prompt_sentences,
            )
            packet_path = out_dir / "packet.json"
            prompt_path = out_dir / "generation_prompt.txt"
            write_json(packet_path, {key: value for key, value in packet.items() if key != "generation_prompt"})
            write_text(prompt_path, packet["generation_prompt"])
            input_data_path = resolve_path(packet["source_paths"]["input_data"])
            copied_input = ""
            if args.copy_input_data and input_data_path.exists():
                copied = out_dir / "input_data.json"
                shutil.copy2(input_data_path, copied)
                copied_input = rel(copied)
            weak_entities = packet["evidence"]["weak_or_missing_entities"]
            index_rows.append(
                {
                    "priority": row.get("priority", idx),
                    "paper_spec": packet["paper_spec"],
                    "lane": packet["lane"],
                    "failure_type": packet["failure_type"],
                    "packet_json": rel(packet_path),
                    "generation_prompt": rel(prompt_path),
                    "input_data": packet["source_paths"]["input_data"],
                    "copied_input_data": copied_input,
                    "entity_count": len(packet["paper_anchor"]["entities"]),
                    "weak_or_missing_entities": "; ".join(weak_entities),
                    "weak_or_missing_entity_count": len(weak_entities),
                    "prompt_sentence_count": packet["evidence"]["prompt_sentence_count"],
                    "frontier_guarded": bool(packet.get("audited_frontier")),
                    "frontier_CG": (packet.get("audited_frontier") or {}).get("CG", ""),
                    "frontier_REA": (packet.get("audited_frontier") or {}).get("REA", ""),
                    "frontier_failed_targets": (packet.get("audited_frontier") or {}).get("noncorrect_targets", ""),
                    "current_best_main_factual_ans": packet["quality_guard"]["current_best_main_factual_ans"],
                    "packet_sha256": sha256_file(packet_path),
                    "prompt_sha256": sha256_file(prompt_path),
                }
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "priority": row.get("priority", idx),
                    "paper_spec": row.get("paper_spec", ""),
                    "lane": row.get("lane", ""),
                    "error": str(exc),
                }
            )

    fieldnames = [
        "priority",
        "paper_spec",
        "lane",
        "failure_type",
        "packet_json",
        "generation_prompt",
        "input_data",
        "copied_input_data",
        "entity_count",
        "weak_or_missing_entities",
        "weak_or_missing_entity_count",
        "prompt_sentence_count",
        "frontier_guarded",
        "frontier_CG",
        "frontier_REA",
        "frontier_failed_targets",
        "current_best_main_factual_ans",
        "packet_sha256",
        "prompt_sha256",
    ]
    write_csv(out_root / "PACKET_INDEX.csv", index_rows, fieldnames)
    write_json(out_root / "PACKET_INDEX.json", {"rows": index_rows})
    write_json(out_root / "PACKET_BUILD_FAILURES.json", failures)

    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "evidence_bound_regeneration_packet_build",
        "queue": rel(queue_path),
        "candidate_frontier": rel(resolve_path(args.candidate_frontier)),
        "report_root": rel(out_root),
        "selected_rows": len(rows),
        "packet_count": len(index_rows),
        "failure_count": len(failures),
        "by_lane": dict(Counter(row["lane"] for row in index_rows)),
        "frontier_guarded_packets": sum(1 for row in index_rows if row.get("frontier_guarded")),
        "weak_or_missing_entity_rows": sum(1 for row in index_rows if int(row.get("weak_or_missing_entity_count") or 0) > 0),
        "weak_or_missing_entity_total": sum(int(row.get("weak_or_missing_entity_count") or 0) for row in index_rows),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
        "files": {
            "packet_index_csv": rel(out_root / "PACKET_INDEX.csv"),
            "packet_index_json": rel(out_root / "PACKET_INDEX.json"),
            "failures": rel(out_root / "PACKET_BUILD_FAILURES.json"),
            "packets_dir": rel(packets_root),
            "model_attempts_dir": rel(attempts_root),
        },
    }
    write_json(out_root / "PACKET_BUILD_SUMMARY.json", summary)
    write_text(out_root / "README.md", build_readme(out_root, summary))
    write_text(out_root / "DESIGN_NOTE.md", build_design_note(summary))

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
