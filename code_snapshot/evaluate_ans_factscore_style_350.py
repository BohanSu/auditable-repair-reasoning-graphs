#!/usr/bin/env python3
"""Compute FActScore-style Atomic Node Support (ANS) for PEARL graph stages.

The metric follows the FActScore evaluation pattern at graph-node level:
decompose generated node text into atomic factual claims, then judge each
atomic claim against the paper evidence corpus. The reliable source corpus is
the same paper evidence/input_data used by PEARL.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[6]
PACKAGE_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
)
MINICHECK_ROOT = PACKAGE_ROOT / "08_minicheck"
DEFAULT_OUT_DIR = PACKAGE_ROOT / "10_standard_flow_350_subset_package" / "08_ans_factscore_style" / "03_current_full_run"

STAGES = {
    "raw_step1_extraction": {
        "label": "LLM raw graph extraction",
        "claims": MINICHECK_ROOT / "baselines" / "raw_step1_extraction" / "claims_input.jsonl",
    },
    "llm_step2_self_fix_final_clean": {
        "label": "LLM autonomous second-pass repair/fallback",
        "claims": MINICHECK_ROOT
        / "baselines"
        / "llm_step2_self_fix_final_clean"
        / "claims_input.jsonl",
    },
    "pearl_terminal_graph": {
        "label": "PEARL semantic repair terminal graph",
        "claims": MINICHECK_ROOT / "current_pearl_terminal_graph" / "claims_input.jsonl",
    },
}

DEFAULT_EXCLUDE_MODELS = {"gpt_5_4", "gpt_5_5"}
MAIN_SCOPE_EXCLUDE_UNIT_TYPES = {"root_common_bridge", "graph_node"}
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-+/().]*")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def register_custom_stages(values: Sequence[str]) -> None:
    for value in values:
        if "=" not in value:
            raise RuntimeError(f"--custom-stage must be name=claims_input.jsonl, got: {value}")
        name, path_text = value.split("=", 1)
        name = name.strip()
        if not name:
            raise RuntimeError(f"--custom-stage has empty stage name: {value}")
        claims_path = resolve_path(path_text.strip())
        if not claims_path.exists():
            raise FileNotFoundError(f"custom-stage claims file not found: {claims_path}")
        STAGES[name] = {
            "label": name.replace("_", " "),
            "claims": claims_path,
        }


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_done_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            node_key = row.get("ans_node_key")
            if node_key and row.get("status") == "ok":
                done.add(str(node_key))
    return done


def load_env_file(path: Optional[Path]) -> None:
    if not path or not path.exists():
        return
    raw: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.startswith("export "):
            text = text[len("export ") :]
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        value = re.sub(
            r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?",
            lambda match: raw.get(match.group(1), os.environ.get(match.group(1), "")),
            value,
        )
        raw[key] = value
        if key and value and key not in os.environ:
            os.environ[key] = value


def clean_text(text: Any) -> str:
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    out = re.sub(
        r"^(?:Source|Deduction-(?:rule|case|reasoning)|Induction-(?:case|common|reasoning)|"
        r"Abduction-(?:phenomenon|knowledge|hypothesis|reasoning)|"
        r"Rule|Case|Common|Reasoning|Hypothesis|Phenomenon|Knowledge)\s*:\s*",
        "",
        out,
        flags=re.IGNORECASE,
    ).strip()
    return out


def tokens(text: str) -> List[str]:
    return [tok.lower() for tok in TOKEN_RE.findall(text or "") if len(tok) > 1]


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_input_data(path_text: str) -> Dict[str, Any]:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return json.loads(path.read_text(encoding="utf-8"))


def evidence_corpus(input_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    corpus: List[Dict[str, Any]] = []
    for sentence in (input_data.get("introduction") or {}).get("sentences") or []:
        if not isinstance(sentence, dict):
            continue
        idx = sentence.get("idx")
        parts = []
        sent = str(sentence.get("sentence") or "").strip()
        if sent:
            parts.append(sent)
        viewpoints = [str(v).strip() for v in sentence.get("viewpoints") or [] if str(v).strip()]
        if viewpoints:
            parts.append("Viewpoints: " + " | ".join(viewpoints[:4]))
        text = " ".join(parts).strip()
        if text:
            corpus.append({"idx": idx, "text": f"[{idx}] {text}", "tokens": set(tokens(text))})
    return corpus


def retrieve_evidence(row: Dict[str, Any], corpus: List[Dict[str, Any]], *, top_k: int, max_chars: int) -> str:
    claim_text = clean_text(row.get("claim_text_for_minicheck") or row.get("raw_node_text") or "")
    query_tokens = set(tokens(claim_text))
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for item in corpus:
        toks = item["tokens"]
        if not toks:
            continue
        overlap = len(query_tokens & toks)
        if overlap <= 0:
            continue
        score = overlap / max(4.0, len(query_tokens) ** 0.5)
        scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("idx"))))

    lines: List[str] = []
    seen = set()
    graph_evidence = str(row.get("evidence_text") or "").strip()
    if graph_evidence:
        for line in graph_evidence.splitlines():
            line = line.strip()
            if line and line not in seen:
                lines.append(line)
                seen.add(line)

    for _score, item in scored[:top_k]:
        text = item["text"].strip()
        if text and text not in seen:
            lines.append(text)
            seen.add(text)

    out_lines: List[str] = []
    used_chars = 0
    for line in lines:
        next_len = len(line) + 1
        if max_chars > 0 and used_chars + next_len > max_chars:
            break
        out_lines.append(line)
        used_chars += next_len
    return "\n".join(out_lines).strip()


def node_key(stage: str, row: Dict[str, Any]) -> str:
    base = "|".join(
        [
            stage,
            str(row.get("claim_id") or ""),
            str(row.get("graph_json") or ""),
            str(row.get("node_id") or ""),
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def eval_key(row: Dict[str, Any]) -> str:
    base = json.dumps(
        {
            "unit_type": row.get("unit_type"),
            "node_text": clean_text(row.get("claim_text_for_minicheck") or row.get("raw_node_text") or ""),
            "evidence": row.get("ans_evidence") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def parse_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("JSON response is not an object")
    return data


class OpenAICompatClient:
    def __init__(
        self,
        *,
        model: str,
        temperature: float,
        timeout: int,
        max_tokens: int,
        api_key_env: str = "OPENAI_API_KEY",
        base_url_env: str = "OPENAI_BASE_URL",
    ):
        base_url = os.environ.get(base_url_env, "https://api.openai.com/v1").rstrip("/")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"{api_key_env} is not set")
        self.url = base_url + "/chat/completions"
        self.key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete_json(self, messages: List[Dict[str, str]], *, retries: int, sleep: float) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        last_error: Optional[str] = None
        for attempt in range(retries + 1):
            try:
                response = requests.post(
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}: {response.text[:500]}"
                    time.sleep(sleep * (attempt + 1))
                    continue
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return parse_json_object(content)
            except Exception as exc:  # noqa: BLE001 - keep batch runner resilient.
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(sleep * (attempt + 1))
        raise RuntimeError(last_error or "unknown completion error")


def build_prompt_items(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for row in rows:
        items.append(
            {
                "id": row["ans_node_key"],
                "node_id": row.get("node_id"),
                "unit_type": row.get("unit_type"),
                "node_text": clean_text(row.get("claim_text_for_minicheck") or row.get("raw_node_text") or ""),
                "evidence": row.get("ans_evidence") or "",
            }
        )
    return items


def extract_atomic_facts_batch(
    client: OpenAICompatClient,
    rows: Sequence[Dict[str, Any]],
    *,
    max_facts_per_node: int,
    retries: int,
    sleep: float,
) -> Dict[str, List[str]]:
    system = (
        "You decompose scientific graph-node text into FActScore-style atomic facts. "
        "Use only the node text. Do not look for support and do not use evidence. "
        "Each atomic fact must be short, self-contained, and factual. Exclude pure "
        "graph syntax, source tuple noise, and non-factual connector text. Return JSON only."
    )
    user = {
        "instruction": (
            "For each item, return at most "
            f"{max_facts_per_node} atomic facts. Preserve scientific qualifiers and "
            "avoid merging multiple facts into one sentence."
        ),
        "output_schema": {
            "items": [
                {
                    "id": "same id as input",
                    "atomic_facts": ["atomic factual claim"],
                }
            ]
        },
        "items": [
            {
                "id": row["ans_node_key"],
                "node_id": row.get("node_id"),
                "unit_type": row.get("unit_type"),
                "node_text": clean_text(row.get("claim_text_for_minicheck") or row.get("raw_node_text") or ""),
            }
            for row in rows
        ],
    }
    data = client.complete_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        retries=retries,
        sleep=sleep,
    )
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Response JSON lacks items list")

    facts_by_id: Dict[str, List[str]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "")
        facts: List[str] = []
        for fact in item.get("atomic_facts") or []:
            fact_text = clean_text(fact)
            if fact_text:
                facts.append(fact_text)
        facts_by_id[key] = facts[:max_facts_per_node]
    return facts_by_id


def verify_atomic_facts_batch(
    client: OpenAICompatClient,
    rows: Sequence[Dict[str, Any]],
    facts_by_id: Dict[str, List[str]],
    *,
    retries: int,
    sleep: float,
) -> Dict[str, List[Dict[str, Any]]]:
    system = (
        "You are a FActScore-style factual support verifier for scientific text. "
        "Judge each atomic fact against only the provided paper evidence. A fact is "
        "supported only if the evidence explicitly states it or directly entails it. "
        "Do not use outside knowledge. If evidence is empty or insufficient, mark "
        "unsupported. Return JSON only."
    )
    user = {
        "instruction": "For each atomic fact, label exactly supported or unsupported and give a short evidence-grounded reason.",
        "output_schema": {
            "items": [
                {
                    "id": "same id as input",
                    "atomic_facts": [
                        {
                            "fact": "same fact text",
                            "label": "supported|unsupported",
                            "reason": "short reason",
                        }
                    ],
                }
            ]
        },
        "items": [
            {
                "id": row["ans_node_key"],
                "node_id": row.get("node_id"),
                "unit_type": row.get("unit_type"),
                "evidence": row.get("ans_evidence") or "",
                "atomic_facts": facts_by_id.get(row["ans_node_key"], []),
            }
            for row in rows
        ],
    }
    data = client.complete_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        retries=retries,
        sleep=sleep,
    )
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Response JSON lacks items list")

    judged_by_id: Dict[str, List[Dict[str, Any]]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "")
        facts = []
        for fact in item.get("atomic_facts") or []:
            if not isinstance(fact, dict):
                continue
            fact_text = clean_text(fact.get("fact") or "")
            if not fact_text:
                continue
            label = str(fact.get("label") or "").strip().lower()
            if label not in {"supported", "unsupported"}:
                label = "unsupported"
            facts.append(
                {
                    "fact": fact_text,
                    "label": label,
                    "supported": label == "supported",
                    "reason": clean_text(fact.get("reason") or ""),
                }
            )
        judged_by_id[key] = facts
    return judged_by_id


def judge_batch(
    client: OpenAICompatClient,
    rows: Sequence[Dict[str, Any]],
    *,
    max_facts_per_node: int,
    retries: int,
    sleep: float,
    combined: bool,
) -> List[Dict[str, Any]]:
    if combined:
        return judge_batch_combined(
            client,
            rows,
            max_facts_per_node=max_facts_per_node,
            retries=retries,
            sleep=sleep,
        )
    facts_by_id = extract_atomic_facts_batch(
        client,
        rows,
        max_facts_per_node=max_facts_per_node,
        retries=retries,
        sleep=sleep,
    )
    judged_by_id = verify_atomic_facts_batch(
        client,
        rows,
        facts_by_id,
        retries=retries,
        sleep=sleep,
    )
    results = []
    for row in rows:
        facts = judged_by_id.get(row["ans_node_key"], [])
        results.append(
            {
                "ans_node_key": row["ans_node_key"],
                "ans_eval_key": row["ans_eval_key"],
                "stage": row["ans_stage"],
                "stage_label": row["ans_stage_label"],
                "claim_id": row.get("claim_id"),
                "paper_spec": row.get("paper_spec"),
                "model": row.get("model"),
                "paper": row.get("paper"),
                "run_id": row.get("run_id"),
                "quality_tier": row.get("quality_tier"),
                "candidate_label": row.get("candidate_label"),
                "graph_json": row.get("graph_json"),
                "node_id": row.get("node_id"),
                "unit_type": row.get("unit_type"),
                "source_tuple": row.get("source_tuple"),
                "node_text": clean_text(row.get("claim_text_for_minicheck") or row.get("raw_node_text") or ""),
                "evidence_empty": not bool(str(row.get("ans_evidence") or "").strip()),
                "atomic_facts": facts,
                "atomic_fact_count": len(facts),
                "supported_fact_count": sum(1 for fact in facts if fact["supported"]),
                "status": "ok",
            }
        )
    return results


def judge_batch_combined(
    client: OpenAICompatClient,
    rows: Sequence[Dict[str, Any]],
    *,
    max_facts_per_node: int,
    retries: int,
    sleep: float,
) -> List[Dict[str, Any]]:
    system = (
        "You are an evaluator implementing FActScore-style atomic factuality for "
        "scientific reasoning graph nodes. For each node, internally perform two "
        "steps: (1) decompose the node text into short atomic factual claims; "
        "(2) judge each atomic fact against only the provided paper evidence. "
        "A fact is supported only if the evidence explicitly states it or directly "
        "entails it. Do not use outside knowledge. If evidence is empty or "
        "insufficient, mark unsupported. Return JSON only."
    )
    user = {
        "instruction": (
            "For each item, return at most "
            f"{max_facts_per_node} atomic facts. Exclude pure graph syntax, source tuple "
            "noise, and non-factual connector text. Preserve scientific qualifiers. "
            "Labels must be exactly supported or unsupported."
        ),
        "output_schema": {
            "items": [
                {
                    "id": "same id as input",
                    "atomic_facts": [
                        {
                            "fact": "atomic factual claim",
                            "label": "supported|unsupported",
                            "reason": "short reason grounded in evidence",
                        }
                    ],
                }
            ]
        },
        "items": build_prompt_items(rows),
    }
    data = client.complete_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        retries=retries,
        sleep=sleep,
    )
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Response JSON lacks items list")

    by_id = {str(item.get("id")): item for item in raw_items if isinstance(item, dict)}
    results = []
    for row in rows:
        item = by_id.get(row["ans_node_key"])
        facts = []
        if item:
            for fact in item.get("atomic_facts") or []:
                if not isinstance(fact, dict):
                    continue
                fact_text = clean_text(fact.get("fact") or "")
                if not fact_text:
                    continue
                label = str(fact.get("label") or "").strip().lower()
                if label not in {"supported", "unsupported"}:
                    label = "unsupported"
                facts.append(
                    {
                        "fact": fact_text,
                        "label": label,
                        "supported": label == "supported",
                        "reason": clean_text(fact.get("reason") or ""),
                    }
                )
        results.append(
            {
                "ans_node_key": row["ans_node_key"],
                "ans_eval_key": row["ans_eval_key"],
                "stage": row["ans_stage"],
                "stage_label": row["ans_stage_label"],
                "claim_id": row.get("claim_id"),
                "paper_spec": row.get("paper_spec"),
                "model": row.get("model"),
                "paper": row.get("paper"),
                "run_id": row.get("run_id"),
                "quality_tier": row.get("quality_tier"),
                "candidate_label": row.get("candidate_label"),
                "graph_json": row.get("graph_json"),
                "node_id": row.get("node_id"),
                "unit_type": row.get("unit_type"),
                "source_tuple": row.get("source_tuple"),
                "node_text": clean_text(row.get("claim_text_for_minicheck") or row.get("raw_node_text") or ""),
                "evidence_empty": not bool(str(row.get("ans_evidence") or "").strip()),
                "atomic_facts": facts[:max_facts_per_node],
                "atomic_fact_count": len(facts[:max_facts_per_node]),
                "supported_fact_count": sum(1 for fact in facts[:max_facts_per_node] if fact["supported"]),
                "status": "ok" if item is not None else "missing_from_model_response",
                "judge_mode": "combined_decompose_and_verify",
            }
        )
    return results


def clone_result(row: Dict[str, Any], facts: List[Dict[str, Any]], *, cache_hit: bool) -> Dict[str, Any]:
    return {
        "ans_node_key": row["ans_node_key"],
        "ans_eval_key": row["ans_eval_key"],
        "stage": row["ans_stage"],
        "stage_label": row["ans_stage_label"],
        "claim_id": row.get("claim_id"),
        "paper_spec": row.get("paper_spec"),
        "model": row.get("model"),
        "paper": row.get("paper"),
        "run_id": row.get("run_id"),
        "quality_tier": row.get("quality_tier"),
        "candidate_label": row.get("candidate_label"),
        "graph_json": row.get("graph_json"),
        "node_id": row.get("node_id"),
        "unit_type": row.get("unit_type"),
        "source_tuple": row.get("source_tuple"),
        "node_text": clean_text(row.get("claim_text_for_minicheck") or row.get("raw_node_text") or ""),
        "evidence_empty": not bool(str(row.get("ans_evidence") or "").strip()),
        "atomic_facts": facts,
        "atomic_fact_count": len(facts),
        "supported_fact_count": sum(1 for fact in facts if fact.get("supported")),
        "status": "ok",
        "cache_hit": cache_hit,
    }


def read_eval_cache(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    cache: Dict[str, List[Dict[str, Any]]] = {}
    if not path.exists():
        return cache
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = row.get("ans_eval_key")
            facts = row.get("atomic_facts")
            if isinstance(key, str) and isinstance(facts, list):
                cache[key] = facts
    return cache


def batched(rows: Sequence[Dict[str, Any]], batch_size: int) -> Iterable[List[Dict[str, Any]]]:
    for idx in range(0, len(rows), batch_size):
        yield list(rows[idx : idx + batch_size])


def run_batch_job(
    *,
    model: str,
    temperature: float,
    timeout: int,
    max_tokens: int,
    api_key_env: str,
    base_url_env: str,
    batch: List[Dict[str, Any]],
    max_facts_per_node: int,
    retries: int,
    sleep: float,
    combined: bool,
) -> List[Dict[str, Any]]:
    client = OpenAICompatClient(
        model=model,
        temperature=temperature,
        timeout=timeout,
        max_tokens=max_tokens,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
    )
    return judge_batch(
        client,
        batch,
        max_facts_per_node=max_facts_per_node,
        retries=retries,
        sleep=sleep,
        combined=combined,
    )


def load_stage_rows(
    *,
    exclude_models: set[str],
    only_stages: Optional[set[str]],
    limit_nodes_per_stage: Optional[int],
    limit_papers_per_stage: Optional[int],
    top_k: int,
    max_evidence_chars: int,
) -> Dict[str, List[Dict[str, Any]]]:
    input_cache: Dict[str, Dict[str, Any]] = {}
    corpus_cache: Dict[str, List[Dict[str, Any]]] = {}
    out: Dict[str, List[Dict[str, Any]]] = {}

    for stage, meta in STAGES.items():
        if only_stages and stage not in only_stages:
            continue
        rows = []
        allowed_papers: Optional[set[str]] = None
        if limit_papers_per_stage is not None:
            all_specs = []
            seen = set()
            for row in read_jsonl(meta["claims"]):
                if row.get("model") in exclude_models:
                    continue
                spec = str(row.get("paper_spec") or "")
                if spec and spec not in seen:
                    seen.add(spec)
                    all_specs.append(spec)
            allowed_papers = set(all_specs[:limit_papers_per_stage])

        for row in read_jsonl(meta["claims"]):
            if row.get("model") in exclude_models:
                continue
            if allowed_papers is not None and row.get("paper_spec") not in allowed_papers:
                continue
            input_path = str(row.get("input_data_path") or "")
            if input_path not in corpus_cache:
                input_data = input_cache.get(input_path)
                if input_data is None:
                    input_data = load_input_data(input_path)
                    input_cache[input_path] = input_data
                corpus_cache[input_path] = evidence_corpus(input_data)
            new_row = dict(row)
            new_row["ans_stage"] = stage
            new_row["ans_stage_label"] = meta["label"]
            new_row["ans_node_key"] = node_key(stage, row)
            new_row["ans_evidence"] = retrieve_evidence(
                row,
                corpus_cache[input_path],
                top_k=top_k,
                max_chars=max_evidence_chars,
            )
            new_row["ans_eval_key"] = eval_key(new_row)
            rows.append(new_row)
            if limit_nodes_per_stage is not None and len(rows) >= limit_nodes_per_stage:
                break
        out[stage] = rows
    return out


def summarize(results: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)

    def add_group(scope: str, key: str, row: Dict[str, Any]) -> None:
        groups[(row["stage"], scope, key)].append(row)

    for row in results:
        add_group("all_nodes", "ALL", row)
        if row.get("unit_type") not in MAIN_SCOPE_EXCLUDE_UNIT_TYPES:
            add_group("main_factual_nodes", "ALL", row)
        add_group("unit_type", str(row.get("unit_type") or "UNKNOWN"), row)
        add_group("model", str(row.get("model") or "UNKNOWN"), row)

    summary_rows = []
    for (stage, scope, key), rows in sorted(groups.items()):
        facts = sum(int(row.get("atomic_fact_count") or 0) for row in rows)
        supported = sum(int(row.get("supported_fact_count") or 0) for row in rows)
        evidence_empty_nodes = sum(1 for row in rows if row.get("evidence_empty"))
        zero_fact_nodes = sum(1 for row in rows if int(row.get("atomic_fact_count") or 0) == 0)
        summary_rows.append(
            {
                "stage": stage,
                "stage_label": STAGES.get(stage, {}).get("label", stage),
                "scope": scope,
                "group": key,
                "nodes_evaluated": len(rows),
                "nodes_with_zero_atomic_facts": zero_fact_nodes,
                "nodes_with_empty_evidence": evidence_empty_nodes,
                "atomic_facts": facts,
                "supported_atomic_facts": supported,
                "unsupported_atomic_facts": facts - supported,
                "ANS": supported / facts if facts else None,
            }
        )

    overall = {
        "metric": "FActScore-style Atomic Node Support (ANS)",
        "definition": "supported atomic facts / total verifiable atomic facts",
        "scope_note": (
            "main_factual_nodes excludes root_common_bridge and graph_node; "
            "all_nodes includes every node that produced atomic facts."
        ),
        "stages": list(STAGES),
        "result_rows": len(results),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    return summary_rows, overall


def write_summary(out_dir: Path, results_path: Path) -> None:
    results = [row for row in read_jsonl(results_path) if row.get("status") == "ok"]
    summary_rows, overall = summarize(results)
    summary_csv = out_dir / "ans_summary.csv"
    fieldnames = [
        "stage",
        "stage_label",
        "scope",
        "group",
        "nodes_evaluated",
        "nodes_with_zero_atomic_facts",
        "nodes_with_empty_evidence",
        "atomic_facts",
        "supported_atomic_facts",
        "unsupported_atomic_facts",
        "ANS",
    ]
    with summary_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)
    write_json(out_dir / "ans_summary.json", {"overall": overall, "rows": summary_rows})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url-env", default="OPENAI_BASE_URL")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-sleep", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--max-evidence-chars", type=int, default=1800)
    parser.add_argument("--max-facts-per-node", type=int, default=6)
    parser.add_argument("--limit-nodes-per-stage", type=int)
    parser.add_argument("--limit-papers-per-stage", type=int)
    parser.add_argument("--only-stage", action="append")
    parser.add_argument(
        "--custom-stage",
        action="append",
        default=[],
        help="Additional stage as name=claims_input.jsonl. Useful for strict merge candidate ANS guards.",
    )
    parser.add_argument("--exclude-model", action="append", default=[])
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument(
        "--combined-judge",
        action="store_true",
        help="Use one API call per batch that performs atomic decomposition and support judgment together.",
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    register_custom_stages(args.custom_stage or [])
    args.out_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.out_dir / "ans_node_results.jsonl"
    errors_path = args.out_dir / "ans_errors.jsonl"
    cache_path = args.out_dir / "ans_eval_cache.jsonl"

    if args.summary_only:
        write_summary(args.out_dir, results_path)
        return 0

    exclude_models = set(DEFAULT_EXCLUDE_MODELS)
    exclude_models.update(args.exclude_model or [])
    only_stages = set(args.only_stage) if args.only_stage else None

    stage_rows = load_stage_rows(
        exclude_models=exclude_models,
        only_stages=only_stages,
        limit_nodes_per_stage=args.limit_nodes_per_stage,
        limit_papers_per_stage=args.limit_papers_per_stage,
        top_k=args.top_k,
        max_evidence_chars=args.max_evidence_chars,
    )
    write_json(
        args.out_dir / "ans_run_config.json",
        {
            "model": args.model,
            "api_key_env": args.api_key_env,
            "base_url_env": args.base_url_env,
            "temperature": args.temperature,
            "batch_size": args.batch_size,
            "top_k": args.top_k,
            "max_evidence_chars": args.max_evidence_chars,
            "max_facts_per_node": args.max_facts_per_node,
            "exclude_models": sorted(exclude_models),
            "limit_nodes_per_stage": args.limit_nodes_per_stage,
            "limit_papers_per_stage": args.limit_papers_per_stage,
            "only_stage": sorted(only_stages) if only_stages else None,
            "judge_mode": "combined_decompose_and_verify" if args.combined_judge else "two_step_decompose_then_verify",
            "stages": {stage: {"rows_loaded": len(rows)} for stage, rows in stage_rows.items()},
        },
    )

    if args.workers < 1:
        raise ValueError("--workers must be >= 1")
    done = read_done_ids(results_path)
    eval_cache = read_eval_cache(cache_path)
    total_loaded = sum(len(rows) for rows in stage_rows.values())
    total_pending = sum(1 for rows in stage_rows.values() for row in rows if row["ans_node_key"] not in done)
    print(
        f"loaded_nodes={total_loaded} pending_nodes={total_pending} cached_eval_keys={len(eval_cache)} out={results_path}",
        flush=True,
    )

    for stage, rows in stage_rows.items():
        pending_all = [row for row in rows if row["ans_node_key"] not in done]
        cached_rows = [row for row in pending_all if row["ans_eval_key"] in eval_cache]
        if cached_rows:
            append_jsonl(
                results_path,
                [clone_result(row, eval_cache[row["ans_eval_key"]], cache_hit=True) for row in cached_rows],
            )
            done.update(row["ans_node_key"] for row in cached_rows)

        pending = [row for row in pending_all if row["ans_node_key"] not in done]
        by_eval_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in pending:
            by_eval_key[row["ans_eval_key"]].append(row)
        representative_rows = [group[0] for group in by_eval_key.values()]

        print(
            f"stage={stage} pending_nodes={len(pending_all)} cache_hits={len(cached_rows)} "
            f"unique_eval_calls={len(representative_rows)}",
            flush=True,
        )
        batches = list(batched(representative_rows, args.batch_size))

        def handle_success(batch_idx: int, judged: List[Dict[str, Any]]) -> None:
            expanded = []
            cache_rows = []
            for judged_row in judged:
                key = judged_row.get("ans_eval_key")
                if not key:
                    # Backward compatibility for rows returned before ans_eval_key
                    source_key = judged_row.get("ans_node_key")
                    source_row = next((row for row in batches[batch_idx - 1] if row["ans_node_key"] == source_key), None)
                    key = source_row["ans_eval_key"] if source_row else None
                if not key:
                    continue
                facts = judged_row.get("atomic_facts") or []
                eval_cache[str(key)] = facts
                cache_rows.append({"ans_eval_key": key, "atomic_facts": facts})
                for original_row in by_eval_key[str(key)]:
                    expanded.append(clone_result(original_row, facts, cache_hit=False))
            append_jsonl(cache_path, cache_rows)
            append_jsonl(results_path, expanded)

        def handle_error(batch_idx: int, batch: List[Dict[str, Any]], exc: Exception) -> None:
            append_jsonl(
                errors_path,
                [
                    {
                        "ans_node_key": row["ans_node_key"],
                        "stage": row["ans_stage"],
                        "claim_id": row.get("claim_id"),
                        "paper_spec": row.get("paper_spec"),
                        "model": row.get("model"),
                        "paper": row.get("paper"),
                        "candidate_label": row.get("candidate_label"),
                        "graph_json": row.get("graph_json"),
                        "node_id": row.get("node_id"),
                        "unit_type": row.get("unit_type"),
                        "status": "api_error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    }
                    for row in batch
                ],
            )
            print(f"stage={stage} batch={batch_idx} ERROR {type(exc).__name__}: {exc}", flush=True)

        if args.workers == 1:
            for batch_idx, batch in enumerate(batches, start=1):
                try:
                    judged = run_batch_job(
                        model=args.model,
                        temperature=args.temperature,
                        timeout=args.timeout,
                        max_tokens=args.max_tokens,
                        api_key_env=args.api_key_env,
                        base_url_env=args.base_url_env,
                        batch=batch,
                        max_facts_per_node=args.max_facts_per_node,
                        retries=args.retries,
                        sleep=args.retry_sleep,
                        combined=args.combined_judge,
                    )
                except Exception as exc:  # noqa: BLE001
                    handle_error(batch_idx, batch, exc)
                    continue
                handle_success(batch_idx, judged)
                if batch_idx == 1 or batch_idx % 10 == 0:
                    completed = len(read_done_ids(results_path))
                    print(f"stage={stage} batch={batch_idx} completed_nodes={completed}", flush=True)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                future_to_batch = {
                    executor.submit(
                        run_batch_job,
                        model=args.model,
                        temperature=args.temperature,
                        timeout=args.timeout,
                        max_tokens=args.max_tokens,
                        api_key_env=args.api_key_env,
                        base_url_env=args.base_url_env,
                        batch=batch,
                        max_facts_per_node=args.max_facts_per_node,
                        retries=args.retries,
                        sleep=args.retry_sleep,
                        combined=args.combined_judge,
                    ): (batch_idx, batch)
                    for batch_idx, batch in enumerate(batches, start=1)
                }
                completed_batches = 0
                for future in concurrent.futures.as_completed(future_to_batch):
                    batch_idx, batch = future_to_batch[future]
                    try:
                        judged = future.result()
                    except Exception as exc:  # noqa: BLE001
                        handle_error(batch_idx, batch, exc)
                    else:
                        handle_success(batch_idx, judged)
                    completed_batches += 1
                    if completed_batches == 1 or completed_batches % 10 == 0:
                        completed = len(read_done_ids(results_path))
                        print(
                            f"stage={stage} completed_batches={completed_batches}/{len(batches)} "
                            f"completed_nodes={completed}",
                            flush=True,
                        )

    write_summary(args.out_dir, results_path)
    print(f"summary={args.out_dir / 'ans_summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
