"""RuntimeData validation for Python-created UE packages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TEXTURE_TYPES = ("basecolor", "normal", "roughness", "height", "metallic")


def validate_runtime_data(runtime_root: Path, expected_maps: set[str], require_texture_files: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    index_path = runtime_root / "map_package_index.json"
    if not index_path.is_file():
        errors.append(f"Missing map_package_index.json at {index_path}")
        return {"passed": False, "errors": errors, "warnings": warnings}
    index = _read_json(index_path)
    if index.get("schema_version") != "map_package_index_v1":
        errors.append(f"Invalid map_package_index schema: {index.get('schema_version')}")
    found_maps = {entry.get("map_id") for entry in index.get("maps", [])}
    if found_maps != expected_maps:
        errors.append(f"RuntimeData maps {sorted(found_maps)} do not match expected {sorted(expected_maps)}")
    for entry in index.get("maps", []):
        map_id = str(entry.get("map_id"))
        _validate_index_entry(runtime_root, entry, errors)
        _validate_map_package(runtime_root, map_id, errors, warnings, require_texture_files=require_texture_files)
    return {"passed": not errors, "errors": errors, "warnings": sorted(set(warnings))}


def validate_no_authoring_files(runtime_root: Path) -> dict[str, Any]:
    forbidden = []
    for name in ["legend.json", "style.txt"]:
        forbidden.extend(str(path) for path in runtime_root.rglob(name))
    return {
        "passed": not forbidden,
        "errors": [f"RuntimeData must not include authoring file: {path}" for path in forbidden],
        "warnings": [],
        "forbidden_authoring_files": forbidden,
    }


def _validate_index_entry(runtime_root: Path, entry: dict[str, Any], errors: list[str]) -> None:
    for key in ["manifest", "layout", "resolved_tileset", "material_manifest"]:
        relative = entry.get(key)
        if not relative:
            errors.append(f"Index entry for {entry.get('map_id')} missing {key}.")
        elif not (runtime_root / relative).is_file():
            errors.append(f"Index path for {entry.get('map_id')} missing: {relative}")


def _validate_map_package(runtime_root: Path, map_id: str, errors: list[str], warnings: list[str], require_texture_files: bool) -> None:
    map_dir = runtime_root / "maps" / map_id
    manifest_path = map_dir / "manifest.json"
    resolved_path = map_dir / "rules" / "resolved_tileset.json"
    material_manifest_path = map_dir / "materials" / "material_manifest.json"
    if not manifest_path.is_file():
        errors.append(f"{map_id}: missing manifest.json")
    elif _read_json(manifest_path).get("schema_version") != "runtime_map_package_v1":
        errors.append(f"{map_id}: invalid runtime map package schema.")
    if not resolved_path.is_file():
        errors.append(f"{map_id}: missing resolved_tileset.json")
    elif _read_json(resolved_path).get("schema_version") != "resolved_tileset_v1":
        errors.append(f"{map_id}: invalid resolved_tileset schema.")
    if not material_manifest_path.is_file():
        errors.append(f"{map_id}: missing material_manifest.json")
        return
    material_manifest = _read_json(material_manifest_path)
    if material_manifest.get("schema_version") != "material_manifest_v1":
        errors.append(f"{map_id}: invalid material manifest schema.")
    material_dir = material_manifest_path.parent
    for material in material_manifest.get("materials", []):
        slot = material.get("material_slot_id", "<missing_slot>")
        textures = material.get("textures", {})
        if "basecolor" not in textures:
            errors.append(f"{map_id}/{slot}: basecolor path missing.")
        for texture_type, relative in textures.items():
            if require_texture_files and not (material_dir / relative).is_file():
                errors.append(f"{map_id}/{slot}: {texture_type} texture missing at {relative}")
        missing_maps = material.get("missing_maps", [])
        if missing_maps:
            warnings.append(f"{map_id}/{slot}: missing texture maps {missing_maps}")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
