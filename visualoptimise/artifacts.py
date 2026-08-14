"""Small filesystem and audit helpers shared by experiment modules."""

from __future__ import annotations

import ast
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def timestamp_for_run() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def timestamp_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dirs(paths: dict[str, Path]) -> None:
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)


def normalize_map_ids(map_ids: list[str] | None, fallback_map_id: str) -> list[str]:
    raw_items = map_ids if map_ids else [fallback_map_id]
    normalized: list[str] = []
    for item in raw_items:
        for part in str(item).split(","):
            value = part.strip()
            if value and value not in normalized:
                normalized.append(value)
    return normalized


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def file_stats(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    return {
        "path": str(path),
        "lines": len(text.splitlines()),
        "function_count": len(functions),
        "class_count": len(classes),
        "round13_functions": [name for name in functions if "round13" in name],
        "round15_functions": [name for name in functions if "round15" in name],
        "round16_functions": [name for name in functions if "round16" in name],
        "round17_functions": [name for name in functions if "round17" in name],
    }


def manifest_entries(run_dir: Path, grouped_files: dict[str, list[Path]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for category, files in grouped_files.items():
        for path in files:
            entries.append(
                {
                    "relative_path": str(path.relative_to(run_dir)),
                    "category": category,
                    "artifact_type": path.suffix.lstrip(".") or "directory",
                    "purpose": f"Round artifact in {category}",
                }
            )
    return entries
