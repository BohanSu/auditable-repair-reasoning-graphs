#!/usr/bin/env python3
"""Batch replay notation-sensitive residual coverage without provider calls."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
RESIDUAL_ROOT = PACKAGE_ROOT / "09_residual_50_closeout"
DESIGN_ROOT = RESIDUAL_ROOT / "closeout_design" / "20260604_evidence_bound_claim_reconstruction"
DEFAULT_LEDGER = DESIGN_ROOT / "RESIDUAL_CLOSEOUT_LEDGER.csv"
DEFAULT_MISSING_ENTITY_CSV = (
    RESIDUAL_ROOT
    / "missing_entity_audit"
    / "20260604_final_metric_gate_v3_official_steps"
    / "MISSING_ENTITY_PATCH_TASKS.csv"
)
DEFAULT_OUT_ROOT = DESIGN_ROOT / "notation_batch_replay_20260604"
DEFAULT_V2_LEDGER = (
    RESIDUAL_ROOT
    / "closeout_design"
    / "20260606_residual_closeout_v2"
    / "V2_RESIDUAL_LEDGER.csv"
)
DEFAULT_EVALUATOR = PACKAGE_ROOT / "07_code" / "evaluator.py"
REPLAY_HELPER = PACKAGE_ROOT / "07_code" / "replay_coverage_with_saved_judgments.py"


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replay_one(
    evaluator_module: Any,
    replay_helper: Any,
    *,
    paper_spec: str,
    graph_file: Path,
    input_data: Path,
    eval_json: Path,
) -> dict[str, Any]:
    saved = json.loads(eval_json.read_text(encoding="utf-8"))
    judgments, judgment_source = replay_helper.extract_saved_judgments(saved)
    entities = saved.get("entities")
    if not isinstance(entities, list):
        raise RuntimeError(f"{paper_spec}: evaluation JSON has no entities list")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copyfile(input_data, tmp_path / "input_data.json")
        evaluator = evaluator_module.GraphEvaluator(str(tmp_path), str(graph_file))
        if not evaluator.load_data():
            raise RuntimeError(f"{paper_spec}: failed to load graph/input_data")
        evaluator.core_idea_entities = entities
        replayed_cg = evaluator.calculate_entity_coverage_from_correct_reasoning(judgments) / 100.0

    old_coverage = saved.get("coverage") or {}
    total_entities = len(entities)
    return {
        "paper_spec": paper_spec,
        "old_CG": old_coverage.get("coverage_rate"),
        "old_covered_entities": old_coverage.get("covered_entities"),
        "old_total_entities": old_coverage.get("total_entities"),
        "replayed_CG": replayed_cg,
        "replayed_covered_entities": int(round(replayed_cg * total_entities)),
        "replayed_total_entities": total_entities,
        "judgment_source": judgment_source,
        "eval_json": rel(eval_json),
        "graph_file": rel(graph_file),
        "input_data": rel(input_data),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    ledger_csv = resolve(args.ledger)
    missing_csv = resolve(args.missing_entity_csv)
    out_root = resolve(args.out_root)
    evaluator_module = load_module("batch_replay_evaluator", resolve(args.evaluator))
    replay_helper = load_module("batch_replay_helper", REPLAY_HELPER)

    ledger_rows = read_csv(ledger_csv)
    target_rows = []
    for row in ledger_rows:
        module = row.get("next_closeout_module") or row.get("v2_module")
        if module in {
            "notation_normalized_replay_then_source_bridge",
            "notation_normalized_source_bridge",
        }:
            target_rows.append(row)
    missing_by_spec: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(missing_csv):
        missing_by_spec[row.get("paper_spec", "")].append(row)

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for target in target_rows:
        paper_spec = target["paper_spec"]
        source_rows = missing_by_spec.get(paper_spec, [])
        if not source_rows:
            errors.append({"paper_spec": paper_spec, "error": "no missing-entity source row"})
            continue
        source = source_rows[0]
        eval_json = resolve(source["final_eval_dir"]) / "evaluation_results.json"
        try:
            result = replay_one(
                evaluator_module,
                replay_helper,
                paper_spec=paper_spec,
                graph_file=resolve(source["final_graph"]),
                input_data=resolve(source["input_data"]),
                eval_json=eval_json,
            )
            result.update(
                {
                    "model": target.get("model", ""),
                    "paper": target.get("paper", ""),
                    "final_REA": target.get("final_REA", ""),
                    "missing_entities": target.get("missing_entities", ""),
                    "notation_sensitive_entities": target.get("notation_sensitive_entities", ""),
                    "source_closeout_module": target.get("next_closeout_module") or target.get("v2_module", ""),
                    "ans_guard_floor": target.get("ans_guard_floor", ""),
                    "batch_ans_floor": target.get("batch_ans_floor", ""),
                    "strict_gate_by_replay": result["replayed_CG"] == 1.0 and float(target.get("final_REA") or 0.0) == 1.0,
                    "status": "replayed",
                }
            )
            rows.append(result)
        except Exception as exc:  # noqa: BLE001 - ledger should retain row-level replay failures.
            errors.append({"paper_spec": paper_spec, "error": str(exc)})

    status_counts = Counter(row["strict_gate_by_replay"] for row in rows)
    summary = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "source_ledger": rel(ledger_csv),
        "source_missing_entity_csv": rel(missing_csv),
        "out_root": rel(out_root),
        "target_module_names": [
            "notation_normalized_replay_then_source_bridge",
            "notation_normalized_source_bridge",
        ],
        "target_rows": len(target_rows),
        "replayed_rows": len(rows),
        "error_rows": len(errors),
        "strict_gate_by_replay_true": status_counts.get(True, 0),
        "strict_gate_by_replay_false": status_counts.get(False, 0),
        "errors": errors,
    }

    fieldnames = [
        "paper_spec",
        "model",
        "paper",
        "old_CG",
        "replayed_CG",
        "old_covered_entities",
        "replayed_covered_entities",
        "replayed_total_entities",
        "final_REA",
        "strict_gate_by_replay",
        "judgment_source",
        "missing_entities",
        "notation_sensitive_entities",
        "source_closeout_module",
        "ans_guard_floor",
        "batch_ans_floor",
        "status",
        "eval_json",
        "graph_file",
        "input_data",
    ]
    out_root.mkdir(parents=True, exist_ok=True)
    write_csv(out_root / "NOTATION_BATCH_REPLAY.csv", rows, fieldnames)
    write_json(out_root / "NOTATION_BATCH_REPLAY.json", {"summary": summary, "rows": rows})
    write_text(out_root / "NOTATION_BATCH_REPLAY.md", render_markdown(summary, rows))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Notation-Sensitive Coverage Replay",
        "",
        f"Created: `{summary['created_at']}`",
        "",
        "This replay does not call providers and does not write canonical accounting.",
        "",
        "## Summary",
        "",
        f"- Target rows: `{summary['target_rows']}`",
        f"- Replayed rows: `{summary['replayed_rows']}`",
        f"- Errors: `{summary['error_rows']}`",
        f"- Strict gate by replay: `{summary['strict_gate_by_replay_true']}`",
        "",
        "## Rows",
        "",
        "| paper_spec | old CG | replayed CG | REA | replay gate | notation-sensitive entities |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| `{paper_spec}` | `{old_CG}` | `{replayed_CG}` | `{final_REA}` | `{strict_gate_by_replay}` | `{notation_sensitive_entities}` |".format(
                **row
            )
        )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument(
        "--use-v2-ledger",
        action="store_true",
        help="Use the 20260606 residual-closeout-v2 ledger unless --ledger is explicitly supplied.",
    )
    parser.add_argument("--missing-entity-csv", default=str(DEFAULT_MISSING_ENTITY_CSV))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--evaluator", default=str(DEFAULT_EVALUATOR))
    args = parser.parse_args()
    if args.use_v2_ledger and args.ledger == str(DEFAULT_LEDGER):
        args.ledger = str(DEFAULT_V2_LEDGER)
    return args


if __name__ == "__main__":
    build(parse_args())
