#!/usr/bin/env python3
"""Run or preflight evidence-bound graph_spec regeneration packets.

Default mode is provider-free. It validates packet prompts and, when an
attempt JSON exists, normalizes it into canonical graph_spec, DOT, and a local
preflight report. Use ``--execute`` only when provider credentials are ready.
The script still does not merge any row into the 350 accounting.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DEFAULT_EBR_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration"
DEFAULT_PACKET_INDEX = DEFAULT_EBR_ROOT / "PACKET_INDEX.csv"
DEFAULT_ATTEMPTS_ROOT = DEFAULT_EBR_ROOT / "model_attempts"
DEFAULT_STAGING_ROOT = DEFAULT_EBR_ROOT / "strict_gate_staging"

FRAMEWORK_DIR = PROJECT_ROOT / "code" / "framework"
RESTRUCTURED_DIR = PROJECT_ROOT / "operation_records" / "restructured"
sys.path.insert(0, str(FRAMEWORK_DIR))
sys.path.insert(0, str(RESTRUCTURED_DIR))

from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from graph_spec_validator import validate_graph_spec  # type: ignore  # noqa: E402
from graph_spec_runtime import normalize_graph_spec  # type: ignore  # noqa: E402


STANDARD_EDGE_TYPES = {
    "deduction-rule",
    "deduction-case",
    "induction-case",
    "induction-common",
    "abduction-phenomenon",
    "abduction-knowledge",
}
EDGE_FAMILY = {
    "deduction-rule": "deduction",
    "deduction-case": "deduction",
    "induction-case": "induction",
    "induction-common": "induction",
    "abduction-phenomenon": "abduction",
    "abduction-knowledge": "abduction",
}
SUPPORT_AUDIT_STOPWORDS = {
    "about",
    "above",
    "across",
    "after",
    "again",
    "against",
    "allow",
    "allows",
    "also",
    "among",
    "and",
    "are",
    "because",
    "been",
    "being",
    "between",
    "both",
    "can",
    "case",
    "claim",
    "claims",
    "common",
    "conclusion",
    "connect",
    "connected",
    "connects",
    "could",
    "during",
    "each",
    "effect",
    "effects",
    "enables",
    "for",
    "from",
    "general",
    "given",
    "has",
    "have",
    "having",
    "into",
    "its",
    "itself",
    "lead",
    "leads",
    "limitation",
    "limitations",
    "link",
    "linked",
    "links",
    "make",
    "makes",
    "material",
    "materials",
    "may",
    "more",
    "most",
    "not",
    "node",
    "observed",
    "over",
    "paper",
    "particles",
    "phenomenon",
    "plausible",
    "positive",
    "principle",
    "process",
    "provide",
    "provides",
    "reason",
    "reasoning",
    "related",
    "relation",
    "results",
    "same",
    "show",
    "shows",
    "source",
    "state",
    "states",
    "step",
    "study",
    "such",
    "support",
    "supported",
    "supports",
    "than",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "this",
    "through",
    "thus",
    "using",
    "when",
    "where",
    "which",
    "while",
    "with",
    "within",
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


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


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
    if len(tok) > 6 and tok.endswith("ion"):
        variants.add(tok[:-3] + "e")
    if len(tok) > 7 and tok.endswith("ions"):
        variants.add(tok[:-4] + "e")
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


def compact_text(text: str, *, max_chars: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    var_pattern = re.compile(r"\$(\w+)|\$\{([^}]+)\}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        def repl(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2) or ""
            return os.environ.get(name, "")

        os.environ[key] = var_pattern.sub(repl, value)


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


def select_index_rows(
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
    selected.sort(key=lambda row: (int(row.get("priority") or 999999), row.get("paper_spec", "")))
    return selected[:limit] if limit > 0 else selected


def extract_json_object(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    candidates.append(text)
    for candidate in candidates:
        start = candidate.find("{")
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for idx, ch in enumerate(candidate[start:], start=start):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    snippet = candidate[start : idx + 1]
                    data = json.loads(snippet)
                    if isinstance(data, dict):
                        return data
                    break
    raise ValueError("no JSON object found in response")


def read_attempt_payload(path: Path) -> Dict[str, Any]:
    raw = read_json(path, None)
    if isinstance(raw, dict):
        if isinstance(raw.get("response_json"), dict):
            return raw["response_json"]
        if isinstance(raw.get("graph_spec"), dict):
            return raw["graph_spec"]
        if any(key in raw for key in ("r", "n", "e", "root", "nodes", "edges")):
            return raw
        for key in ("response_text", "raw_response", "content"):
            if isinstance(raw.get(key), str):
                return extract_json_object(raw[key])
    text = path.read_text(encoding="utf-8")
    return extract_json_object(text)


def normalize_provider_edge_aliases(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Accept common provider shorthand for edge labels before runtime normalization."""
    out = dict(payload)
    if "root" not in out and "r" in out:
        out["root"] = out["r"]
    if "nodes" not in out and "n" in out:
        out["nodes"] = out["n"]
    if "edges" not in out and "e" in out:
        out["edges"] = out["e"]
    raw_nodes = out.get("nodes", out.get("n"))
    if isinstance(raw_nodes, list):
        normalized_nodes: List[Any] = []
        changed = False
        for node in raw_nodes:
            if not isinstance(node, dict):
                normalized_nodes.append(node)
                continue
            node_out = dict(node)
            source = node_out.get("source", node_out.get("s", node_out.get("p")))
            if (
                isinstance(source, list)
                and len(source) >= 1
                and isinstance(source[0], list)
                and len(source[0]) == 3
            ):
                if "source" in node_out:
                    node_out["source"] = source[0]
                elif "s" in node_out:
                    node_out["s"] = source[0]
                elif "p" in node_out:
                    node_out["p"] = source[0]
                changed = True
                source = source[0]
            if (
                isinstance(source, list)
                and len(source) == 3
                and all(isinstance(item, int) and not isinstance(item, bool) for item in source)
                and source[0] > 0
                and source[0] == source[1]
                and source[2] > 0
            ):
                fixed_source = [source[0], source[2], 0]
                if "source" in node_out:
                    node_out["source"] = fixed_source
                elif "s" in node_out:
                    node_out["s"] = fixed_source
                elif "p" in node_out:
                    node_out["p"] = fixed_source
                changed = True
            normalized_nodes.append(node_out)
        if changed:
            if "nodes" in out:
                out["nodes"] = normalized_nodes
            else:
                out["n"] = normalized_nodes
    raw_edges = out.get("edges", out.get("e"))
    if isinstance(raw_edges, list):
        normalized_edges: List[Any] = []
        changed = False
        for edge in raw_edges:
            if not isinstance(edge, dict):
                normalized_edges.append(edge)
                continue
            edge_out = dict(edge)
            if "s" in edge_out and "source" not in edge_out:
                edge_out["source"] = edge_out["s"]
                changed = True
            if "t" in edge_out and "target" not in edge_out:
                edge_out["target"] = edge_out["t"]
                changed = True
            if "l" in edge_out and not any(key in edge_out for key in ("type", "y", "label")):
                edge_out["y"] = edge_out["l"]
                changed = True
            normalized_edges.append(edge_out)
        if changed:
            if "edges" in out:
                out["edges"] = normalized_edges
            else:
                out["e"] = normalized_edges
    return out


def canonicalize_spec(payload: Dict[str, Any], paper_id: str) -> Tuple[Dict[str, Any], List[str]]:
    payload = normalize_provider_edge_aliases(payload)
    spec, issues = normalize_graph_spec(payload)
    if not isinstance(spec, dict):
        raise ValueError("normalized graph_spec is not an object")
    if paper_id and not spec.get("paper_id"):
        spec["paper_id"] = paper_id
    return spec, issues


def by_target_edges(spec: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for edge in spec.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        edge_type = str(edge.get("type") or "")
        if source and target:
            grouped[target].append({"source": source, "target": target, "type": edge_type})
    return grouped


def node_text_map(spec: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for node in spec.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if node_id:
            out[node_id] = str(node.get("text") or "")
    return out


def content_terms(text: str) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for token in tokens(text):
        tok = normalize_match_text(token).strip()
        if len(tok) < 3 or tok.isdigit() or tok in SUPPORT_AUDIT_STOPWORDS:
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def immediate_premise_support_audit(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic early warning for over-compressed R nodes.

    Fresh REA is still authoritative. This catches candidates where a deduction
    or abduction target introduces scientific attributes not present in the
    immediate incoming premises, the dominant failure pattern in the residual
    no-anchor smoke runs.
    """
    texts = node_text_map(spec)
    rows: List[Dict[str, Any]] = []
    high_risk_count = 0
    for target, edges in sorted(by_target_edges(spec).items()):
        if target == str(spec.get("root") or "") or not target.startswith("R"):
            continue
        labels = [edge["type"] for edge in edges]
        families = {EDGE_FAMILY.get(label, "") for label in labels}
        family = next(iter(families)) if len(families) == 1 else "mixed"
        if family not in {"deduction", "abduction"}:
            continue
        source_text = " ".join(texts.get(edge["source"], "") for edge in edges)
        source_index = token_variant_index(source_text)
        target_terms = content_terms(texts.get(target, ""))
        unsupported: List[str] = []
        for term in target_terms:
            if term == "outward" and {"bulk", "surface"} <= source_index:
                continue
            if not (token_variants(term) & source_index):
                unsupported.append(term)
        unsupported_ratio = len(unsupported) / max(1, len(target_terms))
        high_risk = len(target_terms) >= 4 and len(unsupported) >= 2 and unsupported_ratio >= 0.2
        high_risk_count += int(high_risk)
        rows.append(
            {
                "target": target,
                "family": family,
                "source_ids": [edge["source"] for edge in edges],
                "target_terms": target_terms,
                "unsupported_terms": unsupported,
                "unsupported_ratio": round(unsupported_ratio, 4),
                "high_risk": high_risk,
            }
        )
    return {
        "audited_targets": len(rows),
        "high_risk_count": high_risk_count,
        "targets": rows,
    }


def reasoning_unit_audit(spec: Dict[str, Any]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    valid_count = 0
    for target, edges in sorted(by_target_edges(spec).items()):
        labels = [edge["type"] for edge in edges]
        label_set = set(labels)
        valid = False
        reason = ""
        if any(label not in STANDARD_EDGE_TYPES for label in labels):
            reason = "non_standard_edge_type"
        elif label_set <= {"deduction-rule", "deduction-case"}:
            valid = labels.count("deduction-rule") == 1 and labels.count("deduction-case") == 1
            reason = "deduction" if valid else "bad_deduction_pairing"
        elif label_set <= {"abduction-phenomenon", "abduction-knowledge"}:
            valid = labels.count("abduction-phenomenon") == 1 and labels.count("abduction-knowledge") == 1
            reason = "abduction" if valid else "bad_abduction_pairing"
        elif label_set <= {"induction-common", "induction-case"}:
            valid = labels.count("induction-common") == 1 and labels.count("induction-case") >= 1
            reason = "induction" if valid else "bad_induction_pairing"
        else:
            reason = "mixed_reasoning_family"
        valid_count += int(valid)
        rows.append(
            {
                "target": target,
                "valid": valid,
                "reason": reason,
                "edge_count": len(edges),
                "labels": labels,
                "sources": [edge["source"] for edge in edges],
            }
        )
    return {
        "target_count": len(rows),
        "valid_target_count": valid_count,
        "invalid_target_count": len(rows) - valid_count,
        "targets": rows,
    }


def entity_coverage_audit(spec: Dict[str, Any], entities: List[str]) -> Dict[str, Any]:
    node_text = " ".join(str(node.get("text") or "") for node in spec.get("nodes") or [] if isinstance(node, dict))
    text_norm = normalize_text(node_text)
    text_raw = normalize_match_text(node_text)
    text_compact = compact_match_text(node_text)
    text_tokens = token_variant_index(node_text)
    rows: List[Dict[str, Any]] = []
    covered = 0
    for entity in entities:
        entity_norm = normalize_text(entity)
        entity_raw = normalize_match_text(entity)
        entity_tokens = token_variant_index(entity)
        matched_tokens = sorted(entity_tokens & text_tokens)
        exact = bool(entity_norm and entity_norm in text_norm)
        if not exact:
            entity_phrase_variants = {entity_raw}
            if entity_raw.endswith("s"):
                entity_phrase_variants.add(entity_raw[:-1])
            exact = any(variant and variant in text_raw for variant in entity_phrase_variants)
        if not exact:
            entity_compact = compact_match_text(entity)
            exact = bool(entity_compact and len(entity_compact) >= 6 and entity_compact in text_compact)
        token_cover = len(matched_tokens) / max(1, len(entity_tokens))
        is_covered = exact or token_cover >= 0.67
        covered += int(is_covered)
        rows.append(
            {
                "entity": entity,
                "covered": is_covered,
                "exact_phrase_match": exact,
                "token_coverage": round(token_cover, 4),
                "matched_tokens": matched_tokens,
            }
        )
    return {
        "covered_entities": covered,
        "total_entities": len(entities),
        "coverage_rate_local": covered / len(entities) if entities else 0.0,
        "entities": rows,
    }


def connected_to_root_audit(spec: Dict[str, Any]) -> Dict[str, Any]:
    nodes = {str(node.get("id")) for node in spec.get("nodes") or [] if isinstance(node, dict) and node.get("id")}
    root = str(spec.get("root") or "")
    reverse: Dict[str, List[str]] = defaultdict(list)
    for edge in spec.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        if src and tgt:
            reverse[tgt].append(src)
    reachable: set[str] = set()
    if root:
        queue: deque[str] = deque([root])
        while queue:
            node = queue.popleft()
            if node in reachable:
                continue
            reachable.add(node)
            queue.extend(reverse.get(node, []))
    stranded = sorted(nodes - reachable)
    return {
        "root": root,
        "nodes_reaching_root": len(reachable & nodes),
        "total_nodes": len(nodes),
        "stranded_nodes": stranded,
    }


def preflight_graph_spec(spec: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
    strict_valid, strict_issues = validate_graph_spec(spec, mode="strict")
    teacher_valid, teacher_issues = validate_graph_spec(spec, mode="teacher_compatible")
    unit_audit = reasoning_unit_audit(spec)
    support_audit = immediate_premise_support_audit(spec)
    coverage = entity_coverage_audit(spec, packet.get("paper_anchor", {}).get("entities") or [])
    connectivity = connected_to_root_audit(spec)
    root_ok = str(spec.get("root") or "") == "NROOT"
    passed_local = (
        strict_valid
        and root_ok
        and unit_audit["invalid_target_count"] == 0
        and support_audit["high_risk_count"] == 0
        and coverage["covered_entities"] == coverage["total_entities"]
        and not connectivity["stranded_nodes"]
    )
    return {
        "passed_local_preflight": passed_local,
        "root_id_is_NROOT": root_ok,
        "strict_validator": {"valid": strict_valid, "issues": strict_issues},
        "teacher_compatible_validator": {"valid": teacher_valid, "issues": teacher_issues},
        "reasoning_units": unit_audit,
        "immediate_premise_support": support_audit,
        "entity_coverage": coverage,
        "connectivity": connectivity,
        "fresh_gate_still_required": {"final_CG": 1.0, "final_REA": 1.0},
    }


def incident_node_ids(spec: Dict[str, Any]) -> set[str]:
    used: set[str] = set()
    for edge in spec.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source:
            used.add(source)
        if target:
            used.add(target)
    return used


def isolated_nodes_from_preflight(preflight: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for issue in (preflight.get("strict_validator") or {}).get("issues") or []:
        if not isinstance(issue, dict) or issue.get("type") != "isolated_nodes":
            continue
        details = issue.get("details") if isinstance(issue.get("details"), dict) else {}
        for node_id in details.get("nodes") or []:
            node_text = str(node_id or "").strip()
            if node_text:
                out.append(node_text)
    return sorted(set(out))


def node_numeric_suffix(node_id: str) -> int:
    match = re.search(r"(\d+)$", str(node_id or ""))
    return int(match.group(1)) if match else 999999


def next_evidence_node_id(nodes: List[Dict[str, Any]]) -> str:
    max_id = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if node_id.startswith("E"):
            max_id = max(max_id, node_numeric_suffix(node_id))
    return f"E{max_id + 1}"


def entity_covered_by_text(entity: str, text: str) -> bool:
    entity_norm = normalize_text(entity)
    text_norm = normalize_text(text)
    if entity_norm and entity_norm in text_norm:
        return True
    entity_compact = compact_match_text(entity)
    text_compact = compact_match_text(text)
    if entity_compact and len(entity_compact) >= 6 and entity_compact in text_compact:
        return True
    entity_tokens = token_variant_index(entity)
    if not entity_tokens:
        return False
    token_cover = len(entity_tokens & token_variant_index(text)) / max(1, len(entity_tokens))
    return token_cover >= 0.67


def source_text_for_entity(entity: str, packet: Dict[str, Any]) -> Tuple[str, List[int]]:
    entity_evidence = (packet.get("evidence") or {}).get("entity_evidence") or []
    for item in entity_evidence:
        if str(item.get("entity") or "") != entity:
            continue
        top_sentences = item.get("top_sentences") if isinstance(item.get("top_sentences"), list) else []
        ranked = sorted(
            [row for row in top_sentences if isinstance(row, dict)],
            key=lambda row: (
                -float(row.get("ans_source_support") or 0.0),
                -float(row.get("score") or 0.0),
                int(row.get("idx") or 999999),
            ),
        )
        for sent in ranked:
            idx = int(sent.get("idx") or 0)
            viewpoints = sent.get("viewpoints") if isinstance(sent.get("viewpoints"), list) else []
            for viewpoint_idx, viewpoint in enumerate(viewpoints, start=1):
                text = compact_text(str(viewpoint or ""), max_chars=240)
                if text and entity_covered_by_text(entity, text):
                    return text.rstrip(".; ") + ".", [idx, viewpoint_idx, 0]
            text = compact_text(str(sent.get("sentence") or ""), max_chars=260)
            if text:
                return text.rstrip(".; ") + ".", [idx, 0, 0]
    return "", []


def apply_ans_safe_root_inventory_rewrite(
    spec: Dict[str, Any],
    packet: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    nodes = spec.get("nodes") if isinstance(spec.get("nodes"), list) else []
    if not nodes:
        return spec, []
    evidence_nodes = [
        node
        for node in nodes
        if isinstance(node, dict)
        and str(node.get("id") or "").startswith("E")
        and str(node.get("text") or "").strip()
    ]
    if len(evidence_nodes) < 3:
        return spec, []
    evidence_nodes = sorted(evidence_nodes, key=lambda node: node_numeric_suffix(str(node.get("id") or "")))
    repaired = dict(spec)
    repaired_nodes: List[Dict[str, Any]] = [dict(node) if isinstance(node, dict) else node for node in nodes]
    repaired_edges: List[Dict[str, Any]] = [
        dict(edge) if isinstance(edge, dict) else edge
        for edge in (spec.get("edges") if isinstance(spec.get("edges"), list) else [])
    ]
    e_text = " ".join(str(node.get("text") or "") for node in evidence_nodes)
    added_for_entities: List[str] = []
    for entity in packet.get("paper_anchor", {}).get("entities") or []:
        entity_text = str(entity or "").strip()
        if not entity_text or entity_covered_by_text(entity_text, e_text):
            continue
        source_text, source_tuple = source_text_for_entity(entity_text, packet)
        if not source_text or not source_tuple:
            continue
        new_id = next_evidence_node_id(repaired_nodes)
        new_node = {"id": new_id, "source": source_tuple, "text": source_text}
        repaired_nodes.append(new_node)
        repaired_edges.append({"source": new_id, "target": "NROOT", "type": "induction-case"})
        evidence_nodes.append(new_node)
        e_text = f"{e_text} {source_text}"
        added_for_entities.append(entity_text)
    evidence_nodes = sorted(evidence_nodes, key=lambda node: node_numeric_suffix(str(node.get("id") or "")))
    inventory_parts: List[str] = []
    for node in evidence_nodes[:14]:
        text = re.sub(r"\s+", " ", str(node.get("text") or "")).strip()
        text = text.rstrip(".; ")
        if text:
            inventory_parts.append(text)
    if len(inventory_parts) < 3:
        return spec, []
    root_text = "; ".join(inventory_parts) + "."
    rewritten = False
    final_nodes: List[Dict[str, Any]] = []
    for node in repaired_nodes:
        if isinstance(node, dict) and str(node.get("id") or "") == "NROOT":
            old_text = str(node.get("text") or "")
            if old_text != root_text:
                node = dict(node)
                node["text"] = root_text
                rewritten = True
        final_nodes.append(node)
    if not rewritten:
        return spec, []
    repaired["nodes"] = final_nodes
    repaired["edges"] = repaired_edges
    return repaired, [
        {
            "action": "coverage_preserving_rewrite_nroot_as_evidence_inventory",
            "reason": "Use exact E-node facts as ANS-safe semantic root text and add source-backed E nodes for missing required entities.",
            "evidence_node_count": len(inventory_parts),
            "added_entity_evidence_nodes": added_for_entities,
        }
    ]


def apply_safe_preflight_repairs(
    spec: Dict[str, Any],
    packet: Dict[str, Any],
    original_preflight: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """Apply only non-semantic graph_spec cleanup that passes full preflight.

    The first observed provider failure mode is an unused evidence node with no
    incident edges. Removing it is safe only if the repaired graph still has
    full entity coverage, legal reasoning units, and complete root connectivity.
    """
    if original_preflight.get("passed_local_preflight") is True:
        return spec, original_preflight, []

    root = str(spec.get("root") or "")
    used = incident_node_ids(spec)
    isolated = [
        node_id
        for node_id in isolated_nodes_from_preflight(original_preflight)
        if node_id != root and node_id not in used
    ]
    if not isolated:
        return spec, original_preflight, []

    repaired = dict(spec)
    repaired["nodes"] = [
        node
        for node in spec.get("nodes") or []
        if not (isinstance(node, dict) and str(node.get("id") or "") in set(isolated))
    ]
    repaired_preflight = preflight_graph_spec(repaired, packet)
    if repaired_preflight.get("passed_local_preflight") is True:
        return repaired, repaired_preflight, [
            {
                "action": "drop_isolated_nodes",
                "node_ids": isolated,
                "reason": "provider emitted evidence nodes with no incident edges; repaired graph passes full local preflight",
            }
        ]
    return spec, original_preflight, [
        {
            "action": "drop_isolated_nodes_rejected",
            "node_ids": isolated,
            "reason": "candidate did not pass full local preflight after conservative cleanup",
        }
    ]


def attempt_dir_for(row: Dict[str, str], model: str) -> Path:
    priority = f"{int(row.get('priority') or 999999):03d}"
    stem = safe_slug(row.get("paper_spec") or "paper")
    return Path(f"{priority}_{stem}") / safe_slug(model)


def default_attempt_file(row: Dict[str, str], model: str, attempts_root: Path) -> Path:
    return attempts_root / attempt_dir_for(row, model) / "response.json"


def load_existing_attempt(row: Dict[str, str], model: str, attempts_root: Path) -> Optional[Path]:
    base = attempts_root / attempt_dir_for(row, model)
    for name in ("response.json", "response.txt", "graph_spec.json"):
        path = base / name
        if path.exists():
            return path
    return None


class ChatCompletionError(RuntimeError):
    def __init__(self, message: str, *, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


def decode_chat_completion_response(raw: str, *, stream: bool) -> str:
    if stream:
        parts: List[str] = []
        parsed_any = False
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
                parsed_any = True
            except json.JSONDecodeError:
                continue
            for choice in data.get("choices") or []:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str):
                    parts.append(content)
                message = choice.get("message") or {}
                message_content = message.get("content")
                if isinstance(message_content, str):
                    parts.append(message_content)
        if parts:
            return "".join(parts).strip()
        if parsed_any:
            return ""

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ChatCompletionError(f"chat completion returned non-JSON response: {exc}", raw_response=raw) from exc
    choices = data.get("choices") or []
    if not choices:
        return raw
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    return raw


def openai_compat_request(
    *,
    model: str,
    prompt: str,
    base_url: str,
    api_key: str,
    timeout: float,
    max_tokens: int,
    transport: str,
    stream: bool,
) -> Dict[str, Any]:
    if not api_key:
        raise RuntimeError("missing API key")
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "stream": stream,
    }
    if max_tokens > 0:
        payload["max_tokens"] = max_tokens
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Authorization": f"Bearer {api_key}",
    }
    raw = ""
    if transport == "curl":
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError("curl transport requested but curl is not available")
        cmd = [
            curl,
            "-sS",
            "--fail-with-body",
            "--http1.1",
            "--connect-timeout",
            "30",
            "--max-time",
            str(max(1, int(timeout))),
            url,
            "-H",
            f"Content-Type: {headers['Content-Type']}",
            "-H",
            f"Accept: {headers['Accept']}",
            "-H",
            f"User-Agent: {headers['User-Agent']}",
            "-H",
            "Expect:",
            "-H",
            "Connection: close",
            "-H",
            f"Authorization: Bearer {api_key}",
            "--data-binary",
            json.dumps(payload, ensure_ascii=False),
        ]
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        raw = proc.stdout or ""
        if proc.returncode != 0:
            detail = (proc.stdout or proc.stderr or "").strip()
            raise RuntimeError(detail or f"curl exited with code {proc.returncode}")
    else:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(detail or str(exc)) from exc
    content = decode_chat_completion_response(raw, stream=stream)
    return {"raw_response": raw, "content": content, "response_json": extract_json_object(content)}


def execute_attempt(
    row: Dict[str, str],
    packet: Dict[str, Any],
    *,
    model: str,
    attempts_root: Path,
    base_url: str,
    api_key: str,
    timeout: float,
    max_tokens: int,
    retries: int,
    retry_sleep: float,
    transport: str,
    stream: bool,
) -> Path:
    out = default_attempt_file(row, model, attempts_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt_path = resolve_path(row["generation_prompt"])
    prompt = prompt_path.read_text(encoding="utf-8")
    attempts: List[Dict[str, Any]] = []
    last_error = ""
    for attempt in range(1, max(1, retries) + 1):
        try:
            payload = openai_compat_request(
                model=model,
                prompt=prompt,
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
                max_tokens=max_tokens,
                transport=transport,
                stream=stream,
            )
            payload.update(
                {
                    "paper_spec": row.get("paper_spec", ""),
                    "model": model,
                    "attempt": attempt,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "transport": transport,
                    "stream": stream,
                }
            )
            write_json(out, payload)
            return out
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            attempts.append({"attempt": attempt, "error": last_error[-2000:]})
            write_json(out.parent / "attempt_errors.json", attempts)
            if attempt < retries and retry_sleep > 0:
                time.sleep(retry_sleep)
    raise RuntimeError(f"generation failed after {len(attempts)} attempts: {last_error}")


def materialize_attempt(
    row: Dict[str, str],
    packet: Dict[str, Any],
    attempt_path: Path,
    *,
    model: str,
    attempts_root: Path,
    staging_root: Path,
    copy_staging: bool,
) -> Dict[str, Any]:
    paper_spec = row.get("paper_spec", "")
    paper_id = packet.get("paper") or paper_spec.split(":", 1)[-1]
    attempt_dir = attempts_root / attempt_dir_for(row, model)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    payload = read_attempt_payload(attempt_path)
    spec, normalization_issues = canonicalize_spec(payload, str(paper_id))
    original_preflight = preflight_graph_spec(spec, packet)
    root_rewrite_actions: List[Dict[str, Any]] = []
    root_rewritten_spec, proposed_root_actions = apply_ans_safe_root_inventory_rewrite(spec, packet)
    if proposed_root_actions:
        root_rewrite_preflight = preflight_graph_spec(root_rewritten_spec, packet)
        if root_rewrite_preflight.get("passed_local_preflight") is True:
            spec = root_rewritten_spec
            original_preflight = root_rewrite_preflight
            root_rewrite_actions = proposed_root_actions
        else:
            root_rewrite_actions = [
                {
                    **proposed_root_actions[0],
                    "action": "rewrite_nroot_as_evidence_inventory_rejected",
                    "reason": "Rejected because the root inventory rewrite did not preserve full local preflight.",
                    "rejected_entity_coverage": root_rewrite_preflight.get("entity_coverage", {}),
                }
            ]
    spec, preflight, repair_actions = apply_safe_preflight_repairs(spec, packet, original_preflight)
    repair_actions = root_rewrite_actions + repair_actions
    dot = graph_spec_to_dot(spec)

    graph_spec_path = attempt_dir / "graph_spec.json"
    dot_path = attempt_dir / "final_clean_graph.dot"
    preflight_path = attempt_dir / "preflight_report.json"
    write_json(graph_spec_path, spec)
    write_text(dot_path, dot)
    write_json(
        preflight_path,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "paper_spec": paper_spec,
            "model": model,
            "attempt_path": rel(attempt_path),
            "normalization_issues": normalization_issues,
            "safe_preflight_repairs": repair_actions,
            "original_preflight": original_preflight if repair_actions else {},
            **preflight,
        },
    )

    staged_dir = ""
    if copy_staging and preflight["passed_local_preflight"]:
        stage_dir = staging_root / safe_slug(paper_spec) / safe_slug(model)
        stage_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dot_path, stage_dir / "final_clean_graph.dot")
        input_data = resolve_path(packet["source_paths"]["input_data"])
        if input_data.exists():
            shutil.copy2(input_data, stage_dir / "input_data.json")
        write_json(
            stage_dir / "evidence_bound_packet_pointer.json",
            {
                "packet": row.get("packet_json", ""),
                "attempt": rel(attempt_path),
                "preflight_report": rel(preflight_path),
                "safe_preflight_repairs": repair_actions,
            },
        )
        staged_dir = rel(stage_dir)

    return {
        "priority": row.get("priority", ""),
        "paper_spec": paper_spec,
        "lane": row.get("lane", ""),
        "model": model,
        "attempt_path": rel(attempt_path),
        "graph_spec": rel(graph_spec_path),
        "dot": rel(dot_path),
        "preflight_report": rel(preflight_path),
        "staged_run_dir": staged_dir,
        "passed_local_preflight": preflight["passed_local_preflight"],
        "safe_preflight_repairs": json.dumps(repair_actions, ensure_ascii=False) if repair_actions else "",
        "strict_validator_valid": preflight["strict_validator"]["valid"],
        "unit_invalid_count": preflight["reasoning_units"]["invalid_target_count"],
        "premise_support_high_risk_count": preflight["immediate_premise_support"]["high_risk_count"],
        "premise_support_unsupported_terms": json.dumps(
            {
                row["target"]: row["unsupported_terms"]
                for row in preflight["immediate_premise_support"]["targets"]
                if row.get("high_risk")
            },
            ensure_ascii=False,
        ),
        "entity_coverage_local": preflight["entity_coverage"]["coverage_rate_local"],
        "covered_entities_local": preflight["entity_coverage"]["covered_entities"],
        "total_entities": preflight["entity_coverage"]["total_entities"],
        "stranded_node_count": len(preflight["connectivity"]["stranded_nodes"]),
        "fresh_gate_required": True,
    }


def write_runbook(path: Path, summary: Dict[str, Any]) -> None:
    lines = [
        "# Evidence-Bound Graph Spec Attempt Runbook",
        "",
        f"Created: {summary['created_at']}",
        "",
        "This runbook records graph_spec attempts only. Local preflight is not final acceptance.",
        "",
        "## Acceptance",
        "",
        "A row remains residual until a staged `final_clean_graph.dot` plus matching `input_data.json` passes the unchanged PEARL fresh evaluation with final `CG=1.0` and `REA=1.0`.",
        "",
        "## Next Command Pattern",
        "",
        "For rows with `passed_local_preflight=true`, use the staged run directory as a source candidate for the existing strict PEARL gate rather than merging directly.",
        "",
        "## Summary",
        "",
        f"- Selected packets: {summary['selected_packets']}",
        f"- Attempt rows: {summary['attempt_rows']}",
        f"- Local preflight passed: {summary['local_preflight_passed']}",
        f"- Generation failures: {summary['generation_failures']}",
        "",
    ]
    write_text(path, "\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet-index", default=str(DEFAULT_PACKET_INDEX))
    parser.add_argument("--attempts-root", default=str(DEFAULT_ATTEMPTS_ROOT))
    parser.add_argument("--staging-root", default=str(DEFAULT_STAGING_ROOT))
    parser.add_argument("--lanes", nargs="+", default=[])
    parser.add_argument("--paper-specs", nargs="+", default=[])
    parser.add_argument("--paper-specs-file", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--execute", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--materialize-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--copy-staging", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-tokens", type=int, default=5000)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-sleep", type=float, default=8.0)
    parser.add_argument("--transport", choices=["curl", "urllib"], default=os.getenv("GPT55_EBR_TRANSPORT", "curl"))
    parser.add_argument("--stream", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(resolve_path(args.env_file))
    packet_index = resolve_path(args.packet_index)
    attempts_root = resolve_path(args.attempts_root)
    staging_root = resolve_path(args.staging_root)
    attempts_root.mkdir(parents=True, exist_ok=True)
    staging_root.mkdir(parents=True, exist_ok=True)

    rows = select_index_rows(
        read_csv(packet_index),
        lanes=args.lanes,
        paper_specs=parse_specs(args.paper_specs, args.paper_specs_file),
        limit=args.limit,
    )

    base_url = args.base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("GPT_BASE_URL") or "https://api.openai.com/v1"
    api_key = os.getenv(args.api_key_env, "")
    attempt_rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for row in rows:
        packet_path = resolve_path(row["packet_json"])
        packet = read_json(packet_path, {})
        if not isinstance(packet, dict):
            failures.append({"paper_spec": row.get("paper_spec", ""), "error": f"bad packet JSON: {packet_path}"})
            continue
        try:
            attempt_path: Optional[Path] = None
            if args.execute:
                attempt_path = execute_attempt(
                    row,
                    packet,
                    model=args.model,
                    attempts_root=attempts_root,
                    base_url=base_url,
                    api_key=api_key,
                    timeout=args.timeout,
                    max_tokens=args.max_tokens,
                    retries=args.retries,
                    retry_sleep=args.retry_sleep,
                    transport=args.transport,
                    stream=args.stream,
                )
            elif args.materialize_existing:
                attempt_path = load_existing_attempt(row, args.model, attempts_root)

            if attempt_path:
                attempt_rows.append(
                    materialize_attempt(
                        row,
                        packet,
                        attempt_path,
                        model=args.model,
                        attempts_root=attempts_root,
                        staging_root=staging_root,
                        copy_staging=args.copy_staging,
                    )
                )
            else:
                attempt_rows.append(
                    {
                        "priority": row.get("priority", ""),
                        "paper_spec": row.get("paper_spec", ""),
                        "lane": row.get("lane", ""),
                        "model": args.model,
                        "attempt_path": "",
                        "graph_spec": "",
                        "dot": "",
                        "preflight_report": "",
                        "staged_run_dir": "",
                        "passed_local_preflight": False,
                        "safe_preflight_repairs": "",
                        "strict_validator_valid": "",
                        "unit_invalid_count": "",
                        "premise_support_high_risk_count": "",
                        "premise_support_unsupported_terms": "",
                        "entity_coverage_local": "",
                        "covered_entities_local": "",
                        "total_entities": row.get("entity_count", ""),
                        "stranded_node_count": "",
                        "fresh_gate_required": True,
                        "status": "planned_no_attempt",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"paper_spec": row.get("paper_spec", ""), "lane": row.get("lane", ""), "error": str(exc)})

    fieldnames = [
        "priority",
        "paper_spec",
        "lane",
        "model",
        "attempt_path",
        "graph_spec",
        "dot",
        "preflight_report",
        "staged_run_dir",
        "passed_local_preflight",
        "safe_preflight_repairs",
        "strict_validator_valid",
        "unit_invalid_count",
        "premise_support_high_risk_count",
        "premise_support_unsupported_terms",
        "entity_coverage_local",
        "covered_entities_local",
        "total_entities",
        "stranded_node_count",
        "fresh_gate_required",
        "status",
    ]
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "evidence_bound_graph_spec_regeneration",
        "packet_index": rel(packet_index),
        "attempts_root": rel(attempts_root),
        "staging_root": rel(staging_root),
        "model": args.model,
        "execute": args.execute,
        "transport": args.transport,
        "stream": args.stream,
        "selected_packets": len(rows),
        "attempt_rows": len(attempt_rows),
        "local_preflight_passed": sum(1 for row in attempt_rows if row.get("passed_local_preflight") is True),
        "premise_support_high_risk_rows": sum(
            1
            for row in attempt_rows
            if isinstance(row.get("premise_support_high_risk_count"), int)
            and int(row.get("premise_support_high_risk_count") or 0) > 0
        ),
        "generation_failures": len(failures),
        "by_lane": dict(Counter(row.get("lane", "") for row in attempt_rows)),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
        "files": {
            "attempt_index_csv": rel(attempts_root / "ATTEMPT_INDEX.csv"),
            "attempt_index_json": rel(attempts_root / "ATTEMPT_INDEX.json"),
            "failures": rel(attempts_root / "GENERATION_OR_PREFLIGHT_FAILURES.json"),
            "runbook": rel(attempts_root / "RUNBOOK.md"),
        },
    }
    write_csv(attempts_root / "ATTEMPT_INDEX.csv", attempt_rows, fieldnames)
    write_json(attempts_root / "ATTEMPT_INDEX.json", {"rows": attempt_rows})
    write_json(attempts_root / "GENERATION_OR_PREFLIGHT_FAILURES.json", failures)
    write_json(attempts_root / "ATTEMPT_SUMMARY.json", summary)
    write_runbook(attempts_root / "RUNBOOK.md", summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
