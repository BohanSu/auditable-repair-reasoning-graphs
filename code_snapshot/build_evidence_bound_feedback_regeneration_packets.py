#!/usr/bin/env python3
"""Build second-round evidence-bound regeneration packets from fresh judge feedback.

The input is a fresh fixed-anchor evaluation run produced by
`evaluate_evidence_bound_staged_candidate.py`. The output is a new packet index
that can be passed to `run_evidence_bound_graph_spec_regeneration.py`.

This script does not call model providers and does not merge results.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
    / "10_standard_flow_350_subset_package"
)
DEFAULT_FRESH_EVAL_RESULTS = (
    PACKAGE_ROOT
    / "09_residual_50_closeout"
    / "runs"
    / "evidence_bound_gpt55_smoke_fresh_eval_20260603_0018"
    / "FRESH_EVAL_RESULTS.json"
)
DEFAULT_OUT_ROOT = (
    PACKAGE_ROOT
    / "09_residual_50_closeout"
    / "evidence_bound_regeneration"
    / "feedback_regeneration"
    / "round01_from_gpt55_smoke_20260603_0018"
)


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


def compact_text(value: Any, *, max_chars: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def parse_specs(values: Sequence[str], specs_file: str = "") -> List[str]:
    specs = [str(value).strip() for value in values if str(value).strip()]
    if specs_file:
        for line in resolve_path(specs_file).read_text(encoding="utf-8").splitlines():
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


def source_packet_for_row(row: Dict[str, Any]) -> tuple[Path, Dict[str, Any]]:
    stage_dir = resolve_path(str(row.get("staged_run_dir") or ""))
    pointer = read_json(stage_dir / "evidence_bound_packet_pointer.json", {})
    packet_path = resolve_path(str(pointer.get("packet") or ""))
    packet = read_json(packet_path, {})
    if not isinstance(packet, dict) or not packet:
        raise RuntimeError(f"cannot resolve source packet for {row.get('paper_spec')}: {packet_path}")
    return packet_path, packet


def vote_files(eval_dir: Path) -> List[Path]:
    return sorted((eval_dir / "responses").glob("reasoning_validation_*_vote_result.json"))


def model_reason_rows(vote: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    responses = vote.get("model_responses") if isinstance(vote.get("model_responses"), dict) else {}
    for model_key, payload in sorted(responses.items()):
        payload = payload if isinstance(payload, dict) else {}
        out.append(
            {
                "model": str(model_key),
                "result": str(payload.get("result") or ""),
                "reason": compact_text(payload.get("reason") or payload.get("raw_response") or "", max_chars=700),
            }
        )
    return out


def classify_votes(eval_dir: Path) -> Dict[str, Any]:
    semantic_failures: List[Dict[str, Any]] = []
    contaminated_wrong_failures: List[Dict[str, Any]] = []
    provider_errors: List[Dict[str, Any]] = []
    correct_votes: List[Dict[str, Any]] = []
    for path in vote_files(eval_dir):
        vote = read_json(path, {})
        if not isinstance(vote, dict):
            continue
        final = str(vote.get("final_result") or "").lower()
        payload = {
            "vote_file": rel(path),
            "reasoning_id": vote.get("reasoning_id"),
            "target_node": vote.get("target_node"),
            "reasoning_type": vote.get("reasoning_type"),
            "target_content": compact_text(vote.get("target_content"), max_chars=900),
            "source_nodes": vote.get("source_nodes"),
            "source_contents": [compact_text(item, max_chars=700) for item in vote.get("source_contents") or []],
            "edge_types": vote.get("actual_edge_types") or vote.get("edge_types"),
            "model_results": vote.get("model_results"),
            "vote_decision": (vote.get("vote_breakdown") or {}).get("decision")
            if isinstance(vote.get("vote_breakdown"), dict)
            else "",
            "model_reasons": model_reason_rows(vote),
        }
        non_error_wrong = any(
            str(reason.get("result") or "").lower() == "wrong"
            for reason in payload["model_reasons"]
        )
        if final == "wrong":
            semantic_failures.append(payload)
        elif final == "error":
            if non_error_wrong:
                contaminated_wrong_failures.append(payload)
            provider_errors.append(payload)
        elif final == "correct":
            correct_votes.append(payload)
    return {
        "semantic_failures": semantic_failures,
        "contaminated_wrong_failures": contaminated_wrong_failures,
        "provider_errors": provider_errors,
        "correct_votes": correct_votes,
        "semantic_failure_count": len(semantic_failures),
        "contaminated_wrong_failure_count": len(contaminated_wrong_failures),
        "provider_error_count": len(provider_errors),
        "correct_vote_count": len(correct_votes),
    }


def build_feedback_prompt(original_prompt: str, feedback: Dict[str, Any]) -> str:
    base_prompt = original_prompt.split("FRESH STRICT-GATE FEEDBACK FROM THE PREVIOUS CANDIDATE:", 1)[0].rstrip()
    semantic_failures = feedback.get("semantic_failures") or []
    provider_errors = feedback.get("provider_errors") or []
    lines = [
        base_prompt,
        "",
        "FRESH STRICT-GATE FEEDBACK FROM THE PREVIOUS CANDIDATE:",
        "The previous candidate passed local graph_spec preflight, but fresh fixed-anchor judging exposed the following issues.",
        "",
        "Repair policy:",
        "- Treat semantic failures with final_result=wrong as mandatory repair targets.",
        "- Treat provider-contaminated targets with any non-error wrong judge vote as mandatory ambiguity repair targets.",
        "- Do not repeat the rejected target conclusion or the same unsupported premise-to-conclusion leap.",
        "- Provider-error targets with no non-error wrong vote are not semantic failures by themselves; keep those units concise and evidence-bound, but do not overfit to provider outages.",
        "- Preserve full entity coverage, legal paired reasoning units, single NROOT, and complete root connectivity.",
        "- Return a new complete JSON graph_spec only; do not return a patch.",
        "",
        "Semantic failures to repair:",
    ]
    if semantic_failures:
        for item in semantic_failures:
            lines.extend(
                [
                    f"- Target {item.get('target_node')} ({item.get('reasoning_type')}): {item.get('target_content')}",
                    f"  Sources: {json.dumps(item.get('source_contents') or [], ensure_ascii=False)}",
                    f"  Judge decision: {item.get('vote_decision')}",
                    "  Judge reasons:",
                ]
            )
            for reason in item.get("model_reasons") or []:
                lines.append(f"    - {reason.get('model')}: {reason.get('result')} — {reason.get('reason')}")
    else:
        lines.append("- None.")

    contaminated_wrong_failures = feedback.get("contaminated_wrong_failures") or []
    lines.append("")
    lines.append("Provider-contaminated ambiguity repairs to treat as mandatory:")
    if contaminated_wrong_failures:
        for item in contaminated_wrong_failures:
            wrong_reasons = [
                reason
                for reason in item.get("model_reasons") or []
                if str(reason.get("result") or "").lower() == "wrong"
            ]
            lines.extend(
                [
                    f"- Target {item.get('target_node')} ({item.get('reasoning_type')}): {item.get('target_content')}",
                    f"  Sources: {json.dumps(item.get('source_contents') or [], ensure_ascii=False)}",
                    f"  Judge decision: {item.get('vote_decision')}",
                    "  Non-error wrong judge reasons:",
                ]
            )
            for reason in wrong_reasons:
                lines.append(f"    - {reason.get('model')}: {reason.get('result')} — {reason.get('reason')}")
            lines.append(
                "  Required repair: replace circular/restated premises with an evidence-supported unit whose sources add information beyond the target conclusion."
            )
    else:
        lines.append("- None.")

    lines.append("")
    lines.append("Provider-error targets to keep auditable but not treat as semantic rejections:")
    if provider_errors:
        for item in provider_errors:
            if item in contaminated_wrong_failures:
                continue
            non_error = [
                reason
                for reason in item.get("model_reasons") or []
                if str(reason.get("result") or "").lower() != "error"
            ]
            lines.append(
                f"- Target {item.get('target_node')} ({item.get('reasoning_type')}): provider error in at least one judge; non-error judge opinions={json.dumps(non_error, ensure_ascii=False)}"
            )
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def packet_dir_for(row: Dict[str, Any]) -> Path:
    priority = int(row.get("priority") or 999999)
    return Path(f"{priority:03d}_{safe_slug(row.get('paper_spec') or 'paper')}__feedback_round")


def build_feedback_packets(
    rows: List[Dict[str, Any]],
    *,
    out_root: Path,
    paper_specs: Sequence[str],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    spec_set = set(paper_specs)
    index_rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    packets_root = out_root / "packets"
    for row in rows:
        paper_spec = str(row.get("paper_spec") or "")
        if spec_set and paper_spec not in spec_set:
            continue
        try:
            eval_dir = resolve_path(str(row.get("eval_dir") or ""))
            feedback = classify_votes(eval_dir)
            if (
                feedback["semantic_failure_count"] <= 0
                and feedback["contaminated_wrong_failure_count"] <= 0
                and feedback["provider_error_count"] <= 0
            ):
                continue
            source_packet_path, packet = source_packet_for_row(row)
            original_prompt = resolve_path(str(packet.get("generation_prompt") or ""))
            if original_prompt.is_file():
                prompt_text = original_prompt.read_text(encoding="utf-8")
            else:
                # Older packets omit `generation_prompt` from JSON by design;
                # infer it from the packet path.
                prompt_text = (source_packet_path.parent / "generation_prompt.txt").read_text(encoding="utf-8")
            new_packet = dict(packet)
            new_packet["mode"] = "evidence_bound_feedback_regeneration_packet_v1"
            new_packet["created_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            new_packet["source_feedback"] = {
                "fresh_eval_result_row": row,
                "source_packet": rel(source_packet_path),
                **feedback,
            }
            new_prompt = build_feedback_prompt(prompt_text, feedback)

            out_dir = packets_root / packet_dir_for(row)
            packet_path = out_dir / "packet.json"
            prompt_path = out_dir / "generation_prompt.txt"
            write_json(packet_path, new_packet)
            write_text(prompt_path, new_prompt)
            index_rows.append(
                {
                    "priority": row.get("priority", ""),
                    "paper_spec": paper_spec,
                    "lane": row.get("lane", ""),
                    "failure_type": "fresh_feedback_regeneration",
                    "packet_json": rel(packet_path),
                    "generation_prompt": rel(prompt_path),
                    "input_data": packet.get("source_paths", {}).get("input_data", "")
                    if isinstance(packet.get("source_paths"), dict)
                    else "",
                    "entity_count": len((packet.get("paper_anchor") or {}).get("entities") or []),
                    "semantic_failure_count": feedback["semantic_failure_count"],
                    "contaminated_wrong_failure_count": feedback["contaminated_wrong_failure_count"],
                    "provider_error_count": feedback["provider_error_count"],
                    "mandatory_repair_target_count": feedback["semantic_failure_count"]
                    + feedback["contaminated_wrong_failure_count"],
                    "semantic_failed_targets": "; ".join(str(item.get("target_node")) for item in feedback["semantic_failures"]),
                    "contaminated_wrong_targets": "; ".join(
                        str(item.get("target_node")) for item in feedback["contaminated_wrong_failures"]
                    ),
                    "provider_error_targets": "; ".join(str(item.get("target_node")) for item in feedback["provider_errors"]),
                    "source_eval_dir": row.get("eval_dir", ""),
                    "source_attempt_path": row.get("attempt_path", ""),
                    "packet_sha256": sha256_file(packet_path),
                    "prompt_sha256": sha256_file(prompt_path),
                }
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"paper_spec": paper_spec, "error": str(exc)})
    return index_rows, failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh-eval-results", default=str(DEFAULT_FRESH_EVAL_RESULTS))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--paper-specs", nargs="+", default=[])
    parser.add_argument("--paper-specs-file", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fresh_results = read_json(resolve_path(args.fresh_eval_results), {})
    rows = fresh_results.get("rows") if isinstance(fresh_results, dict) else []
    if not isinstance(rows, list):
        rows = []
    out_root = resolve_path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    index_rows, failures = build_feedback_packets(
        rows,
        out_root=out_root,
        paper_specs=parse_specs(args.paper_specs, args.paper_specs_file),
    )
    fieldnames = [
        "priority",
        "paper_spec",
        "lane",
        "failure_type",
        "packet_json",
        "generation_prompt",
        "input_data",
        "entity_count",
        "semantic_failure_count",
        "contaminated_wrong_failure_count",
        "provider_error_count",
        "mandatory_repair_target_count",
        "semantic_failed_targets",
        "contaminated_wrong_targets",
        "provider_error_targets",
        "source_eval_dir",
        "source_attempt_path",
        "packet_sha256",
        "prompt_sha256",
    ]
    write_csv(out_root / "PACKET_INDEX.csv", index_rows, fieldnames)
    write_json(out_root / "PACKET_INDEX.json", {"rows": index_rows})
    write_json(out_root / "PACKET_BUILD_FAILURES.json", failures)
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "evidence_bound_feedback_regeneration_packet_build",
        "fresh_eval_results": rel(resolve_path(args.fresh_eval_results)),
        "out_root": rel(out_root),
        "packet_count": len(index_rows),
        "failure_count": len(failures),
        "semantic_failure_targets": sum(int(row.get("semantic_failure_count") or 0) for row in index_rows),
        "contaminated_wrong_failure_targets": sum(
            int(row.get("contaminated_wrong_failure_count") or 0) for row in index_rows
        ),
        "provider_error_targets": sum(int(row.get("provider_error_count") or 0) for row in index_rows),
        "mandatory_repair_targets": sum(int(row.get("mandatory_repair_target_count") or 0) for row in index_rows),
        "by_lane": dict(Counter(row.get("lane", "") for row in index_rows)),
        "acceptance_boundary": {"final_CG": 1.0, "final_REA": 1.0, "fresh_evaluation_required": True},
    }
    write_json(out_root / "PACKET_BUILD_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
