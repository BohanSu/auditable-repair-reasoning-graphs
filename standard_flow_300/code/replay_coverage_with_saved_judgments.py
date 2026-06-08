#!/usr/bin/env python3
"""Replay coverage from saved evaluator judgments without provider calls."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[6]
DEFAULT_EVALUATOR = PACKAGE_ROOT / "07_code" / "evaluator.py"


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except (OSError, ValueError):
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_evaluator(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("pearl_replay_evaluator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load evaluator module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_saved_judgments(saved: dict[str, Any]) -> tuple[dict[str, Any], str]:
    direct = saved.get("reasoning_validation_results")
    if isinstance(direct, dict):
        return direct, "reasoning_validation_results"

    accuracy = saved.get("accuracy")
    if isinstance(accuracy, dict) and isinstance(accuracy.get("details"), dict):
        return accuracy["details"], "accuracy.details"

    judged_votes = saved.get("judged_votes")
    if isinstance(judged_votes, list):
        converted = {
            str(item["final_reasoning_id"]): item.get("final_result")
            for item in judged_votes
            if isinstance(item, dict) and item.get("final_reasoning_id") is not None
        }
        if converted:
            return converted, "judged_votes"

    raise RuntimeError("evaluation JSON has no reusable saved judgment mapping")


def replay(args: argparse.Namespace) -> dict[str, Any]:
    work_dir = resolve(args.work_dir)
    eval_json = resolve(args.eval_json)
    evaluator_path = resolve(args.evaluator)
    graph_file = resolve(args.graph_file) if args.graph_file else work_dir / "final_clean_graph.dot"
    input_data = resolve(args.input_data) if args.input_data else work_dir / "input_data.json"

    saved = json.loads(eval_json.read_text(encoding="utf-8"))
    reasoning_validation_results, judgment_source = extract_saved_judgments(saved)
    entities = saved.get("entities")
    if not isinstance(entities, list):
        raise RuntimeError("evaluation JSON has no entities list")

    evaluator_module = load_evaluator(evaluator_path)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copyfile(input_data, tmp_path / "input_data.json")
        evaluator = evaluator_module.GraphEvaluator(str(tmp_path), str(graph_file))
        if not evaluator.load_data():
            raise RuntimeError("failed to load graph/input_data for replay")
        evaluator.core_idea_entities = entities
        replayed_cg = evaluator.calculate_entity_coverage_from_correct_reasoning(reasoning_validation_results) / 100.0

    old_coverage = saved.get("coverage") or {}
    total_entities = len(entities)
    replayed_covered = int(round(replayed_cg * total_entities))
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_calls": False,
        "canonical_accounting_write": False,
        "work_dir": rel(work_dir),
        "eval_json": rel(eval_json),
        "evaluator": rel(evaluator_path),
        "graph_file": rel(graph_file),
        "input_data": rel(input_data),
        "judgment_source": judgment_source,
        "old_CG": old_coverage.get("coverage_rate"),
        "old_covered_entities": old_coverage.get("covered_entities"),
        "old_total_entities": old_coverage.get("total_entities"),
        "replayed_CG": replayed_cg,
        "replayed_covered_entities": replayed_covered,
        "replayed_total_entities": total_entities,
        "entities": entities,
    }
    if args.out:
        write_json(resolve(args.out), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--eval-json", required=True)
    parser.add_argument("--graph-file")
    parser.add_argument("--input-data")
    parser.add_argument("--evaluator", default=str(DEFAULT_EVALUATOR))
    parser.add_argument("--out")
    return parser.parse_args()


if __name__ == "__main__":
    replay(parse_args())
