"""Material Manifest and UE RuntimeData export integration.

This stage is export-only. It reads a successful material
generation preview run, selects deterministic material candidates, and writes a
UE-copyable RuntimeData package without calling LLMs or image backends.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from visualoptimise.artifacts import ensure_dirs, normalize_map_ids, read_json, timestamp_for_run, timestamp_iso, write_json, write_text
from visualoptimise.backend_config import load_backend_paths, ue_copy_destination
from visualoptimise.runtime_export_base import build_map_package_index, copy_runtime_snapshot, refresh_runtime_data
from visualoptimise.runtime_validation import validate_no_authoring_files, validate_runtime_data


STAGE_ID = "runtime_export"
COMPATIBILITY_ID = "d6g_a2_material_manifest_runtime_export"
MATERIAL_GENERATION_STAGE_ID = "material_generation"
MATERIAL_GENERATION_COMPATIBILITY_ID = "d6f_a4_full_two_llm_material_generation_preview"
ROUND_ID = STAGE_ID
RUN_SUFFIX = STAGE_ID
SUMMARY_SCHEMA = "runtime_export_summary_v1"
SUMMARY_FILENAME = "runtime_export_summary.json"
REPORT_FILENAME = "runtime_export_report.md"
LEGACY_SUMMARY_FILENAME = f"{COMPATIBILITY_ID}_summary.json"
LEGACY_REPORT_FILENAME = f"{COMPATIBILITY_ID}_report.md"
MATERIAL_MANIFEST_SCHEMA = "material_manifest_v1"
RUNTIME_MAP_PACKAGE_SCHEMA = "runtime_map_package_v1"
RESOLVED_TILESET_EXPORT_SCHEMA = "resolved_tileset_v1"
SELECTION_POLICY = "first_available_seed"
SUPPORTED_BACKENDS = {"sd15", "stablematerials"}
PBR_MAPS = ("basecolor", "normal", "roughness", "height", "metallic")

RUN_DIRS = {
    "run": "00_run",
    "source": "01_source_run",
    "manifest": "02_material_manifest",
    "runtime": "03_runtime_data_package",
    "validation": "04_validation",
    "reports": "05_reports",
}


def run_experiment(
    pipeline: Any,
    fallback_map_id: str,
    map_ids: list[str] | None,
    dry_run: bool,
    reuse_materials_from: Path | None,
    refresh_runtime_data: bool,
    runtime_texture_backend: str,
    include_backend_candidates: bool = True,
    map_root: Path | None = None,
) -> Path:
    maps = normalize_map_ids(map_ids, fallback_map_id)
    if len(maps) != 1:
        raise ValueError("Runtime export handles one material-generation map package per run.")
    map_id = maps[0]
    if runtime_texture_backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported runtime texture backend: {runtime_texture_backend}.")

    source_run = select_source_run(pipeline.output_dir, map_id, reuse_materials_from)
    run_dir = pipeline.output_dir / f"{timestamp_for_run()}_{map_id}_{RUN_SUFFIX}"
    paths = {name: run_dir / rel for name, rel in RUN_DIRS.items()}
    ensure_dirs(paths)
    print(f"[VisualOptimise] Runtime export: using source run {source_run}")

    command = build_command(pipeline.root, map_id, source_run, dry_run, refresh_runtime_data, runtime_texture_backend, include_backend_candidates, map_root)
    write_text(paths["run"] / "command.txt", command)
    write_json(
        paths["run"] / "run_config.json",
        {
            "schema_version": "d6g_a2_run_config_v1",
            "created_at": timestamp_iso(),
            "stage_id": STAGE_ID,
            "round_id": ROUND_ID,
            "compatibility_id": COMPATIBILITY_ID,
            "map_id": map_id,
            "dry_run": dry_run,
            "source_run": str(source_run),
            "runtime_texture_backend": runtime_texture_backend,
            "include_backend_candidates": include_backend_candidates,
            "selection_policy": SELECTION_POLICY,
            "refresh_runtime_data": refresh_runtime_data and not dry_run,
            "llm_calls": 0,
            "sd_webui_generation_calls": 0,
            "stablematerials_generation_calls": 0,
        },
    )

    source = load_and_validate_source_run(source_run, map_id)
    write_json(paths["source"] / "source_run_validation.json", source["validation"])
    write_json(paths["source"] / "source_artifact_index.json", source["artifact_index"])
    write_json(paths["run"] / "source_run_reference.json", {"source_run": str(source_run), "selected_automatically": source["selected_automatically"]})

    material_selection = build_material_selection(
        source_run=source_run,
        resolved_materials=source["resolved_materials"],
        compiled_sd15=source["compiled_sd15"],
        compiled_stablematerials=source["compiled_stablematerials"],
        summary=source["summary"],
        runtime_texture_backend=runtime_texture_backend,
        include_backend_candidates=include_backend_candidates,
    )
    write_json(paths["manifest"] / "material_selection_report.json", material_selection["selection_report"])
    write_json(paths["manifest"] / "material_candidates_index.json", material_selection["candidates_index"])

    resolved_tileset_export = convert_resolved_tileset_for_runtime(
        project_root=pipeline.root,
        map_id=map_id,
        map_facts=source["map_facts"],
        resolved_tileset_v2=source["resolved_tileset"],
        resolved_materials=source["resolved_materials"],
    )
    material_manifest = build_material_manifest(
        map_id=map_id,
        source_run=source_run,
        resolved_materials=source["resolved_materials"],
        compiled_sd15=source["compiled_sd15"],
        compiled_stablematerials=source["compiled_stablematerials"],
        material_selection=material_selection,
        runtime_texture_backend=runtime_texture_backend,
    )

    if dry_run:
        write_json(paths["manifest"] / "material_manifest_planned.json", material_manifest)
        runtime_package_created = False
        generated_runtime_data_refreshed = False
        runtime_data_validation = dry_runtime_validation(source, material_selection)
        material_manifest_validation = validate_material_manifest(material_manifest, require_runtime_files=False, material_dir=None)
        ue_structure_validation = {"passed": True, "errors": [], "warnings": ["Dry-run did not create runtime package files."]}
        copy_instructions_path = paths["runtime"] / "copy_to_ue_instructions.md"
        write_text(copy_instructions_path, build_copy_instructions(pipeline.root, paths["runtime"], dry_run=True))
        runtime_package_path = paths["runtime"]
        runtime_snapshot_path = None
    else:
        print(f"[VisualOptimise] Runtime export: packaging RuntimeData for {map_id}")
        runtime_package_path = paths["runtime"]
        prepare_runtime_package(
            project_root=pipeline.root,
            runtime_root=runtime_package_path,
            map_id=map_id,
            source=source,
            resolved_tileset_export=resolved_tileset_export,
            material_manifest=material_manifest,
            material_selection=material_selection,
            map_root=map_root,
        )
        copy_material_textures(runtime_package_path / "maps" / map_id / "materials", material_selection)
        write_json(paths["manifest"] / "material_manifest.json", material_manifest)
        write_json(runtime_package_path / "runtime_data_export_manifest.json", build_runtime_export_manifest(pipeline.root, run_dir, source_run, map_id, runtime_texture_backend))
        write_text(runtime_package_path / "copy_to_ue_instructions.md", build_copy_instructions(pipeline.root, runtime_package_path, dry_run=False))
        copy_instructions_path = runtime_package_path / "copy_to_ue_instructions.md"

        expected_maps = discover_runtime_maps(runtime_package_path)
        runtime_data_validation = validate_runtime_data(runtime_package_path, expected_maps=expected_maps, require_texture_files=True)
        authoring_validation = validate_no_authoring_files(runtime_package_path)
        ue_structure_validation = merge_validations("ue_copyable_structure_validation_v1", [runtime_data_validation, authoring_validation])
        material_manifest_validation = validate_material_manifest(material_manifest, require_runtime_files=True, material_dir=runtime_package_path / "maps" / map_id / "materials")

        generated_runtime_data_refreshed = False
        runtime_snapshot_path = pipeline.root / "generated" / "ue_ready" / "runtime_data_runs" / f"{run_dir.name}"
        copy_runtime_snapshot(runtime_package_path, runtime_snapshot_path)
        if refresh_runtime_data:
            target_runtime_data = pipeline.root / "generated" / "ue_ready" / "runtime_data"
            refresh_runtime_data_fn(pipeline.root, runtime_package_path, target_runtime_data)
            generated_runtime_data_refreshed = True
            print(f"[VisualOptimise] Runtime export: refreshed latest RuntimeData at {target_runtime_data}")
        runtime_package_created = True

    prior_leak_audit = build_prior_leak_audit(source)
    write_json(paths["validation"] / "runtime_data_schema_validation.json", runtime_data_validation)
    write_json(paths["validation"] / "material_manifest_validation.json", material_manifest_validation)
    write_json(paths["validation"] / "ue_copyable_structure_validation.json", ue_structure_validation)
    write_json(paths["validation"] / "prior_leak_audit.json", prior_leak_audit)

    validation_passed = all(
        item.get("passed", False)
        for item in [source["validation"], material_manifest_validation, runtime_data_validation, ue_structure_validation, prior_leak_audit]
    )
    if dry_run:
        validation_passed = source["validation"].get("passed", False) and material_manifest_validation.get("passed", False) and prior_leak_audit.get("passed", False)

    summary = build_summary(
        run_dir=run_dir,
        command=command,
        map_id=map_id,
        source=source,
        material_selection=material_selection,
        runtime_texture_backend=runtime_texture_backend,
        runtime_package_path=runtime_package_path,
        runtime_snapshot_path=runtime_snapshot_path,
        material_manifest_path=(paths["manifest"] / ("material_manifest_planned.json" if dry_run else "material_manifest.json")),
        copy_instructions_path=copy_instructions_path,
        runtime_package_created=runtime_package_created,
        generated_runtime_data_refreshed=generated_runtime_data_refreshed,
        validation_passed=validation_passed,
        dry_run=dry_run,
    )
    report = build_report(summary, material_selection)
    write_json(paths["reports"] / SUMMARY_FILENAME, summary)
    write_text(paths["reports"] / REPORT_FILENAME, report)
    write_json(paths["reports"] / LEGACY_SUMMARY_FILENAME, summary)
    write_text(paths["reports"] / LEGACY_REPORT_FILENAME, report)
    write_json(paths["run"] / "key_outputs_index.json", build_key_outputs_index(paths, summary))

    if summary["status"] != "passed":
        raise RuntimeError(f"Runtime export failed. See {summary['summary_path']}")
    return run_dir


def select_source_run(output_dir: Path, map_id: str, reuse_materials_from: Path | None) -> Path:
    if reuse_materials_from:
        return reuse_materials_from.resolve()
    candidates = sorted(
        list(output_dir.glob(f"*_{map_id}_{MATERIAL_GENERATION_STAGE_ID}"))
        + list(output_dir.glob(f"*_{map_id}_{MATERIAL_GENERATION_COMPATIBILITY_ID}")),
        key=lambda path: path.name,
        reverse=True,
    )
    for candidate in candidates:
        summary_path = material_generation_summary_path(candidate)
        if summary_path.is_file() and read_json(summary_path).get("status") == "passed":
            return candidate
    raise FileNotFoundError(
        f"No successful material generation source run found for {map_id}. "
        "Run --generate-materials first or pass --reuse-materials-from explicitly."
    )


def load_and_validate_source_run(source_run: Path, map_id: str) -> dict[str, Any]:
    source_run = source_run.resolve()
    source_summary_path = material_generation_summary_path(source_run)
    required = {
        "summary": source_summary_path,
        "map_facts": source_run / "01_map_facts" / "map_facts_v2.json",
        "llm_tile_material_plan": source_run / "02_llm1_material_plan" / "llm_tile_material_plan_v2.json",
        "resolved_tileset": source_run / "03_python_resolver" / "resolved_tileset_v2.json",
        "resolved_materials": source_run / "03_python_resolver" / "resolved_materials_v2.json",
        "dynamic_material_slot_evidence": source_run / "04_dynamic_material_evidence" / "dynamic_material_slot_evidence_v3.json",
        "material_prompt_briefs": source_run / "05_llm2_prompt_briefs" / "material_prompt_briefs_v4.json",
        "compiled_sd15": source_run / "06_compiled_prompts" / "compiled_sd15_prompts_v4.json",
        "compiled_stablematerials": source_run / "06_compiled_prompts" / "compiled_stablematerials_prompts_v4.json",
        "prior_leak_audit": source_run / "09_analysis" / "prior_leak_audit.json",
    }
    errors = []
    artifacts = {}
    for key, path in required.items():
        artifacts[key] = str(path)
        if not path.is_file():
            errors.append(f"Missing required source artifact: {path}")
    if errors:
        raise RuntimeError(f"Source run validation failed: {errors}")
    summary = read_json(required["summary"])
    if summary.get("status") != "passed":
        errors.append(f"Source run status is not passed: {summary.get('status')}")
    if summary.get("map_id") != map_id:
        errors.append(f"Source run map_id {summary.get('map_id')} does not match requested {map_id}.")
    if summary.get("llm1_called") is not True or summary.get("llm2_called") is not True:
        errors.append("Source run does not look like a complete two-LLM material generation preview.")
    validation = {
        "schema_version": "d6g_a2_source_run_validation_v1",
        "passed": not errors,
        "errors": errors,
        "warnings": [],
        "source_run": str(source_run),
        "source_run_status": summary.get("status"),
        "source_map_id": summary.get("map_id"),
    }
    payload = {key: read_json(path) for key, path in required.items() if key not in {"summary"}}
    payload["summary"] = summary
    payload["validation"] = validation
    payload["artifact_index"] = artifacts
    payload["selected_automatically"] = False
    if errors:
        raise RuntimeError(f"Source run validation failed: {errors}")
    return payload


def material_generation_summary_path(source_run: Path) -> Path:
    reports_dir = source_run / "10_reports"
    preferred = reports_dir / "material_generation_summary.json"
    if preferred.is_file():
        return preferred
    return reports_dir / f"{MATERIAL_GENERATION_COMPATIBILITY_ID}_summary.json"


def build_material_selection(
    source_run: Path,
    resolved_materials: dict[str, Any],
    compiled_sd15: dict[str, Any],
    compiled_stablematerials: dict[str, Any],
    summary: dict[str, Any],
    runtime_texture_backend: str,
    include_backend_candidates: bool,
) -> dict[str, Any]:
    display_labels = summary.get("display_labels", {})
    material_entries = resolved_materials.get("materials", [])
    sd_prompts = {item["material_slot_id"]: item for item in compiled_sd15.get("prompts", [])}
    sm_prompts = {item["material_slot_id"]: item for item in compiled_stablematerials.get("prompts", [])}
    records = []
    candidate_rows = []
    errors = []
    warnings = []
    for material in material_entries:
        slot_id = material["material_slot_id"]
        display_label = display_labels.get(slot_id, slot_id.removeprefix("mat_").removesuffix("_material"))
        sd_candidates = discover_sd15_candidates(source_run, slot_id)
        sm_candidates = discover_stablematerials_candidates(source_run, slot_id)
        if not sd_candidates:
            errors.append(f"{slot_id}: no SD1.5 candidates found.")
        if not sm_candidates:
            warnings.append(f"{slot_id}: no StableMaterials candidates found.")
        selected_backend = runtime_texture_backend
        selected = None
        if selected_backend == "sd15":
            selected = sd_candidates[0] if sd_candidates else None
        elif selected_backend == "stablematerials":
            selected = sm_candidates[0] if sm_candidates else None
        if selected is None:
            errors.append(f"{slot_id}: no selected candidate for backend {selected_backend}.")
        record = {
            "material_slot_id": slot_id,
            "canonical_material_id": material.get("canonical_material_id"),
            "display_label": display_label,
            "selected_backend": selected_backend,
            "selected": selected,
            "sd15_candidates": sd_candidates,
            "stablematerials_candidates": sm_candidates,
            "compiled_prompt_refs": {
                "sd15": sd_prompts.get(slot_id, {}),
                "stablematerials": sm_prompts.get(slot_id, {}),
            },
            "source_material": material,
            "selection_policy": SELECTION_POLICY,
        }
        records.append(record)
        candidate_rows.append(
            {
                "material_slot_id": slot_id,
                "display_label": display_label,
                "selected_backend": selected_backend,
                "selected_seed": selected.get("seed") if selected else None,
                "sd15_candidate_count": len(sd_candidates),
                "stablematerials_candidate_count": len(sm_candidates),
                "include_backend_candidates": include_backend_candidates,
            }
        )
    return {
        "records": records,
        "errors": errors,
        "warnings": warnings,
        "selection_report": {
            "schema_version": "d6g_a2_material_selection_report_v1",
            "passed": not errors,
            "selection_policy": SELECTION_POLICY,
            "runtime_texture_backend": runtime_texture_backend,
            "material_count": len(records),
            "errors": errors,
            "warnings": warnings,
            "rows": candidate_rows,
        },
        "candidates_index": {
            "schema_version": "d6g_a2_material_candidates_index_v1",
            "source_run": str(source_run),
            "selection_policy": SELECTION_POLICY,
            "runtime_texture_backend": runtime_texture_backend,
            "include_backend_candidates": include_backend_candidates,
            "materials": [
                {
                    "material_slot_id": record["material_slot_id"],
                    "display_label": record["display_label"],
                    "selected": record["selected"],
                    "sd15_candidates": record["sd15_candidates"],
                    **({"stablematerials_candidates": record["stablematerials_candidates"]} if record["stablematerials_candidates"] else {}),
                }
                for record in records
            ],
        },
    }


def discover_sd15_candidates(source_run: Path, slot_id: str) -> list[dict[str, Any]]:
    slot_dir = source_run / "07_generation" / "sd15" / "plan_a" / slot_id
    candidates = []
    for image_path in sorted(slot_dir.glob("seed_*.png")):
        seed = parse_seed_from_path(image_path)
        candidates.append(
            {
                "backend": "sd15",
                "plan_id": "plan_a",
                "seed": seed,
                "textures": {"basecolor": str(image_path)},
                "source_metadata": str(image_path.with_name(f"{image_path.stem}_metadata.json")),
            }
        )
    return candidates


def discover_stablematerials_candidates(source_run: Path, slot_id: str) -> list[dict[str, Any]]:
    backend_root = source_run / "07_generation" / "stablematerials"
    slot_dir = backend_root / slot_id
    candidates = []
    seed_dirs = []
    if slot_dir.is_dir():
        seed_dirs.extend(path for path in slot_dir.glob("seed_*") if path.is_dir())
    seed_dirs.extend(path for path in backend_root.glob(f"stablematerials_{slot_id}_seed_*") if path.is_dir())
    for seed_dir in sorted(seed_dirs):
        if not seed_dir.is_dir():
            continue
        textures = {}
        missing = []
        for texture_type in PBR_MAPS:
            path = seed_dir / f"{texture_type}.png"
            if path.is_file():
                textures[texture_type] = str(path)
            else:
                missing.append(texture_type)
        if "basecolor" not in textures:
            continue
        candidates.append(
            {
                "backend": "stablematerials",
                "plan_id": "stablematerials",
                "seed": parse_seed_from_path(seed_dir),
                "textures": textures,
                "missing_maps": missing,
                "source_metadata": str(seed_dir / "metadata.json"),
            }
        )
    return candidates


def parse_seed_from_path(path: Path) -> int:
    raw = path.stem if path.is_file() else path.name
    if "_seed_" in raw:
        return int(raw.rsplit("_seed_", 1)[1])
    if raw.startswith("seed_"):
        return int(raw.split("_", 1)[1])
    return int(raw.split("_", 1)[1])


def convert_resolved_tileset_for_runtime(
    project_root: Path,
    map_id: str,
    map_facts: dict[str, Any],
    resolved_tileset_v2: dict[str, Any],
    resolved_materials: dict[str, Any],
) -> dict[str, Any]:
    symbol_counts = map_facts.get("symbol_counts", {})
    material_by_slot = {item.get("material_slot_id"): item for item in resolved_materials.get("materials", [])}
    tiles = []
    for tile in resolved_tileset_v2.get("tiles", []):
        slot_id = tile.get("material_slot_id")
        material = material_by_slot.get(slot_id, {})
        role = choose_runtime_role(tile)
        generate = bool(tile.get("generate_geometry")) and bool(tile.get("mesh_id"))
        tiles.append(
            {
                "symbol": tile.get("symbol"),
                "legend_name": tile.get("legend_name"),
                "tile_type_id": tile.get("tile_type_id"),
                "role": role,
                "mesh_id": tile.get("mesh_id"),
                "material_slot_id": slot_id,
                "slot_id_compat": slot_id,
                "material_family": material.get("material_category") or tile.get("material_category"),
                "height_class": tile.get("height_class"),
                "height": tile.get("height") if tile.get("height") is not None else 0,
                "z_offset": tile.get("z_offset") if tile.get("z_offset") is not None else 0,
                "generate": generate,
                "generate_material": bool(tile.get("generate_material")),
                "shape_type": tile.get("shape_type"),
                "selected_mesh_role_tags": tile.get("selected_mesh_role_tags", []),
                "tile_count_in_map": symbol_counts.get(tile.get("symbol"), 0),
                "inference": {
                    "source": "material_generation_resolved_tileset_v2",
                    "tile_semantics": tile.get("tile_semantics"),
                    "canonical_material_id": tile.get("canonical_material_id"),
                    "material_identity_coarse": material.get("material_identity_coarse"),
                    "material_category": material.get("material_category"),
                },
            }
        )
    return {
        "schema_version": RESOLVED_TILESET_EXPORT_SCHEMA,
        "map_id": map_id,
        "source_map_package": str(Path("data") / "maps" / map_id),
        "solver": {
            "name": "dynamic_tileset_runtime_adapter",
            "version": "runtime_export_v1",
            "source_schema": resolved_tileset_v2.get("schema_version"),
            "llm_calls": 0,
            "sd_calls": 0,
            "stablematerials_calls": 0,
        },
        "ue_registry_expectations": {
            "mesh_registry": "DA_GlobalMeshRegistry",
            "material_registry": "runtime_material_manifest_json",
            "dynamic_material_slots": True,
        },
        "map_summary": {
            "width": map_facts.get("map_size", {}).get("width"),
            "height": map_facts.get("map_size", {}).get("height"),
            "symbol_counts": symbol_counts,
            "style_excerpt": map_facts.get("style_text", ""),
        },
        "tiles": tiles,
        "source_files": {
            "authoring_map_package": str(project_root / "data" / "maps" / map_id),
            "resolved_tileset_v2": "source_run/03_python_resolver/resolved_tileset_v2.json",
            "resolved_materials_v2": "source_run/03_python_resolver/resolved_materials_v2.json",
        },
    }


def choose_runtime_role(tile: dict[str, Any]) -> str:
    tags = tile.get("selected_mesh_role_tags", [])
    for preferred in ("wall_surface", "water_surface", "floor_surface", "door_prop", "gate_prop"):
        if preferred in tags:
            return preferred
    if not tile.get("mesh_id"):
        return "void"
    return tags[0] if tags else "surface"


def build_material_manifest(
    map_id: str,
    source_run: Path,
    resolved_materials: dict[str, Any],
    compiled_sd15: dict[str, Any],
    compiled_stablematerials: dict[str, Any],
    material_selection: dict[str, Any],
    runtime_texture_backend: str,
) -> dict[str, Any]:
    materials = []
    stablematerials_candidates_available = any(record["stablematerials_candidates"] for record in material_selection["records"])
    for record in material_selection["records"]:
        source_material = record["source_material"]
        selected = record["selected"] or {}
        textures = selected_runtime_textures(record)
        backend_candidates = build_manifest_backend_candidates(record)
        compiled_prompt_refs = {
            "sd15": prompt_ref(compiled_sd15, record["material_slot_id"]),
        }
        if record["stablematerials_candidates"]:
            compiled_prompt_refs["stablematerials_lcm"] = prompt_ref(compiled_stablematerials, record["material_slot_id"])
        materials.append(
            {
                "material_slot_id": record["material_slot_id"],
                "canonical_material_id": record["canonical_material_id"],
                "display_label": record["display_label"],
                "material_family": source_material.get("material_category"),
                "semantic_material_family": source_material.get("material_category"),
                "used_by_symbols": source_material.get("covered_symbols", []),
                "used_by_tile_type_ids": [],
                "tile_references": build_tile_references(source_material),
                "textures": textures,
                "selected_backend": runtime_texture_backend,
                "selected": {
                    "backend": selected.get("backend"),
                    "seed": selected.get("seed"),
                    "textures": textures,
                    "selection_policy": SELECTION_POLICY,
                },
                "backend_candidates": backend_candidates,
                "source_textures": selected.get("textures", {}),
                "available_maps": sorted(textures),
                "missing_maps": [name for name in PBR_MAPS if name not in textures],
                "source_backend_hint": runtime_texture_backend,
                "reuse_decision": "export_existing_generated_texture",
                "generated_backends": available_backends(record),
                "sd15_basecolor": first_source_texture(record["sd15_candidates"], "basecolor"),
                "semantic_brief": {
                    "canonical_material_id": record["canonical_material_id"],
                    "identity": source_material.get("material_identity_coarse"),
                    "material_family": source_material.get("material_category"),
                    "raw_material_clues": source_material.get("raw_material_clues", []),
                    "context_clues_for_prompt_llm": source_material.get("context_clues_for_prompt_llm", []),
                },
                "compiled_prompt_refs": compiled_prompt_refs,
                "selection_policy": SELECTION_POLICY,
            }
        )
    source_files = {
        "source_run": str(source_run),
        "resolved_materials_v2": "03_python_resolver/resolved_materials_v2.json",
        "compiled_sd15_prompts": "06_compiled_prompts/compiled_sd15_prompts_v4.json",
        "material_mode": "export-only",
        "runtime_source_backend": runtime_texture_backend,
        "selection_policy": SELECTION_POLICY,
        "llm_calls": 0,
        "sd_calls": 0,
        "stablematerials_calls": 0,
        "new_image_generation": False,
    }
    if stablematerials_candidates_available:
        source_files["compiled_stablematerials_prompts"] = "06_compiled_prompts/compiled_stablematerials_prompts_v4.json"
    return {
        "schema_version": MATERIAL_MANIFEST_SCHEMA,
        "map_id": map_id,
        "created_at": timestamp_iso(),
        "source": source_files,
        "materials": sorted(materials, key=lambda item: item["material_slot_id"]),
        "warnings": material_selection.get("warnings", []),
    }


def selected_runtime_textures(record: dict[str, Any]) -> dict[str, str]:
    selected = record.get("selected") or {}
    backend = selected.get("backend")
    seed = selected.get("seed")
    slot = record["material_slot_id"]
    if backend == "sd15":
        return {"basecolor": f"textures/{slot}/basecolor.png"}
    if backend == "stablematerials":
        return {
            texture_type: f"textures/{slot}/{texture_type}.png"
            for texture_type in PBR_MAPS
            if texture_type in selected.get("textures", {})
        }
    raise ValueError(f"{slot}: unsupported selected backend/seed {backend}/{seed}")


def build_manifest_backend_candidates(record: dict[str, Any]) -> dict[str, Any]:
    slot = record["material_slot_id"]
    candidates = {
        "sd15": {
            "selected_seed": first_seed(record["sd15_candidates"]),
            "candidates": [
                {
                    "seed": item["seed"],
                    "textures": {"basecolor": f"backend_candidates/sd15/{slot}/seed_{item['seed']}/basecolor.png"},
                    "source_textures": item["textures"],
                }
                for item in record["sd15_candidates"]
            ],
        },
    }
    if record["stablematerials_candidates"]:
        candidates["stablematerials"] = {
            "selected_seed": first_seed(record["stablematerials_candidates"]),
            "candidates": [
                {
                    "seed": item["seed"],
                    "textures": {
                        texture_type: f"backend_candidates/stablematerials/{slot}/seed_{item['seed']}/{texture_type}.png"
                        for texture_type in PBR_MAPS
                        if texture_type in item.get("textures", {})
                    },
                    "missing_maps": item.get("missing_maps", []),
                    "source_textures": item["textures"],
                }
                for item in record["stablematerials_candidates"]
            ],
        }
    return candidates


def build_tile_references(source_material: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": symbol,
            "material_slot_id": source_material.get("material_slot_id"),
            "canonical_material_id": source_material.get("canonical_material_id"),
            "expected_mesh_ids": source_material.get("expected_mesh_ids", []),
        }
        for symbol in source_material.get("covered_symbols", [])
    ]


def prompt_ref(compiled: dict[str, Any], slot_id: str) -> dict[str, Any]:
    for item in compiled.get("prompts", []):
        if item.get("material_slot_id") == slot_id:
            return {
                "compiler": compiled.get("schema_version"),
                "backend": compiled.get("backend"),
                "positive_prompt": item.get("positive_prompt"),
                "negative_prompt": item.get("negative_prompt"),
                "source": item.get("source"),
            }
    return {}


def available_backends(record: dict[str, Any]) -> list[str]:
    values = []
    if record["sd15_candidates"]:
        values.append("sd15")
    if record["stablematerials_candidates"]:
        values.append("stablematerials_lcm")
    return values


def first_source_texture(candidates: list[dict[str, Any]], texture_type: str) -> str | None:
    if not candidates:
        return None
    return candidates[0].get("textures", {}).get(texture_type)


def first_seed(candidates: list[dict[str, Any]]) -> int | None:
    return candidates[0]["seed"] if candidates else None


def prepare_runtime_package(
    project_root: Path,
    runtime_root: Path,
    map_id: str,
    source: dict[str, Any],
    resolved_tileset_export: dict[str, Any],
    material_manifest: dict[str, Any],
    material_selection: dict[str, Any],
    map_root: Path | None = None,
) -> None:
    current_runtime = project_root / "generated" / "ue_ready" / "runtime_data"
    if current_runtime.is_dir():
        shutil.copytree(current_runtime, runtime_root, dirs_exist_ok=True)
    else:
        runtime_root.mkdir(parents=True, exist_ok=True)

    map_dir = runtime_root / "maps" / map_id
    if map_dir.exists():
        shutil.rmtree(map_dir)
    layout_dir = map_dir / "layout"
    rules_dir = map_dir / "rules"
    materials_dir = map_dir / "materials"
    layout_dir.mkdir(parents=True, exist_ok=True)
    rules_dir.mkdir(parents=True, exist_ok=True)
    materials_dir.mkdir(parents=True, exist_ok=True)

    authoring_map_root = map_root if map_root is not None else project_root / "data" / "maps"
    if not authoring_map_root.is_absolute():
        authoring_map_root = project_root / authoring_map_root
    authoring_map = authoring_map_root / map_id / "map.txt"
    shutil.copyfile(authoring_map, layout_dir / "map.txt")
    write_json(rules_dir / "resolved_tileset.json", resolved_tileset_export)
    write_json(rules_dir / "resolved_tileset_v2_source.json", source["resolved_tileset"])
    write_json(materials_dir / "material_manifest.json", material_manifest)
    write_json(materials_dir / "material_candidates_index.json", material_selection["candidates_index"])
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
            "backend_candidates": "materials/backend_candidates",
        },
        "generation": {
            "type": "deterministic_grid_builder",
            "coordinate_system": "tile_grid",
            "tile_size": 100,
        },
        "source": {
            "authoring_map_package": str(project_root / "data" / "maps" / map_id),
            "source_material_generation_run": source["validation"]["source_run"],
            "project_root": str(project_root),
            "llm_calls": 0,
            "sd_calls": 0,
            "stablematerials_calls": 0,
            "new_image_generation": False,
        },
        "warnings": [],
    }
    write_json(map_dir / "manifest.json", manifest)
    map_ids = discover_runtime_maps(runtime_root)
    backend_paths = load_backend_paths(project_root)
    index = build_map_package_index(
        project_root,
        runtime_root,
        sorted(map_ids),
        created_by=ROUND_ID,
        runtime_virtual_root=backend_paths.ue_runtime_virtual_root,
        ue_copy_destination=ue_copy_destination(backend_paths, project_root),
    )
    write_json(runtime_root / "map_package_index.json", index)


def copy_material_textures(materials_dir: Path, material_selection: dict[str, Any]) -> None:
    for record in material_selection["records"]:
        selected = record["selected"] or {}
        slot = record["material_slot_id"]
        copy_selected_textures(materials_dir, slot, selected)
        copy_backend_candidate_textures(materials_dir, slot, "sd15", record["sd15_candidates"])
        copy_backend_candidate_textures(materials_dir, slot, "stablematerials", record["stablematerials_candidates"])


def copy_selected_textures(materials_dir: Path, slot: str, selected: dict[str, Any]) -> None:
    textures_dir = materials_dir / "textures" / slot
    textures_dir.mkdir(parents=True, exist_ok=True)
    for texture_type, source in selected.get("textures", {}).items():
        target_name = "basecolor.png" if selected.get("backend") == "sd15" else f"{texture_type}.png"
        shutil.copyfile(Path(source), textures_dir / target_name)


def copy_backend_candidate_textures(materials_dir: Path, slot: str, backend: str, candidates: list[dict[str, Any]]) -> None:
    for candidate in candidates:
        seed_dir = materials_dir / "backend_candidates" / backend / slot / f"seed_{candidate['seed']}"
        seed_dir.mkdir(parents=True, exist_ok=True)
        for texture_type, source in candidate.get("textures", {}).items():
            target_name = "basecolor.png" if backend == "sd15" else f"{texture_type}.png"
            shutil.copyfile(Path(source), seed_dir / target_name)


def discover_runtime_maps(runtime_root: Path) -> set[str]:
    maps_root = runtime_root / "maps"
    if not maps_root.is_dir():
        return set()
    return {path.name for path in maps_root.iterdir() if path.is_dir()}


def validate_material_manifest(material_manifest: dict[str, Any], require_runtime_files: bool, material_dir: Path | None) -> dict[str, Any]:
    errors = []
    warnings = []
    if material_manifest.get("schema_version") != MATERIAL_MANIFEST_SCHEMA:
        errors.append(f"Invalid material manifest schema: {material_manifest.get('schema_version')}")
    for material in material_manifest.get("materials", []):
        slot = material.get("material_slot_id", "<missing>")
        textures = material.get("textures", {})
        if "basecolor" not in textures:
            errors.append(f"{slot}: textures.basecolor is required for current UE loader compatibility.")
        selected = material.get("selected", {})
        if selected.get("backend") != material.get("selected_backend"):
            errors.append(f"{slot}: selected backend mismatch.")
        backend_candidates = material.get("backend_candidates", {})
        if "sd15" not in backend_candidates:
            warnings.append(f"{slot}: backend_candidates should include sd15.")
        if require_runtime_files and material_dir is not None:
            for texture_type, relative in textures.items():
                if not (material_dir / relative).is_file():
                    errors.append(f"{slot}: runtime texture missing: {texture_type} -> {relative}")
    return {"schema_version": "d6g_a2_material_manifest_validation_v1", "passed": not errors, "errors": errors, "warnings": warnings}


def dry_runtime_validation(source: dict[str, Any], material_selection: dict[str, Any]) -> dict[str, Any]:
    errors = list(material_selection.get("errors", []))
    return {
        "schema_version": "d6g_a2_dry_runtime_data_validation_v1",
        "passed": not errors,
        "errors": errors,
        "warnings": ["Dry-run did not create RuntimeData files."],
        "source_run_passed": source["validation"].get("passed", False),
    }


def build_prior_leak_audit(source: dict[str, Any]) -> dict[str, Any]:
    source_prior = source.get("prior_leak_audit", {})
    return {
        "schema_version": "d6g_a2_prior_leak_audit_v1",
        "passed": bool(source_prior.get("passed", True)),
        "source_prior_leak_audit": source_prior,
        "old_material_slot_rules_used": False,
        "old_material_slot_evidence_used": False,
        "suggested_prompt_hint_used": False,
        "note": "Runtime export is export-only and reuses successful source run artifacts without generating new semantic or prompt evidence.",
    }


def merge_validations(schema_version: str, validations: list[dict[str, Any]]) -> dict[str, Any]:
    errors = []
    warnings = []
    for validation in validations:
        errors.extend(validation.get("errors", []))
        warnings.extend(validation.get("warnings", []))
    return {"schema_version": schema_version, "passed": not errors, "errors": errors, "warnings": sorted(set(warnings))}


def build_runtime_export_manifest(project_root: Path, run_dir: Path, source_run: Path, map_id: str, runtime_texture_backend: str) -> dict[str, Any]:
    backend_paths = load_backend_paths(project_root)
    return {
        "schema_version": "d6g_a2_runtime_data_export_manifest_v1",
        "created_at": timestamp_iso(),
        "stage_id": STAGE_ID,
        "round_id": ROUND_ID,
        "compatibility_id": COMPATIBILITY_ID,
        "map_id": map_id,
        "run_dir": str(run_dir),
        "source_material_generation_run": str(source_run),
        "runtime_texture_backend": runtime_texture_backend,
        "selected_backend_is_exposed_via_textures": True,
        "backend_candidates_are_packaged_for_future_ue_switching": True,
        "ue_copy_destination": str(ue_copy_destination(backend_paths, project_root)),
        "llm_calls": 0,
        "sd_webui_generation_calls": 0,
        "stablematerials_generation_calls": 0,
    }


def build_copy_instructions(project_root: Path, runtime_package_path: Path, dry_run: bool) -> str:
    backend_paths = load_backend_paths(project_root)
    destination = ue_copy_destination(backend_paths, project_root)
    if dry_run:
        return (
            "# RuntimeData Copy Instructions (Dry Run)\n\n"
            "Dry-run did not create a complete RuntimeData package. Run without `--dry-run` first.\n"
        )
    return (
        "# RuntimeData Copy Instructions\n\n"
        "Copy the contents of this RuntimeData package into the UE project RuntimeData directory.\n\n"
        f"Source package:\n`{runtime_package_path}`\n\n"
        f"UE destination:\n`{destination}`\n\n"
        "PowerShell example:\n\n"
        "```powershell\n"
        f"Copy-Item -Path \"{runtime_package_path}\\*\" -Destination \"{destination}\" -Recurse -Force\n"
        "```\n\n"
        "Current UE compatibility path reads `materials/textures/<material_slot_id>/basecolor.png`.\n"
        "The exporter also packages `materials/backend_candidates/` for future backend switching, but current UE code may ignore it until extended.\n"
    )


def build_summary(
    run_dir: Path,
    command: str,
    map_id: str,
    source: dict[str, Any],
    material_selection: dict[str, Any],
    runtime_texture_backend: str,
    runtime_package_path: Path,
    runtime_snapshot_path: Path | None,
    material_manifest_path: Path,
    copy_instructions_path: Path,
    runtime_package_created: bool,
    generated_runtime_data_refreshed: bool,
    validation_passed: bool,
    dry_run: bool,
) -> dict[str, Any]:
    status = "passed" if validation_passed else "failed"
    if not dry_run and not runtime_package_created:
        status = "failed"
    selected_count = sum(1 for record in material_selection["records"] if record.get("selected"))
    project_root = run_dir.parents[1].parent
    generated_runtime_data_path = project_root / "generated" / "ue_ready" / "runtime_data"
    summary_path = run_dir / "05_reports" / SUMMARY_FILENAME
    report_path = run_dir / "05_reports" / REPORT_FILENAME
    return {
        "schema_version": SUMMARY_SCHEMA,
        "status": status,
        "stage_id": STAGE_ID,
        "round_id": ROUND_ID,
        "compatibility_id": COMPATIBILITY_ID,
        "compatibility_note": "Behavior-compatible with the validated research RuntimeData export flow; public stage naming is cleaned for final project usage.",
        "dry_run": dry_run,
        "command": command,
        "map_id": map_id,
        "source_run": source["validation"]["source_run"],
        "source_run_status": source["validation"].get("source_run_status"),
        "material_slot_count": len(material_selection["records"]),
        "selected_material_count": selected_count,
        "selection_policy": SELECTION_POLICY,
        "runtime_texture_backend": runtime_texture_backend,
        "material_manifest_created": material_manifest_path.is_file(),
        "material_manifest_path": str(material_manifest_path),
        "runtime_data_package_created": runtime_package_created,
        "runtime_data_package_path": str(runtime_package_path),
        "runtime_data_snapshot_path": str(runtime_snapshot_path) if runtime_snapshot_path else None,
        "generated_runtime_data_refreshed": generated_runtime_data_refreshed,
        "generated_runtime_data_path": str(generated_runtime_data_path),
        "map_package_index_created_or_updated": (runtime_package_path / "map_package_index.json").is_file(),
        "map_package_index_path": str(runtime_package_path / "map_package_index.json"),
        "ue_modified": False,
        "llm_calls": 0,
        "sd_webui_generation_calls": 0,
        "stablematerials_generation_calls": 0,
        "missing_selected_files": material_selection.get("errors", []),
        "validation_passed": validation_passed,
        "copy_to_ue_path": str(ue_copy_destination(load_backend_paths(project_root), project_root)),
        "copy_to_ue_instructions": str(copy_instructions_path),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "warnings": material_selection.get("warnings", []),
    }


def build_report(summary: dict[str, Any], material_selection: dict[str, Any]) -> str:
    rows = []
    for record in material_selection["records"]:
        selected = record.get("selected") or {}
        rows.append(
            f"| {record['material_slot_id']} | {record['display_label']} | {selected.get('backend')} | {selected.get('seed')} | "
            f"{len(record['sd15_candidates'])} | {len(record['stablematerials_candidates'])} |"
        )
    return (
        "# Material Manifest and RuntimeData Export Report\n\n"
        f"- Status: `{summary['status']}`\n"
        f"- Dry-run: `{summary['dry_run']}`\n"
        f"- Source run: `{summary['source_run']}`\n"
        f"- Runtime texture backend: `{summary['runtime_texture_backend']}`\n"
        f"- RuntimeData package: `{summary['runtime_data_package_path']}`\n"
        f"- Generated latest refreshed: `{summary['generated_runtime_data_refreshed']}`\n"
        f"- UE modified: `{summary['ue_modified']}`\n"
        f"- LLM calls: `{summary['llm_calls']}`\n"
        f"- SD/WebUI generation calls: `{summary['sd_webui_generation_calls']}`\n"
        f"- StableMaterials generation calls: `{summary['stablematerials_generation_calls']}`\n\n"
        "## Selection\n\n"
        "| Material Slot | Display | Selected Backend | Seed | SD15 Candidates | StableMaterials Candidates |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        + "\n".join(rows)
        + "\n\n"
        "## UE Compatibility\n\n"
        "The current UE loader can continue reading `textures.basecolor`; the exporter sets this default path to the selected SD1.5 export. "
        "`backend_candidates` stores both SD1.5 and StableMaterials artifacts for future UE-side backend switching without changing current loader behavior.\n"
    )


def build_key_outputs_index(paths: dict[str, Path], summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "d6g_a2_key_outputs_index_v1",
        "summary": summary["summary_path"],
        "report": summary["report_path"],
        "legacy_summary": str(paths["reports"] / LEGACY_SUMMARY_FILENAME),
        "legacy_report": str(paths["reports"] / LEGACY_REPORT_FILENAME),
        "source_validation": str(paths["source"] / "source_run_validation.json"),
        "material_manifest": summary["material_manifest_path"],
        "runtime_data_package": summary["runtime_data_package_path"],
        "copy_to_ue_instructions": summary["copy_to_ue_instructions"],
        "validation_dir": str(paths["validation"]),
    }


def refresh_runtime_data_fn(project_root: Path, runtime_package_path: Path, target_runtime_data: Path) -> None:
    if not target_runtime_data.exists():
        refresh_runtime_data(runtime_package_path, target_runtime_data)
        return

    source_maps = runtime_package_path / "maps"
    target_maps = target_runtime_data / "maps"
    target_maps.mkdir(parents=True, exist_ok=True)
    for source_map_dir in source_maps.iterdir():
        if not source_map_dir.is_dir():
            continue
        target_map_dir = target_maps / source_map_dir.name
        if target_map_dir.exists():
            shutil.rmtree(target_map_dir)
        shutil.copytree(source_map_dir, target_map_dir)
    map_ids = sorted(discover_runtime_maps(target_runtime_data))
    backend_paths = load_backend_paths(project_root)
    index = build_map_package_index(
        project_root,
        target_runtime_data,
        map_ids,
        created_by=ROUND_ID,
        runtime_virtual_root=backend_paths.ue_runtime_virtual_root,
        ue_copy_destination=ue_copy_destination(backend_paths, project_root),
    )
    write_json(target_runtime_data / "map_package_index.json", index)
    write_json(
        target_runtime_data / "runtime_data_refresh_manifest.json",
        {
            "schema_version": "runtime_data_refresh_manifest_v1",
            "created_at": timestamp_iso(),
            "mode": "merge_or_update_map_packages",
            "source_runtime_package": str(runtime_package_path),
            "target_runtime_data": str(target_runtime_data),
            "maps": map_ids,
        },
    )


def build_command(
    project_root: Path,
    map_id: str,
    source_run: Path,
    dry_run: bool,
    refresh_runtime_data: bool,
    runtime_texture_backend: str,
    include_backend_candidates: bool,
    map_root: Path | None = None,
) -> str:
    backend_paths = load_backend_paths(project_root)
    python_executable = str(backend_paths.dissertation_python or "python")
    command = (
        f"{python_executable} "
        f"{project_root / 'run_main_pipeline.py'} "
        f"--map {map_id} --export-runtime-data "
        f"--reuse-materials-from \"{source_run}\" "
        f"--runtime-texture-backend {runtime_texture_backend}"
    )
    if map_root is not None:
        command += f' --map-root "{map_root}"'
    if not include_backend_candidates:
        command += " --no-include-backend-candidates"
    if not refresh_runtime_data:
        command += " --no-refresh-runtime-data"
    if dry_run:
        command += " --dry-run"
    return command
