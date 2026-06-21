#!/usr/bin/env python3
"""Run or materialize evidence-bound micro-edit patches.

The runner accepts packet rows produced by
`build_evidence_bound_micro_edit_packets.py`. It can either call an
OpenAI-compatible model to produce a patch, or materialize an existing
`response.json`. The output is a complete candidate graph_spec and DOT only
when the patch obeys the micro-edit scope and passes local preflight.

This script does not merge candidates into 350 accounting.
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
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


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
DEFAULT_PACKET_INDEX = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "micro_edit_packets"
    / "round01_from_best_r01"
    / "MICRO_EDIT_PACKET_INDEX.csv"
)
DEFAULT_ATTEMPTS_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "micro_edit_packets"
    / "round01_from_best_r01"
    / "model_attempts"
)
DEFAULT_STAGING_ROOT = (
    RESIDUAL_ROOT
    / "evidence_bound_regeneration"
    / "micro_edit_packets"
    / "round01_from_best_r01"
    / "strict_gate_staging"
)

FRAMEWORK_DIR = PROJECT_ROOT / "code" / "framework"
RESTRUCTURED_DIR = PROJECT_ROOT / "operation_records" / "restructured"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(FRAMEWORK_DIR))
sys.path.insert(0, str(RESTRUCTURED_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from graph_spec_to_dot import graph_spec_to_dot  # type: ignore  # noqa: E402
from graph_spec_validator import validate_graph_spec  # type: ignore  # noqa: E402
from run_evidence_bound_graph_spec_regeneration import (  # type: ignore  # noqa: E402
    isolated_nodes_from_preflight,
    preflight_graph_spec,
)


STANDARD_EDGE_TYPES = {
    "deduction-rule",
    "deduction-case",
    "induction-case",
    "induction-common",
    "abduction-phenomenon",
    "abduction-knowledge",
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


def extract_json_object(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
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
                    parsed = json.loads(candidate[start : idx + 1])
                    if isinstance(parsed, dict):
                        return parsed
                    break
    raise ValueError("no JSON object found in response")


def read_attempt_payload(path: Path) -> Dict[str, Any]:
    raw = read_json(path, None)
    if isinstance(raw, dict):
        if isinstance(raw.get("response_json"), dict):
            return raw["response_json"]
        if isinstance(raw.get("patch"), dict):
            return raw["patch"]
        for key in ("response_text", "raw_response", "content"):
            if isinstance(raw.get(key), str):
                return extract_json_object(raw[key])
        if any(key in raw for key in ("replace_nodes", "replace_incoming_edges", "optional_replace_outgoing_edges")):
            return raw
    return extract_json_object(path.read_text(encoding="utf-8"))


def decode_chat_completion(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    choices = data.get("choices") or []
    if choices:
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
) -> Dict[str, Any]:
    if not api_key:
        raise RuntimeError("missing API key")
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "stream": False,
    }
    if max_tokens > 0:
        payload["max_tokens"] = max_tokens
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
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
            "Content-Type: application/json",
            "-H",
            "Accept: application/json",
            "-H",
            "User-Agent: curl/8.7.1",
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
            raise RuntimeError((proc.stdout or proc.stderr or f"curl exited with {proc.returncode}").strip())
    else:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(detail or str(exc)) from exc
    content = decode_chat_completion(raw)
    return {"raw_response": raw, "content": content, "response_json": extract_json_object(content)}


def attempt_dir_for(row: Dict[str, str], model: str) -> Path:
    return Path(f"{int(row.get('priority') or 999999):03d}_{safe_slug(row.get('paper_spec') or 'paper')}__{safe_slug(row.get('target') or 'target')}") / safe_slug(model)


def default_attempt_file(row: Dict[str, str], model: str, attempts_root: Path) -> Path:
    return attempts_root / attempt_dir_for(row, model) / "response.json"


def load_existing_attempt(row: Dict[str, str], model: str, attempts_root: Path) -> Optional[Path]:
    base = attempts_root / attempt_dir_for(row, model)
    for name in ("response.json", "patch.json", "response.txt"):
        path = base / name
        if path.exists():
            return path
    return None


def execute_attempt(
    row: Dict[str, str],
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
) -> Path:
    out = default_attempt_file(row, model, attempts_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt = resolve_path(row["generation_prompt"]).read_text(encoding="utf-8")
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
            )
            payload.update(
                {
                    "paper_spec": row.get("paper_spec", ""),
                    "target": row.get("target", ""),
                    "model": model,
                    "attempt": attempt,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "transport": transport,
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


def edge_pairing_valid(edges: List[Dict[str, str]]) -> tuple[bool, str]:
    labels = [str(edge.get("type") or "") for edge in edges]
    if any(label not in STANDARD_EDGE_TYPES for label in labels):
        return False, "non_standard_edge_type"
    label_set = set(labels)
    if label_set <= {"deduction-rule", "deduction-case"}:
        return (labels.count("deduction-rule") == 1 and labels.count("deduction-case") == 1), "deduction"
    if label_set <= {"abduction-phenomenon", "abduction-knowledge"}:
        return (labels.count("abduction-phenomenon") == 1 and labels.count("abduction-knowledge") == 1), "abduction"
    if label_set <= {"induction-common", "induction-case"}:
        return (labels.count("induction-common") == 1 and labels.count("induction-case") >= 1), "induction"
    return False, "mixed_reasoning_family"


def validate_patch(patch: Dict[str, Any], spec: Dict[str, Any], target: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    node_ids = {str(node.get("id")) for node in spec.get("nodes") or [] if isinstance(node, dict)}
    replace_nodes = patch.get("replace_nodes")
    incoming = patch.get("replace_incoming_edges")
    outgoing = patch.get("optional_replace_outgoing_edges", [])
    if not isinstance(replace_nodes, list) or len(replace_nodes) != 1:
        issues.append({"type": "replace_nodes_must_have_exactly_one_target"})
    else:
        node = replace_nodes[0]
        if not isinstance(node, dict) or node.get("id") != target:
            issues.append({"type": "replace_node_id_must_equal_target", "target": target})
        if node.get("source") != [0, 0, 0]:
            issues.append({"type": "target_source_must_be_bridge_tuple", "target": target})
        if not isinstance(node.get("text"), str) or not node.get("text", "").strip():
            issues.append({"type": "target_text_missing", "target": target})

    if not isinstance(incoming, list) or not incoming:
        issues.append({"type": "replace_incoming_edges_missing"})
    else:
        for edge in incoming:
            if not isinstance(edge, dict):
                issues.append({"type": "incoming_edge_not_object"})
                continue
            if edge.get("target") != target:
                issues.append({"type": "incoming_edge_target_must_be_patch_target", "edge": edge})
            if edge.get("source") not in node_ids:
                issues.append({"type": "incoming_edge_source_unknown", "edge": edge})
            if edge.get("source") == target:
                issues.append({"type": "incoming_edge_self_loop", "edge": edge})
        ok, family = edge_pairing_valid(incoming)
        if not ok:
            issues.append({"type": "incoming_edges_not_legal_reasoning_unit", "family": family, "edges": incoming})

    if outgoing is None:
        outgoing = []
    if not isinstance(outgoing, list):
        issues.append({"type": "optional_replace_outgoing_edges_must_be_list"})
    else:
        for edge in outgoing:
            if not isinstance(edge, dict):
                issues.append({"type": "outgoing_edge_not_object"})
                continue
            if edge.get("source") != target:
                issues.append({"type": "outgoing_edge_source_must_be_patch_target", "edge": edge})
            if edge.get("target") not in node_ids:
                issues.append({"type": "outgoing_edge_target_unknown", "edge": edge})
            if edge.get("type") not in STANDARD_EDGE_TYPES:
                issues.append({"type": "outgoing_edge_type_non_standard", "edge": edge})
    return issues


def merge_patch(spec: Dict[str, Any], patch: Dict[str, Any], target: str) -> Dict[str, Any]:
    replace_node = patch["replace_nodes"][0]
    replacement_incoming = [
        {"source": str(edge["source"]), "target": target, "type": str(edge["type"])}
        for edge in patch["replace_incoming_edges"]
    ]
    replacement_outgoing = patch.get("optional_replace_outgoing_edges") or []
    replacement_outgoing = [
        {"source": target, "target": str(edge["target"]), "type": str(edge["type"])}
        for edge in replacement_outgoing
    ]
    out = dict(spec)
    out["nodes"] = [
        {
            "id": target,
            "source": replace_node.get("source"),
            "text": replace_node.get("text"),
        }
        if isinstance(node, dict) and node.get("id") == target
        else node
        for node in spec.get("nodes") or []
    ]
    edges = [
        edge
        for edge in spec.get("edges") or []
        if not (isinstance(edge, dict) and edge.get("target") == target)
    ]
    if replacement_outgoing:
        edges = [edge for edge in edges if not (isinstance(edge, dict) and edge.get("source") == target)]
    edges.extend(replacement_incoming)
    edges.extend(replacement_outgoing)
    out["edges"] = edges
    return out


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


def incoming_sources_for_target(spec: Dict[str, Any], target: str) -> set[str]:
    return {
        str(edge.get("source") or "")
        for edge in spec.get("edges") or []
        if isinstance(edge, dict) and str(edge.get("target") or "") == target and str(edge.get("source") or "")
    }


def patch_incoming_sources(patch: Dict[str, Any]) -> set[str]:
    return {
        str(edge.get("source") or "")
        for edge in patch.get("replace_incoming_edges") or []
        if isinstance(edge, dict) and str(edge.get("source") or "")
    }


def apply_patch_orphan_evidence_cleanup(
    *,
    raw_spec: Dict[str, Any],
    source_spec: Dict[str, Any],
    patch: Dict[str, Any],
    target: str,
    packet: Dict[str, Any],
    raw_preflight: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """Drop only evidence nodes orphaned by this micro-edit.

    Replacing the incoming premises of one reasoning unit can leave a previously
    used evidence node with no incident edges. This cleanup is allowed only for
    evidence nodes that were removed from the target's incoming edge set by the
    current patch and only if the cleaned graph then passes the full local
    preflight gate.
    """
    if raw_preflight.get("passed_local_preflight") is True:
        return raw_spec, raw_preflight, []

    old_sources = incoming_sources_for_target(source_spec, target)
    new_sources = patch_incoming_sources(patch)
    removed_sources = old_sources - new_sources
    isolated = set(isolated_nodes_from_preflight(raw_preflight))
    used_after_patch = incident_node_ids(raw_spec)
    removable = sorted(
        node_id
        for node_id in isolated & removed_sources
        if node_id.startswith("E") and node_id not in used_after_patch
    )
    if not removable:
        return raw_spec, raw_preflight, []

    cleaned = dict(raw_spec)
    removable_set = set(removable)
    cleaned["nodes"] = [
        node
        for node in raw_spec.get("nodes") or []
        if not (isinstance(node, dict) and str(node.get("id") or "") in removable_set)
    ]
    cleaned_preflight = preflight_graph_spec(cleaned, packet)
    if cleaned_preflight.get("passed_local_preflight") is True:
        return cleaned, cleaned_preflight, [
            {
                "action": "drop_patch_orphan_evidence_nodes",
                "node_ids": removable,
                "target": target,
                "reason": "nodes were removed from the patched target's incoming premises and became isolated; cleaned graph passes full local preflight",
            }
        ]

    return raw_spec, raw_preflight, [
        {
            "action": "drop_patch_orphan_evidence_nodes_rejected",
            "node_ids": removable,
            "target": target,
            "reason": "cleaned graph did not pass full local preflight",
            "post_cleanup_stranded_nodes": cleaned_preflight.get("connectivity", {}).get("stranded_nodes"),
            "post_cleanup_coverage": cleaned_preflight.get("entity_coverage", {}).get("coverage_rate_local"),
        }
    ]


def materialize_attempt(
    row: Dict[str, str],
    attempt_path: Path,
    *,
    model: str,
    attempts_root: Path,
    staging_root: Path,
    copy_staging: bool,
) -> Dict[str, Any]:
    target = row.get("target", "")
    packet = read_json(resolve_path(row["packet_json"]), {})
    source_graph_spec = resolve_path(row["source_graph_spec"])
    source_spec = read_json(source_graph_spec, {})
    source_packet_path = resolve_path(str(packet.get("source_packet") or ""))
    source_packet = read_json(source_packet_path, {})
    patch = read_attempt_payload(attempt_path)
    issues = validate_patch(patch, source_spec, target)
    attempt_dir = attempts_root / attempt_dir_for(row, model)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    patch_path = attempt_dir / "patch.json"
    write_json(patch_path, patch)

    if issues:
        write_json(attempt_dir / "micro_edit_validation_report.json", {"valid": False, "issues": issues})
        return {
            "priority": row.get("priority", ""),
            "paper_spec": row.get("paper_spec", ""),
            "candidate_label": row.get("candidate_label", ""),
            "target": target,
            "model": model,
            "attempt_path": rel(attempt_path),
            "patch": rel(patch_path),
            "graph_spec": "",
            "dot": "",
            "preflight_report": rel(attempt_dir / "micro_edit_validation_report.json"),
            "staged_run_dir": "",
            "patch_valid": False,
            "passed_local_preflight": False,
            "strict_validator_valid": "",
            "unit_invalid_count": "",
            "entity_coverage_local": "",
            "covered_entities_local": "",
            "total_entities": "",
            "stranded_node_count": "",
            "baseline_CG": row.get("baseline_CG", ""),
            "baseline_REA": row.get("baseline_REA", ""),
            "fresh_gate_required": True,
            "status": "patch_validation_failed",
        }

    raw_merged = merge_patch(source_spec, patch, target)
    raw_strict_valid, raw_strict_issues = validate_graph_spec(raw_merged, mode="strict")
    raw_preflight = preflight_graph_spec(raw_merged, source_packet)
    merged, preflight, cleanup_actions = apply_patch_orphan_evidence_cleanup(
        raw_spec=raw_merged,
        source_spec=source_spec,
        patch=patch,
        target=target,
        packet=source_packet,
        raw_preflight=raw_preflight,
    )
    strict_valid = bool((preflight.get("strict_validator") or {}).get("valid"))
    graph_spec_path = attempt_dir / "graph_spec.json"
    dot_path = attempt_dir / "final_clean_graph.dot"
    preflight_path = attempt_dir / "preflight_report.json"
    write_json(attempt_dir / "raw_patch_graph_spec.json", raw_merged)
    write_json(
        attempt_dir / "raw_patch_preflight_report.json",
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "paper_spec": row.get("paper_spec", ""),
            "candidate_label": row.get("candidate_label", ""),
            "target": target,
            "model": model,
            "attempt_path": rel(attempt_path),
            "patch_path": rel(patch_path),
            "patch_validation": {"valid": True, "issues": []},
            "strict_validator": {"valid": raw_strict_valid, "issues": raw_strict_issues},
            **raw_preflight,
        },
    )
    write_json(graph_spec_path, merged)
    write_text(dot_path, graph_spec_to_dot(merged))
    write_json(
        preflight_path,
        {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "paper_spec": row.get("paper_spec", ""),
            "candidate_label": row.get("candidate_label", ""),
            "target": target,
            "model": model,
            "attempt_path": rel(attempt_path),
            "patch_path": rel(patch_path),
            "patch_validation": {"valid": True, "issues": []},
            "materialization_cleanup": cleanup_actions,
            **preflight,
        },
    )

    staged_dir = ""
    if copy_staging and preflight.get("passed_local_preflight") is True:
        stage_dir = staging_root / safe_slug(row.get("paper_spec", "")) / safe_slug(target) / safe_slug(model)
        stage_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dot_path, stage_dir / "final_clean_graph.dot")
        stage_source = resolve_path(str(packet.get("source_selection_ledger_row", {}).get("staged_run_dir") or ""))
        input_data = stage_source / "input_data.json"
        if input_data.exists():
            shutil.copy2(input_data, stage_dir / "input_data.json")
        write_json(
            stage_dir / "evidence_bound_packet_pointer.json",
            {
                "packet": row.get("packet_json", ""),
                "micro_edit_packet": row.get("packet_json", ""),
                "attempt": rel(attempt_path),
                "patch": rel(patch_path),
                "preflight_report": rel(preflight_path),
                "source_graph_spec": row.get("source_graph_spec", ""),
            },
        )
        staged_dir = rel(stage_dir)

    return {
        "priority": row.get("priority", ""),
        "paper_spec": row.get("paper_spec", ""),
        "candidate_label": row.get("candidate_label", ""),
        "target": target,
        "model": model,
        "attempt_path": rel(attempt_path),
        "patch": rel(patch_path),
        "graph_spec": rel(graph_spec_path),
        "dot": rel(dot_path),
        "preflight_report": rel(preflight_path),
        "staged_run_dir": staged_dir,
        "patch_valid": True,
        "passed_local_preflight": preflight.get("passed_local_preflight") is True,
        "strict_validator_valid": strict_valid,
        "unit_invalid_count": preflight.get("reasoning_units", {}).get("invalid_target_count"),
        "entity_coverage_local": preflight.get("entity_coverage", {}).get("coverage_rate_local"),
        "covered_entities_local": preflight.get("entity_coverage", {}).get("covered_entities"),
        "total_entities": preflight.get("entity_coverage", {}).get("total_entities"),
        "stranded_node_count": len(preflight.get("connectivity", {}).get("stranded_nodes") or []),
        "baseline_CG": row.get("baseline_CG", ""),
        "baseline_REA": row.get("baseline_REA", ""),
        "fresh_gate_required": True,
        "materialization_cleanup": ";".join(action.get("action", "") for action in cleanup_actions),
        "status": "",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet-index", default=str(DEFAULT_PACKET_INDEX))
    parser.add_argument("--attempts-root", default=str(DEFAULT_ATTEMPTS_ROOT))
    parser.add_argument("--staging-root", default=str(DEFAULT_STAGING_ROOT))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--execute", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--materialize-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--copy-staging", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-sleep", type=float, default=8.0)
    parser.add_argument("--transport", choices=["curl", "urllib"], default="curl")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(resolve_path(args.env_file))
    packet_index = resolve_path(args.packet_index)
    attempts_root = resolve_path(args.attempts_root)
    staging_root = resolve_path(args.staging_root)
    attempts_root.mkdir(parents=True, exist_ok=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    rows = read_csv(packet_index)
    rows.sort(key=lambda row: int(row.get("priority") or 999999))
    if args.limit > 0:
        rows = rows[: args.limit]
    base_url = args.base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("GPT_BASE_URL") or "https://api.openai.com/v1"
    api_key = os.getenv(args.api_key_env, "")
    attempt_rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for row in rows:
        try:
            attempt_path: Optional[Path] = None
            if args.execute:
                attempt_path = execute_attempt(
                    row,
                    model=args.model,
                    attempts_root=attempts_root,
                    base_url=base_url,
                    api_key=api_key,
                    timeout=args.timeout,
                    max_tokens=args.max_tokens,
                    retries=args.retries,
                    retry_sleep=args.retry_sleep,
                    transport=args.transport,
                )
            elif args.materialize_existing:
                attempt_path = load_existing_attempt(row, args.model, attempts_root)
            if attempt_path:
                attempt_rows.append(
                    materialize_attempt(
                        row,
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
                        "candidate_label": row.get("candidate_label", ""),
                        "target": row.get("target", ""),
                        "model": args.model,
                        "attempt_path": "",
                        "patch": "",
                        "graph_spec": "",
                        "dot": "",
                        "preflight_report": "",
                        "staged_run_dir": "",
                        "patch_valid": False,
                        "passed_local_preflight": False,
                        "strict_validator_valid": "",
                        "unit_invalid_count": "",
                        "entity_coverage_local": "",
                        "covered_entities_local": "",
                        "total_entities": "",
                        "stranded_node_count": "",
                        "baseline_CG": row.get("baseline_CG", ""),
                        "baseline_REA": row.get("baseline_REA", ""),
                        "fresh_gate_required": True,
                        "status": "planned_no_attempt",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"paper_spec": row.get("paper_spec", ""), "target": row.get("target", ""), "error": str(exc)})

    fieldnames = [
        "priority",
        "paper_spec",
        "candidate_label",
        "target",
        "model",
        "attempt_path",
        "patch",
        "graph_spec",
        "dot",
        "preflight_report",
        "staged_run_dir",
        "patch_valid",
        "passed_local_preflight",
        "strict_validator_valid",
        "unit_invalid_count",
        "entity_coverage_local",
        "covered_entities_local",
        "total_entities",
        "stranded_node_count",
        "baseline_CG",
        "baseline_REA",
        "fresh_gate_required",
        "status",
    ]
    write_csv(attempts_root / "MICRO_EDIT_ATTEMPT_INDEX.csv", attempt_rows, fieldnames)
    write_json(attempts_root / "MICRO_EDIT_ATTEMPT_INDEX.json", {"rows": attempt_rows})
    write_json(attempts_root / "MICRO_EDIT_FAILURES.json", failures)
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "evidence_bound_micro_edit_patch",
        "provider_calls": bool(args.execute),
        "packet_index": rel(packet_index),
        "attempts_root": rel(attempts_root),
        "staging_root": rel(staging_root),
        "model": args.model,
        "selected_packets": len(rows),
        "attempt_rows": len(attempt_rows),
        "patch_valid": sum(1 for row in attempt_rows if row.get("patch_valid") is True),
        "local_preflight_passed": sum(1 for row in attempt_rows if row.get("passed_local_preflight") is True),
        "failures": len(failures),
        "by_target": dict(Counter(row.get("target", "") for row in attempt_rows)),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
    }
    write_json(attempts_root / "MICRO_EDIT_ATTEMPT_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
