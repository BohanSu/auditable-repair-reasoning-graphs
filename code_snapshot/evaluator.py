#!/usr/bin/env python3


import os
import sys
import json
import re
import glob
import networkx as nx
import pydot
import openai
import pandas as pd
import urllib.error
import urllib.request
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import unicodedata

dot_parse_lock = threading.Lock()

GREEK_COVERAGE_ALIASES = {
    "α": " alpha ",
    "β": " beta ",
    "γ": " gamma ",
    "δ": " delta ",
    "ε": " epsilon ",
    "ζ": " zeta ",
    "η": " eta ",
    "θ": " theta ",
    "ι": " iota ",
    "κ": " kappa ",
    "λ": " lambda ",
    "μ": " mu ",
    "ν": " nu ",
    "ξ": " xi ",
    "ο": " omicron ",
    "π": " pi ",
    "ρ": " rho ",
    "σ": " sigma ",
    "τ": " tau ",
    "υ": " upsilon ",
    "φ": " phi ",
    "ϕ": " phi ",
    "χ": " chi ",
    "ψ": " psi ",
    "ω": " omega ",
}

SUPERSCRIPT_COVERAGE_TRANS = str.maketrans(
    {
        "ᵃ": "a",
        "ᵇ": "b",
        "ᶜ": "c",
        "ᵈ": "d",
        "ᵉ": "e",
        "ᶠ": "f",
        "ᵍ": "g",
        "ʰ": "h",
        "ⁱ": "i",
        "ʲ": "j",
        "ᵏ": "k",
        "ˡ": "l",
        "ᵐ": "m",
        "ⁿ": "n",
        "ᵒ": "o",
        "ᵖ": "p",
        "ʳ": "r",
        "ˢ": "s",
        "ᵗ": "t",
        "ᵘ": "u",
        "ᵛ": "v",
        "ʷ": "w",
        "ˣ": "x",
        "ʸ": "y",
        "ᶻ": "z",
        "ᴬ": "a",
        "ᴮ": "b",
        "ᴰ": "d",
        "ᴱ": "e",
        "ᴳ": "g",
        "ᴴ": "h",
        "ᴵ": "i",
        "ᴶ": "j",
        "ᴷ": "k",
        "ᴸ": "l",
        "ᴹ": "m",
        "ᴺ": "n",
        "ᴼ": "o",
        "ᴾ": "p",
        "ᴿ": "r",
        "ᵀ": "t",
        "ᵁ": "u",
        "ⱽ": "v",
        "ᵂ": "w",
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
    }
)


def normalize_coverage_text(text: Any) -> str:
    """Normalize scientific symbols for entity-coverage string matching."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).translate(SUPERSCRIPT_COVERAGE_TRANS)
    normalized = "".join(GREEK_COVERAGE_ALIASES.get(ch, ch) for ch in normalized)
    normalized = re.sub(r"[^A-Za-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip().lower()


def get_api_config(model_key: str, model_name: str) -> tuple[str, str]:
    """Read API credentials from environment variables.

    Supports both a shared evaluator endpoint and per-model overrides.
    """
    key_upper = model_key.upper()

    api_key = (
        os.getenv(f"EVAL_API_KEY_{key_upper}")
        or os.getenv(f"API_KEY_{key_upper}")
        or os.getenv("EVAL_API_KEY")
        or os.getenv("API_KEY", "")
    )
    base_url = (
        os.getenv(f"EVAL_BASE_URL_{key_upper}")
        or os.getenv(f"BASE_URL_{key_upper}")
        or os.getenv("EVAL_BASE_URL")
        or os.getenv("BASE_URL", "")
    )
    return api_key, base_url


def get_model_name(model_key: str) -> str:
    """Allow per-model deployment-name overrides via environment variables."""
    return (
        os.getenv(f"EVAL_MODEL_NAME_{model_key.upper()}")
        or os.getenv(f"MODEL_NAME_{model_key.upper()}")
        or EVALUATION_MODELS[model_key]
    )


def _raw_chat_completion(model_key: str, model_name: str, messages: List[Dict[str, str]], extra_params: Optional[Dict[str, Any]] = None) -> str:
    """Fallback raw HTTP call for providers that behave poorly with the SDK."""
    api_key, base_url = get_api_config(model_key, model_name)
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": messages,
    }
    if extra_params:
        payload.update(extra_params)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            # Match the successful manual probe path as closely as possible.
            "User-Agent": "curl/8.7.1",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_eval_request_timeout()) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(detail or str(e)) from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    choices = data.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()

    return raw


def _raw_http_model_keys() -> Set[str]:
    raw = os.getenv("EVAL_RAW_HTTP_MODELS", "gemini,claude")
    return {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }


def _use_raw_http_for_model(model_key: str) -> bool:
    return model_key.lower() in _raw_http_model_keys()


def _suggest_retry_delay(error: Exception, attempt: int) -> float:
    """Honor provider retry hints when available, otherwise use bounded backoff."""
    text = str(error)
    match = re.search(r"'retry_after':\s*(\d+)", text)
    if match:
        return min(max(float(match.group(1)), 1.0), 60.0)
    return min(2.0 * (attempt + 1), 10.0)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _eval_request_timeout() -> int:
    return _env_int("EVAL_REQUEST_TIMEOUT", 120, minimum=5)


def _eval_judge_max_retries() -> int:
    return _env_int("EVAL_JUDGE_MAX_RETRIES", 3, minimum=1)

EVALUATION_MODELS = {
    "o3": "o3",
    "claude": "claude-sonnet-4-20250514-thinking",
    "gemini": "gemini-2.5-pro"
}
# JUDGE SET — inherited verbatim from the ARCHE benchmark (arXiv:2511.12485).
#
# Decision (2026-04-19, sticky): we do NOT upgrade to Claude Sonnet 4.5 /
# Gemini 3.1 Pro, and we do NOT re-run evaluation_outputs/all_evaluation_runs.csv.
# Rationale (see docs/EVALUATION_PROTOCOL.md §9.2):
#
# 1. Protocol fidelity. ARCHE defines the evaluation protocol, including the
#    judge set. Changing judges would make PEARL's teacher baseline numbers
#    incommensurate with ARCHE's published results and any subsequent work
#    that follows the ARCHE protocol.
# 2. Internal consistency. Student and all 5 teachers are scored by the SAME
#    judge set. Whatever bias the judges carry, it applies uniformly — the
#    relative ordering (student vs teacher joint_score) is apples-to-apples,
#    which is the comparison the paper actually claims.
#
# Reviewer defense: "why not upgrade judges?" → "we inherit the ARCHE
# evaluation protocol rather than redefining it; upgrading would make teacher
# numbers non-comparable to the benchmark. We pin snapshots within the
# protocol for reproducibility."
#
# Separate reproducibility TODO (orthogonal to the upgrade question):
#   - "o3" is a floating alias → resolve to o3-YYYY-MM-DD snapshot before
#     camera-ready; record alias→snapshot mapping in eval_env.json.
#   - "gemini-2.5-pro" is a floating alias → same treatment.
#   - "claude-sonnet-4-20250514-thinking" already embeds a date; treat as
#     pinned.
#
# Do NOT silently change this dict. If it must change, also bump
# docs/EVALUATION_PROTOCOL.md §9.2 and docs/MODEL_AND_ARTIFACTS_NOTE.md §1.2
# in the same commit.

clients = {}
for model_key, model_name in EVALUATION_MODELS.items():
    api_key, base_url = get_api_config(model_key, model_name)
    clients[model_key] = openai.OpenAI(
        api_key=api_key,
        base_url=base_url
    )


def _extract_response_text(response: Any) -> str:
    """Normalize provider responses into plain text.

    Some proxy providers return a normal OpenAI SDK object, while others
    occasionally return a plain string payload. Keep evaluator logic stable
    by accepting both shapes here.
    """
    if isinstance(response, str):
        text = response.strip()
        if text.startswith("data:"):
            parts = []
            for line in text.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
            if parts:
                return "".join(parts).strip()
        return text

    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
                else:
                    text = getattr(item, "text", None)
                    if text:
                        parts.append(str(text))
            return "\n".join(parts).strip()

    if hasattr(response, "model_dump"):
        dumped = response.model_dump()
        choices = dumped.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("text"):
                        parts.append(str(item["text"]))
                return "\n".join(parts).strip()

    raise TypeError(f"Unsupported response type: {type(response).__name__}")


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first valid JSON object from noisy model output."""
    candidates = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced_match:
        candidates.append(fenced_match.group(1).strip())

    unquoted_lines = [re.sub(r"^\s*>\s?", "", line) for line in text.splitlines()]
    unquoted_text = "\n".join(unquoted_lines).strip()
    if unquoted_text and unquoted_text not in candidates:
        candidates.append(unquoted_text)

    for candidate in list(candidates):
        start = candidate.find("{")
        if start == -1:
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
                    snippet = candidate[start:idx + 1]
                    try:
                        parsed = json.loads(snippet)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break

    return None


def _classify_model_response_issue(data: Dict[str, Any]) -> Optional[str]:
    """Return a stable issue label when a saved judge response is contaminated."""
    result = str(data.get("result", "")).lower()
    success = data.get("success", None)
    reason = str(data.get("reason", ""))
    raw_response = str(data.get("raw_response", ""))
    combined = f"{reason}\n{raw_response}".lower()

    if result == "error":
        if "json parsing failed" in combined:
            return "json-parse-failed"
        if "insufficient_user_quota" in combined or "额度不足" in combined:
            return "quota"
        if "blocked" in combined:
            return "blocked"
        if "'str' object has no attribute 'choices'" in combined:
            return "sdk-string-bug"
        if "api call failed" in combined:
            return "api-call-failed"
        return "result=error"

    if success is False:
        if "insufficient_user_quota" in combined or "额度不足" in combined:
            return "quota"
        if "blocked" in combined:
            return "blocked"
        if "'str' object has no attribute 'choices'" in combined:
            return "sdk-string-bug"
        if "api call failed" in combined:
            return "api-call-failed"
        return "success=false"

    if "json parsing failed" in combined:
        return "json-parse-failed"

    return None


def _is_provider_health_issue(data: Dict[str, Any]) -> bool:
    issue = _classify_model_response_issue(data)
    if issue in {"quota", "blocked", "api-call-failed"}:
        return True
    combined = f"{data.get('reason', '')}\n{data.get('raw_response', '')}".lower()
    return any(
        marker in combined
        for marker in (
            "insufficient_user_quota",
            "额度不足",
            "rate limit",
            "resource_exhausted",
            "billing",
            "403",
            "429",
        )
    )


def _eval_provider_fail_fast_enabled() -> bool:
    return os.getenv("EVAL_PROVIDER_FAIL_FAST", "").strip().lower() in {"1", "true", "yes", "on"}


def _provider_fail_fast_response(reasoning_id: int, model_key: str, model_name: str, reason: str) -> Dict[str, Any]:
    return {
        "model_key": model_key,
        "model_name": model_name,
        "result": "error",
        "reason": reason,
        "raw_response": "",
        "success": False,
        "provider_fail_fast": True,
    }


def is_eval_dir_clean(eval_dir: Path) -> bool:
    """Treat an eval dir as reusable only if all saved judge responses succeeded."""
    results_file = eval_dir / "evaluation_results.json"
    if not results_file.exists():
        return False

    responses_dir = eval_dir / "responses"
    if not responses_dir.exists():
        return False

    response_files = sorted(responses_dir.glob("reasoning_validation_*_response_*.json"))
    vote_files = sorted(responses_dir.glob("reasoning_validation_*_vote_result.json"))

    for response_file in response_files:
        try:
            data = json.loads(response_file.read_text(encoding="utf-8"))
        except Exception:
            return False
        if _classify_model_response_issue(data):
            return False

    for vote_file in vote_files:
        try:
            vote_data = json.loads(vote_file.read_text(encoding="utf-8"))
        except Exception:
            return False

        if str(vote_data.get("final_result", "")).lower() == "error":
            return False

        model_results = vote_data.get("model_results", {})
        if any(str(result).lower() == "error" for result in model_results.values()):
            return False

    return True


def find_latest_clean_eval_dir(evaluation_outputs_dir: Path) -> Optional[Path]:
    """Return the newest clean eval dir, ignoring stale or contaminated runs."""
    eval_dirs = [d for d in evaluation_outputs_dir.iterdir() if d.is_dir()]
    for eval_dir in sorted(eval_dirs, key=lambda d: d.stat().st_mtime, reverse=True):
        if is_eval_dir_clean(eval_dir):
            return eval_dir
    return None

class GraphEvaluator:
    def __init__(self, output_directory: str, graph_file: str = None):
        self.output_dir = Path(output_directory)
        self.graph_file = Path(graph_file) if graph_file else (self.output_dir / "final_clean_graph.dot")
        self.input_data_file = self.output_dir / "input_data.json"
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        eval_folder_name = f"{Path(output_directory).name}_{timestamp}_evaluation"
        self.eval_dir = self.output_dir / "evaluation_outputs" / eval_folder_name
        self.eval_dir.mkdir(parents=True, exist_ok=True)
        
        (self.eval_dir / "prompts").mkdir(exist_ok=True)
        (self.eval_dir / "responses").mkdir(exist_ok=True)
        (self.eval_dir / "coverage").mkdir(exist_ok=True)
        (self.eval_dir / "accuracy").mkdir(exist_ok=True)
        
        self.standard_edge_types = {
            'deduction-rule', 'deduction-case',
            'induction-case', 'induction-common', 
            'abduction-phenomenon', 'abduction-knowledge'
        }
        
        self.reasoning_pairs = {
            'deductive': ('deduction-rule', 'deduction-case'),
            'inductive': ('induction-case', 'induction-common'),
            'abductive': ('abduction-phenomenon', 'abduction-knowledge')
        }
        
        self.graph = None
        self.input_data = None
        self.core_idea_entities = []
        
        print(f"📁 Eval dir: {self.eval_dir}")

    def load_data(self):
        """Load graph and input data"""
        print("\n🔄     ...")
        
        #      
        if not self.graph_file.exists():
            print(f"❌ Graph file not found: {self.graph_file}")
            return False
        
        try:
            with open(self.graph_file, 'r', encoding='utf-8') as f:
                dot_content = f.read()
            
            #         
            if not dot_content.strip():
                print(f"❌ DOT file is empty: {self.graph_file}")
                return False
            
            #   DOT  -          
            try:
                with dot_parse_lock:
                    graphs = pydot.graph_from_dot_data(dot_content)
                    if not graphs or len(graphs) == 0:
                        print(f"❌ Failed to parse DOT")
                        return False
                    
                    self.graph = graphs[0]
                
                node_count = len(self.graph.get_nodes()) if self.graph.get_nodes() else 0
                print(f"✅ DOT loaded: {node_count} nodes")
                
                if node_count == 0:
                    print(f"❌ DOT has no nodes")
                    return False
                    
            except Exception as parse_error:
                print(f"❌ pydot parse error: {parse_error}")
                print(f"❌ DOT content: {dot_content[:200]}...")
                return False
            
        except Exception as e:
            print(f"❌ Error reading DOT: {e}")
            return False
        
        #       
        if not self.input_data_file.exists():
            print(f"❌ Input data not found: {self.input_data_file}")
            return False
        
        try:
            with open(self.input_data_file, 'r', encoding='utf-8') as f:
                self.input_data = json.load(f)
            print(f"✅ Input data loaded")
        except Exception as e:
            print(f"❌ Error loading input data: {e}")
            return False
        
        return True

    def extract_core_idea_and_entities(self) -> Tuple[str, List[str]]:
        """           idea         -  calculate_coverage_metrics.py    """
        print("\n🔍        idea   ...")
        
        try:
            original_content = self.input_data['introduction']['content']
            
            #           idea -  calculate_coverage_metrics.py     prompt
            idea_extraction_prompt = f"""Please extract the core research idea from this scientific text as a single, complete sentence.

Text: {original_content}

Requirements:
1. The sentence should capture the main research proposal or hypothesis
2. It should be complete and self-contained
3. It should be specific to this research, not a general statement
4. Length can be longer if needed to capture the complete idea

Return only the core research idea sentence, nothing else."""

            #      prompt
            prompt_file = self.eval_dir / "prompts" / "core_idea_extraction_prompt.txt"
            with open(prompt_file, 'w', encoding='utf-8') as f:
                f.write(idea_extraction_prompt)

            idea_messages = [
                {"role": "user", "content": f"You are an expert at identifying core research ideas in scientific papers.\n\n{idea_extraction_prompt}"}
            ]
            core_idea = self._call_model_text_with_retry(
                model_key="o3",
                messages=idea_messages,
                step_name="core_idea_extraction",
            )
            if not core_idea:
                return "", []
            
            response_file = self.eval_dir / "responses" / "core_idea_extraction_response.json"
            with open(response_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "core_idea": core_idea,
                    "raw_response": core_idea
                }, f, ensure_ascii=False, indent=2)
            
            print(f"✅ Core idea: {core_idea}")
            
            #        idea      -     prompt
            entity_extraction_prompt = f"""Extract all key entities from this core research idea sentence.

Core research idea: {core_idea}

Requirements:
1. Extract concrete entities (nouns, technical terms, concepts, methods, materials, etc.)
2. Include both general concepts and specific technical terms
3. Do not include common words like "the", "and", "of", etc.
4. Include compound terms as single entities (e.g., "machine learning" not "machine" and "learning")
5. Do not use entities that are not mentioned in the core research idea
6. Extract 5-15 entities maximum
7. Return as a simple comma-separated list

Example format: entity1, entity2, entity3, entity4

Return only the comma-separated list of entities, nothing else."""

            #      prompt
            prompt_file = self.eval_dir / "prompts" / "entity_extraction_prompt.txt"
            with open(prompt_file, 'w', encoding='utf-8') as f:
                f.write(entity_extraction_prompt)

            entity_messages = [
                {"role": "user", "content": f"You are an expert at extracting key entities from scientific text.\n\n{entity_extraction_prompt}"}
            ]
            entities_text = self._call_model_text_with_retry(
                model_key="o3",
                messages=entity_messages,
                step_name="entity_extraction",
            )
            if not entities_text:
                print("❌ No entities extracted")
                return core_idea, []
            
            #      response
            response_file = self.eval_dir / "responses" / "entity_extraction_response.json"
            with open(response_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "core_idea": core_idea,
                    "entities_text": entities_text,
                    "raw_response": entities_text
                }, f, ensure_ascii=False, indent=2)
            
            #        -  calculate_coverage_metrics.py    
            if entities_text:
                entities = [entity.strip() for entity in entities_text.split(',')]
                entities = [entity for entity in entities if entity]  #     
                print(f"✅ Entities: {entities}")
            else:
                print("❌ No entities extracted")
                return core_idea, []
            
            self.core_idea_entities = entities
            
            return core_idea, entities
            
        except Exception as e:
            print(f"❌ Error extracting core idea: {e}")
            return "", []

    def _get_original_content_from_source(self, source_x: int, source_y: int, source_z: int = None) -> str:
        """           -   3     """
        try:
            #     2     
            if source_z is None:
                #     (x, y) ->       
                if source_x == -1 and source_y == -1:
                    return "[    ]"
                elif source_y == 0:
                    source_z = 0  #      (x, 0, 0)
                else:
                    #                    
                    source_z = source_y
                    source_y = 1  #           
            
            #   3     
            if source_x == 0 and source_y == 0 and source_z == 0:
                # (0, 0, 0)     
                return "[    ]"
            elif source_z == 0 and source_y == 0:
                # (x, 0, 0)     
                sentences = self.input_data['introduction']['sentences']
                for sentence_data in sentences:
                    if sentence_data['idx'] == source_x:
                        return sentence_data['sentence']
                return f"[  {source_x}   ]"
            elif source_z == 0 and source_y != 0:
                # (x, y, 0)     
                sentences = self.input_data['introduction']['sentences']
                for sentence_data in sentences:
                    if sentence_data['idx'] == source_x:
                        if 'viewpoints' in sentence_data and sentence_data['viewpoints']:
                            if len(sentence_data['viewpoints']) >= source_y:
                                content = sentence_data['viewpoints'][source_y - 1]  #      1  
                                return self._remove_reasoning_prefixes(content)
                return f"[    {source_x}-{source_y}   ]"
            else:
                # (x, y, z)     
                sentences = self.input_data['introduction']['sentences']
                for sentence_data in sentences:
                    if sentence_data['idx'] == source_x:
                        if 'references' in sentence_data and sentence_data['references']:
                            #    y     
                            ref_keys = list(sentence_data['references'].keys())
                            if len(ref_keys) >= source_y:
                                ref_id = ref_keys[source_y - 1]  #      1  
                                viewpoints = sentence_data['references'][ref_id]
                                if len(viewpoints) >= source_z:
                                    content = viewpoints[source_z - 1]  #      1  
                                    return self._remove_reasoning_prefixes(content)
                return f"[    {source_x}-{source_y}-{source_z}   ]"
        except Exception as e:
            return f"[    : {e}]"

    def _remove_reasoning_prefixes(self, content: str) -> str:
        """         -       """
        prefixes_to_remove = [
            r'^Deduction reasoning:\s*',
            r'^Induction reasoning:\s*', 
            r'^Abduction reasoning:\s*',
            r'^    :\s*',
            r'^    :\s*',
            r'^    :\s*'
        ]
        
        cleaned_content = content
        for prefix_pattern in prefixes_to_remove:
            cleaned_content = re.sub(prefix_pattern, '', cleaned_content, flags=re.IGNORECASE)
        
        return cleaned_content.strip()

    def find_root_nodes(self) -> List[str]:
        """              """
        #           
        nodes_with_incoming = set()
        for edge in self.graph.get_edges():
            target = edge.get_destination().strip('"')
            if target and target != 'node':
                nodes_with_incoming.add(target)
        
        #               
        root_nodes = []
        for node in self.graph.get_nodes():
            #   FrozenDict  
            name_raw = node.get_name()
            if hasattr(name_raw, 'strip'):
                node_name = name_raw.strip('"')
            else:
                node_name = str(name_raw).strip('"')
            if node_name and node_name != 'node' and node_name not in nodes_with_incoming:
                root_nodes.append(node_name)
        
        print(f"        : {root_nodes}")
        return root_nodes

    def find_connected_nodes(self, root_nodes: List[str]) -> Set[str]:
        """                     """
        connected_nodes = set(root_nodes)
        queue = list(root_nodes)
        
        while queue:
            current_node = queue.pop(0)
            
            #              
            for edge in self.graph.get_edges():
                #   FrozenDict  
                source_raw = edge.get_source()
                target_raw = edge.get_destination()
                
                #            
                if hasattr(source_raw, 'strip'):
                    source = source_raw.strip('"')
                else:
                    source = str(source_raw).strip('"')
                    
                if hasattr(target_raw, 'strip'):
                    target = target_raw.strip('"')
                else:
                    target = str(target_raw).strip('"')
                
                if source == current_node and target not in connected_nodes:
                    connected_nodes.add(target)
                    queue.append(target)
        
        print(f"Connected nodes: {len(connected_nodes)}")
        return connected_nodes

    def extract_entities_from_reasoning_tree(self) -> List[str]:
        """Extract entities from reasoning tree nodes"""
        print("🔍 Extracting entities from reasoning tree...")
        
        if not self.graph:
            print("❌ Graph not loaded")
            return []
        
        #                
        root_nodes = self.find_root_nodes()
        if not root_nodes:
            print("❌ No root nodes found")
            connected_nodes = set()
            for node in self.graph.get_nodes():
                name_raw = node.get_name()
                if hasattr(name_raw, 'strip'):
                    node_name = name_raw.strip('"')
                else:
                    node_name = str(name_raw).strip('"')
                if node_name and node_name != 'node':
                    connected_nodes.add(node_name)
        else:
            connected_nodes = self.find_connected_nodes(root_nodes)
        
        entities = set()
        
        #             
        processed_nodes = 0
        matched_nodes = 0
        for node in self.graph.get_nodes():
            node_name = node.get_name().strip('"')
            if node_name and node_name != 'node' and node_name in connected_nodes:
                processed_nodes += 1
                label = node.get_label()
                if label:
                    label = label.strip('"')
                    
                    #         :       
                    #   1: "Source: (x,y,z)\ncontent"
                    #   2: "(x,y,z) content" 
                    
                    source_x = source_y = source_z = None
                    transcribed_content = ""
                    
                    #     1: "Source: (x,y,z)\ncontent"
                    match = re.match(r'Source:\s*\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)\s*[\n\r]*(.*)', label, re.DOTALL)
                    if match:
                        matched_nodes += 1
                        source_x, source_y, source_z = int(match.group(1)), int(match.group(2)), int(match.group(3))
                        transcribed_content = match.group(4).strip()
                        original_content = self._get_original_content_from_source(source_x, source_y, source_z)
                    else:
                        #     2: "(x,y,z) content"
                        match = re.match(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)\s*(.*)', label)
                        if match:
                            matched_nodes += 1
                            source_x, source_y, source_z = int(match.group(1)), int(match.group(2)), int(match.group(3))
                            transcribed_content = match.group(4).strip()
                            original_content = self._get_original_content_from_source(source_x, source_y, source_z)
                        else:
                            #   2: "(x,y) content"
                            match = re.match(r'\((-?\d+),\s*(-?\d+)\)\s*(.*)', label)
                            if match:
                                matched_nodes += 1
                                source_x, source_y = int(match.group(1)), int(match.group(2))
                                source_z = 0  # default
                                transcribed_content = match.group(3).strip()
                                original_content = self._get_original_content_from_source(source_x, source_y)
                            else:
                                continue  # no match, skip
                    
                    if source_x is None:
                        continue
                    
                    # Debug: first 3 matched nodes
                    if matched_nodes <= 3:
                        print(f"   Debug - node {matched_nodes}: {transcribed_content[:50]}...")
                    
                    # extract entities from original content
                    if original_content and not original_content.startswith('['):
                        normalized_content = normalize_coverage_text(original_content)
                        # 1. single words
                        single_words = re.findall(r'\b[A-Za-z0-9]+\b', normalized_content)
                        # 2. compound terms
                        compound_terms = re.findall(r'\b[A-Za-z0-9]+(?:\s[A-Za-z0-9]+)+\b', normalized_content)
                        
                        all_terms = single_words + compound_terms
                        
                        #            
                        stop_words = {
                            'the', 'and', 'are', 'for', 'with', 'that', 'this', 'can', 'may', 'will', 
                            'has', 'have', 'been', 'more', 'such', 'also', 'used', 'use', 'than', 
                            'these', 'they', 'from', 'into', 'over', 'under', 'their', 'there', 
                            'where', 'when', 'what', 'which', 'while', 'through', 'but', 'not',
                            'all', 'any', 'both', 'each', 'few', 'most', 'other', 'some', 'such',
                            'only', 'own', 'same', 'so', 'then', 'very', 'just', 'now', 'how',
                            'its', 'our', 'out', 'way', 'many', 'could', 'would', 'should'
                        }
                        
                        filtered_words = [
                            word.strip() for word in all_terms 
                            if len(word.strip()) >= 2 and 
                            word.lower().strip() not in stop_words
                        ]
                        entities.update(filtered_words)
                        
                        # Debug:               
                        if matched_nodes <= 3 and len(filtered_words) > 0:
                            print(f"           : {filtered_words[:5]}...")
        
        print(f"   Debug -       : {processed_nodes},      : {matched_nodes}")
        
        entities_list = list(entities)
        print(f"           : {len(entities_list)}  ")
        print(f"     : {entities_list[:10]}{'...' if len(entities_list) > 10 else ''}")
        
        return entities_list

    def calculate_entity_coverage(self) -> float:
        """Calculate entity coverage rate"""
        print("\n📊 Calculating entity coverage...")
        
        if not self.core_idea_entities:
            print("❌ No core entities")
            return 0.0
        
        # extract entities from reasoning tree
        reasoning_entities = self.extract_entities_from_reasoning_tree()
        
        if not reasoning_entities:
            print("❌ No reasoning entities")
            return 0.0
        
        # case-insensitive matching with scientific symbol normalization
        core_entities_lower = [normalize_coverage_text(entity) for entity in self.core_idea_entities]
        reasoning_entities_lower = [normalize_coverage_text(entity) for entity in reasoning_entities]
        
        covered_entities = []
        for core_entity in core_entities_lower:
            for reasoning_entity in reasoning_entities_lower:
                # substring match
                if core_entity in reasoning_entity or reasoning_entity in core_entity:
                    covered_entities.append(core_entity)
                    break
        
        coverage_rate = len(covered_entities) / len(self.core_idea_entities) * 100
        
        print(f"       : {self.core_idea_entities}")
        print(f"        : {reasoning_entities[:10]}{'...' if len(reasoning_entities) > 10 else ''}")
        print(f"        : {covered_entities}")
        print(f"      : {coverage_rate:.1f}% ({len(covered_entities)}/{len(self.core_idea_entities)})")
        
        return coverage_rate

    def calculate_entity_coverage_from_correct_reasoning(self, reasoning_validation_results: Dict) -> float:
        """               """
        print("\n📊                ...")
        
        if not self.core_idea_entities:
            print("❌ No core entities")
            return 0.0
        
        #           
        all_steps, valid_steps = self.filter_valid_reasoning_steps()
        
        #              
        correct_step_nodes = set()
        
        #               
        step_id = 1
        all_step_targets = []
        
        #          -  filter_valid_reasoning_steps     
        edges_by_target = {}
        for edge in self.graph.get_edges():
            #   FrozenDict  
            source_raw = edge.get_source()
            target_raw = edge.get_destination()
            label_raw = edge.get_label()
            
            if hasattr(source_raw, 'strip'):
                source = source_raw.strip('"')
            else:
                source = str(source_raw).strip('"')
                
            if hasattr(target_raw, 'strip'):
                target = target_raw.strip('"')
            else:
                target = str(target_raw).strip('"')
                
            if label_raw:
                if hasattr(label_raw, 'strip'):
                    edge_label = label_raw.strip('"')
                else:
                    edge_label = str(label_raw).strip('"')
            else:
                edge_label = ""
            
            #         
            if edge_label in self.standard_edge_types:
                if target not in edges_by_target:
                    edges_by_target[target] = []
                edges_by_target[target].append((source, edge_label))
        
        #              
        for target_node, edges in edges_by_target.items():
            source_nodes = [source for source, _ in edges]
            edge_types = [label for _, label in edges]
            all_step_targets.append((target_node, source_nodes, edge_types))
        
        #                
        for i, (target_node, source_nodes, edge_types) in enumerate(all_step_targets, 1):
            step_result = reasoning_validation_results.get(str(i), reasoning_validation_results.get(i))
            if step_result == "correct":
                #                  
                correct_step_nodes.add(target_node)
                correct_step_nodes.update(source_nodes)
                print(f"          {i}: {target_node} <- {source_nodes}")
        
        if not correct_step_nodes:
            print("❌ No correct step nodes")
            return 0.0
        
        print(f"             : {len(correct_step_nodes)}  ")
        
        #                
        reasoning_entities = set()
        processed_nodes = 0
        
        for node in self.graph.get_nodes():
            #   FrozenDict  
            name_raw = node.get_name()
            if hasattr(name_raw, 'strip'):
                node_name = name_raw.strip('"')
            else:
                node_name = str(name_raw).strip('"')
                
            if node_name and node_name != 'node' and node_name in correct_step_nodes:
                processed_nodes += 1
                #   FrozenDict  
                label_raw = node.get_label()
                if label_raw:
                    if hasattr(label_raw, 'strip'):
                        label = label_raw.strip('"')
                    else:
                        label = str(label_raw).strip('"')
                else:
                    label = None
                
                if label:
                    #              -        
                    source_x = source_y = source_z = None
                    
                    #       
                    match = re.match(r'Source:\s*\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)\s*[\n\r]*(.*)', label, re.DOTALL)
                    if match:
                        source_x, source_y, source_z = int(match.group(1)), int(match.group(2)), int(match.group(3))
                        original_content = self._get_original_content_from_source(source_x, source_y, source_z)
                    else:
                        match = re.match(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)\s*(.*)', label)
                        if match:
                            source_x, source_y, source_z = int(match.group(1)), int(match.group(2)), int(match.group(3))
                            original_content = self._get_original_content_from_source(source_x, source_y, source_z)
                        else:
                            match = re.match(r'\((-?\d+),\s*(-?\d+)\)\s*(.*)', label)
                            if match:
                                source_x, source_y = int(match.group(1)), int(match.group(2))
                                original_content = self._get_original_content_from_source(source_x, source_y)
                            else:
                                continue
                    
                    #           
                    if original_content and not original_content.startswith('['):
                        normalized_content = normalize_coverage_text(original_content)
                        #       
                        single_words = re.findall(r'\b[A-Za-z0-9]+\b', normalized_content)
                        #         
                        compound_terms = re.findall(r'\b[A-Za-z0-9]+(?:\s[A-Za-z0-9]+)+\b', normalized_content)
                        
                        all_terms = single_words + compound_terms
                        
                        #      
                        stop_words = {
                            'the', 'and', 'are', 'for', 'with', 'that', 'this', 'can', 'may', 'will', 
                            'has', 'have', 'been', 'more', 'such', 'also', 'used', 'use', 'than', 
                            'these', 'they', 'from', 'into', 'over', 'under', 'their', 'there', 
                            'where', 'when', 'what', 'which', 'while', 'through', 'but', 'not',
                            'all', 'any', 'both', 'each', 'few', 'most', 'other', 'some', 'such',
                            'only', 'own', 'same', 'so', 'then', 'very', 'just', 'now', 'how',
                            'its', 'our', 'out', 'way', 'many', 'could', 'would', 'should'
                        }
                        
                        filtered_words = [
                            word.strip() for word in all_terms 
                            if len(word.strip()) >= 2 and 
                            word.lower().strip() not in stop_words
                        ]
                        reasoning_entities.update(filtered_words)
        
        if not reasoning_entities:
            print("❌ No reasoning entities from correct steps")
            return 0.0
        
        #                      
        core_entities_lower = [normalize_coverage_text(entity) for entity in self.core_idea_entities]
        reasoning_entities_lower = [normalize_coverage_text(entity) for entity in reasoning_entities]
        
        covered_entities = []
        for core_entity in core_entities_lower:
            for reasoning_entity in reasoning_entities_lower:
                #           
                if core_entity in reasoning_entity or reasoning_entity in core_entity:
                    covered_entities.append(core_entity)
                    break
        
        coverage_rate = len(covered_entities) / len(self.core_idea_entities) * 100
        
        print(f"       : {self.core_idea_entities}")
        print(f"         : {list(reasoning_entities)[:10]}{'...' if len(reasoning_entities) > 10 else ''}")
        print(f"        : {covered_entities}")
        print(f"      : {coverage_rate:.1f}% ({len(covered_entities)}/{len(self.core_idea_entities)})")
        
        return coverage_rate

    def filter_valid_reasoning_steps(self) -> Tuple[List[Tuple], List[Tuple]]:
        """             -                      """
        print("\n🔍            ...")
        
        #          -   pydot          
        edges_by_target = {}
        non_standard_edges = []  #       
        
        for edge in self.graph.get_edges():
            #   FrozenDict  
            source_raw = edge.get_source()
            target_raw = edge.get_destination()
            label_raw = edge.get_label()
            
            #            
            if hasattr(source_raw, 'strip'):
                source = source_raw.strip('"')
            else:
                source = str(source_raw).strip('"')
                
            if hasattr(target_raw, 'strip'):
                target = target_raw.strip('"')
            else:
                target = str(target_raw).strip('"')
                
            if label_raw:
                if hasattr(label_raw, 'strip'):
                    edge_label = label_raw.strip('"')
                else:
                    edge_label = str(label_raw).strip('"')
            else:
                edge_label = ""
            
            #          
            if target not in edges_by_target:
                edges_by_target[target] = []
            
            #         
            if edge_label not in self.standard_edge_types:
                #                    
                original_label = edge_label
                if edge_label == "":
                    edge_label = "unlabeled_edge"  #     
                elif "style=dashed" in str(edge_label) or edge_label == "dashed" or edge_label == "style=dashed":
                    edge_label = "dashed_edge"  #    
                elif edge_label == "solid" or "style=solid" in str(edge_label):
                    edge_label = "unlabeled_solid_edge"  #         
                else:
                    edge_label = f"invalid_edge:{edge_label}"  #      
                
                non_standard_edges.append((source, target, edge_label))
                if original_label:
                    print(f"   ⚠️ Non-standard edge: {source} -> {target} [mapped: '{original_label}' -> {edge_label}]")
                else:
                    print(f"   ⚠️ Non-standard edge: {source} -> {target} [unlabeled -> {edge_label}]")
            
            edges_by_target[target].append((source, edge_label))
        
        if non_standard_edges:
            print(f"   ⚠️ Found {len(non_standard_edges)} non-standard edges")
        
        #                  
        all_steps = []
        valid_steps = []
        
        for target_node, edges in edges_by_target.items():
            #                         
            source_nodes = [source for source, _ in edges]
            edge_types = [label for _, label in edges]
            
            #           
            step_info = (target_node, source_nodes, edge_types)
            all_steps.append(step_info)
            
            #             
            has_non_standard_edges = any(edge_type not in self.standard_edge_types for edge_type in edge_types)
            
            #         
            if has_non_standard_edges:
                #                 
                non_standard_types = [et for et in edge_types if et not in self.standard_edge_types]
                print(f"   ❌ Invalid step (non-standard edge): {target_node} <- {non_standard_types}")
            elif len(edges) == 1:
                #               LLM  
                print(f"   ❌ Invalid step (single edge): {target_node} <- {edge_types}")
            elif len(edges) >= 2:
                #           
                if self._validate_edge_pairing(edge_types):
                    valid_steps.append(step_info)
                    print(f"   ✅ Valid step (LLM judge): {target_node} <- {edge_types}")
                else:
                    print(f"   ❌ Invalid step (bad pairing): {target_node} <- {edge_types}")
        
        print(f"✅ Total steps: {len(all_steps)}")
        print(f"✅ Valid steps: {len(valid_steps)}") 
        print(f"❌ Invalid steps: {len(all_steps) - len(valid_steps)}")
        
        return all_steps, valid_steps

    def _validate_edge_pairing(self, edge_types: List[str]) -> bool:
        """            -  d_metrics_v3_fixed.py    """
        edge_set = set(edge_types)
        edge_counts = {edge_type: edge_types.count(edge_type) for edge_type in edge_set}
        
        #   deductive         1 rule + 1 case
        if 'deduction-rule' in edge_set and 'deduction-case' in edge_set:
            if (edge_counts.get('deduction-rule', 0) == 1 and 
                edge_counts.get('deduction-case', 0) == 1 and
                len(edge_types) == 2):
                return True
        
        #   abductive         1 phenomenon + 1 knowledge
        if 'abduction-phenomenon' in edge_set and 'abduction-knowledge' in edge_set:
            if (edge_counts.get('abduction-phenomenon', 0) == 1 and 
                edge_counts.get('abduction-knowledge', 0) == 1 and
                len(edge_types) == 2):
                return True
        
        #   inductive      1 common +   1 case     case 
        if 'induction-common' in edge_set and 'induction-case' in edge_set:
            if (edge_counts.get('induction-common', 0) == 1 and 
                edge_counts.get('induction-case', 0) >= 1 and
                edge_counts.get('induction-common', 0) + edge_counts.get('induction-case', 0) == len(edge_types)):
                return True
        
        return False

    def _get_reasoning_type(self, edge_types: List[str]) -> str:
        """            -  d_metrics_v3_fixed.py    """
        edge_set = set(edge_types)
        
        for reasoning_type, (type1, type2) in self.reasoning_pairs.items():
            if type1 in edge_set and type2 in edge_set:
                return reasoning_type
        
        return "unknown"
    
    def _remove_reasoning_prefixes(self, text: str) -> str:
        """                 -  d_metrics_v3_fixed.py    """
        reasoning_prefixes = [
            "Deduction reasoning: ", "Induction reasoning: ", "Abduction reasoning: ",
            "deduction-reasoning: ", "induction-reasoning: ", "abduction-reasoning: ",
            "Phenomenon: ", "Currently there is evidence that ", "Currently there is knowledge that ",
            "Currently, ", "Currently "
        ]
        
        for prefix in reasoning_prefixes:
            if text.startswith(prefix):
                return text[len(prefix):].strip()
        
        return text

    def _get_original_content_from_source_with_node(self, source_x: int, source_y: int, source_z: int = None, node_name: str = None) -> Tuple[str, bool]:
        """            -             3     """
        try:
            #     2     
            if source_z is None:
                #     (x, y) ->       
                if source_x == -1 and source_y == -1:
                    #          DOT       
                    if node_name:
                        #  pydot      
                        for node in self.graph.get_nodes():
                            #   FrozenDict  
                            name_raw = node.get_name()
                            if hasattr(name_raw, 'strip'):
                                current_node_name = name_raw.strip('"')
                            else:
                                current_node_name = str(name_raw).strip('"')
                            if current_node_name == node_name:
                                #   FrozenDict  
                                label_raw = node.get_label()
                                if label_raw:
                                    if hasattr(label_raw, 'strip'):
                                        node_label = label_raw.strip('"')
                                    else:
                                        node_label = str(label_raw).strip('"')
                                else:
                                    node_label = ''
                                #                
                                if '|' in node_label:
                                    transcription = node_label.split('|', 1)[1].strip()
                                    #                 
                                    transcription = self._remove_reasoning_prefixes(transcription)
                                    return transcription, True
                                else:
                                    return node_label, True
                    return "Implicit information/background knowledge", True
                elif source_y == 0:
                    source_z = 0  #      (x, 0, 0)
                else:
                    #                    
                    source_z = source_y
                    source_y = 1  #           
            
            #   3     
            if source_x == 0 and source_y == 0 and source_z == 0:
                # (0, 0, 0)     
                if node_name:
                    #  pydot            
                    for node in self.graph.get_nodes():
                        #   FrozenDict  
                        name_raw = node.get_name()
                        if hasattr(name_raw, 'strip'):
                            current_node_name = name_raw.strip('"')
                        else:
                            current_node_name = str(name_raw).strip('"')
                        if current_node_name == node_name:
                            #   FrozenDict  
                            label_raw = node.get_label()
                            if label_raw:
                                if hasattr(label_raw, 'strip'):
                                    node_label = label_raw.strip('"')
                                else:
                                    node_label = str(label_raw).strip('"')
                            else:
                                node_label = ''
                            #                
                            #       
                            transcription = node_label
                            
                            #   1: "Source: (x,y,z)\ncontent"
                            match = re.match(r'Source:\s*\(.*?\)\s*[\n\r]*(.*)', node_label, re.DOTALL)
                            if match:
                                transcription = match.group(1).strip()
                            else:
                                #   2: "(x,y,z) content"
                                match = re.match(r'\(.*?\)\s*(.*)', node_label)
                                if match:
                                    transcription = match.group(1).strip()
                            
                            transcription = self._remove_reasoning_prefixes(transcription)
                            return transcription, True
                return "Supplementary content/background knowledge", True
            elif source_z == 0 and source_y == 0:
                # (x, 0, 0)     
                sentences = self.input_data['introduction']['sentences']
                for sentence_data in sentences:
                    if sentence_data['idx'] == source_x:
                        return sentence_data['sentence'], True
                return f"Sentence {source_x} not found", False
            elif source_z == 0 and source_y != 0:
                # (x, y, 0)     
                sentences = self.input_data['introduction']['sentences']
                for sentence_data in sentences:
                    if sentence_data['idx'] == source_x:
                        if 'viewpoints' in sentence_data and sentence_data['viewpoints']:
                            if len(sentence_data['viewpoints']) >= source_y:
                                content = sentence_data['viewpoints'][source_y - 1]  #      1  
                                return self._remove_reasoning_prefixes(content), True
                return f"Original viewpoint {source_x}-{source_y} not found", False
            else:
                # (x, y, z)     
                sentences = self.input_data['introduction']['sentences']
                for sentence_data in sentences:
                    if sentence_data['idx'] == source_x:
                        if 'references' in sentence_data and sentence_data['references']:
                            #    y     
                            ref_keys = list(sentence_data['references'].keys())
                            if len(ref_keys) >= source_y:
                                ref_id = ref_keys[source_y - 1]  #      1  
                                viewpoints = sentence_data['references'][ref_id]
                                if len(viewpoints) >= source_z:
                                    content = viewpoints[source_z - 1]  #      1  
                                    return self._remove_reasoning_prefixes(content), True
                return f"Reference viewpoint {source_x}-{source_y}-{source_z} not found", False
                
        except Exception as e:
            print(f"❌ Error: {e}")
            return "Error retrieving content", False

    def generate_reasoning_validation_prompts_for_steps(self, steps: List[Tuple]) -> List[Dict]:
        """          prompts -  d_metrics_v3_fixed.py    """
        prompts = []
        
        def get_node_label(node_name):
            """ pydot        """
            for node in self.graph.get_nodes():
                #   FrozenDict  
                name_raw = node.get_name()
                if hasattr(name_raw, 'strip'):
                    current_node_name = name_raw.strip('"')
                else:
                    current_node_name = str(name_raw).strip('"')
                if current_node_name == node_name:
                    #   FrozenDict  
                    label_raw = node.get_label()
                    if label_raw:
                        if hasattr(label_raw, 'strip'):
                            return label_raw.strip('"')
                        else:
                            return str(label_raw).strip('"')
                    else:
                        return ''
            return ''
        
        def get_edge_label(source_node, target_node):
            """ pydot       """
            for edge in self.graph.get_edges():
                #   FrozenDict  
                source_raw = edge.get_source()
                target_raw = edge.get_destination()
                label_raw = edge.get_label()
                
                #            
                if hasattr(source_raw, 'strip'):
                    edge_source = source_raw.strip('"')
                else:
                    edge_source = str(source_raw).strip('"')
                    
                if hasattr(target_raw, 'strip'):
                    edge_target = target_raw.strip('"')
                else:
                    edge_target = str(target_raw).strip('"')
                
                if edge_source == source_node and edge_target == target_node:
                    if label_raw:
                        if hasattr(label_raw, 'strip'):
                            return label_raw.strip('"')
                        else:
                            return str(label_raw).strip('"')
                    else:
                        return 'unknown'
            return 'unknown'
        
        for i, (target_node, source_nodes, edge_types) in enumerate(steps, 1):
            try:
                #          
                target_label = get_node_label(target_node)
                
                #         
                #   1: "Source: (x,y,z)\ncontent"
                target_source_match = re.search(r'Source:\s*\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', target_label)
                if target_source_match:
                    target_source_x, target_source_y, target_source_z = int(target_source_match.group(1)), int(target_source_match.group(2)), int(target_source_match.group(3))
                    target_content, _ = self._get_original_content_from_source_with_node(target_source_x, target_source_y, target_source_z, target_node)
                else:
                    #   2: "(x,y,z) content"
                    target_source_match = re.search(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', target_label)
                    if target_source_match:
                        target_source_x, target_source_y, target_source_z = int(target_source_match.group(1)), int(target_source_match.group(2)), int(target_source_match.group(3))
                        target_content, _ = self._get_original_content_from_source_with_node(target_source_x, target_source_y, target_source_z, target_node)
                    else:
                        #   3: "(x,y) content" (     )
                        target_source_match = re.search(r'\((-?\d+),\s*(-?\d+)\)', target_label)
                        if target_source_match:
                            target_source_a, target_source_b = int(target_source_match.group(1)), int(target_source_match.group(2))
                            target_content, _ = self._get_original_content_from_source_with_node(target_source_a, target_source_b, node_name=target_node)
                        else:
                            target_content = target_label
                
                #                
                source_contents = []
                actual_edge_types = []
                
                for source_node in source_nodes:
                    source_label = get_node_label(source_node)
                    
                    #         
                    #   1: "Source: (x,y,z)\ncontent"
                    source_match = re.search(r'Source:\s*\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', source_label)
                    if source_match:
                        source_x, source_y, source_z = int(source_match.group(1)), int(source_match.group(2)), int(source_match.group(3))
                        content, _ = self._get_original_content_from_source_with_node(source_x, source_y, source_z, source_node)
                        source_contents.append(content)
                    else:
                        #   2: "(x,y,z) content"
                        source_match = re.search(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', source_label)
                        if source_match:
                            source_x, source_y, source_z = int(source_match.group(1)), int(source_match.group(2)), int(source_match.group(3))
                            content, _ = self._get_original_content_from_source_with_node(source_x, source_y, source_z, source_node)
                            source_contents.append(content)
                        else:
                            #   3: "(x,y) content" (     )
                            source_match = re.search(r'\((-?\d+),\s*(-?\d+)\)', source_label)
                            if source_match:
                                source_a, source_b = int(source_match.group(1)), int(source_match.group(2))
                                content, _ = self._get_original_content_from_source_with_node(source_a, source_b, node_name=source_node)
                                source_contents.append(content)
                            else:
                                source_contents.append(source_label)
                    
                    #    source_node target_node      
                    edge_type = get_edge_label(source_node, target_node)
                    actual_edge_types.append(edge_type)
                
                #              
                reasoning_type = self._get_reasoning_type(actual_edge_types)
                
                #      premise   -        
                premise_descriptions = []
                
                for content, edge_type in zip(source_contents, actual_edge_types):
                    if reasoning_type == "deductive":
                        if edge_type == "deduction-rule":
                            premise_descriptions.append(f"General principle/rule ({edge_type}): {content}")
                        elif edge_type == "deduction-case":
                            premise_descriptions.append(f"Specific observation/case ({edge_type}): {content}")
                    elif reasoning_type == "inductive":
                        if edge_type == "induction-case":
                            premise_descriptions.append(f"Specific case/observation ({edge_type}): {content}")
                        elif edge_type == "induction-common":
                            premise_descriptions.append(f"Common pattern/generalization ({edge_type}): {content}")
                    elif reasoning_type == "abductive":
                        if edge_type == "abduction-phenomenon":
                            premise_descriptions.append(f"Observed phenomenon ({edge_type}): {content}")
                        elif edge_type == "abduction-knowledge":
                            premise_descriptions.append(f"Background knowledge ({edge_type}): {content}")
                    else:
                        premise_descriptions.append(f"Supporting evidence ({edge_type}): {content}")
                
                #     prompt -  d_metrics_v3_fixed.py     JSON  
                validation_prompt = f"""Please evaluate this {reasoning_type} reasoning step for logical correctness.

REASONING STRUCTURE:
{chr(10).join([f"{j+1}. {desc}" for j, desc in enumerate(premise_descriptions)])}

CONCLUSION:
{target_content}

EVALUATION CRITERIA:
1. Logical validity: Does the conclusion logically follow from the premises?
2. Scientific soundness: Is the reasoning scientifically appropriate?
3. Completeness: Are the premises sufficient to support the conclusion?
4. Consistency: Is the reasoning internally consistent?

Please respond in JSON format:
{{
    "result": "correct" or "wrong",
    "reason": "Brief explanation of your evaluation focusing on logical structure and scientific validity"
}}"""

                #   prompt
                prompt_file = self.eval_dir / "prompts" / f"reasoning_validation_{i:03d}_prompt.txt"
                with open(prompt_file, 'w', encoding='utf-8') as f:
                    f.write(validation_prompt)
                
                #   prompt  
                prompt_info = {
                    "reasoning_id": i,
                    "target_node": target_node,
                    "source_nodes": source_nodes,
                    "edge_types": edge_types,
                    "actual_edge_types": actual_edge_types,
                    "reasoning_type": reasoning_type,
                    "target_content": target_content,
                    "source_contents": source_contents,
                    "premise_descriptions": premise_descriptions,
                    "prompt_file": str(prompt_file),
                    "validation_prompt": validation_prompt
                }
                
                prompts.append(prompt_info)
                print(f"   ✅ Generated prompt {i}: {reasoning_type}")
                
            except Exception as e:
                print(f"❌ Error generating prompt {i}: {e}")
                continue
        
        return prompts

    def _call_model_text_with_retry(
        self,
        model_key: str,
        messages: List[Dict[str, str]],
        step_name: str,
        max_retries: int = 3,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Call a single model for plain-text output with retries and fallback HTTP path."""
        actual_model_name = get_model_name(model_key)
        create_params = {
            "model": actual_model_name,
            "messages": messages,
        }
        if extra_params:
            create_params.update(extra_params)

        last_error = None
        for attempt in range(max_retries):
            try:
                if _use_raw_http_for_model(model_key):
                    result = _raw_chat_completion(model_key, actual_model_name, messages, extra_params)
                else:
                    response = clients[model_key].chat.completions.create(**create_params)
                    result = _extract_response_text(response)

                response_file = self.eval_dir / "responses" / f"{step_name}_response.txt"
                with open(response_file, 'w', encoding='utf-8') as f:
                    f.write(result)

                return result
            except Exception as e:
                last_error = e
                print(f"❌ API error (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(_suggest_retry_delay(e, attempt))

        error_file = self.eval_dir / "responses" / f"{step_name}_error.txt"
        with open(error_file, 'w', encoding='utf-8') as f:
            f.write(f"API error: {last_error}")
        return None

    def _validate_single_model(self, model_key: str, model_name: str, prompt_info: Dict) -> Dict:
        """            -  d_metrics_v3_fixed.py    """
        reasoning_id = prompt_info['reasoning_id']
        validation_prompt = prompt_info['validation_prompt']
        actual_model_name = get_model_name(model_key)
        
        #      -  d_metrics_v3_fixed.py    
        system_message = "You are an expert at evaluating logical reasoning in scientific contexts. Always respond in valid JSON format."
        
        #         -  d_metrics_v3_fixed.py
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": validation_prompt}
        ]
        if _use_raw_http_for_model(model_key):
            # Some OpenAI-compatible proxy routes reject separate system-role
            # messages for non-OpenAI judge models. Keep the judge instruction
            # unchanged, but send it as one user message.
            messages = [{"role": "user", "content": f"{system_message}\n\n{validation_prompt}"}]

        create_params = {
            "model": actual_model_name,
            "messages": messages
        }

        #             -  d_metrics_v3_fixed.py
        if "gemini" in model_name.lower():
            create_params.update({
                "temperature": 0.1
            })
        elif "o3" in model_name.lower() or model_key == "claude":
            pass
        else:
            create_params.update({
                "temperature": 0,
                "max_tokens": 200
            })

        max_attempts = _eval_judge_max_retries()
        last_error_reason = "Unknown error"
        last_raw_response = ""

        for attempt in range(max_attempts):
            try:
                if attempt == 0:
                    print(f"      🔄 {model_name} validating step {reasoning_id}...")
                else:
                    print(f"      🔁 {model_name} retry {attempt + 1}/{max_attempts} for step {reasoning_id}...")

                if _use_raw_http_for_model(model_key):
                    result_text = _raw_chat_completion(
                        model_key,
                        actual_model_name,
                        messages,
                        {"temperature": 0.1} if model_key == "gemini" else None,
                    )
                else:
                    response = clients[model_key].chat.completions.create(**create_params)
                    result_text = _extract_response_text(response)

                last_raw_response = result_text
                result_json = _extract_json_object(result_text)
                if result_json is not None:
                    result = str(result_json.get("result", "error")).lower()
                    reason = str(result_json.get("reason", "No reason provided"))
                    if result_text.strip() != json.dumps(result_json, ensure_ascii=False):
                        print(f"      🔧 {model_name} JSON cleaned")

                    model_response = {
                        "model_key": model_key,
                        "model_name": model_name,
                        "result": result,
                        "reason": reason,
                        "raw_response": result_text,
                        "success": True
                    }

                    print(f"      ✅ {model_name}: {result}")
                    return model_response

                print(f"      ❌ {model_name} JSON parse failed")
                print(f"      📝 Raw response: {result_text[:200]}...")
                last_error_reason = "JSON parsing failed"
            except Exception as e:
                last_error_reason = f"API call failed: {e}"
                print(f"      ❌ {model_name} API error: {e}")

            if attempt < max_attempts - 1:
                time.sleep(_suggest_retry_delay(Exception(last_error_reason), attempt))

        return {
            "model_key": model_key,
            "model_name": model_name,
            "result": "error",
            "reason": last_error_reason,
            "raw_response": last_raw_response,
            "success": False if last_error_reason.startswith("API call failed:") else True
        }

    def _vote_on_results(self, model_results: Dict[str, str]) -> Tuple[Dict, str]:
        """             -  d_metrics_v3_fixed.py    """
        #       
        vote_counts = {}
        for result in model_results.values():
            vote_counts[result] = vote_counts.get(result, 0) + 1
        
        #       
        vote_breakdown = {
            "votes": model_results,
            "counts": vote_counts,
            "total_models": len(model_results)
        }
        
        # Preserve strict 3-judge protocol: any judge failure makes the step invalid.
        if "error" in vote_counts:
            final_result = "error"
            vote_breakdown["decision"] = "At least one judge failed"
            return vote_breakdown, final_result

        valid_results = vote_counts

        if len(valid_results) == 1:
            #         
            final_result = list(valid_results.keys())[0]
            vote_breakdown["decision"] = f"Unanimous: {final_result}"
        else:
            #             
            max_votes = max(valid_results.values())
            winners = [result for result, count in valid_results.items() if count == max_votes]
            
            if len(winners) == 1:
                final_result = winners[0]
                vote_breakdown["decision"] = f"Majority: {final_result} ({max_votes}/{len(model_results)})"
            else:
                #          correct -  d_metrics_v3_fixed.py    
                if "correct" in winners:
                    final_result = "correct"
                    vote_breakdown["decision"] = f"Tie broken in favor of 'correct'"
                else:
                    final_result = winners[0]  #     tie-breaking  
                    vote_breakdown["decision"] = f"Tie: defaulted to {final_result}"
        
        return vote_breakdown, final_result

    def _write_reasoning_model_response(self, reasoning_id: int, model_key: str, model_response: Dict[str, Any]) -> None:
        response_file = self.eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_response_{model_key}.json"
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(model_response, f, ensure_ascii=False, indent=2)

    def _fill_provider_fail_fast_results(
        self,
        prompts: List[Dict[str, Any]],
        all_model_results: Dict[int, Dict[str, Dict[str, Any]]],
        reason: str,
    ) -> None:
        for prompt_info in prompts:
            reasoning_id = prompt_info['reasoning_id']
            if reasoning_id not in all_model_results:
                all_model_results[reasoning_id] = {}
            for model_key, model_name in EVALUATION_MODELS.items():
                if model_key in all_model_results[reasoning_id]:
                    continue
                model_response = _provider_fail_fast_response(
                    reasoning_id,
                    model_key,
                    model_name,
                    reason,
                )
                all_model_results[reasoning_id][model_key] = model_response
                self._write_reasoning_model_response(reasoning_id, model_key, model_response)

    def validate_reasoning_steps_with_llm(self) -> Tuple[float, Dict]:
        """  LLM           -  d_metrics_v3_fixed.py    """
        print("\n🔍          ...")
        
        #         
        all_steps, valid_steps = self.filter_valid_reasoning_steps()
        
        if not all_steps:
            print("❌ No reasoning steps")
            return 0.0, {}
        
        reasoning_validation_results = {}
        
        # 1.             prompts  LLM  
        if valid_steps:
            prompts = self.generate_reasoning_validation_prompts_for_steps(valid_steps)
            
            #             ×           
            print(f"🚀 Starting validation: {len(prompts)} steps × {len(EVALUATION_MODELS)} models = {len(prompts) * len(EVALUATION_MODELS)} API calls")
            
            #                   "can't start new thread"  
            configured_inner_workers = int(os.getenv("EVAL_INNER_MAX_WORKERS", "20"))
            inner_max_workers = min(max(1, configured_inner_workers), len(prompts) * len(EVALUATION_MODELS))
            provider_fail_fast_enabled = _eval_provider_fail_fast_enabled()
            all_model_results = {}
            if provider_fail_fast_enabled:
                provider_fail_fast_triggered = False
                provider_fail_fast_reason = ""
                for prompt_info in prompts:
                    if provider_fail_fast_triggered:
                        break
                    reasoning_id = prompt_info['reasoning_id']
                    if reasoning_id not in all_model_results:
                        all_model_results[reasoning_id] = {}
                    for model_key, model_name in EVALUATION_MODELS.items():
                        model_response = self._validate_single_model(model_key, model_name, prompt_info)
                        all_model_results[reasoning_id][model_key] = model_response
                        print(f"   ✅ Step {reasoning_id} - {model_response['model_name']}: {model_response['result']}")
                        self._write_reasoning_model_response(reasoning_id, model_key, model_response)
                        if _is_provider_health_issue(model_response):
                            provider_fail_fast_triggered = True
                            provider_fail_fast_reason = (
                                f"provider_fail_fast_after_step_{reasoning_id}_model_{model_key}: "
                                f"{model_response.get('reason', '')}"
                            )
                            print("   ⚠️ Provider fail-fast triggered; remaining judge calls were not submitted")
                            self._fill_provider_fail_fast_results(
                                prompts,
                                all_model_results,
                                provider_fail_fast_reason,
                            )
                            break
            else:
                with ThreadPoolExecutor(max_workers=inner_max_workers) as executor:
                #        ×           
                    future_to_task = {}
                
                    for prompt_info in prompts:
                        reasoning_id = prompt_info['reasoning_id']
                        for model_key, model_name in EVALUATION_MODELS.items():
                            future = executor.submit(self._validate_single_model, model_key, model_name, prompt_info)
                            future_to_task[future] = (reasoning_id, model_key)
                
                #       
                    for future in as_completed(future_to_task):
                        reasoning_id, model_key = future_to_task[future]
                        try:
                            model_response = future.result(timeout=60)
                        
                            if reasoning_id not in all_model_results:
                                all_model_results[reasoning_id] = {}
                            all_model_results[reasoning_id][model_key] = model_response
                        
                            print(f"   ✅ Step {reasoning_id} - {model_response['model_name']}: {model_response['result']}")
                        
                        #        response
                            self._write_reasoning_model_response(reasoning_id, model_key, model_response)
                        
                        except Exception as e:
                            print(f"   ❌ Step {reasoning_id} - {EVALUATION_MODELS[model_key]}: task failed - {e}")
                        
                            if reasoning_id not in all_model_results:
                                all_model_results[reasoning_id] = {}
                            all_model_results[reasoning_id][model_key] = {
                                "model_key": model_key,
                                "model_name": EVALUATION_MODELS[model_key],
                                "result": "error",
                                "reason": f"Task failed: {e}",
                                "raw_response": "",
                                "success": False
                            }

            #            
            for reasoning_id, model_responses in all_model_results.items():
                model_results = {k: v["result"] for k, v in model_responses.items()}
                vote_result, final_result = self._vote_on_results(model_results)
                
                reasoning_validation_results[reasoning_id] = final_result
                print(f"   📊 Step {reasoning_id} vote: {vote_result['decision']} → {final_result}")
                
                #       
                prompt_info = next(p for p in prompts if p['reasoning_id'] == reasoning_id)
                vote_summary = {
                    "reasoning_id": reasoning_id,
                    "target_node": prompt_info['target_node'],
                    "source_nodes": prompt_info['source_nodes'],
                    "edge_types": prompt_info['edge_types'],
                    "actual_edge_types": prompt_info['actual_edge_types'],
                    "reasoning_type": prompt_info['reasoning_type'],
                    "target_content": prompt_info['target_content'],
                    "source_contents": prompt_info['source_contents'],
                    "premise_descriptions": prompt_info['premise_descriptions'],
                    "model_results": model_results,
                    "model_responses": model_responses,
                    "vote_breakdown": vote_result,
                    "final_result": final_result
                }
                
                vote_file = self.eval_dir / "responses" / f"reasoning_validation_{reasoning_id:03d}_vote_result.json"
                with open(vote_file, 'w', encoding='utf-8') as f:
                    json.dump(vote_summary, f, ensure_ascii=False, indent=2)
        
        # 2.                           
        invalid_steps = [step for step in all_steps if step not in valid_steps]
        invalid_count = len(invalid_steps)
        
        #        ID      
        current_id = len(valid_steps) + 1
        for i, (target_node, source_nodes, edge_types) in enumerate(invalid_steps):
            reasoning_id = current_id + i
            
            #       
            has_non_standard_edges = any(edge_type not in self.standard_edge_types for edge_type in edge_types)
            
            if has_non_standard_edges:
                #         
                reasoning_validation_results[str(reasoning_id)] = "non_standard_edge_error"
                non_standard_types = [et for et in edge_types if et not in self.standard_edge_types]
                print(f"      {reasoning_id}: non_standard_edge_error (    : {target_node} <-    {non_standard_types})")
            elif len(edge_types) == 1:
                #     
                reasoning_validation_results[str(reasoning_id)] = "single_edge_error"
                print(f"      {reasoning_id}: single_edge_error (    : {target_node} <- {edge_types})")
            else:
                #               
                reasoning_validation_results[str(reasoning_id)] = "format_error"
                print(f"      {reasoning_id}: format_error (    : {target_node} <- {edge_types})")
        
        #         
        correct_count = sum(1 for result in reasoning_validation_results.values() if result == "correct")
        total_count = len(reasoning_validation_results)
        correctness_rate = (correct_count / total_count * 100) if total_count > 0 else 0
        
        #         
        single_edge_count = sum(1 for result in reasoning_validation_results.values() if result == "single_edge_error")
        format_error_count = sum(1 for result in reasoning_validation_results.values() if result == "format_error")
        non_standard_edge_count = sum(1 for result in reasoning_validation_results.values() if result == "non_standard_edge_error")
        llm_verified_count = len(valid_steps)
        
        print(f"\n📊 Validation Results:")
        print(f"Total steps:         {total_count}")
        print(f"LLM verified:        {llm_verified_count}")
        print(f"Single edge errors:  {single_edge_count}")
        print(f"Format errors:       {format_error_count}")
        print(f"Non-standard edges:  {non_standard_edge_count}")
        print(f"Correct:             {correct_count}")
        print(f"✅ Step Validity (SV): {correctness_rate:.1f}% ({correct_count}/{total_count})")
        
        return correctness_rate, reasoning_validation_results

    def calculate_accuracy_score(self, reasoning_validation_results: Dict) -> Dict:
        """Calculate accuracy score from reasoning_validation_results"""
        print("\n📊 Calculating accuracy score...")
        
        total_steps = len(reasoning_validation_results)
        if total_steps == 0:
            return {
                "total_steps": 0,
                "valid_steps": 0,
                "accuracy_score": 0,
                "details": {}
            }
        
        #          
        valid_steps = sum(1 for result in reasoning_validation_results.values() if result == "correct")
        
        accuracy_score = valid_steps / total_steps
        
        accuracy_result = {
            "total_steps": total_steps,
            "valid_steps": valid_steps,
            "invalid_steps": total_steps - valid_steps,
            "accuracy_score": accuracy_score,
            "details": reasoning_validation_results
        }
        
        print(f"✅ Accuracy: {accuracy_score:.2%} ({valid_steps}/{total_steps})")
        
        return accuracy_result

    def _call_api_with_retry(self, prompt: str, step_name: str, max_retries: int = 3) -> Optional[str]:
        """    API  """
        
        #   prompt
        prompt_file = self.eval_dir / "prompts" / f"{step_name}_prompt.txt"
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(prompt)
        
        return self._call_model_text_with_retry(
            model_key="claude",
            messages=[{"role": "user", "content": prompt}],
            step_name=step_name,
            max_retries=max_retries,
        )

    def run_evaluation(self) -> Dict:
        """       -    d_metrics_v3_fixed.py          """
        print("\n🚀      ...")
        
        # 1.     
        if not self.load_data():
            return {"error": "      "}
        
        # 2.     idea   
        core_idea, entities = self.extract_core_idea_and_entities()
        if not entities:
            return {"error": "      "}
        
        self.core_idea_entities = entities
        
        # 3.         -            
        correctness_rate, reasoning_validation_results = self.validate_reasoning_steps_with_llm()
        
        # 4.                
        coverage_rate = self.calculate_entity_coverage_from_correct_reasoning(reasoning_validation_results)
        
        #                
        coverage_result = {
            "coverage_rate": coverage_rate / 100,  #      
            "total_entities": len(entities),
            "covered_entities": int(len(entities) * coverage_rate / 100)
        }
        
        # 5.        
        accuracy_result = self.calculate_accuracy_score(reasoning_validation_results)
        
        # 6.     
        final_result = {
            "timestamp": datetime.now().isoformat(),
            "output_dir": str(self.output_dir),
            "core_idea": core_idea,
            "entities": entities,
            "coverage": coverage_result,
            "accuracy": accuracy_result,
            "evaluation_summary": {
                "entity_coverage_score": coverage_result["coverage_rate"],
                "accuracy_score": accuracy_result["accuracy_score"],
                "total_entities": len(entities),
                "covered_entities": coverage_result["covered_entities"],
                "total_reasoning_steps": accuracy_result["total_steps"],
                "valid_reasoning_steps": accuracy_result["valid_steps"]
            }
        }
        
        # 7.     
        results_file = self.eval_dir / "evaluation_results.json"
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(final_result, f, ensure_ascii=False, indent=2)
        
        # 8.       
        summary = f"""Evaluation Summary
{'='*50}
Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Output Dir: {self.output_dir}
Eval Dir: {self.eval_dir}

Metrics:
- Content Grounding (CG): {coverage_result['coverage_rate']:.2%} ({coverage_result['covered_entities']}/{coverage_result['total_entities']})
- Step Validity (SV):     {accuracy_result['accuracy_score']:.2%} ({accuracy_result['valid_steps']}/{accuracy_result['total_steps']})

Core Idea:
{core_idea}

Output Files:
- evaluation_results.json: full evaluation results
- prompts/: API prompts
- responses/: API responses
- coverage/: coverage details
- accuracy/: accuracy details

Status: completed
"""
        
        summary_file = self.eval_dir / "evaluation_summary.txt"
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write(summary)
        
        print(f"\n🎉 Evaluation complete!")
        print(f"📄 Results: {results_file}")
        print(f"📊 CG: {coverage_result['coverage_rate']:.2%}")
        print(f"📊 SV: {accuracy_result['accuracy_score']:.2%}")
        
        return final_result

def find_graph_outputs(base_dir: str = "outputs") -> List[str]:
    """Find all valid graph output directories"""
    if not os.path.exists(base_dir):
        return []
    
    #                
    file_model_outputs = {}
    
    for root, dirs, files in os.walk(base_dir):
        if "final_clean_graph.dot" in files and "input_data.json" in files:
            #     : outputs/filename/model_timestamp
            path_parts = Path(root).parts
            if len(path_parts) >= 2:
                filename = path_parts[-2]  #    
                model_timestamp = path_parts[-1]  #   _   
                
                #          
                if '_' in model_timestamp:
                    parts = model_timestamp.split('_')
                    if len(parts) >= 2:
                        #            
                        timestamp = None
                        for i in range(len(parts) - 1, -1, -1):
                            if parts[i].replace('_', '').isdigit():
                                model_name = '_'.join(parts[:i])
                                timestamp = '_'.join(parts[i:])
                                break
                        
                        if timestamp and model_name:
                            key = (filename, model_name)
                            if key not in file_model_outputs:
                                file_model_outputs[key] = []
                            file_model_outputs[key].append((timestamp, root))
    
    #      -             
    latest_outputs = []
    for (filename, model_name), outputs in file_model_outputs.items():
        if outputs:
            #             
            outputs.sort(key=lambda x: x[0], reverse=True)
            latest_timestamp, latest_dir = outputs[0]
            latest_outputs.append(latest_dir)
            
            if len(outputs) > 1:
                print(f"⏭️ Using latest: {filename} + {model_name} -> {latest_timestamp} ({len(outputs)} total)")
    
    print(f"📊 Found {len(latest_outputs)} outputs ({sum(len(outputs) for outputs in file_model_outputs.values())} total)")
    
    return latest_outputs

def parallel_evaluation(output_dirs: List[str], max_workers: int = 4) -> List[Dict]:
    """Run evaluation in parallel"""
    print(f"🚀 Starting parallel evaluation")
    print(f"📊 Output dirs: {len(output_dirs)}")
    print(f"👥 Max workers: {max_workers}")
    
    results = []
    
    def evaluate_single_output(output_dir: str) -> Dict:
        """Evaluate a single output directory"""
        try:
            # check if already evaluated
            output_path = Path(output_dir)
            evaluation_outputs_dir = output_path / "evaluation_outputs"
            
            if evaluation_outputs_dir.exists():
                clean_eval_dir = find_latest_clean_eval_dir(evaluation_outputs_dir)
                if clean_eval_dir is not None:
                    results_file = clean_eval_dir / "evaluation_results.json"
                    if results_file.exists():
                        print(f"⏭️ Skipping existing clean evaluation: {output_dir}")
                        with open(results_file, 'r', encoding='utf-8') as f:
                            result = json.load(f)
                        result["output_dir"] = output_dir
                        result["success"] = True
                        result["skipped"] = True
                        return result
            
            #      
            evaluator = GraphEvaluator(output_dir)
            result = evaluator.run_evaluation()
            result["output_dir"] = output_dir
            result["success"] = "error" not in result
            result["skipped"] = False
            return result
        except Exception as e:
            return {
                "output_dir": output_dir,
                "success": False,
                "error": f"    : {e}",
                "skipped": False
            }
    
    #       
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        #       
        future_to_dir = {
            executor.submit(evaluate_single_output, output_dir): output_dir 
            for output_dir in output_dirs
        }
        
        #        
        for future in as_completed(future_to_dir):
            output_dir = future_to_dir[future]
            try:
                result = future.result()
                results.append(result)
                
                if result["success"]:
                    if result.get("skipped", False):
                        #              evaluate_single_output       
                        pass
                    else:
                        print(f"✅ Evaluated: {output_dir}")
                else:
                    print(f"❌ Failed: {output_dir} - {result.get('error', 'unknown error')}")
                    
            except Exception as e:
                print(f"💥 Exception: {output_dir} - {e}")
                results.append({
                    "output_dir": output_dir,
                    "success": False,
                    "error": f"    : {e}"
                })
    
    return results

def save_batch_evaluation_results(results: List[Dict], base_dir: str = "outputs") -> str:
    """Save batch evaluation results to disk"""
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(base_dir, f"batch_evaluation_results_{timestamp}")
    os.makedirs(results_dir, exist_ok=True)
    
    # save results
    results_file = os.path.join(results_dir, "batch_evaluation_results.json")
    with open(results_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    # compute stats
    total_evaluations = len(results)
    successful_evaluations = sum(1 for r in results if r["success"])
    failed_evaluations = total_evaluations - successful_evaluations
    skipped_evaluations = sum(1 for r in results if r.get("skipped", False))
    
    #       
    if successful_evaluations > 0:
        avg_coverage = sum(r["evaluation_summary"]["entity_coverage_score"] 
                          for r in results if r["success"]) / successful_evaluations
        avg_accuracy = sum(r["evaluation_summary"]["accuracy_score"] 
                          for r in results if r["success"]) / successful_evaluations
    else:
        avg_coverage = 0
        avg_accuracy = 0
    
    summary = f"""Batch Evaluation Summary
{'='*50}
Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Total:      {total_evaluations}
Successful: {successful_evaluations}
Failed:     {failed_evaluations}
Skipped:    {skipped_evaluations}
Success Rate: {successful_evaluations/total_evaluations*100:.1f}%

Average Metrics:
- Avg Content Grounding (CG): {avg_coverage:.2%}
- Avg Step Validity (SV):     {avg_accuracy:.2%}

Results File: {results_file}
"""
    
    summary_file = os.path.join(results_dir, "batch_summary.txt")
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write(summary)
    
    print(f"\n📊 Batch Evaluation Results:")
    print(f"Total:      {total_evaluations}")
    print(f"Successful: {successful_evaluations}")
    print(f"Failed:     {failed_evaluations}")
    print(f"Skipped:    {skipped_evaluations}")
    print(f"Success Rate: {successful_evaluations/total_evaluations*100:.1f}%")
    print(f"Avg CG: {avg_coverage:.2%}")
    print(f"Avg SV: {avg_accuracy:.2%}")
    print(f"📄 Results dir: {results_dir}")
    
    return results_dir

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='ARCHE Evaluator - CG and SV metrics')
    parser.add_argument('--output-dir', default='outputs', 
                       help='Output directory')
    parser.add_argument('--max-workers', type=int, default=4, 
                       help='Max parallel workers')
    parser.add_argument('--single-dir', 
                       help='Single output directory to evaluate')
    
    args = parser.parse_args()
    
    if args.single_dir:
        #       
        print(f"🎯 Single dir: {args.single_dir}")
        evaluator = GraphEvaluator(args.single_dir)
        result = evaluator.run_evaluation()
        if "error" not in result:
            print("✅ Evaluation complete")
        else:
            print(f"❌ Evaluation failed: {result['error']}")
    else:
        #     
        print(f"🎯 Batch evaluation")
        print(f"📁 Output dir: {args.output_dir}")
        
        #          
        output_dirs = find_graph_outputs(args.output_dir)
        if not output_dirs:
            print(f"❌ No outputs found in {args.output_dir}")
            return
        
        print(f"📊 Found {len(output_dirs)} outputs to evaluate")
        
        #     
        results = parallel_evaluation(output_dirs, args.max_workers)
        
        #       
        results_dir = save_batch_evaluation_results(results, args.output_dir)
        
        print(f"\n🎉 Batch evaluation complete!")
        print(f"📄 Results dir: {results_dir}")

if __name__ == "__main__":
    main()
