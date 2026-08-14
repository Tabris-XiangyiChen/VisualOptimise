"""UE-copyable RuntimeData tree builder."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from visualoptimise.artifacts import timestamp_iso, write_json


MAP_PACKAGE_INDEX_SCHEMA = "map_package_index_v1"
RUNTIME_MAP_PACKAGE_SCHEMA = "runtime_map_package_v1"


def build_runtime_map_package(
    project_root: Path,
    runtime_root: Path,
    map_id: str,
    map_dir: Path,
    resolved_tileset: dict[str, Any],
    material_manifest: dict[str, Any],
) -> dict[str, Any]:
    map_runtime_dir = runtime_root / "maps" / map_id
    layout_dir = map_runtime_dir / "layout"
    rules_dir = map_runtime_dir / "rules"
    materials_dir = map_runtime_dir / "materials"
    layout_dir.mkdir(parents=True, exist_ok=True)
    rules_dir.mkdir(parents=True, exist_ok=True)
    materials_dir.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(map_dir / "map.txt", layout_dir / "map.txt")
    write_json(rules_dir / "resolved_tileset.json", resolved_tileset)
    write_json(materials_dir / "material_manifest.json", material_manifest)
    manifest = {
        "schema_version": RUNTIME_MAP_PACKAGE_SCHEMA,
        "map_id": map_id,
        "display_name": map_id,
        "package_dir": f"maps/{map_id}",
        "files": {
            "layout": "layout/map.txt",
            "resolved_tileset": "rules/resolved_tileset.json",
            "material_manifest": "materials/material_manifest.json",
        },
        "directories": {
            "material_textures": "materials/textures",
        },
        "generation": {
            "type": "deterministic_grid_builder",
            "coordinate_system": "tile_grid",
            "tile_size": 100,
        },
        "source": {
            "authoring_map_package": str(map_dir),
            "project_root": str(project_root),
            "llm_calls": 0,
            "sd_calls": 0,
            "stablematerials_calls": 0,
            "new_image_generation": False,
        },
        "warnings": [],
    }
    write_json(map_runtime_dir / "manifest.json", manifest)
    return manifest


def build_map_package_index(project_root: Path, runtime_root: Path, map_ids: list[str], created_by: str, warnings: list[str] | None = None) -> dict[str, Any]:
    maps = []
    for map_id in sorted(map_ids):
        maps.append(
            {
                "map_id": map_id,
                "display_name": map_id,
                "package_dir": f"maps/{map_id}",
                "manifest": f"maps/{map_id}/manifest.json",
                "layout": f"maps/{map_id}/layout/map.txt",
                "resolved_tileset": f"maps/{map_id}/rules/resolved_tileset.json",
                "material_manifest": f"maps/{map_id}/materials/material_manifest.json",
                "material_textures_dir": f"maps/{map_id}/materials/textures",
            }
        )
    return {
        "schema_version": MAP_PACKAGE_INDEX_SCHEMA,
        "created_at": timestamp_iso(),
        "created_by": created_by,
        "runtime_root": "VisualOptimization/RuntimeData",
        "runtime_root_absolute": str(runtime_root),
        "ue_copy_destination": str(project_root.parent / "VisualOptimizationUE" / "Content" / "VisualOptimization" / "RuntimeData"),
        "maps": maps,
        "warnings": sorted(set(warnings or [])),
    }


def refresh_runtime_data(source_runtime_root: Path, target_runtime_root: Path) -> None:
    if target_runtime_root.exists():
        shutil.rmtree(target_runtime_root)
    shutil.copytree(source_runtime_root, target_runtime_root)


def copy_runtime_snapshot(source_runtime_root: Path, snapshot_root: Path) -> None:
    if snapshot_root.exists():
        raise FileExistsError(f"RuntimeData snapshot already exists: {snapshot_root}")
    shutil.copytree(source_runtime_root, snapshot_root)
