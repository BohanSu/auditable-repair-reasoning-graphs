#!/usr/bin/env python3
"""Build no-anchor evidence-bound reconstruction packets after notation replay.

The packets are provider-free staging artifacts. They do not claim closure and
do not edit canonical or proposed accounting. They isolate the current 42-row
after-notation residual ledger's no-anchor rows for claim-level reconstruction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RUN_ROOT = PACKAGE_ROOT.parents[0]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"

DEFAULT_LEDGER = RESIDUAL_ROOT / "closeout_design" / "20260604_after_notation_replay_v1" / "RESIDUAL_CLOSEOUT_LEDGER.json"
DEFAULT_ACCOUNTING = (
    RESIDUAL_ROOT
    / "proposed_accounting_merges"
    / "20260604_combined_guarded_v5_plus_anchor_v3_plus_same_paper_v2_plus_ans_surface_v4_plus_coverage_v5_plus_rub15_v1_plus_notation_replay_v1"
    / "PROPOSED_FULL_350_ACCOUNTING.csv"
)
DEFAULT_ANS_NODE_RESULTS = PACKAGE_ROOT / "08_ans_factscore_style" / "03_current_full_run" / "ans_node_results.jsonl"
DEFAULT_OUT_ROOT = RESIDUAL_ROOT / "evidence_bound_regeneration" / "no_anchor_after_notation_v1"

STANDARD_EDGE_TYPES = [
    "deduction-rule",
    "deduction-case",
    "abduction-phenomenon",
    "abduction-knowledge",
    "induction-case",
    "induction-common",
]
ANS_STAGES = [
    "raw_step1_extraction",
    "llm_step2_self_fix_final_clean",
    "pearl_terminal_graph",
]
EXCLUDED_ANS_UNIT_TYPES = {"root_common_bridge", "graph_node"}
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_slug(value: str, limit: int = 128) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return (slug or "item")[:limit]


def compact_text(text: Any, max_chars: int = 650) -> str:
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    return out if len(out) <= max_chars else out[: max_chars - 3].rstrip() + "..."


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = text.replace("μ", "mu").replace("µ", "mu")
    text = text.replace("σ", "sigma").replace("β", "beta").replace("α", "alpha")
    return " ".join(TOKEN_RE.findall(text))


def token_set(text: str) -> set[str]:
    return set(normalize_text(text).split())


def source_tuple_key(value: Any) -> str:
    if not isinstance(value, list) or len(value) < 2:
        return ""
    try:
        sent_idx = int(value[0])
        viewpoint_idx = int(value[1])
    except (TypeError, ValueError):
        return ""
    if sent_idx <= 0:
        return ""
    return f"{sent_idx}:{viewpoint_idx}"


def load_ans(path: Path, wanted_specs: set[str]) -> dict[str, dict[str, Any]]:
    stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"supported": 0, "total": 0, "nodes": 0})
    source_stats: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"supported": 0, "total": 0, "nodes": 0}
    )
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            spec = str(row.get("paper_spec") or "")
            if spec not in wanted_specs:
                continue
            if row.get("unit_type") in EXCLUDED_ANS_UNIT_TYPES:
                continue
            stage = str(row.get("stage") or "")
            if stage not in ANS_STAGES:
                continue
            cell = stats[(spec, stage)]
            cell["supported"] += int(row.get("supported_fact_count") or 0)
            cell["total"] += int(row.get("atomic_fact_count") or 0)
            cell["nodes"] += 1
            source_key = source_tuple_key(row.get("source_tuple"))
            if source_key:
                source_cell = source_stats[(spec, stage, source_key)]
                source_cell["supported"] += int(row.get("supported_fact_count") or 0)
                source_cell["total"] += int(row.get("atomic_fact_count") or 0)
                source_cell["nodes"] += 1
    out: dict[str, dict[str, Any]] = {}
    for spec in wanted_specs:
        stage_rows: dict[str, Any] = {}
        scores = []
        best_stage = ""
        best_score = -1.0
        for stage in ANS_STAGES:
            cell = stats[(spec, stage)]
            score = cell["supported"] / cell["total"] if cell["total"] else None
            if score is not None:
                scores.append(score)
                if score > best_score:
                    best_score = score
                    best_stage = stage
            stage_rows[stage] = {
                "supported": cell["supported"],
                "total": cell["total"],
                "nodes": cell["nodes"],
                "ans": score,
            }
        source_support: dict[str, Any] = {}
        sentence_support: dict[str, float] = {}
        if best_stage:
            for (source_spec, stage, source_key), cell in source_stats.items():
                if source_spec != spec or stage != best_stage or not cell["total"]:
                    continue
                score = cell["supported"] / cell["total"]
                source_support[source_key] = {
                    "supported": cell["supported"],
                    "total": cell["total"],
                    "nodes": cell["nodes"],
                    "ans": score,
                }
                sent_idx = source_key.split(":", 1)[0]
                sentence_support[sent_idx] = max(sentence_support.get(sent_idx, 0.0), score)
        out[spec] = {
            "stages": stage_rows,
            "current_best_main_factual_ans": max(scores) if scores else None,
            "current_best_stage": best_stage,
            "source_support": source_support,
            "sentence_support": sentence_support,
        }
    return out


def run_id_from_eval_dir(eval_dir: str) -> str:
    parts = Path(eval_dir).parts
    if "model_outputs" in parts:
        idx = parts.index("model_outputs")
        if len(parts) > idx + 3:
            return parts[idx + 3]
    return ""


def package_input_path(model: str, paper: str, run_id: str) -> Path | None:
    base = RUN_ROOT / "01_teacher_pool_inputs" / model / paper
    candidates: list[Path] = []
    if run_id:
        candidates.append(base / run_id / "input_data.json")
    if base.exists():
        candidates.extend(sorted(base.glob("*/input_data.json")))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def source_eval_results(source_eval_dir: str) -> tuple[Path | None, dict[str, Any]]:
    if not source_eval_dir:
        return None, {}
    path = resolve(source_eval_dir)
    direct = path / "evaluation_results.json"
    if direct.exists():
        payload = read_json(direct)
        return direct, payload if isinstance(payload, dict) else {}
    if path.name == "evaluation_outputs" and path.exists():
        candidates = sorted(path.glob("*/evaluation_results.json"))
        if candidates:
            payload = read_json(candidates[-1])
            return candidates[-1], payload if isinstance(payload, dict) else {}
    return None, {}


def sentence_rows(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sent in (input_data.get("introduction") or {}).get("sentences") or []:
        if not isinstance(sent, dict):
            continue
        idx = sent.get("idx")
        sentence = compact_text(sent.get("sentence"), 900)
        viewpoints = [compact_text(v, 420) for v in sent.get("viewpoints") or [] if str(v).strip()]
        text = " ".join([sentence, *viewpoints])
        rows.append(
            {
                "idx": idx,
                "source": [idx, 0, 0],
                "sentence": sentence,
                "viewpoints": viewpoints[:4],
                "tokens": token_set(text),
            }
        )
    return rows


def entity_map(
    entities: list[str],
    sentences: list[dict[str, Any]],
    top_k: int,
    sentence_support: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    sentence_support = sentence_support or {}
    for entity in entities:
        entity_tokens = token_set(entity)
        scored: list[dict[str, Any]] = []
        for sent in sentences:
            overlap = sorted(entity_tokens & sent["tokens"])
            exact = normalize_text(entity) in normalize_text(sent["sentence"])
            if not overlap and not exact:
                continue
            ans_support = float(sentence_support.get(str(sent["idx"]), 0.0) or 0.0)
            score = len(overlap) / max(1, len(entity_tokens)) + (1.0 if exact else 0.0) + 0.35 * ans_support
            scored.append(
                {
                    "idx": sent["idx"],
                    "source": sent["source"],
                    "score": round(score, 4),
                    "ans_source_support": round(ans_support, 4),
                    "exact": exact,
                    "matched_tokens": overlap,
                    "sentence": sent["sentence"],
                    "viewpoints": sent["viewpoints"][:2],
                }
            )
        scored.sort(key=lambda row: (-float(row["score"]), int(row.get("idx") or 0)))
        mapped.append(
            {
                "entity": entity,
                "status": "mapped" if scored else "weak_or_missing",
                "top_sentences": scored[:top_k],
            }
        )
    return mapped


def selected_prompt_sentences(entity_evidence: list[dict[str, Any]], sentences: list[dict[str, Any]], max_sentences: int) -> list[dict[str, Any]]:
    by_idx = {row["idx"]: row for row in sentences}
    scores: dict[int, float] = {}
    ans_support_by_idx: dict[int, float] = {}
    for item in entity_evidence:
        for rank, sent in enumerate(item.get("top_sentences") or []):
            idx = int(sent["idx"])
            ans_support = float(sent.get("ans_source_support") or 0.0)
            scores[idx] = max(scores.get(idx, 0.0), float(sent["score"]) + 0.2 * ans_support - rank * 0.01)
            ans_support_by_idx[idx] = max(ans_support_by_idx.get(idx, 0.0), ans_support)
    chosen = sorted(scores, key=lambda idx: (-scores[idx], idx))[:max_sentences]
    return [
        {
            "idx": idx,
            "source": [idx, 0, 0],
            "sentence": by_idx[idx]["sentence"],
            "viewpoints": by_idx[idx]["viewpoints"][:3],
            "ans_source_support": round(ans_support_by_idx.get(idx, 0.0), 4),
        }
        for idx in sorted(chosen)
        if idx in by_idx
    ]


def build_prompt(packet: dict[str, Any]) -> str:
    evidence_lines: list[str] = []
    for sent in packet["evidence"]["prompt_sentences"]:
        support = float(sent.get("ans_source_support") or 0.0)
        support_tag = f" ANS-support={support:.2f}" if support > 0 else ""
        evidence_lines.append(f"[S{sent['idx']}{support_tag}] {sent['sentence']}")
        for vp_i, viewpoint in enumerate(sent.get("viewpoints") or [], start=1):
            evidence_lines.append(f"  [S{sent['idx']}.V{vp_i}] {viewpoint}")
    entity_lines = []
    for item in packet["evidence"]["entity_evidence"]:
        refs = [f"S{s['idx']}" for s in item.get("top_sentences") or []]
        entity_lines.append(f"- {item['entity']}: {', '.join(refs[:5]) or 'NO_STRONG_MATCH'}")
    return f"""You are reconstructing a PEARL scientific reasoning graph for a no-anchor residual row.

Return only one canonical JSON graph_spec object with keys "root", "nodes", and "edges". Do not return compact r/n/e keys, DOT, markdown, or explanation.

Required JSON skeleton:
{{
  "root": "NROOT",
  "nodes": [
    {{"id": "NROOT", "source": [0,0,0], "text": "paper-level synthesis"}},
    {{"id": "E1", "source": [1,1,0], "text": "one evidence fact"}},
    {{"id": "R1", "source": [0,0,0], "text": "one validated intermediate claim"}}
  ],
  "edges": [
    {{"source": "E1", "target": "R1", "type": "deduction-case"}},
    {{"source": "R1", "target": "NROOT", "type": "induction-case"}}
  ]
}}

Root hard constraints:
- The root field must be exactly "NROOT".
- There must be a node with id exactly "NROOT".
- Do not set any R node as root.
- Every E and R node must have a directed path into NROOT.

Root:
- Use root id exactly "NROOT".
- NROOT must synthesize the paper-level semantic conclusion using only relations already stated by evidence nodes.
- ANS-safe NROOT rule: do not write meta claims such as "the paper concludes", "the study proposes", "paper-level conclusion", "may synthesize", "convergent findings", or "is proposed as" unless those exact relations are stated in evidence. Prefer a short conjunction of evidence-supported clauses copied from Sx.Vy lines.

Node rules:
- Evidence nodes use ids E1, E2, ... and cite exactly one source tuple as "source": [sentence_index, viewpoint_index, 0]. For example, S29.V2 must be "source": [29,2,0], not [29,29,2]. Do not wrap the source tuple in another list.
- Reasoning nodes use ids R1, R2, ... and may use source tuple "source": [0,0,0] only for concise structural bridge rules. Do not write "source": [[0,0,0]].
- Every node text must be short, source-grounded, and scientifically precise.
- Cover every required entity at least once in connected node text.

Edge rules:
- Edge labels must be exactly one of: {", ".join(STANDARD_EDGE_TYPES)}.
- A deduction target must have exactly one deduction-rule edge and one deduction-case edge.
- An abduction target must have exactly one abduction-knowledge edge and one abduction-phenomenon edge.
- An induction target must have one or more induction-case edges and one induction-common edge.
- Do not mix reasoning families into the same target.
- No isolated nodes, no self-loops, and no outgoing edges from NROOT.
- Every non-root node must be used by at least one edge. Do not include unused evidence nodes.
- A target cannot receive an extra edge from a second family. For example, do not add an induction-case edge to a target that already has deduction-rule and deduction-case.

Quality guard:
- Fresh acceptance requires final CG=1.0 and final REA=1.0.
- Preserve ANS factuality; current best main-factual ANS is {packet['quality_guard'].get('current_best_main_factual_ans')}.
- Do not add unsupported claims only to cover entities.
- Evidence lines marked ANS-support close to 1.00 were factual under the current best stage. Prefer those lines when choosing E nodes and when wording NROOT, especially when the current best ANS is high.

Atomic support protocol:
- Treat each evidence viewpoint Sx.Vy as an atomic fact. Prefer copying the scientific relation from a viewpoint rather than paraphrasing it into a broader causal claim.
- Each reasoning node should introduce at most one new scientific relation. If a statement needs two relations, split it into two R nodes.
- For deduction and abduction targets, every non-generic scientific attribute in the target must already appear in an incoming premise node. Do not introduce a new property such as lower reactivity, homogeneous redox, capacity retention, cycling stability, or degradation suppression unless one incoming source explicitly states it.
- Required entity coverage is not permission to overclaim. If a required entity is a compound phrase but the evidence only supports its parts, put the exact compound phrase only in NROOT and keep intermediate R nodes decomposed into supported parts.
- Do not create a generic rule such as "source-stated facts are accepted" and then use it to restate evidence as R nodes. Judges often reject this as circular.
- If a source viewpoint already states an atomic claim, use that E node directly as an induction-case into NROOT instead of making a tautological E -> R deduction.
- If two effects share the same cause, do not infer that one effect supports the other unless an evidence sentence states that bridge. Use an induction summary instead of a deduction if the graph is only collecting parallel effects.
- Avoid weak bridge verbs as the main claim: connects, explains, supports, enables, drives, thereby, improves. Use the exact source relation when available, for example "S28 states X creates Y" or "S29 states improved surface stability allows Z".
- Keep NROOT as a synthesis, but keep all intermediate R nodes literal and evidence-close. A judge should be able to validate each R node using only its immediate incoming sources.
- Prefer zero R nodes for no-anchor reconstruction. If the graph only collects source-stated facts, connect E nodes directly to NROOT.
- For the required induction-common edge into NROOT, prefer reusing a broad, explicit E node as the induction-common source. Do not invent a generic R node such as "convergent findings are synthesized" or "paper-level conclusion may synthesize"; ANS treats these as unsupported.
- If an R node is unavoidable, its text must be a literal relation from an incoming E node plus one generic bridge word at most. It must not contain "paper-level", "conclusion", "synthesize", "proposed", "may", "co-contextual", or "convergent" unless those words appear in an incoming evidence sentence.

Recommended compact structure:
- Use 8-14 evidence nodes selected from the strongest Sx.Vy lines.
- Use 1-4 reasoning nodes only when they add a real bridge beyond an evidence restatement.
- Prefer direct evidence nodes as induction-case inputs to NROOT for explicitly stated paper claims.
- Use one final induction into NROOT from evidence nodes and any truly necessary R bridge nodes; in most no-anchor rows, use only E nodes feeding NROOT.
- If an evidence sentence has several viewpoints, create separate E nodes, one source tuple per E node.

Core idea:
{packet['paper_anchor']['core_idea']}

Required entities:
{json.dumps(packet['paper_anchor']['entities'], ensure_ascii=False)}

Entity evidence map:
{chr(10).join(entity_lines)}

Evidence:
{chr(10).join(evidence_lines)}
"""


def build_packet(
    *,
    ledger_row: dict[str, Any],
    accounting_row: dict[str, str],
    ans: dict[str, Any],
    top_k: int,
    max_prompt_sentences: int,
) -> dict[str, Any]:
    model = ledger_row["model"]
    paper = ledger_row["paper"]
    run_id = run_id_from_eval_dir(accounting_row.get("source_eval_dir", ""))
    input_path = package_input_path(model, paper, run_id)
    if input_path is None:
        raise FileNotFoundError(f"input_data.json not found for {ledger_row['paper_spec']}")
    eval_json, eval_payload = source_eval_results(accounting_row.get("source_eval_dir", ""))
    if not eval_payload:
        raise FileNotFoundError(f"source evaluation_results.json not found for {ledger_row['paper_spec']}")
    input_data = read_json(input_path)
    sentences = sentence_rows(input_data)
    entities = [str(item).strip() for item in eval_payload.get("entities") or [] if str(item).strip()]
    evidence = entity_map(entities, sentences, top_k, ans.get("sentence_support") or {})
    prompt_sentences = selected_prompt_sentences(evidence, sentences, max_prompt_sentences)
    weak = [item["entity"] for item in evidence if item["status"] != "mapped"]
    packet = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "no_anchor_evidence_bound_claim_reconstruction_after_notation_v1",
        "provider_calls": False,
        "canonical_accounting_write": False,
        "paper_spec": ledger_row["paper_spec"],
        "model": model,
        "paper": paper,
        "run_id": run_id,
        "failure_type": ledger_row.get("failure_type", ""),
        "source_paths": {
            "input_data": rel(input_path),
            "source_eval_dir": accounting_row.get("source_eval_dir", ""),
            "source_evaluation_results": rel(eval_json) if eval_json else "",
            "source_evaluation_results_sha256": sha256_file(eval_json) if eval_json else "",
        },
        "paper_anchor": {
            "core_idea": eval_payload.get("core_idea", ""),
            "entities": entities,
        },
        "current_metrics": {
            "final_CG": to_float(accounting_row.get("final_CG")),
            "final_REA": to_float(accounting_row.get("final_REA")),
            "original_CG": to_float(accounting_row.get("original_CG")),
            "original_REA": to_float(accounting_row.get("original_REA")),
        },
        "quality_guard": {
            "ans": ans,
            "current_best_main_factual_ans": ans.get("current_best_main_factual_ans"),
            "strict_gate": {"final_CG": 1.0, "final_REA": 1.0},
            "candidate_policy": "fresh CG/REA strict gate plus ANS non-regression before proposed merge",
        },
        "evidence": {
            "sentence_count": len(sentences),
            "prompt_sentence_count": len(prompt_sentences),
            "prompt_sentences": prompt_sentences,
            "entity_evidence": evidence,
            "weak_or_missing_entities": weak,
        },
        "generation_contract": {
            "output_format": "graph_spec_json",
            "root_id": "NROOT",
            "claim_inventory_first": True,
            "graph_changed": True,
            "fresh_evaluation_required": True,
            "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0},
        },
    }
    packet["generation_prompt"] = build_prompt(packet)
    return packet


def build_design_note(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# No-Anchor Evidence-Bound Reconstruction Packets",
            "",
            f"Created: `{summary['created_at']}`",
            "",
            "This package is a staging layer for the 28 no-anchor residual rows after notation replay. It does not call providers, edit graphs, or write canonical accounting.",
            "",
            "## Literature-Grounded Constraints",
            "",
            "- FActScore (2023): score factuality at atomic fact level rather than whole-response level. https://arxiv.org/abs/2305.14251",
            "- SAFE (2024): use search/evidence-supported long-form factuality checking. https://arxiv.org/abs/2403.18802",
            "- RefChecker (2024): decompose outputs into fine-grained claims and verify against references. https://arxiv.org/abs/2405.14486",
            "- VeriScore (2024): separate verifiable claims from unverifiable/structural text. https://arxiv.org/abs/2406.19276",
            "- RAGChecker (2024): diagnose evidence/retrieval coverage separately from generation correctness. https://arxiv.org/abs/2408.08067",
            "- VeriFastScore (2025): batch claim extraction and verification to reduce repeated calls. https://arxiv.org/abs/2505.16973",
            "- FASTFACT (2025): use chunk/document-level evidence for long-form factuality rather than isolated snippets. https://arxiv.org/abs/2510.12839",
            "",
            "## Module Contract",
            "",
            "1. Start from paper evidence and source evaluator core idea/entities, not from the failed no-anchor graph.",
            "2. Build an entity-to-evidence inventory before graph generation.",
            "3. Ask for typed graph_spec JSON with explicit reasoning families and NROOT.",
            "4. Fresh evaluate every generated graph; only CG=1.0 and REA=1.0 can enter a merge package.",
            "5. Apply ANS non-regression guard before any proposed accounting merge.",
            "",
            "## Summary",
            "",
            f"- Selected no-anchor rows: `{summary['selected_rows']}`",
            f"- Packet count: `{summary['packet_count']}`",
            f"- Build failures: `{summary['failure_count']}`",
            f"- Weak/missing entity rows: `{summary['weak_or_missing_entity_rows']}`",
            "",
        ]
    )


def build(args: argparse.Namespace) -> dict[str, Any]:
    ledger = read_json(resolve(args.ledger))
    ledger_rows = [
        row
        for row in ledger.get("rows", [])
        if isinstance(row, dict) and row.get("next_closeout_module") == "evidence_bound_claim_graph_reconstruction"
    ]
    selected_specs = {row["paper_spec"] for row in ledger_rows}
    accounting_rows = read_csv(resolve(args.accounting))
    accounting_by_spec = {row["paper_spec"]: row for row in accounting_rows}
    ans_by_spec = load_ans(resolve(args.ans_node_results), selected_specs)
    out_root = resolve(args.out_root)
    packets_root = out_root / "packets"
    packets_root.mkdir(parents=True, exist_ok=True)

    index_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for priority, ledger_row in enumerate(sorted(ledger_rows, key=lambda row: row["paper_spec"]), start=1):
        spec = ledger_row["paper_spec"]
        try:
            accounting_row = accounting_by_spec[spec]
            packet = build_packet(
                ledger_row=ledger_row,
                accounting_row=accounting_row,
                ans=ans_by_spec.get(spec, {}),
                top_k=args.top_k_evidence,
                max_prompt_sentences=args.max_prompt_sentences,
            )
            packet_dir = packets_root / f"{priority:03d}_{safe_slug(spec)}"
            packet_json = packet_dir / "packet.json"
            prompt_txt = packet_dir / "generation_prompt.txt"
            write_json(packet_json, {key: value for key, value in packet.items() if key != "generation_prompt"})
            write_text(prompt_txt, packet["generation_prompt"])
            weak = packet["evidence"]["weak_or_missing_entities"]
            index_rows.append(
                {
                    "priority": priority,
                    "paper_spec": spec,
                    "lane": "evidence_bound_claim_graph_reconstruction",
                    "model": packet["model"],
                    "paper": packet["paper"],
                    "run_id": packet["run_id"],
                    "failure_type": packet["failure_type"],
                    "packet_json": rel(packet_json),
                    "generation_prompt": rel(prompt_txt),
                    "input_data": packet["source_paths"]["input_data"],
                    "source_evaluation_results": packet["source_paths"]["source_evaluation_results"],
                    "entity_count": len(packet["paper_anchor"]["entities"]),
                    "weak_or_missing_entity_count": len(weak),
                    "weak_or_missing_entities": "; ".join(weak),
                    "prompt_sentence_count": packet["evidence"]["prompt_sentence_count"],
                    "current_best_main_factual_ans": packet["quality_guard"].get("current_best_main_factual_ans"),
                    "packet_sha256": sha256_file(packet_json),
                    "prompt_sha256": sha256_file(prompt_txt),
                }
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"paper_spec": spec, "error": str(exc)})

    fieldnames = [
        "priority",
        "paper_spec",
        "lane",
        "model",
        "paper",
        "run_id",
        "failure_type",
        "packet_json",
        "generation_prompt",
        "input_data",
        "source_evaluation_results",
        "entity_count",
        "weak_or_missing_entity_count",
        "weak_or_missing_entities",
        "prompt_sentence_count",
        "current_best_main_factual_ans",
        "packet_sha256",
        "prompt_sha256",
    ]
    write_csv(out_root / "NO_ANCHOR_PACKET_INDEX.csv", index_rows, fieldnames)
    write_json(out_root / "NO_ANCHOR_PACKET_INDEX.json", {"rows": index_rows})
    write_json(out_root / "NO_ANCHOR_PACKET_FAILURES.json", failures)
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_ledger": rel(resolve(args.ledger)),
        "source_accounting": rel(resolve(args.accounting)),
        "out_root": rel(out_root),
        "selected_rows": len(ledger_rows),
        "packet_count": len(index_rows),
        "failure_count": len(failures),
        "by_model": dict(Counter(row["model"] for row in index_rows)),
        "weak_or_missing_entity_rows": sum(1 for row in index_rows if int(row["weak_or_missing_entity_count"]) > 0),
        "weak_or_missing_entity_total": sum(int(row["weak_or_missing_entity_count"]) for row in index_rows),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
    }
    write_json(out_root / "NO_ANCHOR_PACKET_SUMMARY.json", summary)
    write_text(out_root / "NO_ANCHOR_RECONSTRUCTION_DESIGN.md", build_design_note(summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--accounting", default=str(DEFAULT_ACCOUNTING))
    parser.add_argument("--ans-node-results", default=str(DEFAULT_ANS_NODE_RESULTS))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--top-k-evidence", type=int, default=5)
    parser.add_argument("--max-prompt-sentences", type=int, default=34)
    return parser.parse_args()


def main() -> int:
    build(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
