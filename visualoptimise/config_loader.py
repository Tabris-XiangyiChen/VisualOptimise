"""Configuration loading for the self-contained production pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "default_map": "test_map1_clean",
    "runtime_texture_backend": "sd15",
    "semantic_mode": "llm",
    "material_mode": "preview-only",
    "llm_max_attempts": 2,
    "prompt_llm_max_attempts": 2,
    "refresh_runtime_data": True,
}


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return loaded


def load_settings(project_root: Path) -> dict[str, Any]:
    return read_json_if_exists(project_root / "config" / "settings.json")


def load_defaults(project_root: Path) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    for relative in (
        Path("settings") / "pipeline_defaults.json",
        Path("settings") / "runtime_export_defaults.json",
    ):
        config.update(read_json_if_exists(project_root / relative))
    return config
