"""Material planning helpers for map-driven semantic planning."""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from visualoptimise.llm_artifacts import build_json_chat_payload, call_one_json_llm, parse_json_response
from visualoptimise.artifacts import write_json, write_text

LLM1_SCHEMA = "llm_tile_material_plan_v2"

LLM2_SCHEMA = "material_prompt_briefs_v4"

LLM1_FORBIDDEN_FIELDS = {
    "surface_terms",
    "prompt_ready_terms",
    "positive_tags",
    "negative_terms",
    "positive_prompt",
    "negative_prompt",
    "sd15",
    "stablematerials",
    "material_slot_id",
    "tile_type_id",
    "texture_path",
    "material_instance_path",
    "suggested_prompt_hint",
    "source_policy_reason",
    "material_slot_rules",
    "EXPECTED_SLOTS",
    "SLOT_VIEW_MODE",
}

PRIOR_FORBIDDEN_DATA_TERMS = {
    "material_slot_rules",
    "material_slot_evidence",
    "suggested_prompt_hint",
    "source_policy_reason",
    "EXPECTED_SLOTS",
    "SLOT_VIEW_MODE",
    "fixed legacy slots",
}

VIEW_TAG_BY_MODE = {
    "top_down_closeup_surface": "top down closeup surface",
    "front_facing_closeup_surface": "front facing closeup surface",
    "front_facing_panel_surface": "front facing panel surface",
}

OLD_FIXED_SLOT_IDS = {
    "grass_ground",
    "stone_floor",
    "stone_wall",
    "water",
    "wood_planks",
    "wooden_door",
}

SYMBOL_ALIASES = {
    "#": "hash",
    ".": "dot",
    "=": "equals",
    "~": "tilde",
    "^": "caret",
    "0": "zero",
    ",": "comma",
    "_": "underscore",
}

VIEW_MODE_BY_SHAPE = {
    "flat_tile": "top_down_closeup_surface",
    "vertical_block": "front_facing_closeup_surface",
    "vertical_prop": "front_facing_panel_surface",
}

SURFACE_ORIENTATIONS = {
    "horizontal_surface",
    "vertical_surface",
    "panel_surface",
    "liquid_surface",
    "sloped_surface",
}

SURFACE_ORIENTATION_BY_LEGACY_VIEW_MODE = {
    "top_down_closeup_surface": "horizontal_surface",
    "front_facing_closeup_surface": "vertical_surface",
    "front_facing_panel_surface": "panel_surface",
}

CONTEXT_REASON_ENUM = {
    "object_shape",
    "usage_context",
    "map_setting",
    "landform_context",
    "gameplay_role",
    "symbolic_marker",
    "mesh_context",
    "other",
}

def resolve_map_root(project_root: Path, map_root: Path | str | None = None) -> Path:
    candidate = Path(map_root) if map_root is not None else project_root / "data" / "maps"
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def resolve_mesh_catalog_path(project_root: Path, mesh_catalog: Path | str | None = None) -> Path:
    candidate = Path(mesh_catalog) if mesh_catalog is not None else project_root / "data" / "ue_asset_catalogs" / "mesh_catalog.json"
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def validate_map_package(project_root: Path, map_id: str, map_root: Path | str | None = None) -> dict[str, Any]:
    map_dir = resolve_map_root(project_root, map_root) / map_id
    map_path = map_dir / "map.txt"
    legend_path = map_dir / "legend.json"
    style_path = map_dir / "style.txt"
    errors: list[Any] = []
    warnings: list[Any] = []
    for path in (map_path, legend_path, style_path):
        if not path.is_file():
            errors.append({"missing_file": str(path)})
    if errors:
        return {
            "schema_version": "map_package_validation_v1",
            "passed": False,
            "map_id": map_id,
            "map_package": str(map_dir),
            "errors": errors,
            "warnings": warnings,
        }

    rows = map_path.read_text(encoding="utf-8").splitlines()
    try:
        legend = json.loads(legend_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        legend = {}
        errors.append({"legend_json": str(legend_path), "error": str(exc)})
    if not rows:
        errors.append({"map_txt": str(map_path), "error": "map.txt is empty"})
    if not isinstance(legend, dict):
        errors.append({"legend_json": str(legend_path), "error": "legend.json must be a JSON object keyed by symbol"})
        legend = {}

    used_symbols = sorted(set(ch for row in rows for ch in row))
    legend_symbols = sorted(str(symbol) for symbol in legend.keys())
    missing_legend_symbols = sorted(set(used_symbols) - set(legend_symbols))
    unused_legend_symbols = sorted(set(legend_symbols) - set(used_symbols))
    if missing_legend_symbols:
        errors.append({"symbols_missing_from_legend": missing_legend_symbols})
    if unused_legend_symbols:
        warnings.append({"legend_symbols_not_used_in_map": unused_legend_symbols})

    for symbol in used_symbols:
        entry = legend.get(symbol)
        if not isinstance(entry, dict):
            errors.append({"symbol": symbol, "error": "legend entry must be an object"})
            continue
        for field in ("name", "description"):
            if not str(entry.get(field, "")).strip():
                warnings.append({"symbol": symbol, "missing_recommended_field": field})

    widths = sorted({len(row) for row in rows})
    if len(widths) > 1:
        warnings.append({"ragged_map_row_widths": widths})

    return {
        "schema_version": "map_package_validation_v1",
        "passed": not errors,
        "map_id": map_id,
        "map_package": str(map_dir),
        "source_files": {
            "map_txt": str(map_path),
            "legend_json": str(legend_path),
            "style_txt": str(style_path),
        },
        "map_size": {"width": max(widths) if widths else 0, "height": len(rows)},
        "used_symbols": used_symbols,
        "legend_symbols": legend_symbols,
        "missing_legend_symbols": missing_legend_symbols,
        "unused_legend_symbols": unused_legend_symbols,
        "errors": errors,
        "warnings": warnings,
    }

def build_map_facts(project_root: Path, map_id: str, map_root: Path | str | None = None) -> dict[str, Any]:
    map_dir = resolve_map_root(project_root, map_root) / map_id
    map_path = map_dir / "map.txt"
    legend_path = map_dir / "legend.json"
    style_path = map_dir / "style.txt"
    rows = map_path.read_text(encoding="utf-8").splitlines()
    legend = json.loads(legend_path.read_text(encoding="utf-8"))
    style_text = style_path.read_text(encoding="utf-8").strip()
    counts = Counter(ch for row in rows for ch in row)
    used_symbols = sorted(counts.keys(), key=lambda symbol: (symbol not in legend, symbol))
    samples = {symbol: [] for symbol in used_symbols}
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if len(samples[ch]) < 12:
                samples[ch].append({"x": x, "y": y})
    legend_entries = []
    for symbol in used_symbols:
        entry = legend.get(symbol, {})
        legend_entries.append(
            {
                "symbol": symbol,
                "name": entry.get("name"),
                "description": entry.get("description"),
                "declared_material_family": entry.get("material_family"),
                "generate_material": entry.get("generate_material"),
            }
        )
    return {
        "schema_version": "map_facts_v2",
        "map_id": map_id,
        "source_files": {
            "map_txt": str(map_path),
            "legend_json": str(legend_path),
            "style_txt": str(style_path),
        },
        "map_size": {"width": max((len(row) for row in rows), default=0), "height": len(rows)},
        "map_rows": rows,
        "used_symbols": used_symbols,
        "symbol_counts": dict(sorted(counts.items())),
        "symbol_coordinate_samples": samples,
        "legend_entries": legend_entries,
        "style_text": style_text,
    }

def build_mesh_catalog_snapshot(project_root: Path, mesh_catalog: Path | str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    path = resolve_mesh_catalog_path(project_root, mesh_catalog)
    full = json.loads(path.read_text(encoding="utf-8"))
    snapshot = {
        "schema_version": "mesh_catalog_snapshot_for_llm_v1",
        "source_file": str(path),
        "meshes": [
            {
                "mesh_id": mesh.get("mesh_id"),
                "description": mesh.get("description"),
                "role_tags": mesh.get("role_tags", []),
                "shape_type": mesh.get("shape_type"),
                "height_class": mesh.get("height_class"),
                "surface_orientation": mesh.get("surface_orientation"),
            }
            for mesh in full.get("meshes", [])
        ],
        "provenance": "Sanitized UE logical mesh capability data. Material slot rules and material evidence were not read.",
    }
    return full, snapshot

def validate_map_facts(map_facts: dict[str, Any]) -> dict[str, Any]:
    errors: list[Any] = []
    if map_facts.get("schema_version") != "map_facts_v2":
        errors.append("schema_version must be map_facts_v2.")
    rows = map_facts.get("map_rows", [])
    width = map_facts.get("map_size", {}).get("width")
    if not rows:
        errors.append("map_rows is empty.")
    if any(len(row) != width for row in rows):
        errors.append("Map rows are not rectangular.")
    if find_key_hits(map_facts, {"material_slot_id", "tile_type_id", "mesh_id", "pcg_role", "suggested_prompt_hint", "source_policy_reason", "material_slot_rules"}):
        errors.append("Forbidden prior/runtime keys found in map_facts_v2.")
    return {"schema_version": "map_facts_v2_validation", "passed": not errors, "errors": errors}

def validate_mesh_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    errors: list[Any] = []
    warnings: list[Any] = []
    mesh_ids = []
    for mesh in snapshot.get("meshes", []):
        mesh_id = mesh.get("mesh_id")
        if not mesh_id:
            errors.append("Mesh entry missing mesh_id.")
            continue
        if mesh_id in mesh_ids:
            errors.append(f"Duplicate mesh_id: {mesh_id}")
        mesh_ids.append(mesh_id)
        if not isinstance(mesh.get("role_tags"), list):
            errors.append(f"{mesh_id}.role_tags must be a list.")
        orientation = mesh.get("surface_orientation")
        if orientation is None:
            warnings.append(
                {
                    "mesh_id": mesh_id,
                    "warning": "surface_orientation missing; legacy shape-to-view fallback will be used",
                }
            )
        elif orientation not in SURFACE_ORIENTATIONS:
            errors.append(
                {
                    "mesh_id": mesh_id,
                    "surface_orientation": orientation,
                    "error": "surface_orientation is not a supported catalog value",
                    "allowed": sorted(SURFACE_ORIENTATIONS),
                }
            )
    if find_key_hits(snapshot, {"material_slot_id", "suggested_prompt_hint", "source_policy_reason"}):
        errors.append("Forbidden prior keys found in mesh snapshot.")
    return {
        "schema_version": "mesh_catalog_snapshot_validation_v1",
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "mesh_ids": mesh_ids,
        "surface_orientation_declared_count": sum(
            1 for mesh in snapshot.get("meshes", []) if mesh.get("surface_orientation") in SURFACE_ORIENTATIONS
        ),
        "legacy_fallback_count": len(warnings),
    }

def build_llm1_system_prompt() -> str:
    return "\n".join(
        [
            "You are a semantic material planner for a tile-based game map.",
            "Your task is to read pure map facts and a sanitized UE mesh catalog, then produce a structured material planning result.",
            "You are not creating image-generation prompts, SD1.5 tags, StableMaterials phrases, final prompt-ready surface terms, or backend-specific prompts.",
            "You only decide symbol meaning, geometry generation, available mesh selection, material generation, reusable material grouping, primary prompt symbols, excluded detail symbols, coarse material identity/category, raw map clues, and context clues for a later prompt-generation LLM.",
            "Use only map_facts_v2 and mesh_catalog_snapshot_for_llm.",
            "Do not rely on previous experiments, old material slot lists, old prompt styles, hidden project conventions, or information outside the provided inputs.",
            "Choose mesh_id only from mesh_catalog_snapshot_for_llm.meshes[*].mesh_id, or null for no geometry.",
            "If mesh_id is not null, selected_mesh_role_tags must be copied only from that selected mesh's role_tags.",
            "surface_orientation is read-only mesh capability metadata supplied by the catalog. Do not invent, rewrite, or output it.",
            "canonical_material_id_proposal must be descriptive lowercase snake_case and based on material identity, not fixed legacy slot IDs.",
            "Raw legend names that look like old slots may appear only as raw map facts in legend_name or raw clues, never as runtime slots.",
            "Do not merge symbols that need different material viewing families into one canonical material group. Keep flat surfaces, vertical blocks, and vertical props as separate groups even when the broad substance is similar.",
            "Do not output fields named surface_terms, prompt_ready_terms, positive_tags, negative_terms, positive_prompt, negative_prompt, stablematerials, sd15, material_slot_id, tile_type_id, texture_path, material_instance_path.",
            "Return only valid JSON following llm_tile_material_plan_v2.",
        ]
    )

def build_llm1_user_prompt(map_facts: dict[str, Any], mesh_snapshot: dict[str, Any]) -> str:
    schema = {
        "schema_version": LLM1_SCHEMA,
        "map_id": "...",
        "symbol_plans": [
            {
                "symbol": "...",
                "legend_name": "...",
                "tile_semantics": "...",
                "generate_geometry": True,
                "mesh_id": "... or null",
                "selected_mesh_role_tags": ["..."],
                "mesh_selection_reason": "...",
                "generate_material": True,
                "assigned_canonical_material_id_proposal": "... or null",
                "material_group_role": "primary_source | shared_base | detail_source | excluded_detail | no_material | unclear",
                "raw_legend_clues": [{"text": "...", "source_field": "legend.name | legend.description | style_text"}],
                "context_clues": [{"term": "...", "reason": "object_shape | usage_context | map_setting | landform_context | gameplay_role | symbolic_marker | mesh_context | other"}],
                "planning_reason": "...",
                "confidence": 0.0,
            }
        ],
        "canonical_material_groups": [
            {
                "canonical_material_id_proposal": "...",
                "source_symbols": ["..."],
                "covered_symbols": ["..."],
                "primary_prompt_symbol": "...",
                "prompt_source_symbols": ["..."],
                "excluded_detail_symbols": ["..."],
                "material_identity_coarse": "...",
                "material_category": "...",
                "raw_material_clues": [{"text": "...", "source_symbol": "...", "source_field": "legend.name | legend.description | style_text"}],
                "context_clues_for_prompt_llm": [{"term": "...", "reason": "object_shape | usage_context | map_setting | landform_context | gameplay_role | symbolic_marker | mesh_context | other", "source_symbol": "..."}],
                "expected_mesh_ids": ["..."],
                "detail_symbol_policy": {"related_detail_symbols": ["..."], "excluded_from_base_prompt": ["..."], "reason": "..."},
                "planning_confidence": 0.0,
            }
        ],
        "warnings": [],
    }
    return "\n".join(
        [
            "Analyze the following map package and mesh catalog. Produce a semantic material planning result.",
            "This is not a prompt-generation task. Do not output surface_terms, prompt-ready tags, SD1.5 tags, StableMaterials phrases, or final image prompts.",
            "Every used symbol must appear exactly once in symbol_plans.",
            "mesh_id must be from the provided mesh catalog, or null.",
            "selected_mesh_role_tags must be copied from the selected mesh role_tags.",
            "If generate_geometry is false, mesh_id must be null, selected_mesh_role_tags must be [], and generate_material must be false.",
            "If generate_material is true, assigned_canonical_material_id_proposal must reference exactly one canonical_material_groups item.",
            "For material groups: covered_symbols are all related symbols; prompt_source_symbols define the base material; excluded_detail_symbols are related but must not define the base prompt.",
            "Create separate canonical material groups when source symbols use incompatible mesh shape families. Do not group flat tile_plane surfaces with wall_block or door_proxy surfaces in the same canonical material group.",
            "raw_legend_clues and raw_material_clues may contain original map wording. context_clues may contain object, usage, scene, landform, gameplay, mesh, or symbolic terms.",
            "Do not classify prompt-ready surface terms.",
            "Do not output surface_terms, rejected_terms, backend fields, material_slot_id, or tile_type_id.",
            "",
            "Output JSON with this exact top-level structure:",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "",
            "map_facts_v2:",
            json.dumps(map_facts, ensure_ascii=False, indent=2),
            "",
            "mesh_catalog_snapshot_for_llm:",
            json.dumps(mesh_snapshot, ensure_ascii=False, indent=2),
        ]
    )

def call_llm_until_valid(
    pipeline: Any,
    payload: dict[str, Any],
    validator: Any,
    max_attempts: int,
    out_dir: Path,
    label: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    current_payload = copy.deepcopy(payload)
    last_raw = ""
    last_parsed: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        write_json(out_dir / f"{label}_request_attempt_{attempt}.json", sanitize_payload_for_disk(current_payload))
        try:
            raw, parsed = call_one_json_llm(pipeline.settings, pipeline.root, current_payload)
            last_raw = raw
            last_parsed = parsed
            validation = validator(parsed)
            write_text(out_dir / f"{label}_raw_attempt_{attempt}.txt", raw)
            write_json(out_dir / f"{label}_parsed_attempt_{attempt}.json", parsed)
            write_json(out_dir / f"{label}_validation_attempt_{attempt}.json", validation)
            attempts.append({"attempt": attempt, "parse_ok": True, "validation_passed": validation.get("passed"), "errors": validation.get("errors", [])})
            if validation.get("passed"):
                return parsed, raw, {"schema_version": f"{label}_attempts_summary_v1", "attempts": attempts, "llm_call_count": attempt, "retry_count": attempt - 1}
            if attempt < max_attempts:
                current_payload = build_repair_payload(current_payload, raw, validation.get("errors", []))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            attempts.append({"attempt": attempt, "parse_ok": False, "validation_passed": False, "errors": [error]})
            write_text(out_dir / f"{label}_error_attempt_{attempt}.txt", error)
            if attempt < max_attempts:
                current_payload = build_repair_payload(current_payload, last_raw, [error])
    if last_parsed is not None:
        return last_parsed, last_raw, {"schema_version": f"{label}_attempts_summary_v1", "attempts": attempts, "llm_call_count": len(attempts), "retry_count": max(0, len(attempts) - 1)}
    raise RuntimeError(f"{label} failed without parseable response.")

def build_repair_payload(payload: dict[str, Any], raw: str, errors: list[Any]) -> dict[str, Any]:
    repaired = copy.deepcopy(payload)
    content = repaired["messages"][-1]["content"]
    content += "\n\nThe previous response failed validation. Return corrected JSON only."
    content += "\nValidation errors:\n" + json.dumps(errors, ensure_ascii=False, indent=2)
    if raw:
        content += "\nPrevious raw response excerpt:\n" + raw[:6000]
    repaired["messages"][-1]["content"] = content
    return repaired

def build_template_llm1_plan(map_facts: dict[str, Any], mesh_snapshot: dict[str, Any]) -> dict[str, Any]:
    legend = {entry["symbol"]: entry for entry in map_facts["legend_entries"]}
    role_tags_by_mesh = {mesh["mesh_id"]: mesh["role_tags"] for mesh in mesh_snapshot["meshes"]}
    mesh_by_id = {mesh["mesh_id"]: mesh for mesh in mesh_snapshot["meshes"]}
    groups_by_id: dict[str, dict[str, Any]] = {}
    symbol_plans = []
    for symbol in map_facts["used_symbols"]:
        entry = legend.get(symbol, {"name": symbol, "description": symbol})
        generate_material = template_generate_material(entry)
        mesh_id = template_mesh_id(entry, mesh_by_id) if generate_material else None
        if not generate_material or mesh_id is None:
            symbol_plans.append(
                {
                    "symbol": symbol,
                    "legend_name": entry.get("name"),
                    "tile_semantics": entry.get("description"),
                    "generate_geometry": False,
                    "mesh_id": None,
                    "selected_mesh_role_tags": [],
                    "mesh_selection_reason": "Template dry-run selected no geometry from legend generate_material/material_family clues.",
                    "generate_material": False,
                    "assigned_canonical_material_id_proposal": None,
                    "material_group_role": "no_material",
                    "raw_legend_clues": [{"text": entry.get("description"), "source_field": "legend.description"}],
                    "context_clues": [],
                    "planning_reason": "Template dry-run no-material symbol plan.",
                    "confidence": 0.8,
                }
            )
            continue
        material_identity = template_material_identity(entry)
        base_canonical_id = normalize_identifier(material_identity)
        canonical_id = base_canonical_id
        if canonical_id in groups_by_id and mesh_id not in groups_by_id[canonical_id]["expected_mesh_ids"]:
            canonical_id = normalize_identifier(f"{base_canonical_id}_{mesh_id}")
        material_category = template_material_category(entry)
        if canonical_id not in groups_by_id:
            groups_by_id[canonical_id] = {
                "canonical_material_id_proposal": canonical_id,
                "source_symbols": [],
                "covered_symbols": [],
                "primary_prompt_symbol": symbol,
                "prompt_source_symbols": [],
                "excluded_detail_symbols": [],
                "material_identity_coarse": material_identity,
                "material_category": material_category,
                "raw_material_clues": [],
                "context_clues_for_prompt_llm": [],
                "expected_mesh_ids": [mesh_id],
                "detail_symbol_policy": {
                    "related_detail_symbols": [],
                    "excluded_from_base_prompt": [],
                    "reason": "Template dry-run groups symbols by normalized material identity.",
                },
                "planning_confidence": 0.75,
            }
        group = groups_by_id[canonical_id]
        if symbol not in group["source_symbols"]:
            group["source_symbols"].append(symbol)
        if symbol not in group["covered_symbols"]:
            group["covered_symbols"].append(symbol)
        if symbol not in group["prompt_source_symbols"]:
            group["prompt_source_symbols"].append(symbol)
        if mesh_id not in group["expected_mesh_ids"]:
            group["expected_mesh_ids"].append(mesh_id)
        group["raw_material_clues"].append({"text": entry.get("description"), "source_symbol": symbol, "source_field": "legend.description"})
        symbol_plans.append(
            {
                "symbol": symbol,
                "legend_name": entry.get("name"),
                "tile_semantics": entry.get("description"),
                "generate_geometry": True,
                "mesh_id": mesh_id,
                "selected_mesh_role_tags": role_tags_by_mesh[mesh_id][:3],
                "mesh_selection_reason": "Template dry-run mesh selection from legend and sanitized mesh catalog.",
                "generate_material": True,
                "assigned_canonical_material_id_proposal": canonical_id,
                "material_group_role": "primary_source",
                "raw_legend_clues": [{"text": entry.get("description"), "source_field": "legend.description"}],
                "context_clues": [],
                "planning_reason": "Template dry-run material planning evidence.",
                "confidence": 0.75,
            }
        )
    canonical_groups = list(groups_by_id.values())
    return {"schema_version": LLM1_SCHEMA, "map_id": map_facts["map_id"], "symbol_plans": symbol_plans, "canonical_material_groups": canonical_groups, "warnings": ["Template dry-run only."]}

def template_generate_material(entry: dict[str, Any]) -> bool:
    if entry.get("generate_material") is False:
        return False
    text = " ".join(str(entry.get(key, "")) for key in ("name", "description", "material_family")).lower()
    if any(term in text for term in ("void", "empty", "outside-play-area", "no visible surface", "no geometry")):
        return False
    return True

def template_mesh_id(entry: dict[str, Any], mesh_by_id: dict[str, dict[str, Any]]) -> str | None:
    text = " ".join(str(entry.get(key, "")) for key in ("name", "description", "material_family", "pcg_role")).lower()
    preferences: list[str]
    if any(term in text for term in ("water", "liquid", "stream", "river")):
        preferences = ["water_plane", "tile_plane"]
    elif any(term in text for term in ("door", "gate")):
        preferences = ["door_proxy", "wall_block", "tile_plane"]
    elif any(term in text for term in ("wall", "cliff", "boundary", "block", "blocking")):
        preferences = ["wall_block", "tile_plane"]
    else:
        preferences = ["tile_plane"]
    for mesh_id in preferences:
        if mesh_id in mesh_by_id:
            return mesh_id
    return next(iter(mesh_by_id), None)

def template_material_identity(entry: dict[str, Any]) -> str:
    description = str(entry.get("description") or "").strip()
    name = str(entry.get("name") or "").strip()
    family = str(entry.get("material_family") or "").strip()
    source = description or name or family or "unnamed material"
    words = source.replace(";", " ").replace(",", " ").split()
    return " ".join(words[:12]) or "unnamed material"

def template_material_category(entry: dict[str, Any]) -> str:
    family = str(entry.get("material_family") or "").strip()
    if family:
        return normalize_identifier(family)
    text = " ".join(str(entry.get(key, "")) for key in ("name", "description")).lower()
    if "water" in text:
        return "water_like"
    if "wood" in text:
        return "wood"
    if "grass" in text or "organic" in text:
        return "ground_organic"
    if "stone" in text or "rock" in text:
        return "solid_stone"
    return "generic_material"

def normalize_llm1_plan_shapes(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = copy.deepcopy(plan)
    changes: list[dict[str, Any]] = []
    list_fields = {"selected_mesh_role_tags", "source_symbols", "covered_symbols", "prompt_source_symbols", "excluded_detail_symbols", "expected_mesh_ids", "warnings"}
    scalar_fields = {"schema_version", "map_id", "symbol", "legend_name", "tile_semantics", "mesh_id", "mesh_selection_reason", "assigned_canonical_material_id_proposal", "material_group_role", "planning_reason", "canonical_material_id_proposal", "primary_prompt_symbol", "material_identity_coarse", "material_category"}

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            return {key: normalize_value(key, walk(child, f"{path}.{key}"), f"{path}.{key}") for key, child in value.items()}
        if isinstance(value, list):
            return [walk(child, f"{path}[{idx}]") for idx, child in enumerate(value)]
        return value

    def normalize_value(key: str, value: Any, path: str) -> Any:
        if key in scalar_fields and isinstance(value, list) and len(value) == 1:
            changes.append({"path": path, "from": "single_item_array", "to": "scalar"})
            return value[0]
        if key in list_fields and isinstance(value, str):
            changes.append({"path": path, "from": "string", "to": "single_item_array"})
            return [value]
        if key in list_fields and isinstance(value, list):
            deduped = dedupe_preserve_order(value)
            if deduped != value:
                changes.append({"path": path, "from": "array_with_duplicates", "to": "deduplicated_array"})
            return deduped
        return value

    normalized = walk(normalized, "$")
    repair_symbol_group_references(normalized, changes)
    return normalized, {"schema_version": "d6f_a2_llm1_normalization_report_v1", "changed": bool(changes), "changes": changes}

def repair_symbol_group_references(plan: dict[str, Any], changes: list[dict[str, Any]]) -> None:
    groups = plan.get("canonical_material_groups", [])
    if not isinstance(groups, list):
        return
    group_ids = {group.get("canonical_material_id_proposal") for group in groups if isinstance(group, dict)}
    symbol_to_group: dict[str, str] = {}
    ambiguous: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = group.get("canonical_material_id_proposal")
        if not isinstance(group_id, str):
            continue
        for symbol in dedupe_preserve_order((group.get("source_symbols") or []) + (group.get("covered_symbols") or [])):
            if symbol in symbol_to_group and symbol_to_group[symbol] != group_id:
                ambiguous.add(symbol)
            else:
                symbol_to_group[symbol] = group_id
    for index, symbol_plan in enumerate(plan.get("symbol_plans", [])):
        if not isinstance(symbol_plan, dict) or not symbol_plan.get("generate_material"):
            continue
        assigned = symbol_plan.get("assigned_canonical_material_id_proposal")
        symbol = symbol_plan.get("symbol")
        if assigned not in group_ids and symbol in symbol_to_group and symbol not in ambiguous:
            repaired = symbol_to_group[symbol]
            symbol_plan["assigned_canonical_material_id_proposal"] = repaired
            changes.append(
                {
                    "path": f"$.symbol_plans[{index}].assigned_canonical_material_id_proposal",
                    "from": assigned,
                    "to": repaired,
                    "repair": "single_containing_canonical_group_reference",
                }
            )

def validate_llm1_plan(plan: dict[str, Any], map_facts: dict[str, Any], mesh_snapshot: dict[str, Any]) -> dict[str, Any]:
    errors: list[Any] = []
    details: dict[str, Any] = {"schema_version": "d6f_a2_llm1_validation_details_v1"}
    mesh_by_id = {mesh["mesh_id"]: mesh for mesh in mesh_snapshot["meshes"]}
    used = set(map_facts["used_symbols"])
    if plan.get("schema_version") != LLM1_SCHEMA:
        errors.append(f"schema_version must be {LLM1_SCHEMA}.")
    if plan.get("map_id") != map_facts["map_id"]:
        errors.append("map_id mismatch.")
    forbidden_hits = find_key_hits(plan, LLM1_FORBIDDEN_FIELDS)
    if forbidden_hits:
        errors.append({"forbidden_field_hits": forbidden_hits})
    symbol_plans = plan.get("symbol_plans", [])
    if not isinstance(symbol_plans, list):
        errors.append("symbol_plans must be a list.")
        symbol_plans = []
    symbols = [item.get("symbol") for item in symbol_plans if isinstance(item, dict)]
    if set(symbols) != used:
        errors.append({"symbol_coverage": {"missing": sorted(used - set(symbols)), "extra": sorted(set(symbols) - used)}})
    duplicates = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    if duplicates:
        errors.append({"duplicate_symbols": duplicates})
    groups = plan.get("canonical_material_groups", [])
    if not isinstance(groups, list):
        errors.append("canonical_material_groups must be a list.")
        groups = []
    group_by_id = {group.get("canonical_material_id_proposal"): group for group in groups if isinstance(group, dict)}
    group_ids = list(group_by_id.keys())
    duplicate_group_ids = sorted({gid for gid in group_ids if group_ids.count(gid) > 1})
    if duplicate_group_ids:
        errors.append({"duplicate_canonical_material_ids": duplicate_group_ids})
    symbol_by_id = {item.get("symbol"): item for item in symbol_plans if isinstance(item, dict)}
    for symbol, item in symbol_by_id.items():
        mesh_id = item.get("mesh_id")
        tags = item.get("selected_mesh_role_tags", [])
        if mesh_id is not None and mesh_id not in mesh_by_id:
            errors.append({"symbol": symbol, "mesh_id": mesh_id, "error": "mesh_id not in catalog"})
        if mesh_id is None:
            if item.get("generate_geometry") is not False or tags not in ([], None):
                errors.append({"symbol": symbol, "error": "null mesh requires generate_geometry=false and empty tags"})
        else:
            allowed = set(mesh_by_id[mesh_id].get("role_tags", []))
            if not isinstance(tags, list) or not tags:
                errors.append({"symbol": symbol, "error": "selected_mesh_role_tags must be non-empty"})
            elif any(tag not in allowed for tag in tags):
                errors.append({"symbol": symbol, "invalid_role_tags": [tag for tag in tags if tag not in allowed], "allowed": sorted(allowed)})
        assigned = item.get("assigned_canonical_material_id_proposal")
        if item.get("generate_material") and assigned not in group_by_id:
            errors.append({"symbol": symbol, "assigned_canonical_material_id_proposal": assigned, "error": "missing group"})
        if not item.get("generate_material") and assigned is not None:
            errors.append({"symbol": symbol, "error": "no-material symbol must not assign canonical material"})
    for group_id, group in group_by_id.items():
        if not isinstance(group_id, str) or not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", group_id):
            errors.append({"canonical_material_id_proposal": group_id, "error": "must be lowercase snake_case"})
        source_symbols = group.get("source_symbols", [])
        covered_symbols = group.get("covered_symbols", [])
        prompt_symbols = group.get("prompt_source_symbols", [])
        excluded_symbols = group.get("excluded_detail_symbols", [])
        if not source_symbols or not all(symbol in used for symbol in source_symbols):
            errors.append({"canonical_material_id_proposal": group_id, "error": "invalid source_symbols"})
        if not covered_symbols or not all(symbol in used for symbol in covered_symbols):
            errors.append({"canonical_material_id_proposal": group_id, "error": "invalid covered_symbols"})
        if group.get("primary_prompt_symbol") not in source_symbols:
            errors.append({"canonical_material_id_proposal": group_id, "error": "primary_prompt_symbol must be in source_symbols"})
        if not prompt_symbols or any(symbol not in source_symbols for symbol in prompt_symbols):
            errors.append({"canonical_material_id_proposal": group_id, "error": "prompt_source_symbols must be a non-empty subset of source_symbols"})
        if any(symbol not in covered_symbols for symbol in excluded_symbols):
            errors.append({"canonical_material_id_proposal": group_id, "error": "excluded_detail_symbols must be a subset of covered_symbols"})
        expected_mesh_ids = sorted(group.get("expected_mesh_ids", []))
        actual_mesh_ids = sorted({symbol_by_id[symbol].get("mesh_id") for symbol in source_symbols if symbol in symbol_by_id and symbol_by_id[symbol].get("mesh_id") is not None})
        if expected_mesh_ids != actual_mesh_ids:
            errors.append({"canonical_material_id_proposal": group_id, "expected_mesh_ids": expected_mesh_ids, "actual_source_mesh_ids": actual_mesh_ids, "error": "expected_mesh_ids mismatch"})
        orientation_records = [resolve_mesh_surface_orientation(mesh_by_id[mesh_id]) for mesh_id in expected_mesh_ids if mesh_id in mesh_by_id]
        details.setdefault("surface_orientation_audit", []).append(
            {
                "canonical_material_id_proposal": group_id,
                "expected_mesh_ids": expected_mesh_ids,
                "orientations": sorted({record["surface_orientation"] for record in orientation_records}),
                "sources": sorted({record["surface_orientation_source"] for record in orientation_records}),
                "blocking": False,
            }
        )
    details["mesh_id_all_from_catalog"] = not any(isinstance(error, dict) and "mesh_id" in error for error in errors)
    details["selected_mesh_role_tags_valid"] = not any(isinstance(error, dict) and "invalid_role_tags" in error for error in errors)
    details["legacy_material_name_collisions"] = sorted(
        group_id for group_id in group_by_id if group_id in OLD_FIXED_SLOT_IDS
    )
    summary = {
        "schema_version": "d6f_a2_llm1_validation_summary_v1",
        "passed": not errors,
        "errors": errors,
        "surface_terms_absent": not find_key_hits(plan, {"surface_terms"}),
        "mesh_id_all_from_catalog": details["mesh_id_all_from_catalog"],
        "selected_mesh_role_tags_valid": details["selected_mesh_role_tags_valid"],
        "legacy_material_name_collisions": details["legacy_material_name_collisions"],
        "legacy_material_name_collisions_blocking": False,
    }
    details["summary"] = summary
    return details

def resolve_dynamic_outputs(plan: dict[str, Any], full_mesh_catalog: dict[str, Any], map_facts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
    group_by_id = {group["canonical_material_id_proposal"]: group for group in plan["canonical_material_groups"]}
    used_ids: dict[str, int] = {}
    id_map: dict[str, dict[str, str]] = {}
    normalization = {"schema_version": "canonical_id_normalization_report_v2", "items": []}
    for original in group_by_id:
        normalized = normalize_identifier(original)
        base = normalized
        if normalized in used_ids:
            used_ids[normalized] += 1
            normalized = f"{base}_{used_ids[base]}"
        else:
            used_ids[normalized] = 1
        item = {"original": original, "canonical_material_id": normalized, "material_slot_id": f"mat_{normalized}"}
        id_map[original] = item
        normalization["items"].append(item)
    mesh_by_id = {mesh["mesh_id"]: mesh for mesh in full_mesh_catalog["meshes"]}
    materials = []
    for original, group in group_by_id.items():
        ids = id_map[original]
        materials.append(
            {
                "canonical_material_id": ids["canonical_material_id"],
                "original_canonical_material_id_proposal": original,
                "material_slot_id": ids["material_slot_id"],
                "source_symbols": group.get("source_symbols", []),
                "covered_symbols": group.get("covered_symbols", []),
                "primary_prompt_symbol": group.get("primary_prompt_symbol"),
                "prompt_source_symbols": group.get("prompt_source_symbols", []),
                "excluded_detail_symbols": group.get("excluded_detail_symbols", []),
                "material_identity_coarse": group.get("material_identity_coarse"),
                "material_category": group.get("material_category"),
                "raw_material_clues": group.get("raw_material_clues", []),
                "context_clues_for_prompt_llm": group.get("context_clues_for_prompt_llm", []),
                "expected_mesh_ids": group.get("expected_mesh_ids", []),
                "detail_symbol_policy": group.get("detail_symbol_policy", {}),
                "source": LLM1_SCHEMA,
            }
        )
    alias_map = {symbol: symbol_alias(symbol) for symbol in map_facts["used_symbols"]}
    tiles = []
    for item in plan["symbol_plans"]:
        symbol = item["symbol"]
        mesh_id = item.get("mesh_id")
        mesh = mesh_by_id.get(mesh_id, {}) if mesh_id else {}
        assigned = item.get("assigned_canonical_material_id_proposal")
        ids = id_map.get(assigned)
        if item.get("generate_geometry") is False:
            tile_type_id = f"tile_{map_facts['map_id']}_{alias_map[symbol]}_no_geometry"
        elif ids:
            tile_type_id = f"tile_{map_facts['map_id']}_{alias_map[symbol]}_{ids['canonical_material_id']}"
        else:
            tile_type_id = f"tile_{map_facts['map_id']}_{alias_map[symbol]}_no_material"
        tiles.append(
            {
                "symbol": symbol,
                "legend_name": item.get("legend_name"),
                "tile_type_id": tile_type_id,
                "tile_semantics": item.get("tile_semantics"),
                "mesh_id": mesh_id,
                "selected_mesh_role_tags": item.get("selected_mesh_role_tags", []),
                "shape_type": mesh.get("shape_type"),
                "height_class": mesh.get("height_class"),
                "height": mesh.get("default_height"),
                "z_offset": mesh.get("default_z_offset"),
                "generate_geometry": item.get("generate_geometry"),
                "generate_material": item.get("generate_material"),
                "canonical_material_id": ids["canonical_material_id"] if ids else None,
                "material_slot_id": ids["material_slot_id"] if ids else None,
            }
        )
    return (
        {"schema_version": "resolved_materials_v2", "map_id": map_facts["map_id"], "id_policy": "material_slot_id = mat_<normalized_canonical_material_id>", "materials": materials},
        {"schema_version": "resolved_tileset_v2", "map_id": map_facts["map_id"], "id_policy": "tile_type_id = tile_<map_id>_<symbol_alias>_<normalized_canonical_material_id>", "tiles": tiles},
        normalization,
        alias_map,
    )

def build_dynamic_material_evidence(plan: dict[str, Any], resolved_materials: dict[str, Any], resolved_tileset: dict[str, Any], map_facts: dict[str, Any], full_mesh_catalog: dict[str, Any]) -> dict[str, Any]:
    legend_by_symbol = {entry["symbol"]: entry for entry in map_facts["legend_entries"]}
    mesh_by_id = {mesh["mesh_id"]: mesh for mesh in full_mesh_catalog["meshes"]}
    tile_by_slot: dict[str, list[dict[str, Any]]] = {}
    for tile in resolved_tileset["tiles"]:
        if tile.get("material_slot_id"):
            tile_by_slot.setdefault(tile["material_slot_id"], []).append(tile)
    slots = []
    for material in resolved_materials["materials"]:
        material_slot_id = material["material_slot_id"]
        tiles = tile_by_slot.get(material_slot_id, [])
        mesh_ids = sorted({tile["mesh_id"] for tile in tiles if tile.get("mesh_id")})
        mesh_context = [
            {
                "mesh_id": mesh_id,
                "shape_type": mesh_by_id.get(mesh_id, {}).get("shape_type"),
                "height_class": mesh_by_id.get(mesh_id, {}).get("height_class"),
                "role_tags": mesh_by_id.get(mesh_id, {}).get("role_tags", []),
                **resolve_mesh_surface_orientation(mesh_by_id.get(mesh_id, {})),
            }
            for mesh_id in mesh_ids
        ]
        orientation_values = sorted({item["surface_orientation"] for item in mesh_context})
        primary_symbol = material.get("primary_prompt_symbol")
        primary_mesh_id = next(
            (tile.get("mesh_id") for tile in tiles if tile.get("symbol") == primary_symbol and tile.get("mesh_id")),
            None,
        )
        primary_context = next((item for item in mesh_context if item["mesh_id"] == primary_mesh_id), None)
        selected_orientation = (
            primary_context["surface_orientation"]
            if primary_context
            else orientation_values[0] if orientation_values else "horizontal_surface"
        )
        selected_orientation_source = (
            primary_context["surface_orientation_source"]
            if primary_context
            else mesh_context[0]["surface_orientation_source"] if mesh_context else "legacy_shape_view_mode_fallback"
        )
        orientation_warnings = []
        if len(orientation_values) > 1:
            orientation_warnings.append(
                {
                    "warning": "material group covers multiple surface orientations",
                    "orientations": orientation_values,
                    "selected_from_primary_prompt_symbol": primary_symbol,
                    "blocking": False,
                }
            )
        slots.append(
            {
                "material_slot_id": material_slot_id,
                "canonical_material_id": material["canonical_material_id"],
                "source_symbols": material.get("source_symbols", []),
                "covered_symbols": material.get("covered_symbols", []),
                "primary_prompt_symbol": material.get("primary_prompt_symbol"),
                "prompt_source_symbols": material.get("prompt_source_symbols", []),
                "excluded_detail_symbols": material.get("excluded_detail_symbols", []),
                "prompt_source_legend_entries": [legend_by_symbol[symbol] for symbol in material.get("prompt_source_symbols", []) if symbol in legend_by_symbol],
                "excluded_detail_legend_entries": [legend_by_symbol[symbol] for symbol in material.get("excluded_detail_symbols", []) if symbol in legend_by_symbol],
                "target_mesh_ids": mesh_ids,
                "target_mesh_context": mesh_context,
                "surface_orientation": selected_orientation,
                "surface_orientation_source": selected_orientation_source,
                "target_surface_orientations": orientation_values,
                "surface_orientation_warnings": orientation_warnings,
                "material_identity_coarse": material.get("material_identity_coarse"),
                "material_category": material.get("material_category"),
                "raw_material_clues": material.get("raw_material_clues", []),
                "context_clues_for_prompt_llm": material.get("context_clues_for_prompt_llm", []),
                "prompt_generation_contract": {
                    "use_raw_clues_as_evidence": True,
                    "do_not_directly_copy_context_clues": True,
                    "generate_reusable_surface_material_only": True,
                    "second_llm_must_select_prompt_ready_terms": True,
                },
                "provenance": {
                    "material_group_source": LLM1_SCHEMA,
                    "material_slot_id_source": "python_dynamic_id_policy",
                    "mesh_context_source": "mesh_catalog",
                    "surface_orientation_source": selected_orientation_source,
                },
            }
        )
    return {"schema_version": "dynamic_material_slot_evidence_v3", "map_id": map_facts["map_id"], "material_slots": slots}

def build_prompt_llm_input(dynamic_evidence: dict[str, Any], map_facts: dict[str, Any]) -> dict[str, Any]:
    slots = dynamic_evidence["material_slots"]
    return {
        "schema_version": "prompt_llm_input_v3",
        "map_id": map_facts["map_id"],
        "task_boundary": {
            "task": "Generate backend-specific prompt briefs from dynamic map-derived material evidence.",
            "no_image_generation": True,
            "do_not_change_material_slots": True,
            "do_not_copy_context_clues_directly": True,
        },
        "output_schema": {"schema_version": LLM2_SCHEMA},
        "hard_rules": [
            "Use only dynamic material evidence provided here.",
            "Do not use old fixed material slots, old prompt hints, or previous experiment knowledge.",
            "Context clues are explanation only; convert to reusable material surface language or reject them.",
            "Material identity and appearance terms belong on the positive side. Do not place them in negative/rejected/avoid fields.",
            "Use negative/rejected/avoid fields only for true object, hardware, scene, camera, or unrelated context terms.",
        ],
        "used_symbols": map_facts["used_symbols"],
        "symbol_semantic_evidence": [
            {"symbol": entry["symbol"], "legend_name": entry.get("name"), "description": entry.get("description")}
            for entry in map_facts["legend_entries"]
        ],
        "map_evidence": {"map_id": map_facts["map_id"], "style_text": map_facts.get("style_text", "")},
        "dynamic_material_slots": [
            {
                "material_slot_id": slot["material_slot_id"],
                "canonical_material_id": slot["canonical_material_id"],
                "surface_orientation": slot["surface_orientation"],
                "surface_orientation_source": slot["surface_orientation_source"],
            }
            for slot in slots
        ],
        "material_slot_evidence": slots,
        "evidence_provenance": "dynamic_material_slot_evidence_v3",
    }

def validate_dynamic_evidence(evidence: dict[str, Any], resolved_materials: dict[str, Any]) -> dict[str, Any]:
    errors: list[Any] = []
    expected_slots = {material["material_slot_id"] for material in resolved_materials["materials"]}
    actual_slots = {slot.get("material_slot_id") for slot in evidence.get("material_slots", [])}
    if expected_slots != actual_slots:
        errors.append({"slot_mismatch": {"expected": sorted(expected_slots), "actual": sorted(actual_slots)}})
    for slot in evidence.get("material_slots", []):
        material_slot_id = slot.get("material_slot_id", "")
        if not re.fullmatch(r"mat_[a-z0-9_]+", material_slot_id):
            errors.append({"material_slot_id": material_slot_id, "error": "must be dynamic mat_* id"})
        orientation = slot.get("surface_orientation")
        if orientation not in SURFACE_ORIENTATIONS:
            errors.append(
                {
                    "material_slot_id": material_slot_id,
                    "surface_orientation": orientation,
                    "error": "invalid resolved surface_orientation",
                }
            )
    legacy_name_collisions = sorted(
        slot.get("material_slot_id")
        for slot in evidence.get("material_slots", [])
        if slot.get("material_slot_id", "").replace("mat_", "", 1) in OLD_FIXED_SLOT_IDS
    )
    return {
        "schema_version": "dynamic_material_slot_evidence_validation_v1",
        "passed": not errors,
        "errors": errors,
        "legacy_material_name_collisions": legacy_name_collisions,
        "legacy_material_name_collisions_blocking": False,
    }

def audit_prompt_llm_input(prompt_input: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(prompt_input, ensure_ascii=False)
    forbidden_hits = [term for term in ["suggested_prompt_hint", "source_policy_reason", "EXPECTED_SLOTS", "SLOT_VIEW_MODE"] if term in text]
    old_slot_id_matches = [
        slot["material_slot_id"]
        for slot in prompt_input.get("material_slot_evidence", [])
        if slot.get("material_slot_id", "").replace("mat_", "", 1) in OLD_FIXED_SLOT_IDS
    ]
    return {
        "schema_version": "prior_leak_audit_for_prompt_llm_v1",
        "passed": not forbidden_hits,
        "forbidden_term_hits": forbidden_hits,
        "old_fixed_slot_id_matches": old_slot_id_matches,
        "old_fixed_slot_id_matches_blocking": False,
        "material_slot_evidence_is_dynamic": prompt_input.get("evidence_provenance") == "dynamic_material_slot_evidence_v3",
    }

def sanitize_payload_for_disk(payload: dict[str, Any]) -> dict[str, Any]:
    clone = copy.deepcopy(payload)
    for key in list(clone.keys()):
        if "authorization" in key.lower() or "api" in key.lower() and "key" in key.lower():
            clone[key] = "[redacted]"
    return clone

def audit_payload_inputs(map_facts: dict[str, Any], mesh_snapshot: dict[str, Any]) -> dict[str, Any]:
    data_text = json.dumps({"map_facts_v2": map_facts, "mesh_catalog_snapshot_for_llm": mesh_snapshot}, ensure_ascii=False)
    hits = [term for term in PRIOR_FORBIDDEN_DATA_TERMS if term.lower() in data_text.lower()]
    return {
        "schema_version": "d6f_a3_llm1_request_prior_leak_audit_v1",
        "passed": not hits,
        "forbidden_data_hits": hits,
        "material_slot_rules_excluded": "material_slot_rules" not in data_text.lower(),
        "old_material_slot_evidence_excluded": "material_slot_evidence" not in data_text.lower(),
        "note": "Legend names may look like legacy slots only as raw map facts, never as runtime slot IDs or fixed slot lists.",
    }

def normalize_identifier(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return re.sub(r"_+", "_", text).strip("_") or "unnamed_material"

def symbol_alias(symbol: str) -> str:
    if symbol in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[symbol]
    if re.fullmatch(r"[A-Za-z0-9]+", symbol):
        return symbol.lower()
    return "u" + "_".join(f"{ord(ch):x}" for ch in symbol)

def view_mode_for_mesh_context(mesh_context: list[dict[str, Any]]) -> str:
    shapes = {item.get("shape_type") for item in mesh_context}
    if "vertical_prop" in shapes:
        return "front_facing_panel_surface"
    if "vertical_block" in shapes:
        return "front_facing_closeup_surface"
    return "top_down_closeup_surface"

def view_mode_for_mesh_id(mesh_id: str, mesh_by_id: dict[str, dict[str, Any]]) -> str:
    shape_type = mesh_by_id.get(mesh_id, {}).get("shape_type")
    return VIEW_MODE_BY_SHAPE.get(shape_type, "top_down_closeup_surface")


def resolve_mesh_surface_orientation(mesh: dict[str, Any]) -> dict[str, Any]:
    declared = mesh.get("surface_orientation")
    if declared in SURFACE_ORIENTATIONS:
        return {
            "surface_orientation": declared,
            "surface_orientation_source": "mesh_catalog",
            "legacy_view_mode_fallback": None,
        }
    legacy_view_mode = VIEW_MODE_BY_SHAPE.get(mesh.get("shape_type"), "top_down_closeup_surface")
    return {
        "surface_orientation": SURFACE_ORIENTATION_BY_LEGACY_VIEW_MODE[legacy_view_mode],
        "surface_orientation_source": "legacy_shape_view_mode_fallback",
        "legacy_view_mode_fallback": legacy_view_mode,
    }

def find_key_hits(value: Any, forbidden: set[str], path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden:
                hits.append(f"{path}.{key}")
            hits.extend(find_key_hits(child, forbidden, f"{path}.{key}"))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            hits.extend(find_key_hits(child, forbidden, f"{path}[{idx}]"))
    return hits

def dedupe_preserve_order(items: list[Any]) -> list[Any]:
    output: list[Any] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output

