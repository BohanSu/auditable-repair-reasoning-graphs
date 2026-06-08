#!/usr/bin/env python3
"""Build local evidence-bound micro-edit packets for residual graph repair.

This is deliberately narrower than full graph regeneration. It starts from the
current best residual candidate selected by the evidence-bound selection ledger,
extracts provider-contaminated or semantically wrong reasoning units, and asks a
model to return a patch for only those target units. No provider call is made by
this script and no 350 accounting file is modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


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
DEFAULT_LEDGER = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "selection_ledger"
    / "20260603_gpt55_feedback_smoke"
    / "CANDIDATE_SELECTION_LEDGER.json"
)
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "micro_edit_packets" / "round01_from_best_r01"


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


def compact_text(value: Any, *, max_chars: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def by_id(nodes: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(node.get("id")): node for node in nodes if isinstance(node, dict) and node.get("id")}


def incoming_edges(spec: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for edge in spec.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        typ = str(edge.get("type") or "")
        if src and tgt:
            grouped[tgt].append({"source": src, "target": tgt, "type": typ})
    return grouped


def outgoing_edges(spec: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for edge in spec.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        typ = str(edge.get("type") or "")
        if src and tgt:
            grouped[src].append({"source": src, "target": tgt, "type": typ})
    return grouped


def parse_target_list(value: Any) -> List[str]:
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def selected_best_rows(ledger_path: Path) -> List[Dict[str, Any]]:
    ledger = read_json(ledger_path, {})
    rows = ledger.get("rows") if isinstance(ledger, dict) else []
    if not isinstance(rows, list):
        return []
    selected = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("selection_decision") == "current_best_residual_candidate"
    ]
    return selected


def vote_file_for_target(eval_dir: Path, target: str) -> Path | None:
    for path in sorted((eval_dir / "responses").glob("reasoning_validation_*_vote_result.json")):
        vote = read_json(path, {})
        if isinstance(vote, dict) and str(vote.get("target_node") or "") == target:
            return path
    return None


def source_packet_for_stage(stage_dir: Path) -> tuple[Path, Dict[str, Any]]:
    pointer = read_json(stage_dir / "evidence_bound_packet_pointer.json", {})
    packet_path = resolve_path(str(pointer.get("packet") or ""))
    packet = read_json(packet_path, {})
    if not isinstance(packet, dict) or not packet:
        raise RuntimeError(f"cannot resolve source packet from {stage_dir}")
    return packet_path, packet


def compact_node(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": node.get("id"),
        "source": node.get("source"),
        "text": compact_text(node.get("text"), max_chars=900),
    }


def compact_edge(edge: Dict[str, str]) -> Dict[str, str]:
    return {"source": edge.get("source", ""), "target": edge.get("target", ""), "type": edge.get("type", "")}


def target_context(spec: Dict[str, Any], target: str) -> Dict[str, Any]:
    nodes = by_id(spec.get("nodes") or [])
    inc = incoming_edges(spec)
    out = outgoing_edges(spec)
    source_ids = [edge["source"] for edge in inc.get(target, [])]
    child_ids = [edge["target"] for edge in out.get(target, [])]
    context_ids = [target] + source_ids + child_ids
    return {
        "target_node": compact_node(nodes[target]) if target in nodes else {"id": target},
        "incoming_edges": [compact_edge(edge) for edge in inc.get(target, [])],
        "outgoing_edges": [compact_edge(edge) for edge in out.get(target, [])],
        "source_nodes": [compact_node(nodes[item]) for item in source_ids if item in nodes],
        "direct_children": [compact_node(nodes[item]) for item in child_ids if item in nodes],
        "context_node_ids": context_ids,
    }


def candidate_relevant_evidence(spec: Dict[str, Any], packet: Dict[str, Any], target: str) -> List[Dict[str, Any]]:
    nodes = by_id(spec.get("nodes") or [])
    evidence: Dict[str, Dict[str, Any]] = {}
    inc = incoming_edges(spec)
    out = outgoing_edges(spec)
    for edge in inc.get(target, []):
        src = edge["source"]
        if src in nodes and src.startswith("E"):
            evidence[src] = compact_node(nodes[src])
    for edge in out.get(target, []):
        child = edge["target"]
        for child_edge in inc.get(child, []):
            src = child_edge["source"]
            if src in nodes and src.startswith("E"):
                evidence[src] = compact_node(nodes[src])
    for node in spec.get("nodes") or []:
        if isinstance(node, dict) and str(node.get("id") or "").startswith("E"):
            source = node.get("source")
            if isinstance(source, list) and source and int(source[0]) in {30, 31, 32, 33, 34, 35}:
                evidence[str(node.get("id"))] = compact_node(node)

    # Keep packet anchor visible even if some evidence is not in the best graph.
    anchor = packet.get("paper_anchor") if isinstance(packet.get("paper_anchor"), dict) else {}
    return [
        *sorted(evidence.values(), key=lambda item: str(item.get("id"))),
        {
            "id": "ANCHOR",
            "source": [0, 0, 0],
            "text": compact_text(anchor.get("core_idea"), max_chars=1200),
        },
    ]


def build_prompt(packet: Dict[str, Any], spec: Dict[str, Any], target: str, vote: Dict[str, Any]) -> str:
    anchor = packet.get("paper_anchor") if isinstance(packet.get("paper_anchor"), dict) else {}
    ctx = target_context(spec, target)
    evidence = candidate_relevant_evidence(spec, packet, target)
    wrong_reasons = []
    responses = vote.get("model_responses") if isinstance(vote.get("model_responses"), dict) else {}
    for model_key, response in sorted(responses.items()):
        response = response if isinstance(response, dict) else {}
        if str(response.get("result") or "").lower() == "wrong":
            wrong_reasons.append(
                {
                    "model": model_key,
                    "reason": compact_text(response.get("reason") or response.get("raw_response"), max_chars=1100),
                }
            )
    lines = [
        "You are repairing exactly one reasoning unit in an evidence-bound PEARL graph_spec.",
        "",
        "Return ONLY one JSON object with this schema:",
        "{",
        '  "replace_nodes": [{"id": "R6", "source": [0,0,0], "text": "deduction-reasoning: ..."}],',
        '  "replace_incoming_edges": [{"source": "E10", "target": "R6", "type": "deduction-case"}, {"source": "E9", "target": "R6", "type": "deduction-rule"}],',
        '  "optional_replace_outgoing_edges": []',
        "}",
        "",
        "Hard constraints:",
        f"- Patch ONLY target `{target}`. Do not add, delete, or rename any other node.",
        "- Keep the target id unchanged.",
        "- Replace only the target node text and its incoming reasoning-unit edges.",
        "- You may optionally change outgoing edges from the target only if this is necessary to preserve graph logic; otherwise return an empty list.",
        "- The replacement incoming edges must form exactly one legal reasoning unit: deduction has one deduction-case plus one deduction-rule; induction has one or more induction-case plus one induction-common; abduction has one abduction-phenomenon plus one abduction-knowledge.",
        "- Use only existing node ids from the candidate graph. Do not invent new evidence nodes.",
        "- Avoid circular reasoning: the source premises must add information beyond the target conclusion.",
        "- Preserve required entity coverage and do not weaken the paper-level conclusion.",
        "- The final merged graph must still pass fresh CG=1.0 and REA=1.0; this patch is not accepted until fresh evaluation passes.",
        "",
        "Paper anchor:",
        compact_text(anchor.get("core_idea"), max_chars=1300),
        "",
        "Required entities:",
        json.dumps(anchor.get("entities") or [], ensure_ascii=False),
        "",
        "Current target context:",
        json.dumps(ctx, ensure_ascii=False, indent=2),
        "",
        "Fresh judge failure to repair:",
        json.dumps(
            {
                "target": vote.get("target_node"),
                "reasoning_type": vote.get("reasoning_type"),
                "target_content": vote.get("target_content"),
                "source_contents": vote.get("source_contents"),
                "model_results": vote.get("model_results"),
                "vote_decision": (vote.get("vote_breakdown") or {}).get("decision")
                if isinstance(vote.get("vote_breakdown"), dict)
                else "",
                "wrong_reasons": wrong_reasons,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "",
        "Relevant existing evidence/reasoning nodes you may use as sources:",
        json.dumps(evidence, ensure_ascii=False, indent=2),
        "",
        "Return the JSON patch only.",
    ]
    return "\n".join(lines)


def build_packets(rows: List[Dict[str, Any]], out_root: Path, target_filter: Sequence[str]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    target_set = set(target_filter)
    packets_root = out_root / "packets"
    index_rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for row in rows:
        try:
            graph_spec_path = resolve_path(str(row.get("graph_spec") or ""))
            spec = read_json(graph_spec_path, {})
            if not isinstance(spec, dict) or not spec:
                raise RuntimeError(f"bad graph spec: {graph_spec_path}")
            stage_dir = resolve_path(str(row.get("staged_run_dir") or ""))
            packet_path, packet = source_packet_for_stage(stage_dir)
            eval_dir = resolve_path(str(row.get("eval_dir") or ""))
            targets = sorted(set(parse_target_list(row.get("contaminated_wrong_targets")) + parse_target_list(row.get("wrong_targets"))))
            if target_set:
                targets = [target for target in targets if target in target_set]
            for target in targets:
                vote_path = vote_file_for_target(eval_dir, target)
                if vote_path is None:
                    failures.append({"candidate_label": row.get("candidate_label", ""), "target": target, "error": "missing vote file"})
                    continue
                vote = read_json(vote_path, {})
                out_dir = packets_root / f"{safe_slug(row.get('candidate_label') or 'candidate')}__{safe_slug(target)}"
                packet_out = out_dir / "packet.json"
                prompt_out = out_dir / "generation_prompt.txt"
                micro_packet = {
                    "mode": "evidence_bound_micro_edit_packet_v1",
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "paper_spec": row.get("paper_spec", ""),
                    "candidate_label": row.get("candidate_label", ""),
                    "target": target,
                    "source_selection_ledger_row": row,
                    "source_packet": rel(packet_path),
                    "source_graph_spec": rel(graph_spec_path),
                    "source_eval_dir": row.get("eval_dir", ""),
                    "source_vote_file": rel(vote_path),
                    "paper_anchor": packet.get("paper_anchor"),
                    "target_context": target_context(spec, target),
                    "candidate_relevant_evidence": candidate_relevant_evidence(spec, packet, target),
                    "acceptance_boundary": {
                        "final_CG": 1.0,
                        "final_REA": 1.0,
                        "no_provider_contamination": True,
                        "non_regression_against_candidate_label": row.get("candidate_label", ""),
                        "baseline_CG": row.get("CG"),
                        "baseline_REA": row.get("REA"),
                    },
                }
                prompt = build_prompt(packet, spec, target, vote)
                write_json(packet_out, micro_packet)
                write_text(prompt_out, prompt)
                index_rows.append(
                    {
                        "priority": len(index_rows) + 1,
                        "paper_spec": row.get("paper_spec", ""),
                        "candidate_label": row.get("candidate_label", ""),
                        "target": target,
                        "lane": row.get("lane", ""),
                        "packet_json": rel(packet_out),
                        "generation_prompt": rel(prompt_out),
                        "source_graph_spec": rel(graph_spec_path),
                        "source_eval_dir": row.get("eval_dir", ""),
                        "source_vote_file": rel(vote_path),
                        "baseline_CG": row.get("CG", ""),
                        "baseline_REA": row.get("REA", ""),
                        "packet_sha256": sha256_file(packet_out),
                        "prompt_sha256": sha256_file(prompt_out),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"candidate_label": row.get("candidate_label", ""), "error": str(exc)})
    return index_rows, failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--targets", nargs="+", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ledger_path = resolve_path(args.selection_ledger)
    out_root = resolve_path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    rows = selected_best_rows(ledger_path)
    index_rows, failures = build_packets(rows, out_root, args.targets)
    fieldnames = [
        "priority",
        "paper_spec",
        "candidate_label",
        "target",
        "lane",
        "packet_json",
        "generation_prompt",
        "source_graph_spec",
        "source_eval_dir",
        "source_vote_file",
        "baseline_CG",
        "baseline_REA",
        "packet_sha256",
        "prompt_sha256",
    ]
    write_csv(out_root / "MICRO_EDIT_PACKET_INDEX.csv", index_rows, fieldnames)
    write_json(out_root / "MICRO_EDIT_PACKET_INDEX.json", {"rows": index_rows})
    write_json(out_root / "MICRO_EDIT_PACKET_FAILURES.json", failures)
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "evidence_bound_micro_edit_packet_build",
        "provider_calls": False,
        "selection_ledger": rel(ledger_path),
        "out_root": rel(out_root),
        "selected_best_rows": len(rows),
        "packet_count": len(index_rows),
        "failure_count": len(failures),
        "targets": sorted({row["target"] for row in index_rows}),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
    }
    write_json(out_root / "MICRO_EDIT_PACKET_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
