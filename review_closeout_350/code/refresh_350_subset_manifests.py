#!/usr/bin/env python3
"""Refresh file manifests for the 350 subset package."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    PROJECT_ROOT
    / "reports"
    / "pearl_runs"
    / "00_CURRENT_STANDARD_FLOW_20260527"
    / "standard_flow_490_clean_package_20260528"
    / "10_standard_flow_350_subset_package"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def build_manifest(root: Path, *, package: str, scope: str, exclude_names: set[str]) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.name in exclude_names:
            continue
        files.append({"file": rel(path, root), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {
        "package": package,
        "scope": scope,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "file_count": len(files),
        "files": files,
    }


def main() -> int:
    code_root = PACKAGE_ROOT / "07_code"
    residual_root = PACKAGE_ROOT / "09_residual_50_closeout"

    code_manifest = {
        "scope": "10_standard_flow_350_subset_package/07_code",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "file_count": 0,
        "files": [],
    }
    for path in sorted(code_root.glob("*.py")):
        code_manifest["files"].append(
            {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    code_manifest["file_count"] = len(code_manifest["files"])
    write_json(code_root / "CODE_SNAPSHOT_MANIFEST_20260602.json", code_manifest)

    residual_manifest = build_manifest(
        residual_root,
        package="09_residual_50_closeout",
        scope="closeout package and evidence-bound/local-window residual repair artifacts for the 350 subset",
        exclude_names={"RESIDUAL_50_CLOSEOUT_MANIFEST.json"},
    )
    write_json(residual_root / "RESIDUAL_50_CLOSEOUT_MANIFEST.json", residual_manifest)

    package_manifest = build_manifest(
        PACKAGE_ROOT,
        package="10_standard_flow_350_subset_package",
        scope="350-paper subset package, excluding GPT-5.4/GPT-5.5 generator rows from reported dataset results",
        exclude_names={"SUBSET_350_PACKAGE_MANIFEST.json", "350_SUBPACKAGE_MANIFEST.json"},
    )
    write_json(PACKAGE_ROOT / "SUBSET_350_PACKAGE_MANIFEST.json", package_manifest)
    write_json(PACKAGE_ROOT / "350_SUBPACKAGE_MANIFEST.json", package_manifest)

    print(
        json.dumps(
            {
                "code_file_count": code_manifest["file_count"],
                "residual_file_count": residual_manifest["file_count"],
                "package_file_count": package_manifest["file_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
