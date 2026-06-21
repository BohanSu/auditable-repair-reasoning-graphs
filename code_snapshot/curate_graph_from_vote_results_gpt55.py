#!/usr/bin/env python3
"""PEARL vote-guided GPT-5.5 curation and semantic root recovery.

This script is an implementation component of PEARL. It does not overwrite
existing PEARL or teacher-pool artifacts. Given one historical model-output run
with evaluator vote files, it writes:

  - curation_packet.json
  - gpt55_curation_prompt.txt
  - deterministic_vote_kept_graph.{json,dot}
  - optional gpt55_curated_graph.{json,dot} if the model is called

The default curation policy is not allowed to add a synthetic aggregation/root
node. When `--semantic-root` is used, the script instead recovers a true
paper-level `NROOT` as PEARL's semantic root recovery module.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK_DIR = PROJECT_ROOT / "code" / "framework"
sys.path.insert(0, str(FRAMEWORK_DIR))

from dot_to_graph_spec import dot_to_graph_spec  # type: ignore  # noqa: E402
from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from reasoning_graph_validator import assess_dot_quality  # type: ignore  # noqa: E402


STANDARD_EDGE_TYPES = {
    "deduction-rule",
    "deduction-case",
    "induction-case",
    "induction-common",
    "abduction-phenomenon",
    "abduction-knowledge",
}

DEFAULT_RUN_DIR = (
    PROJECT_ROOT
    / "data"
    / "teacher_pool"
    / "model_outputs"
    / "qwen3_5_397b_a17b"
    / "s41467-025-55907-w"
    / "20260316_190953"
)
DEFAULT_OUT_ROOT = PROJECT_ROOT / "reports" / "pearl_runs" / "gpt55_vote_guided_curation_pilot"
DEFAULT_GPT55_CURATION_BASE_URL = "https://xingyecode.xyz/v1"
DEFAULT_GPT55_CURATION_MODEL = "gpt-5.5"
DEFAULT_GPT55_CURATION_TRANSPORT = "curl"
DEFAULT_GPT55_CURATION_STREAM = True


class ChatCompletionError(RuntimeError):
    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    var_pattern = re.compile(r"\$(\w+)|\$\{([^}]+)\}")

    def expand(value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2) or ""
            return os.environ.get(name, match.group(0))

        return var_pattern.sub(repl, value)

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        expanded = expand(value)
        existing = os.environ.get(key)
        if existing is None or "$" in existing:
            os.environ[key] = expanded


def expand_env_refs(value: str) -> str:
    var_pattern = re.compile(r"\$(\w+)|\$\{([^}]+)\}")

    def repl(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2) or ""
        return os.environ.get(name, match.group(0))

    return var_pattern.sub(repl, value).strip()


def first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = expand_env_refs(os.getenv(name, ""))
        if value and "$" not in value:
            return value
    return expand_env_refs(default)


def gpt55_endpoint_configs(args: argparse.Namespace) -> List[Dict[str, str]]:
    def add_endpoint(name: str, api_key: str, base_url: str) -> None:
        api_key = expand_env_refs(api_key)
        base_url = expand_env_refs(base_url)
        if not api_key or not base_url:
            return
        if "$" in api_key or "$" in base_url:
            return
        if not base_url.startswith(("http://", "https://")):
            return
        normalized = base_url.rstrip("/")
        for endpoint in endpoints:
            if endpoint["base_url"].rstrip("/") == normalized:
                return
        endpoints.append(
            {
                "name": name,
                "api_key": api_key,
                "base_url": base_url,
            }
        )

    primary_api_key = args.api_key or first_env(
        "GPT55_CURATION_API_KEY",
        "OPENAI_API_KEY",
        "GPT_API_KEY",
        "API_KEY",
    )
    primary_base_url = args.base_url or first_env(
        "GPT55_CURATION_BASE_URL",
        "OPENAI_BASE_URL",
        "GPT_BASE_URL",
        "BASE_URL",
        default=DEFAULT_GPT55_CURATION_BASE_URL,
    )
    endpoints: List[Dict[str, str]] = []
    add_endpoint("primary", primary_api_key, primary_base_url)

    fallback_api_key = args.fallback_api_key or first_env(
        "GPT55_CURATION_FALLBACK_API_KEY",
        "OPENAI_FALLBACK_API_KEY",
    )
    fallback_base_url = args.fallback_base_url or first_env(
        "GPT55_CURATION_FALLBACK_BASE_URL",
        "OPENAI_FALLBACK_BASE_URL",
    )
    if os.getenv("GPT55_CURATION_DISABLE_FALLBACKS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return endpoints
    add_endpoint("fallback", fallback_api_key, fallback_base_url)

    fallback_api_key_2 = args.fallback_api_key_2 or first_env(
        "GPT55_CURATION_FALLBACK_API_KEY_2",
        "OPENAI_FALLBACK_API_KEY_2",
    )
    fallback_base_url_2 = args.fallback_base_url_2 or first_env(
        "GPT55_CURATION_FALLBACK_BASE_URL_2",
        "OPENAI_FALLBACK_BASE_URL_2",
    )
    add_endpoint("fallback_2", fallback_api_key_2, fallback_base_url_2)
    return endpoints


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def compact_repair_feedback(feedback: Dict[str, Any]) -> Dict[str, Any]:
    vote = feedback.get("vote", {}) if isinstance(feedback, dict) else {}
    failed_target = str(feedback.get("failed_target_node", ""))
    repair_target_reasoning_id = feedback.get("repair_target_reasoning_id")
    if repair_target_reasoning_id is None and failed_target.startswith("R"):
        try:
            repair_target_reasoning_id = int(failed_target[1:])
        except ValueError:
            repair_target_reasoning_id = None
    model_reasons: Dict[str, Dict[str, str]] = {}
    for model_key, payload in (vote.get("model_responses") or {}).items():
        if not isinstance(payload, dict):
            continue
        model_reasons[str(model_key)] = {
            "result": str(payload.get("result", "")),
            "reason": shorten(payload.get("reason", ""), 420),
        }
    compacted = {
        "status": feedback.get("status", ""),
        "failed_validation_prompt_id": feedback.get(
            "failed_validation_prompt_id",
            feedback.get("failed_reasoning_id"),
        ),
        "failed_target_node": failed_target,
        "repair_target_reasoning_id": repair_target_reasoning_id,
        "source_nodes": vote.get("source_nodes", []),
        "edge_types": vote.get("edge_types", []),
        "target_content": shorten(vote.get("target_content", ""), 700),
        "source_contents": [shorten(item, 500) for item in vote.get("source_contents", [])],
        "premise_descriptions": [shorten(item, 500) for item in vote.get("premise_descriptions", [])],
        "model_results": vote.get("model_results", {}),
        "judge_reasons": model_reasons,
    }
    if isinstance(feedback.get("repair_target_mapping"), dict):
        compacted["repair_target_mapping"] = feedback["repair_target_mapping"]
    for key in (
        "metric_feedback",
        "final_metrics",
        "frontier_metrics",
        "audited_frontier",
        "thresholds",
        "missing_entities",
        "coverage_note",
        "required_action",
    ):
        if key in feedback:
            compacted[key] = feedback[key]
    return compacted


def repair_target_reasoning_id_from_feedback(repair_feedback: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(repair_feedback, dict):
        return None
    compacted = compact_repair_feedback(repair_feedback)
    rid = compacted.get("repair_target_reasoning_id")
    try:
        return int(rid)
    except (TypeError, ValueError):
        return None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def shorten(text: Any, limit: int = 600) -> str:
    if not isinstance(text, str):
        text = str(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def infer_ids(run_dir: Path) -> Dict[str, str]:
    parts = run_dir.resolve().parts
    model = ""
    paper_id = run_dir.parent.name
    run_id = run_dir.name
    if "model_outputs" in parts:
        idx = parts.index("model_outputs")
        if len(parts) > idx + 1:
            model = parts[idx + 1]
    return {"model": model, "paper_id": paper_id, "run_id": run_id}


def latest_eval_dir(run_dir: Path) -> Path:
    eval_root = run_dir / "evaluation_outputs"
    if not eval_root.exists():
        raise FileNotFoundError(f"evaluation_outputs missing: {eval_root}")
    candidates = [
        path
        for path in eval_root.iterdir()
        if path.is_dir() and (path / "evaluation_results.json").exists()
    ]
    if not candidates:
        raise FileNotFoundError(f"no evaluation_results.json found under {eval_root}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def valid_pair(edge_types: Sequence[str]) -> bool:
    counts = Counter(edge_types)
    edge_set = set(edge_types)
    if edge_set - STANDARD_EDGE_TYPES:
        return False
    if "deduction-rule" in edge_set or "deduction-case" in edge_set:
        return (
            counts["deduction-rule"] == 1
            and counts["deduction-case"] == 1
            and len(edge_types) == 2
        )
    if "abduction-phenomenon" in edge_set or "abduction-knowledge" in edge_set:
        return (
            counts["abduction-phenomenon"] == 1
            and counts["abduction-knowledge"] == 1
            and len(edge_types) == 2
        )
    if "induction-common" in edge_set or "induction-case" in edge_set:
        return (
            counts["induction-common"] == 1
            and counts["induction-case"] >= 1
            and counts["induction-common"] + counts["induction-case"] == len(edge_types)
        )
    return False


def has_structural_self_loop(step: Dict[str, Any]) -> bool:
    return any(
        str(edge.get("source", "")) == str(edge.get("target", ""))
        for edge in step.get("candidate_edges", [])
        if isinstance(edge, dict)
    )


def step_requires_structural_repair(step: Dict[str, Any]) -> bool:
    return step.get("result") == "correct" and has_structural_self_loop(step)


def evaluator_ordered_steps(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reconstruct evaluator step order without importing evaluator.py."""
    by_target: "OrderedDict[str, List[Dict[str, str]]]" = OrderedDict()
    for edge in spec.get("edges", []):
        if not isinstance(edge, dict):
            continue
        target = str(edge.get("target", ""))
        if not target:
            continue
        by_target.setdefault(target, []).append(
            {
                "source": str(edge.get("source", "")),
                "target": target,
                "type": str(edge.get("type", "")),
            }
        )

    all_steps: List[Dict[str, Any]] = []
    valid_steps: List[Dict[str, Any]] = []
    invalid_steps: List[Dict[str, Any]] = []
    for target, edges in by_target.items():
        source_nodes = [edge["source"] for edge in edges]
        edge_types = [edge["type"] for edge in edges]
        if any(edge_type not in STANDARD_EDGE_TYPES for edge_type in edge_types):
            structural_status = "non_standard_edge_error"
        elif len(edge_types) == 1:
            structural_status = "single_edge_error"
        elif valid_pair(edge_types):
            structural_status = "llm_verified_candidate"
        else:
            structural_status = "format_error"
        step = {
            "target_node": target,
            "source_nodes": source_nodes,
            "edge_types": edge_types,
            "candidate_edges": edges,
            "structural_status": structural_status,
        }
        all_steps.append(step)
        if structural_status == "llm_verified_candidate":
            valid_steps.append(step)
        else:
            invalid_steps.append(step)

    ordered: List[Dict[str, Any]] = []
    for idx, step in enumerate(valid_steps, start=1):
        item = dict(step)
        item["reasoning_id"] = idx
        ordered.append(item)
    offset = len(valid_steps)
    for idx, step in enumerate(invalid_steps, start=1):
        item = dict(step)
        item["reasoning_id"] = offset + idx
        ordered.append(item)
    return ordered


def compact_vote(vote: Dict[str, Any]) -> Dict[str, Any]:
    judge_reasons = {}
    for model_key, payload in (vote.get("model_responses") or {}).items():
        if isinstance(payload, dict):
            judge_reasons[model_key] = {
                "result": payload.get("result", ""),
                "reason": shorten(payload.get("reason", ""), 420),
            }
    return {
        "reasoning_id": vote.get("reasoning_id"),
        "final_result": str(vote.get("final_result", "")).lower(),
        "vote_breakdown": vote.get("vote_breakdown", {}),
        "model_results": vote.get("model_results", {}),
        "judge_reasons": judge_reasons,
        "target_content": shorten(vote.get("target_content", ""), 700),
        "source_contents": [shorten(item, 500) for item in vote.get("source_contents", [])],
        "premise_descriptions": [shorten(item, 500) for item in vote.get("premise_descriptions", [])],
    }


def build_packet(run_dir: Path, eval_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ids = infer_ids(run_dir)
    dot_path = run_dir / "final_clean_graph.dot"
    dot_text = dot_path.read_text(encoding="utf-8")
    spec, conversion_issues = dot_to_graph_spec(dot_text, paper_id=ids["paper_id"])
    if spec is None:
        raise RuntimeError(f"failed to parse DOT: {conversion_issues}")

    node_by_id = {
        str(node.get("id")): node
        for node in spec.get("nodes", [])
        if isinstance(node, dict) and node.get("id")
    }
    evaluation_results = load_json(eval_dir / "evaluation_results.json", {})
    details = (evaluation_results.get("accuracy") or {}).get("details") or {}
    votes = {
        int(vote.get("reasoning_id")): vote
        for path in sorted((eval_dir / "responses").glob("reasoning_validation_*_vote_result.json"))
        for vote in [load_json(path, {})]
        if isinstance(vote, dict) and vote.get("reasoning_id") is not None
    }

    steps: List[Dict[str, Any]] = []
    for step in evaluator_ordered_steps(spec):
        rid = int(step["reasoning_id"])
        vote = votes.get(rid, {})
        result = str(details.get(str(rid), details.get(rid, vote.get("final_result", "")))).lower()
        target_node = node_by_id.get(step["target_node"], {})
        source_nodes = [node_by_id.get(node_id, {}) for node_id in step["source_nodes"]]
        steps.append(
            {
                **step,
                "result": result or "missing",
                "target_text": shorten(target_node.get("text", ""), 700),
                "source_texts": [shorten(node.get("text", ""), 500) for node in source_nodes],
                "target_source": target_node.get("source", [0, 0, 0]),
                "source_sources": [node.get("source", [0, 0, 0]) for node in source_nodes],
                "vote": compact_vote(vote) if vote else None,
            }
        )

    kept_edges = [
        edge
        for step in steps
        if step["result"] == "correct"
        for edge in step["candidate_edges"]
    ]
    used_node_ids = sorted(
        {edge["source"] for edge in kept_edges} | {edge["target"] for edge in kept_edges}
    )
    kept_nodes = [node_by_id[node_id] for node_id in used_node_ids if node_id in node_by_id]
    terminal_nodes = sorted(
        {edge["target"] for edge in kept_edges} - {edge["source"] for edge in kept_edges}
    )
    deterministic_spec = {
        "paper_id": ids["paper_id"],
        "root": terminal_nodes[0] if len(terminal_nodes) == 1 else "",
        "nodes": kept_nodes,
        "edges": dedupe_edges(kept_edges),
        "curation": {
            "policy": "keep_only_majority_correct_vote_units",
            "no_synthetic_root": True,
            "terminal_nodes": terminal_nodes,
            "source_eval_dir": str(eval_dir),
        },
    }

    packet = {
        "metadata": {
            **ids,
            "run_dir": str(run_dir),
            "eval_dir": str(eval_dir),
            "graph_file": str(dot_path),
        },
        "evaluation_context": {
            "core_idea": evaluation_results.get("core_idea", ""),
            "entities": evaluation_results.get("entities", []),
        },
        "original_graph": {
            "node_count": len(spec.get("nodes", [])),
            "edge_count": len(spec.get("edges", [])),
            "conversion_issues": conversion_issues,
        },
        "evaluation_summary": evaluation_results.get("evaluation_summary", {}),
        "curation_policy": {
            "goal": "Produce a high-quality semantic graph dataset entry from existing evaluator evidence.",
            "do_not_create_synthetic_root": True,
            "allow_forest": True,
            "allowed_edge_types": sorted(STANDARD_EDGE_TYPES),
            "allowed_node_ids": sorted(node_by_id),
            "default_rule": (
                "Keep majority-correct reasoning units; drop wrong/error/format units unless a repair "
                "uses existing evidence and is explicitly justified."
            ),
        },
        "steps": steps,
        "deterministic_vote_kept_baseline": {
            "kept_reasoning_ids": [step["reasoning_id"] for step in steps if step["result"] == "correct"],
            "dropped_reasoning_ids": [step["reasoning_id"] for step in steps if step["result"] != "correct"],
            "node_count": len(kept_nodes),
            "edge_count": len(deterministic_spec["edges"]),
            "terminal_nodes": terminal_nodes,
        },
    }
    return packet, deterministic_spec


def dedupe_edges(edges: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for edge in edges:
        item = {
            "source": str(edge.get("source", "")),
            "target": str(edge.get("target", "")),
            "type": str(edge.get("type", "")),
        }
        key = (item["source"], item["target"], item["type"])
        if not all(key) or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_prompt(packet: Dict[str, Any]) -> str:
    compact_steps = []
    for step in packet["steps"]:
        vote = step["vote"] or {}
        vote_breakdown = vote.get("vote_breakdown", {}) if isinstance(vote, dict) else {}
        judge_reasons = vote.get("judge_reasons", {}) if isinstance(vote, dict) else {}
        short_reasons = {}
        if step["result"] != "correct":
            for model_key, payload in judge_reasons.items():
                if not isinstance(payload, dict):
                    continue
                short_reasons[model_key] = {
                    "result": payload.get("result", ""),
                    "reason": shorten(payload.get("reason", ""), 180),
                }
        compact_steps.append(
            {
                "reasoning_id": step["reasoning_id"],
                "target_node": step["target_node"],
                "source_nodes": step["source_nodes"],
                "edge_types": step["edge_types"],
                "candidate_edges": step["candidate_edges"],
                "structural_status": step["structural_status"],
                "result": step["result"],
                "target_text": shorten(step["target_text"], 280),
                "source_texts": [shorten(text, 200) for text in step["source_texts"]],
                "vote": {
                    "final_result": vote.get("final_result", step["result"]) if isinstance(vote, dict) else step["result"],
                    "model_results": vote.get("model_results", {}) if isinstance(vote, dict) else {},
                    "decision": vote_breakdown.get("decision", ""),
                    "wrong_or_error_rationales": short_reasons,
                },
            }
        )
    prompt_packet = {
        "metadata": packet["metadata"],
        "evaluation_summary": packet["evaluation_summary"],
        "curation_policy": packet["curation_policy"],
        "steps": compact_steps,
    }
    schema = {
        "paper_id": "string",
        "overall_decision": "curated|insufficient_evidence",
        "final_edges": [
            {
                "source": "existing node id",
                "target": "existing node id",
                "type": "one allowed edge type",
                "supporting_reasoning_ids": [1],
                "decision": "keep|repair",
                "rationale": "short evidence-grounded rationale",
            }
        ],
        "dropped_reasoning_ids": [
            {
                "reasoning_id": 2,
                "reason": "wrong majority / format error / insufficient support",
            }
        ],
        "terminal_nodes": ["node ids with incoming edges and no outgoing edges"],
        "quality_notes": ["short notes"],
    }
    return (
        "You are GPT-5.5 acting as a conservative curator of a scientific reasoning graph.\n"
        "Use only the supplied JSON evidence. Do not invent nodes, node text, sources, or paper facts.\n"
        "Do not add a synthetic/formal aggregation root. If multiple terminal conclusions remain, keep a forest.\n"
        "Prefer keeping majority-correct vote units and dropping wrong/error/format units. A repair is allowed only "
        "when it uses existing node ids, allowed edge types, and has direct support from the supplied judge rationales.\n\n"
        "Return only one JSON object with this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Curation evidence packet:\n"
        f"{json.dumps(prompt_packet, ensure_ascii=False, indent=2)}"
    )


def _compact_step_for_prompt(step: Dict[str, Any], *, include_wrong_reasons: bool = True) -> Dict[str, Any]:
    vote = step["vote"] or {}
    vote_breakdown = vote.get("vote_breakdown", {}) if isinstance(vote, dict) else {}
    judge_reasons = vote.get("judge_reasons", {}) if isinstance(vote, dict) else {}
    short_reasons = {}
    if include_wrong_reasons and step["result"] != "correct":
        for model_key, payload in judge_reasons.items():
            if not isinstance(payload, dict):
                continue
            short_reasons[model_key] = {
                "result": payload.get("result", ""),
                "reason": shorten(payload.get("reason", ""), 180),
            }
    return {
        "reasoning_id": step["reasoning_id"],
        "target_node": step["target_node"],
        "source_nodes": step["source_nodes"],
        "edge_types": step["edge_types"],
        "candidate_edges": step["candidate_edges"],
        "structural_status": step["structural_status"],
        "result": step["result"],
        "target_text": shorten(step["target_text"], 320),
        "source_texts": [shorten(text, 220) for text in step["source_texts"]],
        "vote": {
            "final_result": vote.get("final_result", step["result"]) if isinstance(vote, dict) else step["result"],
            "model_results": vote.get("model_results", {}) if isinstance(vote, dict) else {},
            "decision": vote_breakdown.get("decision", ""),
            "wrong_or_error_rationales": short_reasons,
        },
    }


def downstream_nodes_by_start(steps: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    outgoing: Dict[str, set[str]] = {}
    node_ids: set[str] = set()
    for step in steps:
        target = str(step.get("target_node", ""))
        if not target:
            continue
        node_ids.add(target)
        for source in step.get("source_nodes", []):
            source_id = str(source)
            if not source_id:
                continue
            node_ids.add(source_id)
            outgoing.setdefault(source_id, set()).add(target)

    descendants: Dict[str, List[str]] = {}
    for start in sorted(node_ids):
        seen: set[str] = set()
        stack = list(outgoing.get(start, set()))
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            stack.extend(outgoing.get(node_id, set()) - seen)
        descendants[start] = sorted(seen)
    return descendants


def cascade_correct_steps(packet: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Correct units downstream of rejected/strict-invalid targets need a decision."""
    steps = list(packet.get("steps", []))
    rejected_targets = {
        str(step.get("target_node", ""))
        for step in steps
        if step.get("result") != "correct"
    }
    structural_repair_targets = {
        str(step.get("target_node", ""))
        for step in steps
        if step_requires_structural_repair(step)
    }
    rejected_targets |= structural_repair_targets
    frontier = set(rejected_targets)
    candidate_ids: set[int] = set()
    candidates: List[Dict[str, Any]] = []
    for step in steps:
        if not step_requires_structural_repair(step):
            continue
        rid = int(step.get("reasoning_id", -1))
        if rid in candidate_ids:
            continue
        candidate_ids.add(rid)
        candidates.append(step)
    while frontier:
        next_frontier: set[str] = set()
        for step in steps:
            if step.get("result") != "correct":
                continue
            rid = int(step.get("reasoning_id", -1))
            if rid in candidate_ids:
                continue
            if any(str(source) in frontier for source in step.get("source_nodes", [])):
                candidate_ids.add(rid)
                candidates.append(step)
                target = str(step.get("target_node", ""))
                if target:
                    next_frontier.add(target)
        frontier = next_frontier
    return candidates


def entity_preservation_contract(packet: Dict[str, Any]) -> Dict[str, Any]:
    """Build compact coverage requirements for repair prompts."""
    context = packet.get("evaluation_context", {}) if isinstance(packet.get("evaluation_context"), dict) else {}
    summary = packet.get("evaluation_summary", {}) if isinstance(packet.get("evaluation_summary"), dict) else {}
    entities = [str(entity) for entity in context.get("entities", []) if str(entity).strip()]
    total_entities = int(summary.get("total_entities") or len(entities) or 0)
    covered_entities = int(summary.get("covered_entities") or 0)
    if not covered_entities and total_entities:
        try:
            covered_entities = int(round(float(summary.get("entity_coverage_score", 0.0)) * total_entities))
        except (TypeError, ValueError):
            covered_entities = 0
    return {
        "core_entities": entities,
        "original_CG": float(summary.get("entity_coverage_score", 0.0) or 0.0),
        "original_covered_entities": covered_entities,
        "total_entities": total_entities,
        "minimum_final_covered_entities": covered_entities,
        "coverage_goal": (
            "Do not reduce final clean-graph entity coverage below the original evaluator coverage. "
            "If the original graph covered every listed core entity, the repaired graph should keep every "
            "listed entity represented in judge-correct retained/repaired reasoning and the semantic root."
        ),
    }


def build_semantic_root_prompt(packet: Dict[str, Any], *, compact: bool = False) -> str:
    """Ask GPT-5.5 to recover a true semantic single root, not a formal root."""
    correct_steps = []
    for step in packet["steps"]:
        if step["result"] != "correct":
            continue
        if compact:
            correct_steps.append(
                {
                    "reasoning_id": step["reasoning_id"],
                    "target_node": step["target_node"],
                    "target_text": shorten(step["target_text"], 260),
                    "source_nodes": step["source_nodes"],
                    "edge_types": step["edge_types"],
                }
            )
        else:
            correct_steps.append(_compact_step_for_prompt(step, include_wrong_reasons=False))
    rejected_steps = [] if compact else [
        {
            "reasoning_id": step["reasoning_id"],
            "target_node": step["target_node"],
            "result": step["result"],
            "target_text": shorten(step["target_text"], 220),
        }
        for step in packet["steps"]
        if step["result"] != "correct"
    ]
    terminal_nodes = packet["deterministic_vote_kept_baseline"]["terminal_nodes"]
    terminal_texts = [
        {
            "node_id": step["target_node"],
            "text": shorten(step["target_text"], 360),
            "supporting_reasoning_id": step["reasoning_id"],
        }
        for step in packet["steps"]
        if step["result"] == "correct" and step["target_node"] in terminal_nodes
    ]
    prompt_packet = {
        "metadata": packet["metadata"],
        "evaluation_context": packet.get("evaluation_context", {}),
        "entity_preservation_contract": entity_preservation_contract(packet),
        "original_evaluation_summary": packet["evaluation_summary"],
        "kept_correct_steps": correct_steps,
        "rejected_steps": rejected_steps,
        "current_terminal_conclusions": terminal_texts,
    }
    schema = {
        "paper_id": "string",
        "overall_decision": "single_root_curated|insufficient_evidence",
        "root": "NROOT",
        "new_nodes": [
            {
                "id": "NROOT",
                "source": [0, 0, 0],
                "text": "A real paper-level scientific research proposal supported by the kept terminal conclusions.",
                "rationale": "why this is a semantic root rather than a formal root"
            },
            {
                "id": "NROOT_COMMON",
                "source": [0, 0, 0],
                "text": "A real common pattern/bridge claim if needed for an induction-common edge.",
                "rationale": "why this bridge is scientifically meaningful"
            }
        ],
        "final_edges": [
            {
                "source": "existing or new node id",
                "target": "existing or new node id",
                "type": "one allowed edge type",
                "supporting_reasoning_ids": [1],
                "decision": "keep|semantic_root_link",
                "rationale": "short evidence-grounded rationale"
            }
        ],
        "dropped_reasoning_ids": [
            {
                "reasoning_id": 6,
                "reason": "wrong majority / format error / insufficient support"
            }
        ],
        "root_rationale": "why all retained terminal conclusions support NROOT",
        "quality_notes": ["short notes"]
    }
    if compact:
        schema = {
            "paper_id": "string",
            "overall_decision": "single_root_curated|insufficient_evidence",
            "root": "NROOT",
            "new_nodes": [
                {
                    "id": "NROOT",
                    "source": [0, 0, 0],
                    "text": "Real paper-level scientific research proposal.",
                    "rationale": "why this is a semantic root"
                },
                {
                    "id": "NROOT_COMMON",
                    "source": [0, 0, 0],
                    "text": "Real common pattern connecting retained conclusions to NROOT.",
                    "rationale": "why this is a semantic bridge"
                }
            ],
            "root_edges": [
                {
                    "source": "terminal node id or NROOT_COMMON",
                    "target": "NROOT",
                    "type": "induction-case|induction-common",
                    "supporting_reasoning_ids": [1],
                    "rationale": "short rationale"
                }
            ],
            "root_rationale": "why the retained terminal conclusions jointly support NROOT",
            "quality_notes": ["short notes"]
        }
    return (
        "You are GPT-5.5 curating a scientific Peircean reasoning graph into a TRUE single-root graph.\n"
        "The current graph is a forest after dropping judge-rejected reasoning units. Your task is to recover a "
        "single semantic root, not to add a formal/structural aggregation node.\n\n"
        "Hard constraints:\n"
        "1. Do NOT use PEARL_STRUCTURAL_ROOT, FORMAL_ROOT, AGGREGATION_ROOT, or any root text that merely says "
        "'structural aggregation'.\n"
        "2. The root must be `NROOT` and must state a real paper-level research proposal/aim supported by the "
        "retained conclusions. Keep it at the level of motivation/aim when the retained evidence only motivates "
        "a method; do not claim that a specific method works, is superior, gives good yields, improves a catalyst, "
        "or breaks a limitation unless the retained terminal conclusions explicitly establish that result.\n"
        "3. You may add at most two new implicit nodes: `NROOT` and, if needed, `NROOT_COMMON`. Both must have "
        "source [0,0,0], real semantic text, and a rationale.\n"
        "4. Preserve all majority-correct reasoning-unit edges unless there is a clear contradiction in the supplied evidence.\n"
        "5. Drop all rejected reasoning units.\n"
        "6. The final graph must satisfy Peircean pairing for every target. For a multi-terminal synthesis, the usual "
        "safe pattern is: each retained terminal conclusion -> NROOT as `induction-case`, plus exactly one semantic "
        "`induction-common` edge into NROOT from an existing or new common-pattern node.\n"
        "7. Use only existing node ids from kept steps plus the allowed new node ids. Do not invent paper facts.\n"
        "8. Prefer cautious wording such as 'investigate', 'develop/evaluate', 'test whether', or 'motivate' when "
        "the sources are a research gap, vision, or feasibility premise rather than an experimentally established "
        "outcome.\n\n"
        "Return only one JSON object with this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Evidence packet:\n"
        f"{json.dumps(prompt_packet, ensure_ascii=False, indent=2)}"
    )


def build_semantic_repair_root_prompt(
    packet: Dict[str, Any],
    *,
    compact: bool = False,
    repair_feedback: Optional[Dict[str, Any]] = None,
) -> str:
    """Ask GPT-5.5 to repair rejected units using evaluator feedback, then root."""
    entity_contract = entity_preservation_contract(packet)
    compacted_repair_feedback = compact_repair_feedback(repair_feedback) if repair_feedback else None
    if compacted_repair_feedback:
        thresholds = compacted_repair_feedback.get("thresholds")
        if isinstance(thresholds, dict):
            try:
                frontier_min = int(thresholds.get("min_frontier_covered_entities"))
            except (TypeError, ValueError):
                frontier_min = 0
            current_min = int(entity_contract.get("minimum_final_covered_entities") or 0)
            if frontier_min > current_min:
                entity_contract["minimum_final_covered_entities"] = frontier_min
                entity_contract["coverage_goal"] = (
                    f"Preserve the current repair frontier coverage: at least {frontier_min}/"
                    f"{entity_contract.get('total_entities')} core entities must remain represented in "
                    "judge-correct retained/repaired reasoning and NROOT while fixing the failed target."
                )
    explicit_feedback_repair_id = repair_target_reasoning_id_from_feedback(repair_feedback)
    if compacted_repair_feedback:
        failed = compacted_repair_feedback
        failed_target = str(failed.get("failed_target_node", ""))
        failed_rid = explicit_feedback_repair_id
        if failed_rid is None and failed_target.startswith("R"):
            try:
                failed_rid = int(failed_target[1:])
            except ValueError:
                failed_rid = None
        relevant_ids = {failed_rid} if failed_rid is not None else set()
        for step in cascade_correct_steps(packet):
            if failed_target and failed_target in [str(source) for source in step.get("source_nodes", [])]:
                relevant_ids.add(int(step["reasoning_id"]))
        relevant_steps = [
            _compact_step_for_prompt(step, include_wrong_reasons=True)
            for step in packet["steps"]
            if int(step.get("reasoning_id", -1)) in relevant_ids
        ]
        correct_steps = [
            {
                "reasoning_id": step["reasoning_id"],
                "target_node": step["target_node"],
                "target_text": shorten(step["target_text"], 300),
                "source_nodes": step["source_nodes"],
                "source_texts": [shorten(text, 220) for text in step["source_texts"]],
                "edge_types": step["edge_types"],
            }
            for step in packet["steps"]
            if step["result"] == "correct" and not step_requires_structural_repair(step)
        ]
        rejected_steps = [
            _compact_step_for_prompt(step, include_wrong_reasons=True)
            for step in packet["steps"]
            if step["result"] != "correct" or step_requires_structural_repair(step)
        ]
        cascade_steps = [
            _compact_step_for_prompt(step, include_wrong_reasons=False)
            for step in cascade_correct_steps(packet)
        ]
        terminal_nodes = packet["deterministic_vote_kept_baseline"]["terminal_nodes"]
        downstream_by_start = downstream_nodes_by_start(packet["steps"])
        rejected_target_nodes = sorted(
            {
                str(step["target_node"])
                for step in packet["steps"]
                if step["result"] != "correct" or step_requires_structural_repair(step)
            }
        )
        allowed_repair_source_nodes = sorted(
            (
                {
                    str(node_id)
                    for step in packet["steps"]
                    for node_id in step["source_nodes"]
                }
                | {
                    str(step["target_node"])
                    for step in packet["steps"]
                    if step["result"] == "correct"
                }
            )
            - set(rejected_target_nodes)
        )
        prompt_packet = {
            "metadata": packet["metadata"],
            "evaluation_context": packet.get("evaluation_context", {}),
            "entity_preservation_contract": entity_contract,
            "original_evaluation_summary": packet["evaluation_summary"],
            "previous_repair_judge_failure": failed,
            "must_decide_reasoning_ids": sorted(relevant_ids),
            "allowed_repair_reasoning_ids": sorted(
                {
                    int(step["reasoning_id"])
                    for step in packet["steps"]
                    if (
                        step["result"] != "correct"
                        or step_requires_structural_repair(step)
                        or int(step["reasoning_id"]) in relevant_ids
                    )
                }
            ),
            "focused_steps_for_failed_target": relevant_steps,
            "kept_correct_steps": correct_steps,
            "rejected_or_invalid_steps_with_judge_feedback": rejected_steps,
            "correct_steps_that_may_need_cascade_repair_after_upstream_repair": cascade_steps,
            "current_terminal_nodes_after_kept_steps": terminal_nodes,
            "allowed_repair_source_node_ids": allowed_repair_source_nodes,
            "forbidden_rejected_target_node_ids": rejected_target_nodes,
            "forbidden_downstream_sources_by_rejected_target": {
                node_id: downstream_by_start.get(node_id, [])
                for node_id in rejected_target_nodes
            },
            "instruction": (
                "Return a complete replacement patch for the graph, but focus changes on the failed target "
                "and its dependents. Treat `failed_validation_prompt_id` as an evaluation-order label only; "
                "the graph node to repair is `failed_target_node`, and the repaired unit id must be "
                "`repair_target_reasoning_id` when that field is present. Keep already sound repairs only if "
                "they do not depend on the failed target. Use the entity preservation contract as a hard "
                "coverage guard."
            ),
        }
        schema = {
            "paper_id": "string",
            "overall_decision": "repair_and_single_root_curated|insufficient_evidence",
            "root": "NROOT",
            "new_nodes": [
                {"id": "R2", "source": [0, 0, 0], "text": "Corrected or replacement conclusion."},
                {"id": "NROOT", "source": [0, 0, 0], "text": "Real paper-level scientific research proposal."},
                {"id": "NROOT_COMMON", "source": [0, 0, 0], "text": "Real common bridge pattern."},
            ],
            "repaired_reasoning_units": [
                {
                    "reasoning_id": 2,
                    "target": "R2",
                    "edges": [
                        {"source": "existing node id or earlier R id", "target": "R2", "type": "deduction-rule"},
                        {"source": "existing node id or earlier R id", "target": "R2", "type": "deduction-case"},
                    ],
                    "repair_rationale": "must directly address judge failure",
                }
            ],
            "dropped_reasoning_ids": [{"reasoning_id": 2, "reason": "why unsupported after judge feedback"}],
            "root_edges": [],
            "root_rationale": "why final terminals support NROOT",
            "quality_notes": ["short notes"],
        }
        return (
            "You are GPT-5.5 revising a PEARL graph repair after final judge validation failed.\n"
            "Use the judge feedback as mandatory. If the failed conclusion introduced a key entity or claim "
            "not present in its selected premises, drop that reasoning_id or rewrite it to only state what the "
            "premises actually support. Do not repeat the failed conclusion.\n\n"
            "Hard constraints:\n"
            "1. Return a complete replacement patch JSON, not a diff.\n"
            "1a. Entity preservation is a hard goal: do not reduce covered core entity count below "
            "`entity_preservation_contract.minimum_final_covered_entities`. If the failed target was the only "
            "support for a listed core entity, repair or reroute that support from allowed sources before dropping it.\n"
            "2. Every rejected/relevant failed reasoning_id you touch must be either repaired as R<id> or dropped.\n"
            "2a. If previous feedback has `failed_validation_prompt_id` and `repair_target_reasoning_id`, do not "
            "confuse them: `failed_validation_prompt_id` is just the final validation prompt order, while "
            "`repair_target_reasoning_id` is the graph reasoning id to repair as `R<repair_target_reasoning_id>`.\n"
            "2b. Use only `allowed_repair_reasoning_ids` in `repaired_reasoning_units`. Every "
            "`must_decide_reasoning_ids` item must be repaired as R<id> or placed in `dropped_reasoning_ids`.\n"
            "3. Repair edges may use existing source ids or earlier successfully repaired R ids only; no future R dependencies.\n"
            "4. Every repaired unit must have exactly one valid Peircean pair, except induction may use one common plus one or more cases.\n"
            "5. NROOT must be a real semantic paper-level research proposal, not a formal aggregation root.\n"
            "6. PEARL will deterministically rebuild root edges from final terminal nodes; root_edges may be empty.\n\n"
            "Return only one JSON object with this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
            "Focused feedback packet:\n"
            f"{json.dumps(prompt_packet, ensure_ascii=False, indent=2)}"
        )

    correct_steps = [
        {
            "reasoning_id": step["reasoning_id"],
            "target_node": step["target_node"],
            "target_text": shorten(step["target_text"], 300),
            "source_nodes": step["source_nodes"],
            "source_texts": [shorten(text, 220) for text in step["source_texts"]],
            "edge_types": step["edge_types"],
        }
        for step in packet["steps"]
        if step["result"] == "correct" and not step_requires_structural_repair(step)
    ]
    rejected_steps = [
        _compact_step_for_prompt(step, include_wrong_reasons=True)
        for step in packet["steps"]
        if step["result"] != "correct" or step_requires_structural_repair(step)
    ]
    cascade_steps = [
        _compact_step_for_prompt(step, include_wrong_reasons=False)
        for step in cascade_correct_steps(packet)
    ]
    mandatory_repair_ids = {
        int(step["reasoning_id"])
        for step in packet["steps"]
        if step["result"] != "correct" or step_requires_structural_repair(step)
    }
    if explicit_feedback_repair_id is not None:
        mandatory_repair_ids.add(explicit_feedback_repair_id)
    allowed_repair_ids = mandatory_repair_ids | {
        int(step["reasoning_id"]) for step in cascade_correct_steps(packet)
    }
    terminal_nodes = packet["deterministic_vote_kept_baseline"]["terminal_nodes"]
    downstream_by_start = downstream_nodes_by_start(packet["steps"])
    rejected_target_nodes = sorted(
        {
            str(step["target_node"])
            for step in packet["steps"]
            if step["result"] != "correct" or step_requires_structural_repair(step)
        }
    )
    allowed_repair_source_nodes = sorted(
        (
            {
                str(node_id)
                for step in packet["steps"]
                for node_id in step["source_nodes"]
            }
            | {
                str(step["target_node"])
                for step in packet["steps"]
                if step["result"] == "correct"
            }
        )
        - set(rejected_target_nodes)
    )
    prompt_packet = {
        "metadata": packet["metadata"],
        "evaluation_context": packet.get("evaluation_context", {}),
        "entity_preservation_contract": entity_contract,
        "original_evaluation_summary": packet["evaluation_summary"],
        "kept_correct_steps": correct_steps,
        "rejected_or_invalid_steps_with_judge_feedback": rejected_steps,
        "correct_steps_that_may_need_cascade_repair_after_upstream_repair": cascade_steps,
        "must_decide_reasoning_ids": sorted(mandatory_repair_ids),
        "allowed_repair_reasoning_ids": sorted(allowed_repair_ids),
        "current_terminal_nodes_after_kept_steps": terminal_nodes,
        "allowed_repair_source_node_ids": allowed_repair_source_nodes,
        "forbidden_rejected_target_node_ids": rejected_target_nodes,
        "forbidden_downstream_sources_by_rejected_target": {
            node_id: downstream_by_start.get(node_id, [])
            for node_id in rejected_target_nodes
        },
    }
    if repair_feedback:
        prompt_packet["previous_repair_feedback"] = compact_repair_feedback(repair_feedback)
    schema = {
        "paper_id": "string",
        "overall_decision": "repair_and_single_root_curated|insufficient_evidence",
        "root": "NROOT",
        "new_nodes": [
            {
                "id": "R2",
                "source": [0, 0, 0],
                "text": "Corrected conclusion for rejected reasoning_id 2.",
                "repair_of_reasoning_id": 2,
                "rationale": "which judge issue this fixes"
            },
            {
                "id": "NROOT",
                "source": [0, 0, 0],
                "text": "Real paper-level scientific research proposal.",
                "rationale": "why this is a semantic root"
            },
            {
                "id": "NROOT_COMMON",
                "source": [0, 0, 0],
                "text": "Real common pattern connecting retained/repaired conclusions to NROOT.",
                "rationale": "why this is a semantic bridge"
            }
        ],
        "repaired_reasoning_units": [
            {
                "reasoning_id": 2,
                "target": "R2",
                "edges": [
                    {"source": "existing node id", "target": "R2", "type": "deduction-rule"},
                    {"source": "existing node id", "target": "R2", "type": "deduction-case"}
                ],
                "repair_rationale": "short source-grounded explanation"
            }
        ],
        "dropped_reasoning_ids": [
            {
                "reasoning_id": 3,
                "reason": "not repairable without inventing evidence"
            }
        ],
        "root_edges": [
            {
                "source": "semantic suggestion only; PEARL will rebuild exact terminal root edges locally",
                "target": "NROOT",
                "type": "induction-case|induction-common",
                "supporting_reasoning_ids": [1],
                "rationale": "short rationale"
            }
        ],
        "root_rationale": "why retained and repaired terminal conclusions jointly support NROOT",
        "quality_notes": ["short notes"]
    }
    return (
        "You are GPT-5.5 repairing and curating a scientific Peircean reasoning graph.\n"
        "Use evaluator/metric feedback as actionable repair instructions, not as a reason to blindly discard every rejected unit.\n"
        "Your job is to preserve useful scientific semantics while ensuring the final graph is clean enough for SFT.\n\n"
        + (
            "Important: a previous repair attempt failed final validation. Use `previous_repair_feedback` "
            "as mandatory feedback: repair uncovered entity support, repair or reroute the failed target more "
            "conservatively, or drop the unsupported unit. Do not repeat a failed conclusion.\n\n"
            if repair_feedback
            else ""
        )
        +
        "Hard constraints:\n"
        "1. Preserve all majority-correct reasoning-unit edges unless a clear contradiction is shown.\n"
        "1a. Preserve content grounding: the final clean graph should cover at least "
        "`entity_preservation_contract.minimum_final_covered_entities` core entities. If the original graph covered "
        "all core entities, ensure every listed core entity remains represented in a judge-correct retained or "
        "repaired reasoning unit and is reflected by NROOT. Prefer repairing a bounded entity-bearing unit over "
        "dropping the only support for that entity.\n"
        "1b. If `previous_repair_feedback.audited_frontier` is present, treat it as a rollback baseline from a "
        "fresh evaluated candidate. Preserve the frontier's coverage-bearing retained/repaired reasoning and repair "
        "only the listed noncorrect target(s), unless a replacement graph reaches both final CG=1.0 and REA=1.0. "
        "Do not solve a failed R* unit by pruning away the entities that made the audited frontier reach full CG.\n"
        "2. For a rejected reasoning unit, choose exactly one of two actions:\n"
        "   - repair: create one new implicit corrected conclusion node with id `R<reasoning_id>` and source [0,0,0], then add one valid Peircean paired unit into that repaired target.\n"
        "   - drop: if the judge feedback shows missing evidence, invented facts, unusable sources, or no bounded fix.\n"
        "2a. Use only `allowed_repair_reasoning_ids` in `repaired_reasoning_units`. Every "
        "`must_decide_reasoning_ids` item must be repaired as R<id> or placed in `dropped_reasoning_ids`.\n"
        "3. A repair must directly address the judge rationales. Do not just restate the rejected target.\n"
        "3a. If a majority-correct unit is listed in `rejected_or_invalid_steps_with_judge_feedback` with "
        "`structural_status` such as `self_loop_error`, repair it structurally as `R<reasoning_id>` or drop it; "
        "do not preserve the self-loop edge.\n"
        "4. Repair edges may use only `allowed_repair_source_node_ids` as sources, or earlier R ids that are themselves valid paired repairs. Do not use any `forbidden_rejected_target_node_ids` as a repair premise.\n"
        "5. Do not use descendants listed in `forbidden_downstream_sources_by_rejected_target` to repair that rejected target; repairing an upstream unit from its downstream conclusion creates circular support.\n"
        "6. If repairing an upstream rejected unit weakens or changes a premise used by a downstream correct step, then include that downstream step in the repair plan as `R<reasoning_id>` too, or drop it if it no longer follows. The field `correct_steps_that_may_need_cascade_repair_after_upstream_repair` lists likely cascade candidates.\n"
        "7. New nodes `R<reasoning_id>`, `NROOT`, and optional `NROOT_COMMON` must be implicit [0,0,0]. Do not alter original source-grounded nodes.\n"
        "8. Every repaired unit must have a valid Peircean pair: deduction-rule + deduction-case, abduction-phenomenon + abduction-knowledge, or at least one induction-case plus exactly one induction-common.\n"
        "8a. If a rejected unit cannot be repaired without a forbidden/downstream source, or if the remaining allowed sources do not form a full valid pair, put that reasoning_id in `dropped_reasoning_ids` instead of emitting a partial repair.\n"
        "9. Finish with a true semantic `NROOT`, not a formal/structural aggregation root. `NROOT` must state the paper-level research proposal/aim supported by retained and repaired terminal conclusions.\n"
        "10. Root synthesis must semantically cover every final terminal conclusion. PEARL will deterministically rebuild the exact root edges from final terminal retained/repaired conclusions into NROOT; your `root_edges` are only semantic suggestions.\n"
        "11. Prefer cautious wording such as 'investigate', 'develop/evaluate', 'test whether', or 'motivate' when evidence is motivational rather than a demonstrated result.\n"
        "12. Do not introduce a key entity, mechanism, method, material, condition, or claim in an `R*` conclusion unless it appears in at least one selected premise or is a minimal generic reasoning connective. If a missing key premise is needed, drop the unit instead of repairing it.\n"
        "13. For each repaired unit, use the smallest sufficient paired support: exactly one deduction-rule + one deduction-case, exactly one abduction-phenomenon + one abduction-knowledge, or one induction-common plus one or more induction-case edges. Avoid dumping all related sources into one repaired unit.\n"
        "14. Every rejected reasoning_id must appear exactly once: either in `repaired_reasoning_units` or in `dropped_reasoning_ids`. Put only rejected reasoning ids in `dropped_reasoning_ids`; if a majority-correct downstream cascade becomes invalid, repair it as `R<reasoning_id>` or explain the cascade in `quality_notes` so PEARL can verify it locally.\n\n"
        "Return only one JSON object with this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Evidence packet:\n"
        f"{json.dumps(prompt_packet, ensure_ascii=False, indent=2)}"
    )


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    for candidate in candidates:
        start = candidate.find("{")
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for idx, char in enumerate(candidate[start:], start=start):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(candidate[start : idx + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
    return None


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def decode_chat_completion_response(raw: str, *, stream: bool) -> str:
    if stream:
        parts: List[str] = []
        parsed_any = False
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                line = line[len("data:") :].strip()
            if line == "[DONE]":
                parsed_any = True
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            parsed_any = True
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
        raise ChatCompletionError(
            f"chat completion returned non-JSON response: {exc}",
            raw_response=raw,
        ) from exc
    choices = data.get("choices") or []
    if not choices:
        return raw
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    return raw


def call_chat_completion(
    prompt: str,
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int,
    max_tokens: int,
    retries: int = 0,
    retry_sleep: float = 8.0,
    transport: str = "urllib",
    stream: bool = DEFAULT_GPT55_CURATION_STREAM,
) -> str:
    extra_parse_retries = int(os.getenv("GPT55_CURATION_EXTRA_PARSE_RETRIES", "1"))
    attempts = max(1, retries + 1 + max(0, extra_parse_retries))
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        ),
        "Authorization": f"Bearer {api_key}",
    }
    raw = ""
    decoded = ""
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
            str(max(1, timeout)),
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
        last_error: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            proc = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            raw = proc.stdout or ""
            if proc.returncode == 0:
                try:
                    decoded = decode_chat_completion_response(raw, stream=stream)
                except ChatCompletionError as exc:
                    last_error = exc
                else:
                    if decoded.strip():
                        return decoded
                    last_error = RuntimeError("empty decoded chat completion response")
                if attempt >= attempts:
                    raise last_error
                if retry_sleep > 0:
                    time.sleep(retry_sleep * attempt)
                continue
            detail = (proc.stdout or proc.stderr or "").strip()
            last_error = RuntimeError(detail or f"curl exited with code {proc.returncode}")
            if attempt >= attempts:
                raise last_error
            if retry_sleep > 0:
                time.sleep(retry_sleep * attempt)
        else:
            raise RuntimeError(str(last_error) if last_error else "curl chat completion failed")
    else:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read().decode("utf-8")
                try:
                    decoded = decode_chat_completion_response(raw, stream=stream)
                except ChatCompletionError as exc:
                    last_error = exc
                else:
                    if decoded.strip():
                        return decoded
                    last_error = RuntimeError("empty decoded chat completion response")
                if attempt >= attempts:
                    raise last_error
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                status = getattr(exc, "code", 0)
                last_error = RuntimeError(detail or str(exc))
                if status not in {429, 500, 502, 503, 504} or attempt >= attempts:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_error = exc
                if attempt >= attempts:
                    raise RuntimeError(str(exc)) from exc
            if retry_sleep > 0:
                time.sleep(retry_sleep * attempt)
        else:
            raise RuntimeError(str(last_error) if last_error else "chat completion failed")
    return decoded


def spec_from_patch(packet: Dict[str, Any], patch: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    allowed_nodes = {
        node_id
        for step in packet["steps"]
        for node_id in [step["target_node"], *step["source_nodes"]]
    }
    node_by_id = {}
    for step in packet["steps"]:
        target = step["target_node"]
        node_by_id.setdefault(
            target,
            {
                "id": target,
                "source": step.get("target_source", [0, 0, 0]),
                "text": step.get("target_text", target),
            },
        )
        for node_id, source, text in zip(
            step["source_nodes"],
            step.get("source_sources", []),
            step.get("source_texts", []),
        ):
            node_by_id.setdefault(node_id, {"id": node_id, "source": source, "text": text})

    issues: List[str] = []
    final_edges = patch.get("final_edges", [])
    if not isinstance(final_edges, list):
        issues.append("patch_final_edges_not_list")
        final_edges = []

    edges: List[Dict[str, str]] = []
    for idx, edge in enumerate(final_edges):
        if not isinstance(edge, dict):
            issues.append(f"edge_{idx}_not_object")
            continue
        src = str(edge.get("source", ""))
        tgt = str(edge.get("target", ""))
        edge_type = str(edge.get("type", ""))
        if src not in allowed_nodes or tgt not in allowed_nodes:
            issues.append(f"edge_{idx}_unknown_endpoint:{src}->{tgt}")
            continue
        if edge_type not in STANDARD_EDGE_TYPES:
            issues.append(f"edge_{idx}_non_standard_type:{edge_type}")
            continue
        if src == tgt:
            issues.append(f"edge_{idx}_self_loop:{src}")
            continue
        edges.append({"source": src, "target": tgt, "type": edge_type})

    edges = dedupe_edges(edges)
    used = sorted({edge["source"] for edge in edges} | {edge["target"] for edge in edges})
    terminal_nodes = sorted({edge["target"] for edge in edges} - {edge["source"] for edge in edges})
    spec = {
        "paper_id": packet["metadata"]["paper_id"],
        "root": terminal_nodes[0] if len(terminal_nodes) == 1 else "",
        "nodes": [node_by_id[node_id] for node_id in used if node_id in node_by_id],
        "edges": edges,
        "curation": {
            "policy": "gpt55_vote_guided",
            "no_synthetic_root": True,
            "terminal_nodes": terminal_nodes,
            "patch_quality_notes": patch.get("quality_notes", []),
        },
    }
    return spec, issues


def spec_from_semantic_root_patch(packet: Dict[str, Any], patch: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    allowed_existing_nodes = {
        node_id
        for step in packet["steps"]
        for node_id in [step["target_node"], *step["source_nodes"]]
        if step["result"] == "correct"
    }
    allowed_new_nodes = {"NROOT", "NROOT_COMMON"}
    allowed_nodes = allowed_existing_nodes | allowed_new_nodes

    node_by_id: Dict[str, Dict[str, Any]] = {}
    for step in packet["steps"]:
        if step["result"] != "correct":
            continue
        target = step["target_node"]
        node_by_id.setdefault(
            target,
            {
                "id": target,
                "source": step.get("target_source", [0, 0, 0]),
                "text": step.get("target_text", target),
            },
        )
        for node_id, source, text in zip(
            step["source_nodes"],
            step.get("source_sources", []),
            step.get("source_texts", []),
        ):
            node_by_id.setdefault(node_id, {"id": node_id, "source": source, "text": text})

    issues: List[str] = []
    root = str(patch.get("root", "NROOT") or "NROOT")
    if root != "NROOT":
        issues.append(f"root_not_nroot:{root}")
        root = "NROOT"

    new_nodes = patch.get("new_nodes", [])
    if not isinstance(new_nodes, list):
        issues.append("new_nodes_not_list")
        new_nodes = []
    for idx, node in enumerate(new_nodes):
        if not isinstance(node, dict):
            issues.append(f"new_node_{idx}_not_object")
            continue
        node_id = str(node.get("id", ""))
        if node_id not in allowed_new_nodes:
            issues.append(f"new_node_{idx}_not_allowed:{node_id}")
            continue
        text = str(node.get("text", "")).strip()
        if not text:
            issues.append(f"new_node_{idx}_empty_text:{node_id}")
            continue
        lowered = text.lower()
        if any(bad in lowered for bad in ("structural aggregation", "formal root", "aggregation root")):
            issues.append(f"new_node_{idx}_looks_formal:{node_id}")
        source = node.get("source", [0, 0, 0])
        if not (isinstance(source, list) and len(source) == 3 and all(isinstance(x, int) for x in source)):
            issues.append(f"new_node_{idx}_bad_source:{node_id}")
            source = [0, 0, 0]
        node_by_id[node_id] = {"id": node_id, "source": source, "text": text}

    if "NROOT" not in node_by_id:
        issues.append("missing_semantic_nroot")
        node_by_id["NROOT"] = {
            "id": "NROOT",
            "source": [0, 0, 0],
            "text": str(patch.get("root_rationale", "")).strip()
            or "Paper-level scientific research proposal synthesized from the retained validated reasoning conclusions.",
        }

    final_edges = patch.get("final_edges")
    if (not isinstance(final_edges, list) or not final_edges) and isinstance(patch.get("root_edges"), list):
        kept_edges = [
            edge
            for step in packet["steps"]
            if step["result"] == "correct"
            for edge in step["candidate_edges"]
        ]
        final_edges = kept_edges + list(patch.get("root_edges", []))
    if not isinstance(final_edges, list):
        issues.append("patch_final_edges_not_list")
        final_edges = []

    edges: List[Dict[str, str]] = []
    for idx, edge in enumerate(final_edges):
        if not isinstance(edge, dict):
            issues.append(f"edge_{idx}_not_object")
            continue
        src = str(edge.get("source", ""))
        tgt = str(edge.get("target", ""))
        edge_type = str(edge.get("type", ""))
        if src not in allowed_nodes or tgt not in allowed_nodes:
            issues.append(f"edge_{idx}_unknown_endpoint:{src}->{tgt}")
            continue
        if src not in node_by_id:
            issues.append(f"edge_{idx}_source_node_missing:{src}")
            continue
        if tgt not in node_by_id:
            issues.append(f"edge_{idx}_target_node_missing:{tgt}")
            continue
        if edge_type not in STANDARD_EDGE_TYPES:
            issues.append(f"edge_{idx}_non_standard_type:{edge_type}")
            continue
        if src == tgt:
            issues.append(f"edge_{idx}_self_loop:{src}")
            continue
        edges.append({"source": src, "target": tgt, "type": edge_type})

    edges = dedupe_edges(edges)
    used = sorted({edge["source"] for edge in edges} | {edge["target"] for edge in edges})
    if "NROOT" not in used:
        used.append("NROOT")
    terminal_nodes = sorted({edge["target"] for edge in edges} - {edge["source"] for edge in edges})
    if terminal_nodes != ["NROOT"]:
        issues.append(f"terminal_nodes_not_single_nroot:{terminal_nodes}")

    spec = {
        "paper_id": packet["metadata"]["paper_id"],
        "root": "NROOT",
        "nodes": [node_by_id[node_id] for node_id in used if node_id in node_by_id],
        "edges": edges,
        "curation": {
            "policy": "gpt55_semantic_single_root",
            "no_synthetic_root": True,
            "terminal_nodes": terminal_nodes,
            "root_rationale": patch.get("root_rationale", ""),
            "patch_quality_notes": patch.get("quality_notes", []),
        },
    }
    return spec, issues


def spec_from_semantic_repair_root_patch(
    packet: Dict[str, Any],
    patch: Dict[str, Any],
    repair_feedback: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Build final spec from a patch that repairs rejected units and adds NROOT."""
    rejected_target_nodes = {
        str(step["target_node"])
        for step in packet["steps"]
        if step["result"] != "correct" or step_requires_structural_repair(step)
    }
    existing_premise_nodes = {
        str(node_id)
        for step in packet["steps"]
        for node_id in step["source_nodes"]
    }
    correct_target_nodes = {
        str(step["target_node"])
        for step in packet["steps"]
        if step["result"] == "correct"
    }
    allowed_repair_sources = (existing_premise_nodes | correct_target_nodes) - rejected_target_nodes
    all_existing_nodes = {
        node_id
        for step in packet["steps"]
        for node_id in [step["target_node"], *step["source_nodes"]]
    }
    rejected_reasoning_ids = {
        int(step["reasoning_id"])
        for step in packet["steps"]
        if step["result"] != "correct"
    }
    structurally_invalid_correct_reasoning_ids = {
        int(step["reasoning_id"])
        for step in packet["steps"]
        if step_requires_structural_repair(step)
    }
    explicit_feedback_repair_id = repair_target_reasoning_id_from_feedback(repair_feedback)
    explicit_feedback_repair_ids = (
        {explicit_feedback_repair_id} if explicit_feedback_repair_id is not None else set()
    )
    cascade_reasoning_ids = {
        int(step["reasoning_id"])
        for step in cascade_correct_steps(packet)
    }
    repairable_reasoning_ids = (
        rejected_reasoning_ids
        | structurally_invalid_correct_reasoning_ids
        | cascade_reasoning_ids
        | explicit_feedback_repair_ids
    )
    step_by_reasoning_id = {
        int(step["reasoning_id"]): step
        for step in packet["steps"]
    }
    downstream_by_start = downstream_nodes_by_start(packet["steps"])
    allowed_new_nodes = {"NROOT", "NROOT_COMMON"} | {
        f"R{rid}" for rid in repairable_reasoning_ids
    }
    node_by_id: Dict[str, Dict[str, Any]] = {}

    def add_step_nodes(step: Dict[str, Any]) -> None:
        target = step["target_node"]
        node_by_id.setdefault(
            target,
            {
                "id": target,
                "source": step.get("target_source", [0, 0, 0]),
                "text": step.get("target_text", target),
            },
        )
        for node_id, source, text in zip(
            step["source_nodes"],
            step.get("source_sources", []),
            step.get("source_texts", []),
        ):
            node_by_id.setdefault(node_id, {"id": node_id, "source": source, "text": text})

    def add_existing_node(node_id: str) -> None:
        """Add one existing node using source-role metadata before target metadata."""
        for step in packet["steps"]:
            for src, source, text in zip(
                step["source_nodes"],
                step.get("source_sources", []),
                step.get("source_texts", []),
            ):
                if str(src) == node_id:
                    node_by_id.setdefault(node_id, {"id": node_id, "source": source, "text": text})
                    return
        for step in packet["steps"]:
            if str(step["target_node"]) == node_id:
                node_by_id.setdefault(
                    node_id,
                    {
                        "id": node_id,
                        "source": step.get("target_source", [0, 0, 0]),
                        "text": step.get("target_text", node_id),
                    },
                )
                return

    for step in packet["steps"]:
        if step["result"] == "correct":
            add_step_nodes(step)

    issues: List[str] = []
    root = str(patch.get("root", "NROOT") or "NROOT")
    if root != "NROOT":
        issues.append(f"root_not_nroot:{root}")

    repair_targets: set[str] = set()
    attempted_repair_ids: set[int] = set()
    dropped_repair_ids: set[int] = set()
    auto_dropped_invalid_repair_reasoning_ids: set[int] = set()
    invalid_repair_records: List[Dict[str, Any]] = []
    new_nodes = patch.get("new_nodes", [])
    if not isinstance(new_nodes, list):
        issues.append("new_nodes_not_list")
        new_nodes = []
    for idx, node in enumerate(new_nodes):
        if not isinstance(node, dict):
            issues.append(f"new_node_{idx}_not_object")
            continue
        node_id = str(node.get("id", ""))
        if node_id not in allowed_new_nodes:
            if node_id.startswith("R"):
                try:
                    rid = int(node_id[1:])
                except ValueError:
                    rid = -1
                if rid in repairable_reasoning_ids:
                    # Some providers omit a repair unit but still emit the R* node.
                    # Treat it as an attempted repair target instead of making the
                    # whole patch unrecoverable; the later edge/unit checks decide
                    # whether it is usable or should be auto-dropped.
                    allowed_new_nodes.add(node_id)
                else:
                    invalid_repair_records.append(
                        {
                            "node_id": node_id,
                            "policy": "ignore_unknown_repair_node",
                            "reason": "new R* node is not repairable in this packet",
                        }
                    )
                    continue
            else:
                invalid_repair_records.append(
                    {
                        "node_id": node_id,
                        "policy": "ignore_unknown_new_node",
                        "reason": "new node is outside the PEARL repair/root schema",
                    }
                )
                continue
        text = str(node.get("text", "")).strip()
        if not text:
            issues.append(f"new_node_{idx}_empty_text:{node_id}")
            continue
        lowered = text.lower()
        if any(bad in lowered for bad in ("structural aggregation", "formal root", "aggregation root")):
            issues.append(f"new_node_{idx}_looks_formal:{node_id}")
        source = node.get("source", [0, 0, 0])
        if not (isinstance(source, list) and len(source) == 3 and all(isinstance(x, int) for x in source)):
            issues.append(f"new_node_{idx}_bad_source:{node_id}")
            source = [0, 0, 0]
        node_by_id[node_id] = {"id": node_id, "source": source, "text": text}

    repaired_units = patch.get("repaired_reasoning_units", [])
    if not isinstance(repaired_units, list):
        issues.append("repaired_reasoning_units_not_list")
        repaired_units = []

    repair_edges: List[Dict[str, str]] = []
    repair_replacements: Dict[str, str] = {}
    normalized_repair_edges: List[Dict[str, Any]] = []

    def choose_minimal_valid_pair(unit_edges: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], str]:
        by_type: Dict[str, List[Dict[str, str]]] = {}
        for edge in unit_edges:
            by_type.setdefault(edge["type"], []).append(edge)
        if by_type.get("deduction-rule") and by_type.get("deduction-case"):
            return [by_type["deduction-rule"][0], by_type["deduction-case"][0]], "deduction_pair_first_valid_roles"
        if by_type.get("abduction-phenomenon") and by_type.get("abduction-knowledge"):
            return [
                by_type["abduction-phenomenon"][0],
                by_type["abduction-knowledge"][0],
            ], "abduction_pair_first_valid_roles"
        if by_type.get("induction-common") and by_type.get("induction-case"):
            return [
                by_type["induction-case"][0],
                by_type["induction-common"][0],
            ], "induction_pair_first_case_common"
        return [], "no_valid_pair_roles"

    for unit_idx, unit in enumerate(repaired_units):
        if not isinstance(unit, dict):
            issues.append(f"repair_unit_{unit_idx}_not_object")
            continue
        try:
            rid = int(unit.get("reasoning_id"))
        except (TypeError, ValueError):
            issues.append(f"repair_unit_{unit_idx}_bad_reasoning_id")
            continue
        if rid not in repairable_reasoning_ids:
            issues.append(f"repair_unit_{unit_idx}_reasoning_id_not_repairable:{rid}")
            continue
        if rid in attempted_repair_ids:
            issues.append(f"repair_unit_{unit_idx}_duplicate_reasoning_id:{rid}")
            continue
        target = str(unit.get("target", f"R{rid}") or f"R{rid}")
        if target != f"R{rid}":
            invalid_repair_records.append(
                {
                    "reasoning_id": rid,
                    "target": target,
                    "policy": "drop_repair_unit_bad_target",
                    "reason": f"repair target must be R{rid}",
                }
            )
            auto_dropped_invalid_repair_reasoning_ids.add(rid)
            continue
        if target not in node_by_id:
            source_step = step_by_reasoning_id.get(rid)
            node_by_id[target] = {
                "id": target,
                "source": [0, 0, 0],
                "text": str(unit.get("text") or unit.get("conclusion") or (source_step or {}).get("target_text") or target).strip(),
            }
            invalid_repair_records.append(
                {
                    "reasoning_id": rid,
                    "target": target,
                    "policy": "synthesize_missing_repair_node_from_unit",
                    "reason": "repair unit referenced R* without a matching new_nodes entry",
                }
            )
        edges = unit.get("edges", [])
        if not isinstance(edges, list):
            issues.append(f"repair_unit_{unit_idx}_edges_not_list")
            continue
        unit_edge_types: List[str] = []
        unit_edges: List[Dict[str, str]] = []
        unit_invalid_reasons: List[str] = []
        for edge_idx, edge in enumerate(edges):
            if not isinstance(edge, dict):
                unit_invalid_reasons.append(f"edge_{edge_idx}_not_object")
                continue
            src = str(edge.get("source", ""))
            tgt = str(edge.get("target", ""))
            edge_type = str(edge.get("type", ""))
            if tgt != target:
                unit_invalid_reasons.append(f"edge_{edge_idx}_target_mismatch:{tgt}")
                continue
            if src.startswith("R"):
                try:
                    src_repair_id = int(src[1:])
                except ValueError:
                    unit_invalid_reasons.append(f"edge_{edge_idx}_bad_repair_source:{src}")
                    continue
                if src not in repair_targets:
                    unit_invalid_reasons.append(f"edge_{edge_idx}_unknown_or_invalid_repair_source:{src}")
                    continue
                if src_repair_id >= rid:
                    unit_invalid_reasons.append(f"edge_{edge_idx}_noncausal_repair_source:{src}")
                    continue
            elif src not in all_existing_nodes:
                unit_invalid_reasons.append(f"edge_{edge_idx}_unknown_source:{src}")
                continue
            if not src.startswith("R") and src not in allowed_repair_sources:
                unit_invalid_reasons.append(f"edge_{edge_idx}_not_allowed_repair_source:{src}")
                continue
            source_step = step_by_reasoning_id.get(rid)
            source_target = str(source_step.get("target_node", "")) if source_step else ""
            if source_target and not src.startswith("R") and src in set(downstream_by_start.get(source_target, [])):
                unit_invalid_reasons.append(f"edge_{edge_idx}_uses_downstream_source:{src}")
                continue
            if src not in node_by_id:
                add_existing_node(src)
            if src not in node_by_id:
                unit_invalid_reasons.append(f"edge_{edge_idx}_source_missing:{src}")
                continue
            if edge_type not in STANDARD_EDGE_TYPES:
                unit_invalid_reasons.append(f"edge_{edge_idx}_non_standard_type:{edge_type}")
                continue
            if src == tgt:
                unit_invalid_reasons.append(f"edge_{edge_idx}_self_loop:{src}")
                continue
            unit_edge_types.append(edge_type)
            unit_edges.append({"source": src, "target": tgt, "type": edge_type})
        selected_edges, normalize_policy = choose_minimal_valid_pair(unit_edges)
        if not selected_edges:
            auto_dropped_invalid_repair_reasoning_ids.add(rid)
            invalid_repair_records.append(
                {
                    "reasoning_id": rid,
                    "target": target,
                    "invalid_reasons": unit_invalid_reasons,
                    "remaining_edge_types": unit_edge_types,
                    "policy": "drop_invalid_repair_unit",
                }
            )
            continue
        attempted_repair_ids.add(rid)
        repair_targets.add(target)
        repair_edges.extend(selected_edges)
        if len(selected_edges) != len(unit_edges):
            normalized_repair_edges.append(
                {
                    "reasoning_id": rid,
                    "target": target,
                    "policy": normalize_policy,
                    "input_edge_count": len(unit_edges),
                    "kept_edge_count": len(selected_edges),
                    "discarded_invalid_reasons": unit_invalid_reasons,
                }
            )
        source_step = step_by_reasoning_id.get(rid)
        if source_step:
            repair_replacements[str(source_step["target_node"])] = target

    dropped_units = patch.get("dropped_reasoning_ids", [])
    if not isinstance(dropped_units, list):
        issues.append("dropped_reasoning_ids_not_list")
        dropped_units = []
    declared_dropped_cascade_reasoning_ids: set[int] = set()
    ignored_non_rejected_dropped_reasoning_ids: set[int] = set()
    for drop_idx, item in enumerate(dropped_units):
        if isinstance(item, dict):
            rid_value = item.get("reasoning_id")
        else:
            rid_value = item
        try:
            rid = int(rid_value)
        except (TypeError, ValueError):
            issues.append(f"dropped_reasoning_{drop_idx}_bad_reasoning_id")
            continue
        if (
            rid not in rejected_reasoning_ids
            and rid not in structurally_invalid_correct_reasoning_ids
            and rid not in explicit_feedback_repair_ids
        ):
            if rid in cascade_reasoning_ids:
                declared_dropped_cascade_reasoning_ids.add(rid)
            else:
                ignored_non_rejected_dropped_reasoning_ids.add(rid)
            continue
        if rid in dropped_repair_ids:
            issues.append(f"dropped_reasoning_{drop_idx}_duplicate_reasoning_id:{rid}")
            continue
        dropped_repair_ids.add(rid)

    overlap = attempted_repair_ids & dropped_repair_ids
    if overlap:
        issues.append(f"reasoning_ids_both_repaired_and_dropped:{sorted(overlap)}")
    mandatory_decision_ids = (
        rejected_reasoning_ids
        | structurally_invalid_correct_reasoning_ids
        | explicit_feedback_repair_ids
    )
    missing_decisions = (
        mandatory_decision_ids
        - attempted_repair_ids
        - dropped_repair_ids
        - auto_dropped_invalid_repair_reasoning_ids
    )
    if missing_decisions:
        # In feedback-repair mode, some providers occasionally return a focused
        # patch for the latest failed target and omit unchanged rejected units.
        # Conservatively treat omitted rejected units as dropped instead of
        # accepting partial repairs or failing the whole patch contract.
        dropped_repair_ids.update(missing_decisions)
        auto_dropped_invalid_repair_reasoning_ids.update(missing_decisions)
        invalid_repair_records.append(
            {
                "reasoning_ids": sorted(missing_decisions),
                "policy": "auto_drop_missing_repair_or_drop_decisions",
                "reason": "GPT patch omitted mandatory repair/drop decisions under feedback repair",
            }
        )

    if "NROOT" not in node_by_id:
        issues.append("missing_semantic_nroot")
        node_by_id["NROOT"] = {
            "id": "NROOT",
            "source": [0, 0, 0],
            "text": str(patch.get("root_rationale", "")).strip()
            or "Paper-level scientific research proposal synthesized from retained and repaired validated reasoning conclusions.",
        }

    # Dropping a rejected reasoning unit removes the incoming reasoning edges to
    # its target; it does not prove that the target text can never be used as a
    # leaf premise by a downstream majority-correct step. Keep those downstream
    # steps unless GPT explicitly declared them dropped. If an upstream unit was
    # repaired to R*, rewrite downstream sources below and let final judges gate
    # the revised local reasoning.
    auto_dropped_cascade_reasoning_ids: set[int] = set()

    kept_edges: List[Dict[str, str]] = []
    rewritten_edge_records: List[Dict[str, Any]] = []
    for step in packet["steps"]:
        if step["result"] != "correct":
            continue
        rid = int(step.get("reasoning_id", -1))
        if rid in attempted_repair_ids:
            continue
        if rid in structurally_invalid_correct_reasoning_ids:
            continue
        if rid in declared_dropped_cascade_reasoning_ids:
            continue
        if rid in auto_dropped_cascade_reasoning_ids:
            continue
        for edge in step["candidate_edges"]:
            src = str(edge.get("source", ""))
            tgt = str(edge.get("target", ""))
            edge_type = str(edge.get("type", ""))
            new_src = repair_replacements.get(src, src)
            new_tgt = repair_replacements.get(tgt, tgt)
            if new_tgt != tgt:
                issues.append(
                    f"kept_edge_target_would_rewrite_rejected_target:"
                    f"reasoning_id={step.get('reasoning_id')}:{tgt}->{new_tgt}"
                )
                continue
            if new_src != src:
                rewritten_edge_records.append(
                    {
                        "reasoning_id": step.get("reasoning_id"),
                        "source": src,
                        "rewritten_source": new_src,
                        "target": tgt,
                        "type": edge_type,
                    }
                )
            kept_edges.append({"source": new_src, "target": new_tgt, "type": edge_type})
    pre_root_edges = dedupe_edges(kept_edges + repair_edges)
    pre_root_terminal_nodes = sorted(
        {edge["target"] for edge in pre_root_edges} - {edge["source"] for edge in pre_root_edges}
    )
    if not pre_root_terminal_nodes:
        # If every repaired/rejected path was dropped, preserve a conservative
        # terminal from retained majority-correct steps instead of returning a
        # structurally terminal-less graph. This still has to pass final judges
        # and metrics, but it avoids patch-contract death before evaluation.
        retained_targets = [
            str(step.get("target_node"))
            for step in packet["steps"]
            if step.get("result") == "correct"
            and not step_requires_structural_repair(step)
            and int(step.get("reasoning_id", -1)) not in auto_dropped_cascade_reasoning_ids
        ]
        retained_sources = {edge["source"] for edge in kept_edges}
        fallback_terminals = sorted(
            target for target in retained_targets if target and target not in retained_sources
        )
        if not fallback_terminals:
            baseline_terminals = packet["deterministic_vote_kept_baseline"].get("terminal_nodes", [])
            auto_dropped_targets = {
                str(step.get("target_node", ""))
                for step in packet["steps"]
                if int(step.get("reasoning_id", -1)) in auto_dropped_cascade_reasoning_ids
            }
            fallback_terminals = sorted(
                str(node_id)
                for node_id in baseline_terminals
                if str(node_id) and str(node_id) not in auto_dropped_targets
            )
        if fallback_terminals:
            pre_root_terminal_nodes = fallback_terminals
            invalid_repair_records.append(
                {
                    "policy": "fallback_to_retained_terminal_nodes",
                    "terminal_nodes": pre_root_terminal_nodes,
                    "reason": "GPT repair patch dropped all pre-root terminals",
                }
            )
        else:
            issues.append("no_pre_root_terminal_nodes")

    if "NROOT_COMMON" not in node_by_id:
        node_by_id["NROOT_COMMON"] = {
            "id": "NROOT_COMMON",
            "source": [0, 0, 0],
            "text": (
                "The retained and repaired terminal conclusions jointly motivate the "
                "paper-level research proposal."
            ),
        }
    checked_root_edges: List[Dict[str, str]] = [
        {"source": node_id, "target": "NROOT", "type": "induction-case"}
        for node_id in pre_root_terminal_nodes
        if node_id != "NROOT"
    ]
    checked_root_edges.append(
        {"source": "NROOT_COMMON", "target": "NROOT", "type": "induction-common"}
    )

    edges = dedupe_edges(kept_edges + repair_edges + checked_root_edges)
    used = sorted({edge["source"] for edge in edges} | {edge["target"] for edge in edges})
    if "NROOT" not in used:
        used.append("NROOT")
    terminal_nodes = sorted({edge["target"] for edge in edges} - {edge["source"] for edge in edges})
    if terminal_nodes != ["NROOT"]:
        issues.append(f"terminal_nodes_not_single_nroot:{terminal_nodes}")

    spec = {
        "paper_id": packet["metadata"]["paper_id"],
        "root": "NROOT",
        "nodes": [node_by_id[node_id] for node_id in used if node_id in node_by_id],
        "edges": edges,
        "curation": {
            "policy": "gpt55_eval_feedback_repair_semantic_single_root",
            "no_synthetic_root": True,
            "terminal_nodes": terminal_nodes,
            "root_rationale": patch.get("root_rationale", ""),
            "patch_quality_notes": patch.get("quality_notes", []),
            "repaired_reasoning_ids": sorted(
                int(unit.get("reasoning_id"))
                for unit in repaired_units
                if isinstance(unit, dict)
                and str(unit.get("target", "")).startswith("R")
                and str(unit.get("target", "")) in repair_targets
            ),
            "repair_replacements": repair_replacements,
            "rewritten_edges_using_repaired_nodes": rewritten_edge_records,
            "normalized_repair_edges": normalized_repair_edges,
            "auto_dropped_cascade_reasoning_ids": sorted(auto_dropped_cascade_reasoning_ids),
            "auto_dropped_invalid_repair_reasoning_ids": sorted(
                auto_dropped_invalid_repair_reasoning_ids
            ),
            "invalid_repair_records": invalid_repair_records,
            "gpt_declared_dropped_rejected_reasoning_ids": sorted(dropped_repair_ids),
            "gpt_declared_dropped_cascade_reasoning_ids": sorted(declared_dropped_cascade_reasoning_ids),
            "ignored_non_rejected_dropped_reasoning_ids": sorted(ignored_non_rejected_dropped_reasoning_ids),
            "pre_root_terminal_nodes": pre_root_terminal_nodes,
            "root_edges_source": "deterministic_from_pre_root_terminal_nodes",
            "gpt_suggested_root_edges": patch.get("root_edges", []),
            "dropped_reasoning_ids": sorted(
                dropped_repair_ids
                | declared_dropped_cascade_reasoning_ids
                | auto_dropped_invalid_repair_reasoning_ids
                | auto_dropped_cascade_reasoning_ids
            ),
        },
    }
    return spec, issues


def write_graph_outputs(prefix: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    write_json(prefix.with_suffix(".json"), spec)
    dot = graph_spec_to_dot(spec)
    prefix.with_suffix(".dot").write_text(dot, encoding="utf-8")
    parseable, strict_ok, strict_valid, strict_issues = assess_dot_quality(dot, mode="strict")
    _, teacher_ok, teacher_valid, teacher_issues = assess_dot_quality(dot, mode="teacher_compatible")
    return {
        "json": str(prefix.with_suffix(".json")),
        "dot": str(prefix.with_suffix(".dot")),
        "node_count": len(spec.get("nodes", [])),
        "edge_count": len(spec.get("edges", [])),
        "terminal_nodes": spec.get("curation", {}).get("terminal_nodes", []),
        "parseable": parseable,
        "strict_valid": strict_valid,
        "strict_ok": strict_ok,
        "strict_issues": strict_issues[:20],
        "teacher_compatible_valid": teacher_valid,
        "teacher_compatible_ok": teacher_ok,
        "teacher_compatible_issues": teacher_issues[:20],
    }


def write_readme(out_dir: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# GPT-5.5 Vote-guided Curation Pilot",
        "",
        "This directory is an isolated pilot. It does not overwrite teacher-pool or PEARL release artifacts.",
        "",
        "## Inputs",
        "",
        f"- Run dir: `{report['run_dir']}`",
        f"- Eval dir: `{report['eval_dir']}`",
        "",
        "## Deterministic Baseline",
        "",
        f"- Kept majority-correct reasoning units: {report['deterministic']['kept_reasoning_count']}",
        f"- Dropped non-correct/error units: {report['deterministic']['dropped_reasoning_count']}",
        f"- Output graph: `{Path(report['deterministic']['dot']).name}`",
        f"- Nodes/edges: {report['deterministic']['node_count']}/{report['deterministic']['edge_count']}",
        f"- Terminal nodes: `{report['deterministic']['terminal_nodes']}`",
        "",
        "The baseline intentionally keeps a forest when multiple terminal conclusions remain; no synthetic root is added.",
    ]
    if report.get("gpt55"):
        lines.extend(
            [
                "",
                "## GPT-5.5 Patch",
                "",
                f"- Model: `{report['gpt55']['model']}`",
                f"- Patch parsed: `{report['gpt55']['patch_parsed']}`",
                f"- Output graph: `{Path(report['gpt55'].get('dot', '')).name if report['gpt55'].get('dot') else ''}`",
                f"- Patch issues: `{report['gpt55'].get('patch_issues', [])}`",
            ]
        )
    if report.get("gpt55_semantic_root"):
        lines.extend(
            [
                "",
                "## GPT-5.5 Semantic Single Root",
                "",
                f"- Model: `{report['gpt55_semantic_root']['model']}`",
                f"- Patch parsed: `{report['gpt55_semantic_root']['patch_parsed']}`",
                f"- Output graph: `{Path(report['gpt55_semantic_root'].get('dot', '')).name if report['gpt55_semantic_root'].get('dot') else ''}`",
                f"- Strict valid: `{report['gpt55_semantic_root'].get('strict_valid', '')}`",
                f"- Patch issues: `{report['gpt55_semantic_root'].get('patch_issues', [])}`",
            ]
        )
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--eval-dir", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument(
        "--model",
        default=first_env(
            "GPT55_CURATION_MODEL",
            "OPENAI_MODEL",
            default=DEFAULT_GPT55_CURATION_MODEL,
        ),
    )
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--fallback-api-key", default="")
    parser.add_argument("--fallback-base-url", default="")
    parser.add_argument("--fallback-api-key-2", default="")
    parser.add_argument("--fallback-base-url-2", default="")
    parser.add_argument(
        "--gpt-transport",
        choices=["curl", "urllib"],
        default=first_env(
            "GPT55_CURATION_TRANSPORT",
            "OPENAI_TRANSPORT",
            default=DEFAULT_GPT55_CURATION_TRANSPORT,
        ),
        help="HTTP transport for GPT-5.5 curation calls. curl is more robust with some proxy providers.",
    )
    parser.add_argument(
        "--gpt-stream",
        action=argparse.BooleanOptionalAction,
        default=env_bool("GPT55_CURATION_STREAM", DEFAULT_GPT55_CURATION_STREAM),
        help="Use OpenAI-compatible streaming responses for GPT-5.5 curation calls.",
    )
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-tokens", type=int, default=32000)
    parser.add_argument(
        "--gpt-retries",
        type=int,
        default=2,
        help="Retries inside the GPT-5.5 patch call for transient provider/network failures.",
    )
    parser.add_argument(
        "--gpt-retry-sleep",
        type=float,
        default=8.0,
        help="Base sleep seconds between GPT-5.5 retry attempts.",
    )
    parser.add_argument("--call-gpt", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--semantic-root",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Ask GPT-5.5 to recover a real semantic NROOT instead of preserving a forest.",
    )
    parser.add_argument(
        "--compact-root-prompt",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="For --semantic-root, ask only for NROOT/new bridge/root edges and merge kept edges locally.",
    )
    parser.add_argument(
        "--repair-rejected",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use evaluator feedback to repair rejected reasoning units before adding semantic NROOT.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(Path(args.env_file))

    run_dir = Path(args.run_dir)
    eval_dir = Path(args.eval_dir) if args.eval_dir else latest_eval_dir(run_dir)
    ids = infer_ids(run_dir)
    out_dir = Path(args.out_dir) if args.out_dir else (
        DEFAULT_OUT_ROOT / ids["model"] / ids["paper_id"] / ids["run_id"]
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    packet, deterministic_spec = build_packet(run_dir, eval_dir)
    write_json(out_dir / "curation_packet.json", packet)
    repair_feedback_path = out_dir / "repair_feedback.json"
    repair_feedback = load_json(repair_feedback_path, None)
    if not isinstance(repair_feedback, dict):
        repair_feedback = None

    if args.semantic_root and args.repair_rejected:
        prompt = build_semantic_repair_root_prompt(
            packet,
            compact=args.compact_root_prompt,
            repair_feedback=repair_feedback,
        )
    elif args.semantic_root:
        prompt = build_semantic_root_prompt(packet, compact=args.compact_root_prompt)
    else:
        prompt = build_prompt(packet)
    prompt_path = out_dir / (
        "gpt55_semantic_root_prompt.txt" if args.semantic_root else "gpt55_curation_prompt.txt"
    )
    prompt_path.write_text(prompt, encoding="utf-8")

    deterministic_info = write_graph_outputs(out_dir / "deterministic_vote_kept_graph", deterministic_spec)
    deterministic_info["kept_reasoning_count"] = len(
        packet["deterministic_vote_kept_baseline"]["kept_reasoning_ids"]
    )
    deterministic_info["dropped_reasoning_count"] = len(
        packet["deterministic_vote_kept_baseline"]["dropped_reasoning_ids"]
    )

    report: Dict[str, Any] = {
        "run_dir": str(run_dir),
        "eval_dir": str(eval_dir),
        "out_dir": str(out_dir),
        "packet": str(out_dir / "curation_packet.json"),
        "prompt": str(prompt_path),
        "original_evaluation_summary": packet.get("evaluation_summary", {}),
        "deterministic": deterministic_info,
    }

    if args.call_gpt:
        report_key = (
            "gpt55_semantic_repair_root"
            if args.semantic_root and args.repair_rejected
            else "gpt55_semantic_root"
            if args.semantic_root
            else "gpt55"
        )
        patch_name = (
            "gpt55_semantic_root_patch.json"
            if args.semantic_root
            else "gpt55_curation_patch.json"
        )
        graph_prefix = (
            out_dir / "gpt55_semantic_root_graph"
            if args.semantic_root
            else out_dir / "gpt55_curated_graph"
        )
        existing_patch_path = out_dir / patch_name
        if existing_patch_path.exists() and repair_feedback is None:
            patch = load_json(existing_patch_path, {})
            if isinstance(patch, dict) and patch:
                if args.semantic_root and args.repair_rejected:
                    gpt_spec, patch_issues = spec_from_semantic_repair_root_patch(packet, patch, repair_feedback)
                elif args.semantic_root:
                    gpt_spec, patch_issues = spec_from_semantic_root_patch(packet, patch)
                else:
                    gpt_spec, patch_issues = spec_from_patch(packet, patch)
                gpt_info = write_graph_outputs(graph_prefix, gpt_spec)
                report[report_key] = {
                    "model": args.model,
                    "called": False,
                    "reused_existing_patch": True,
                    "patch_parsed": True,
                    "patch": str(existing_patch_path),
                    "patch_issues": patch_issues,
                    **gpt_info,
                }
                write_json(out_dir / "report.json", report)
                write_readme(out_dir, report)
                return 0
        if repair_feedback is None:
            raw_candidates: List[Path] = []
            if args.semantic_root:
                raw_candidates.extend(
                    [
                        out_dir / "gpt55_semantic_root_raw_response.txt",
                        out_dir / "gpt55_semantic_root_raw_response_primary.txt",
                    ]
                )
                raw_candidates.extend(sorted(out_dir.glob("gpt55_semantic_root_raw_response_*.txt")))
            else:
                raw_candidates.extend(
                    [
                        out_dir / "gpt55_curation_raw_response.txt",
                        out_dir / "gpt55_curation_raw_response_primary.txt",
                    ]
                )
                raw_candidates.extend(sorted(out_dir.glob("gpt55_curation_raw_response_*.txt")))
            seen_raw_paths: set[Path] = set()
            soft_patch_issue_prefixes = (
                "root_not_nroot:",
                "kept_edge_target_would_rewrite_rejected_target:",
            )
            for raw_path in raw_candidates:
                if raw_path in seen_raw_paths or not raw_path.exists():
                    continue
                seen_raw_paths.add(raw_path)
                raw = raw_path.read_text(encoding="utf-8", errors="replace")
                patch = extract_json_object(raw)
                if patch is None:
                    continue
                if args.semantic_root and args.repair_rejected:
                    gpt_spec, patch_issues = spec_from_semantic_repair_root_patch(packet, patch, repair_feedback)
                elif args.semantic_root:
                    gpt_spec, patch_issues = spec_from_semantic_root_patch(packet, patch)
                else:
                    gpt_spec, patch_issues = spec_from_patch(packet, patch)
                hard_patch_issues = [
                    issue for issue in patch_issues
                    if not str(issue).startswith(soft_patch_issue_prefixes)
                ]
                if hard_patch_issues:
                    continue
                canonical_raw_path = out_dir / (
                    "gpt55_semantic_root_raw_response.txt"
                    if args.semantic_root
                    else "gpt55_curation_raw_response.txt"
                )
                if raw_path != canonical_raw_path:
                    canonical_raw_path.write_text(raw, encoding="utf-8")
                write_json(out_dir / patch_name, patch)
                gpt_info = write_graph_outputs(graph_prefix, gpt_spec)
                report[report_key] = {
                    "model": args.model,
                    "called": False,
                    "reused_existing_raw_response": True,
                    "raw_response_path": str(raw_path),
                    "patch_parsed": True,
                    "patch": str(out_dir / patch_name),
                    "patch_issues": patch_issues,
                    **gpt_info,
                }
                write_json(out_dir / "report.json", report)
                write_readme(out_dir, report)
                return 0
        # This module is explicitly routed to GPT-5.5 OpenAI-compatible
        # curation endpoints. Judge routes stay separate in evaluator.py.
        endpoints = gpt55_endpoint_configs(args)
        if not endpoints:
            report[report_key] = {
                "model": args.model,
                "called": False,
                "error": "missing API key",
            }
        else:
            raw = ""
            endpoint_errors: List[Dict[str, str]] = []
            for endpoint in endpoints:
                canonical_raw_name = (
                    "gpt55_semantic_root_raw_response.txt"
                    if args.semantic_root
                    else "gpt55_curation_raw_response.txt"
                )
                stream_modes = [args.gpt_stream]
                if args.gpt_stream:
                    stream_modes.append(False)
                for stream_mode in stream_modes:
                    suffix = endpoint["name"]
                    if stream_mode != args.gpt_stream:
                        suffix = f"{endpoint['name']}_nonstream_retry"
                    raw_name = (
                        f"gpt55_semantic_root_raw_response_{suffix}.txt"
                        if args.semantic_root
                        else f"gpt55_curation_raw_response_{suffix}.txt"
                    )
                    try:
                        raw = call_chat_completion(
                            prompt,
                            model=args.model,
                            api_key=endpoint["api_key"],
                            base_url=endpoint["base_url"],
                            timeout=args.timeout,
                            max_tokens=args.max_tokens,
                            retries=args.gpt_retries,
                            retry_sleep=args.gpt_retry_sleep,
                            transport=args.gpt_transport,
                            stream=stream_mode,
                        )
                    except ChatCompletionError as exc:
                        raw_http_name = (
                            f"gpt55_semantic_root_raw_http_response_{suffix}.txt"
                            if args.semantic_root
                            else f"gpt55_curation_raw_http_response_{suffix}.txt"
                        )
                        raw_response_path = ""
                        if exc.raw_response:
                            raw_response_path = str(out_dir / raw_http_name)
                            (out_dir / raw_http_name).write_text(
                                exc.raw_response,
                                encoding="utf-8",
                            )
                        endpoint_errors.append(
                            {
                                "endpoint": endpoint["name"],
                                "base_url": endpoint["base_url"],
                                "error": str(exc),
                                "raw_response_path": raw_response_path,
                                "raw_response_preview": shorten(exc.raw_response, 500),
                                "transport": args.gpt_transport,
                                "stream": stream_mode,
                            }
                        )
                        continue
                    except Exception as exc:  # noqa: BLE001
                        raw_response_path = ""
                        if raw:
                            raw_response_path = str(out_dir / raw_name)
                            (out_dir / raw_name).write_text(raw, encoding="utf-8")
                        endpoint_errors.append(
                            {
                                "endpoint": endpoint["name"],
                                "base_url": endpoint["base_url"],
                                "error": str(exc),
                                "raw_response_path": raw_response_path,
                                "transport": args.gpt_transport,
                                "stream": stream_mode,
                            }
                        )
                        continue
                    (out_dir / raw_name).write_text(raw, encoding="utf-8")
                    patch = extract_json_object(raw)
                    if patch is None:
                        endpoint_errors.append(
                            {
                                "endpoint": endpoint["name"],
                                "base_url": endpoint["base_url"],
                                "error": "no JSON object parsed from response",
                                "raw_response_path": str(out_dir / raw_name),
                                "raw_response_preview": shorten(raw, 500),
                                "transport": args.gpt_transport,
                                "stream": stream_mode,
                            }
                        )
                        continue
                    if args.semantic_root and args.repair_rejected:
                        gpt_spec, patch_issues = spec_from_semantic_repair_root_patch(packet, patch, repair_feedback)
                    elif args.semantic_root:
                        gpt_spec, patch_issues = spec_from_semantic_root_patch(packet, patch)
                    else:
                        gpt_spec, patch_issues = spec_from_patch(packet, patch)
                    soft_patch_issue_prefixes = (
                        "root_not_nroot:",
                        "kept_edge_target_would_rewrite_rejected_target:",
                    )
                    hard_patch_issues = [
                        issue for issue in patch_issues
                        if not str(issue).startswith(soft_patch_issue_prefixes)
                    ]
                    if hard_patch_issues:
                        endpoint_errors.append(
                            {
                                "endpoint": endpoint["name"],
                                "base_url": endpoint["base_url"],
                                "error": "patch contract issues",
                                "patch_issues": patch_issues[:30],
                                "raw_response_path": str(out_dir / raw_name),
                                "raw_response_preview": shorten(raw, 500),
                                "transport": args.gpt_transport,
                                "stream": stream_mode,
                            }
                        )
                        continue
                    (out_dir / canonical_raw_name).write_text(raw, encoding="utf-8")
                    write_json(out_dir / patch_name, patch)
                    gpt_info = write_graph_outputs(graph_prefix, gpt_spec)
                    report[report_key] = {
                        "model": args.model,
                        "called": True,
                        "patch_parsed": True,
                        "endpoint": endpoint["name"],
                        "base_url": endpoint["base_url"],
                        "transport": args.gpt_transport,
                        "stream": stream_mode,
                        "endpoint_errors": endpoint_errors,
                        "patch": str(out_dir / patch_name),
                        "patch_issues": patch_issues,
                        **gpt_info,
                    }
                    break
                if report.get(report_key, {}).get("patch_parsed"):
                    break
            else:
                report[report_key] = {
                    "model": args.model,
                    "called": True,
                    "patch_parsed": False,
                    "error": endpoint_errors[-1]["error"] if endpoint_errors else "all GPT-5.5 endpoints failed",
                    "transport": args.gpt_transport,
                    "stream": args.gpt_stream,
                    "endpoint_errors": endpoint_errors,
                }

    write_json(out_dir / "report.json", report)
    write_readme(out_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
