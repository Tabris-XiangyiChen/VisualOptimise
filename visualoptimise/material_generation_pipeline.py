"""Map-driven two-LLM material generation preview pipeline."""

from __future__ import annotations

import json
import random
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from visualoptimise import prompt_generation as mainline_prompting
from visualoptimise import preview_generation
from visualoptimise import semantic_planning as material_planning
from visualoptimise.artifacts import ensure_dirs, normalize_map_ids, read_json, timestamp_for_run, timestamp_iso, write_json, write_text
from visualoptimise.llm_artifacts import build_json_chat_payload, parse_json_response

from .material_generation_report import build_markdown_report


ROUND_ID = "d6f_a4_full_two_llm_material_generation_preview"
RUN_SUFFIX = ROUND_ID
SUPPORTED_SEMANTIC_MODE = "llm"
SUPPORTED_MATERIAL_MODE = "preview-only"
GENERATION_CONFIG_RELATIVE = Path("settings") / "material_generation_defaults.json"
DEFAULT_RANDOM_IMAGES_PER_MATERIAL = 1
SD15_SETTINGS = preview_generation.SD15_SETTINGS
STABLEMATERIALS_SETTINGS = preview_generation.STABLEMATERIALS_SETTINGS

RUN_DIRS = {
    "run": "00_run",
    "map_facts": "01_map_facts",
    "llm1": "02_llm1_material_plan",
    "resolver": "03_python_resolver",
    "evidence": "04_dynamic_material_evidence",
    "llm2": "05_llm2_prompt_briefs",
    "compiled": "06_compiled_prompts",
    "generation": "07_generation",
    "sd15": "07_generation/sd15",
    "stablematerials": "07_generation/stablematerials",
    "previews": "07_generation/tiled_previews",
    "contact_sheets": "08_contact_sheets",
    "analysis": "09_analysis",
    "reports": "10_reports",
}


def run_experiment(
    pipeline: Any,
    fallback_map_id: str,
    map_ids: list[str] | None,
    dry_run: bool,
    semantic_mode: str,
    material_mode: str,
    llm_max_attempts: int,
    prompt_llm_max_attempts: int,
    seeds: list[int] | None = None,
    images_per_material: int | None = None,
) -> Path:
    maps = normalize_map_ids(map_ids, fallback_map_id)
    if len(maps) != 1:
        raise ValueError("Material generation handles one map package per run; use the main CLI batch runner for multiple maps.")
    map_id = maps[0]
    if semantic_mode != SUPPORTED_SEMANTIC_MODE:
        raise ValueError(f"Material generation requires semantic-mode={SUPPORTED_SEMANTIC_MODE}.")
    if material_mode != SUPPORTED_MATERIAL_MODE:
        raise ValueError(f"Material generation requires material-mode={SUPPORTED_MATERIAL_MODE}.")

    started = time.perf_counter()
    run_dir = pipeline.output_dir / f"{timestamp_for_run()}_{map_id}_{RUN_SUFFIX}"
    paths = paths_for_run(run_dir)
    ensure_dirs(paths)
    generation_config = resolve_generation_seed_config(pipeline.root, seeds, images_per_material)
    resolved_seeds = generation_config["seeds"]
    command = build_command(map_id, dry_run, llm_max_attempts, prompt_llm_max_attempts, seeds, images_per_material)
    write_text(paths["run"] / "command.txt", command)
    write_json(paths["run"] / "run_config.json", build_run_config(map_id, dry_run, llm_max_attempts, prompt_llm_max_attempts, generation_config))
    write_json(paths["run"] / "generation_seed_config.json", generation_config)

    component_manifest = {
        "schema_version": "d6f_a4_component_reuse_manifest_v2",
        "material_planning": "visualoptimise.semantic_planning",
        "prompt_brief_planning": "visualoptimise.prompt_generation",
        "preview_generation": "visualoptimise.preview_generation",
        "minimal_compatibility_patches": [
            "A4 builds a dynamic Plan A request table instead of the fixed A/B experiment table.",
            "A4 writes a full-pipeline output structure while reusing component functions.",
            "A4 adjusts only the dry-run template tileability order so it satisfies the reused D6E-style validator; real LLM2 prompt text is unchanged.",
            "A4 applies bounded post-retry prompt-contract normalization for missing/misplaced tileability tags and removable weak meta tags, with a saved report.",
        ],
    }
    write_json(paths["run"] / "component_reuse_manifest.json", component_manifest)

    llm1_result = run_llm1_and_resolver(pipeline, paths, map_id, dry_run, llm_max_attempts)
    llm2_result = run_fix3_llm2(pipeline, paths, llm1_result, dry_run, prompt_llm_max_attempts)
    material_slots = material_slots_from_compiled(llm2_result["compiled_sd15"])
    display_labels = display_labels_from_slots(material_slots, llm2_result["compiled_sd15"], llm1_result["dynamic_evidence"])

    plan_a = build_sd15_plan_a(llm2_result["compiled_sd15"])
    sd15_requests = build_sd15_requests(plan_a, material_slots, display_labels, resolved_seeds)
    sm_requests = build_stablematerials_requests(llm2_result["compiled_stablematerials"], material_slots, display_labels, resolved_seeds)
    write_json(paths["compiled"] / "compiled_sd15_plan_a.json", plan_a)
    write_json(paths["compiled"] / "compiled_stablematerials_preview.json", llm2_result["compiled_stablematerials"])
    write_json(paths["compiled"] / "sd15_request_table.json", {"schema_version": "d6f_a4_sd15_plan_a_request_table_v1", "settings": SD15_SETTINGS, "requests": sd15_requests})
    write_json(paths["compiled"] / "stablematerials_request_table.json", {"schema_version": "d6f_a4_stablematerials_request_table_v1", "settings": STABLEMATERIALS_SETTINGS, "requests": sm_requests})

    positive_audit = preview_generation.audit_positive_side_pollution(llm2_result["prompt_briefs"], llm1_result["dynamic_evidence"])
    write_json(paths["analysis"] / "positive_side_audit_reported_only.json", positive_audit)

    preflight = preview_generation.preflight_backends(pipeline)
    write_json(paths["run"] / "backend_preflight.json", preflight)
    sd15_outputs: list[dict[str, Any]] = []
    sm_outputs: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    non_blocking_failed_items: list[dict[str, Any]] = []
    timings: list[dict[str, Any]] = []

    if not dry_run:
        if not preflight["sd15"]["passed"]:
            raise RuntimeError("SD1.5 preflight failed: " + json.dumps(preflight["sd15"]["errors"], ensure_ascii=False))
        stablematerials_enabled = llm2_result.get("stablematerials_policy", {}).get("generation_enabled", True)
        if not preflight["stablematerials"]["passed"]:
            stablematerials_enabled = False
            non_blocking_failed_items.append(
                {
                    "backend": "stablematerials_lcm",
                    "request_id": "stablematerials_preflight",
                    "non_blocking": True,
                    "error": "StableMaterials preflight failed: " + json.dumps(preflight["stablematerials"]["errors"], ensure_ascii=False),
                }
            )
        sd15_outputs, sd_failures, sd_timings = preview_generation.run_sd15_generation(pipeline, paths, sd15_requests, preflight)
        failed_items.extend(sd_failures)
        timings.extend(sd_timings)
        if stablematerials_enabled:
            sm_outputs, sm_failures, sm_timings = preview_generation.run_stablematerials_generation(paths, sm_requests)
            non_blocking_failed_items.extend({**item, "non_blocking": True} for item in sm_failures)
            timings.extend(sm_timings)
        else:
            non_blocking_failed_items.append(
                {
                    "backend": "stablematerials_lcm",
                    "request_id": "stablematerials_prompt_length_gate",
                    "non_blocking": True,
                    "error": "StableMaterials generation skipped because prompt length validation remained over budget after LLM retries.",
                    "policy": llm2_result.get("stablematerials_policy", {}),
                }
            )

    diagnostics = build_generation_diagnostics(material_slots, resolved_seeds, sd15_outputs, sm_outputs, failed_items, non_blocking_failed_items, timings, dry_run)
    write_json(paths["analysis"] / "generation_summary.json", diagnostics["generation_summary"])
    write_json(paths["analysis"] / "missing_outputs.json", diagnostics["missing_outputs"])
    write_json(paths["analysis"] / "non_blocking_failed_items.json", diagnostics["non_blocking_failed_items"])
    write_json(paths["analysis"] / "timing_summary.json", diagnostics["timing_summary"])
    analysis = build_analysis(material_slots, display_labels, sd15_outputs, sm_outputs, dry_run)
    write_json(paths["analysis"] / "stability_observation.json", analysis["stability_observation"])
    write_json(paths["analysis"] / "per_material_notes.json", analysis["per_material_notes"])
    write_text(paths["analysis"] / "direct_visual_review.md", build_direct_visual_review(analysis, dry_run))

    contact_sheets: dict[str, str] = {}
    if not dry_run:
        contact_sheets = create_contact_sheets(paths, material_slots, display_labels, resolved_seeds, sd15_outputs, sm_outputs)

    prior_audit = build_prior_leak_audit(llm1_result, llm2_result, positive_audit)
    write_json(paths["analysis"] / "prior_leak_audit.json", prior_audit)

    summary = build_summary(
        run_dir=run_dir,
        command=command,
        map_id=map_id,
        dry_run=dry_run,
        material_slots=material_slots,
        display_labels=display_labels,
        generation_seed_config=generation_config,
        llm1_result=llm1_result,
        llm2_result=llm2_result,
        preflight=preflight,
        diagnostics=diagnostics,
        analysis=analysis,
        contact_sheets=contact_sheets,
        prior_audit=prior_audit,
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )
    write_json(paths["reports"] / "d6f_a4_full_two_llm_material_generation_preview_summary.json", summary)
    write_text(paths["reports"] / "d6f_a4_full_two_llm_material_generation_preview_report.md", build_markdown_report(summary))
    write_json(paths["run"] / "run_manifest.json", build_run_manifest(run_dir, command, dry_run, summary))
    write_json(paths["run"] / "key_outputs_index.json", build_key_outputs_index(paths, summary))

    if not dry_run and summary["status"] != "passed":
        raise RuntimeError(f"D6F-A4 completed but did not pass natively. See {summary['summary_path']}")
    return run_dir


def paths_for_run(run_dir: Path) -> dict[str, Path]:
    return {name: run_dir / rel for name, rel in RUN_DIRS.items()}


def build_command(
    map_id: str,
    dry_run: bool,
    llm_max_attempts: int,
    prompt_llm_max_attempts: int,
    cli_seeds: list[int] | None,
    cli_images_per_material: int | None,
) -> str:
    command = (
        r"I:\MiniConda3\envs\dissertation\python.exe "
        rf"I:\Disertation\VisualOptimise\run_main_pipeline.py --map {map_id} "
        r"--generate-materials --semantic-mode llm "
        r"--material-mode preview-only "
        f"--llm-max-attempts {llm_max_attempts} --prompt-llm-max-attempts {prompt_llm_max_attempts}"
    )
    if cli_seeds is not None:
        command += " --seeds " + " ".join(str(seed) for seed in cli_seeds)
    if cli_images_per_material is not None:
        command += f" --images-per-material {cli_images_per_material}"
    if dry_run:
        command += " --dry-run"
    return command


def build_run_config(
    map_id: str,
    dry_run: bool,
    llm_max_attempts: int,
    prompt_llm_max_attempts: int,
    generation_config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "d6f_a4_run_config_v1",
        "round_id": ROUND_ID,
        "map_id": map_id,
        "dry_run": dry_run,
        "semantic_mode": SUPPORTED_SEMANTIC_MODE,
        "material_mode": SUPPORTED_MATERIAL_MODE,
        "llm_max_attempts": llm_max_attempts,
        "prompt_llm_max_attempts": prompt_llm_max_attempts,
        "generation_seed_config": generation_config,
        "started_at": timestamp_iso(),
        "plan_policy": "SD1.5 Plan A only; Fix3 positives and Fix3 negatives as-is.",
        "strict_exclusions": {
            "runtime_data_export": "not_called",
            "generated_package_export": "not_called",
            "ue_project": "not_modified",
            "plan_b": "not_run",
        },
    }


def resolve_generation_seed_config(
    project_root: Path,
    cli_seeds: list[int] | None,
    cli_images_per_material: int | None,
) -> dict[str, Any]:
    if cli_images_per_material is not None and cli_images_per_material < 1:
        raise ValueError("--images-per-material must be at least 1.")
    if cli_seeds is not None:
        if cli_images_per_material is not None and cli_images_per_material != len(cli_seeds):
            raise ValueError("--images-per-material must match the number of --seeds when both are provided.")
        return {
            "schema_version": "d6f_a4_generation_seed_config_v1",
            "source": "cli.seeds",
            "config_file": None,
            "seeds": validate_seed_list(cli_seeds),
            "images_per_material": len(cli_seeds),
            "random_seed_generation_used": False,
        }
    if cli_images_per_material is not None:
        generated = generate_random_seeds(cli_images_per_material)
        return {
            "schema_version": "d6f_a4_generation_seed_config_v1",
            "source": "cli.images_per_material",
            "config_file": None,
            "seeds": generated,
            "images_per_material": cli_images_per_material,
            "random_seed_generation_used": True,
        }

    config_path = project_root / GENERATION_CONFIG_RELATIVE
    config: dict[str, Any] = {}
    if config_path.is_file():
        config = read_json(config_path)
        config_seeds = config.get("seeds")
        config_images = config.get("images_per_material")
        if config_seeds:
            seeds = validate_seed_list(config_seeds)
            return {
                "schema_version": "d6f_a4_generation_seed_config_v1",
                "source": "config.seeds",
                "config_file": str(config_path),
                "seeds": seeds,
                "images_per_material": len(seeds),
                "random_seed_generation_used": False,
                "config": config,
            }
        if config_images is not None:
            count = int(config_images)
            if count < 1:
                raise ValueError("settings/material_generation_defaults.json images_per_material must be at least 1.")
            generated = generate_random_seeds(count)
            return {
                "schema_version": "d6f_a4_generation_seed_config_v1",
                "source": "config.images_per_material",
                "config_file": str(config_path),
                "seeds": generated,
                "images_per_material": count,
                "random_seed_generation_used": True,
                "config": config,
            }

    count = DEFAULT_RANDOM_IMAGES_PER_MATERIAL
    if count < 1:
        raise ValueError("images_per_material must be at least 1.")
    generated = generate_random_seeds(count)
    return {
        "schema_version": "d6f_a4_generation_seed_config_v1",
        "source": "fallback.random_default",
        "config_file": str(config_path) if config_path.is_file() else None,
        "seeds": generated,
        "images_per_material": count,
        "random_seed_generation_used": True,
        "config": config,
    }


def validate_seed_list(values: Any) -> list[int]:
    if not isinstance(values, list):
        raise ValueError("Seed list must be an array/list.")
    seeds: list[int] = []
    for value in values:
        seed = int(value)
        if seed < 0:
            raise ValueError("Seed values must be non-negative integers.")
        seeds.append(seed)
    if not seeds:
        raise ValueError("Seed list must not be empty.")
    return seeds


def generate_random_seeds(count: int) -> list[int]:
    rng = random.SystemRandom()
    seeds: list[int] = []
    while len(seeds) < count:
        seed = rng.randint(0, 2_147_483_647)
        if seed not in seeds:
            seeds.append(seed)
    return seeds


def run_llm1_and_resolver(pipeline: Any, paths: dict[str, Path], map_id: str, dry_run: bool, max_attempts: int) -> dict[str, Any]:
    map_package_validation = material_planning.validate_map_package(pipeline.root, map_id)
    write_json(paths["map_facts"] / "map_package_validation.json", map_package_validation)
    if not map_package_validation["passed"]:
        raise RuntimeError("Map package validation failed.")
    map_facts = material_planning.build_map_facts(pipeline.root, map_id)
    mesh_catalog_full, mesh_snapshot = material_planning.build_mesh_catalog_snapshot(pipeline.root)
    map_validation = material_planning.validate_map_facts(map_facts)
    mesh_validation = material_planning.validate_mesh_snapshot(mesh_snapshot)
    llm1_system = material_planning.build_llm1_system_prompt()
    llm1_user = material_planning.build_llm1_user_prompt(map_facts, mesh_snapshot)
    llm1_payload = build_json_chat_payload(pipeline.settings, llm1_system, llm1_user)
    llm1_request_audit = material_planning.audit_payload_inputs(map_facts, mesh_snapshot)

    write_json(paths["map_facts"] / "map_facts_v2.json", map_facts)
    write_json(paths["map_facts"] / "map_facts_v2_validation.json", map_validation)
    write_json(paths["map_facts"] / "mesh_catalog_snapshot_for_llm.json", mesh_snapshot)
    write_json(paths["map_facts"] / "mesh_catalog_snapshot_validation.json", mesh_validation)
    write_json(paths["map_facts"] / "mesh_catalog_runtime_reference.json", mesh_catalog_full)
    write_text(paths["llm1"] / "llm1_system_prompt.txt", llm1_system)
    write_text(paths["llm1"] / "llm1_user_prompt.txt", llm1_user)
    write_json(paths["llm1"] / "llm1_request_payload.json", material_planning.sanitize_payload_for_disk(llm1_payload))
    write_json(paths["llm1"] / "llm1_request_prior_leak_audit.json", llm1_request_audit)

    preflight = {
        "schema_version": "d6f_a4_llm1_preflight_v1",
        "map_package_valid": map_package_validation["passed"],
        "map_facts_valid": map_validation["passed"],
        "mesh_catalog_valid": mesh_validation["passed"],
        "llm1_prior_leak_audit_passed": llm1_request_audit["passed"],
        "material_slot_rules_excluded": llm1_request_audit["material_slot_rules_excluded"],
        "old_material_slot_evidence_excluded": llm1_request_audit["old_material_slot_evidence_excluded"],
    }
    write_json(paths["llm1"] / "llm1_preflight.json", preflight)
    if not all([map_package_validation["passed"], map_validation["passed"], mesh_validation["passed"], llm1_request_audit["passed"]]):
        raise RuntimeError("LLM1 preflight failed.")

    if dry_run:
        llm1_plan = material_planning.build_template_llm1_plan(map_facts, mesh_snapshot)
        llm1_raw = json.dumps(llm1_plan, ensure_ascii=False, indent=2)
        llm1_attempts = {"schema_version": "llm1_attempts_summary_v1", "attempts": [], "llm_call_count": 0, "retry_count": 0, "dry_run_template": True}
        write_text(paths["llm1"] / "llm1_raw_response.txt", llm1_raw)
        write_json(paths["llm1"] / "llm1_parsed_response.json", llm1_plan)
    else:
        llm1_plan, llm1_raw, llm1_attempts = material_planning.call_llm_until_valid(
            pipeline,
            llm1_payload,
            lambda response: material_planning.validate_llm1_plan(material_planning.normalize_llm1_plan_shapes(response)[0], map_facts, mesh_snapshot)["summary"],
            max(1, max_attempts),
            paths["llm1"],
            "llm1",
        )
        write_text(paths["llm1"] / "llm1_raw_response.txt", llm1_raw)
        write_json(paths["llm1"] / "llm1_parsed_response.json", llm1_plan)
    write_json(paths["llm1"] / "llm1_attempts_summary.json", llm1_attempts)

    llm1_plan, normalization = material_planning.normalize_llm1_plan_shapes(llm1_plan)
    llm1_validation = material_planning.validate_llm1_plan(llm1_plan, map_facts, mesh_snapshot)
    write_json(paths["llm1"] / "llm_tile_material_plan_v2.json", llm1_plan)
    write_json(paths["llm1"] / "llm1_normalization_report.json", normalization)
    write_json(paths["llm1"] / "llm1_validation_report.json", llm1_validation)
    if not llm1_validation["summary"]["passed"]:
        raise RuntimeError("LLM1 validation failed.")

    resolved_materials, resolved_tileset, id_report, alias_map = material_planning.resolve_dynamic_outputs(llm1_plan, mesh_catalog_full, map_facts)
    dynamic_evidence = material_planning.build_dynamic_material_evidence(llm1_plan, resolved_materials, resolved_tileset, map_facts, mesh_catalog_full)
    prompt_llm_input = material_planning.build_prompt_llm_input(dynamic_evidence, map_facts)
    evidence_validation = material_planning.validate_dynamic_evidence(dynamic_evidence, resolved_materials)
    prompt_input_audit = material_planning.audit_prompt_llm_input(prompt_llm_input)

    write_json(paths["resolver"] / "canonical_id_normalization_report.json", id_report)
    write_json(paths["resolver"] / "symbol_alias_map.json", alias_map)
    write_json(paths["resolver"] / "resolved_materials_v2.json", resolved_materials)
    write_json(paths["resolver"] / "resolved_tileset_v2.json", resolved_tileset)
    write_json(paths["evidence"] / "dynamic_material_slot_evidence_v3.json", dynamic_evidence)
    write_json(paths["evidence"] / "prompt_llm_input_v3.json", prompt_llm_input)
    write_json(paths["evidence"] / "dynamic_material_slot_evidence_validation.json", evidence_validation)
    write_json(paths["evidence"] / "prior_leak_audit_for_prompt_llm.json", prompt_input_audit)
    if not evidence_validation["passed"] or not prompt_input_audit["passed"]:
        raise RuntimeError("Dynamic evidence validation failed.")

    return {
        "map_package_validation": map_package_validation,
        "map_facts": map_facts,
        "mesh_snapshot": mesh_snapshot,
        "llm1_request_audit": llm1_request_audit,
        "llm1_attempts": llm1_attempts,
        "llm1_validation": llm1_validation,
        "resolved_materials": resolved_materials,
        "resolved_tileset": resolved_tileset,
        "dynamic_evidence": dynamic_evidence,
        "prompt_llm_input": prompt_llm_input,
        "evidence_validation": evidence_validation,
        "prompt_input_audit": prompt_input_audit,
    }


def run_fix3_llm2(
    pipeline: Any,
    paths: dict[str, Path],
    llm1_result: dict[str, Any],
    dry_run: bool,
    max_attempts: int,
) -> dict[str, Any]:
    prompt_input = llm1_result["prompt_llm_input"]
    dynamic_evidence = llm1_result["dynamic_evidence"]
    system_prompt = mainline_prompting.build_llm2_system_prompt()
    user_prompt = mainline_prompting.build_llm2_user_prompt(prompt_input)
    payload = build_json_chat_payload(pipeline.settings, system_prompt, user_prompt)
    write_text(paths["llm2"] / "llm2_system_prompt.txt", system_prompt)
    write_text(paths["llm2"] / "llm2_user_prompt.txt", user_prompt)
    write_json(paths["llm2"] / "llm2_request_payload.json", material_planning.sanitize_payload_for_disk(payload))

    if dry_run:
        source_files = {"evidence": dynamic_evidence, "prompt_input": prompt_input, "previous_briefs": {}}
        raw_response = json.dumps(fix3_dry_run_briefs_with_valid_tileability_order(source_files), ensure_ascii=False, indent=2)
        prompt_briefs = parse_json_response(raw_response)
        llm2_attempts = {"llm2_called": False, "attempts": 0, "retry_count": 0, "dry_run_template": True}
        write_text(paths["llm2"] / "llm2_raw_response_attempt_1.txt", raw_response)
        write_json(paths["llm2"] / "llm2_parsed_response_attempt_1.json", prompt_briefs)
    else:
        prompt_briefs, raw_response, llm2_attempts = mainline_prompting.call_llm2_until_valid(
            pipeline,
            payload,
            prompt_input,
            {"request": paths["llm2"], "response": paths["llm2"]},
            max_attempts=max(1, max_attempts),
        )
        write_text(paths["llm2"] / "llm2_raw_response_final.txt", raw_response)

    prompt_validation_before = mainline_prompting.validate_prompt_briefs(prompt_briefs, prompt_input)
    write_json(paths["llm2"] / "material_prompt_briefs_v4_before_normalization.json", prompt_briefs)
    write_json(paths["llm2"] / "llm2_validation_report_before_normalization.json", prompt_validation_before)
    prompt_briefs, normalization_report = mainline_prompting.normalize_prompt_briefs_for_contract(prompt_briefs, prompt_input)
    llm2_attempts["bounded_normalization"] = normalization_report["summary"]
    prompt_validation = mainline_prompting.validate_prompt_briefs(prompt_briefs, prompt_input)
    compiled_sd15 = mainline_prompting.compile_sd15_prompts(prompt_briefs, dynamic_evidence)
    compiled_sm = mainline_prompting.compile_stablematerials_prompts(prompt_briefs, dynamic_evidence)
    comparison = mainline_prompting.build_prompt_comparison(pipeline.output_dir, llm1_result["map_facts"]["map_id"], compiled_sd15)
    prior_usage = mainline_prompting.build_prior_usage_audit(system_prompt, user_prompt)

    write_json(paths["llm2"] / "material_prompt_briefs_v4.json", prompt_briefs)
    write_json(paths["llm2"] / "prompt_normalization_report.json", normalization_report)
    write_json(paths["llm2"] / "llm2_attempts_summary.json", llm2_attempts)
    write_json(paths["llm2"] / "prompt_order_validation.json", prompt_validation["prompt_order"])
    write_json(paths["llm2"] / "richness_tag_validation.json", prompt_validation["richness"])
    write_json(paths["llm2"] / "negative_prompt_validation.json", prompt_validation["negative"])
    write_json(paths["llm2"] / "context_audit_validation.json", prompt_validation["context_audit"])
    write_json(paths["llm2"] / "stablematerials_length_validation.json", prompt_validation["stablematerials_length"])
    write_json(paths["llm2"] / "symbolic_marker_audit.json", prompt_validation["symbolic_marker_audit"])
    write_json(paths["llm2"] / "llm2_validation_report.json", prompt_validation)
    write_json(paths["llm2"] / "prior_usage_audit.json", prior_usage)
    write_json(paths["compiled"] / "compiled_sd15_prompts_v4.json", compiled_sd15)
    write_json(paths["compiled"] / "compiled_stablematerials_prompts_v4.json", compiled_sm)
    write_json(paths["compiled"] / "d6e_fix2_fix3_prompt_comparison.json", comparison)
    write_text(paths["compiled"] / "prompt_comparison_report.md", mainline_prompting.build_comparison_report(comparison))

    stablematerials_policy = build_stablematerials_policy(prompt_validation)
    if stablematerials_policy["downgraded_to_warning"]:
        prompt_validation = downgrade_stablematerials_length_validation(prompt_validation, stablematerials_policy)
        write_json(paths["llm2"] / "stablematerials_length_validation_downgraded.json", prompt_validation["stablematerials_length"])
        write_json(paths["llm2"] / "llm2_validation_report.json", prompt_validation)
    write_json(paths["llm2"] / "stablematerials_length_policy.json", stablematerials_policy)

    if not prompt_validation["summary"]["passed"] or not prior_usage["passed"]:
        raise RuntimeError("LLM2 prompt validation failed.")
    return {
        "prompt_briefs": prompt_briefs,
        "llm2_attempts": llm2_attempts,
        "prompt_validation": prompt_validation,
        "prompt_normalization": normalization_report,
        "compiled_sd15": compiled_sd15,
        "compiled_stablematerials": compiled_sm,
        "prompt_comparison": comparison,
        "prior_usage_audit": prior_usage,
        "stablematerials_policy": stablematerials_policy,
    }


def build_stablematerials_policy(prompt_validation: dict[str, Any]) -> dict[str, Any]:
    summary = prompt_validation.get("summary", {})
    stable_rows = prompt_validation.get("stablematerials_length", {}).get("rows", [])
    stable_errors = [
        {"material_slot_id": row.get("material_slot_id"), "errors": row.get("errors", [])}
        for row in stable_rows
        if not row.get("passed", True)
    ]
    only_stablematerials_length_errors = bool(stable_errors) and all(
        error.get("error") == "stablematerials_length_validation_failed"
        for error in summary.get("errors", [])
    )
    return {
        "schema_version": "stablematerials_non_blocking_length_policy_v1",
        "generation_enabled": not only_stablematerials_length_errors,
        "downgraded_to_warning": only_stablematerials_length_errors,
        "reason": (
            "Only StableMaterials prompt length validation failed after LLM2 retries; SD1.5 remains eligible for generation and RuntimeData export."
            if only_stablematerials_length_errors
            else "StableMaterials prompt length validation passed or other blocking errors exist."
        ),
        "stablematerials_length_errors": stable_errors,
        "policy": [
            "LLM2 is asked to keep StableMaterials prompts concise.",
            "Python validates length but does not semantically trim prompts.",
            "After LLM2 retries, StableMaterials length-only failure disables StableMaterials generation as a warning.",
            "SD1.5 success remains sufficient for default RuntimeData export.",
        ],
    }


def downgrade_stablematerials_length_validation(prompt_validation: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    downgraded = json.loads(json.dumps(prompt_validation))
    old_errors = downgraded.get("summary", {}).get("errors", [])
    kept_errors = [error for error in old_errors if error.get("error") != "stablematerials_length_validation_failed"]
    downgraded["summary"]["errors"] = kept_errors
    downgraded["summary"]["error_count"] = len(kept_errors)
    downgraded["summary"]["passed"] = not kept_errors
    downgraded["summary"]["stablematerials_length_downgraded_to_warning"] = True
    downgraded["summary"]["stablematerials_length_policy"] = policy
    downgraded.setdefault("stablematerials_length", {})["blocking"] = False
    downgraded["stablematerials_length"]["warning_only"] = True
    return downgraded


def fix3_dry_run_briefs_with_valid_tileability_order(source_files: dict[str, Any]) -> dict[str, Any]:
    """Use Fix3 dry-run briefs, then apply a dry-run-only tileability order fix."""
    briefs = mainline_prompting.build_dry_run_briefs(source_files)
    for item in briefs.get("backend_prompt_briefs", []):
        tags = [str(tag) for tag in item.get("sd15", {}).get("positive_tags", [])]
        tile_tags = [tag for tag in tags if "tileable" in tag.lower() or "seamless" in tag.lower()]
        non_tile_tags = [tag for tag in tags if tag not in tile_tags]
        if tile_tags and len(non_tile_tags) >= 2:
            tags = non_tile_tags[:3] + tile_tags[:1] + non_tile_tags[3:]
            item["sd15"]["positive_tags"] = tags[:10]
            richness = item.get("sd15", {}).get("richness_tags", [])
            for rich in richness:
                if rich.get("tag") in tile_tags:
                    rich["tag"] = tags[1] if len(tags) > 1 else tags[0]
    briefs.setdefault("warnings", []).append("A4 dry-run-only compatibility patch moved tileability tags after tag 2.")
    return briefs


def material_slots_from_compiled(compiled_sd15: dict[str, Any]) -> list[str]:
    slots = [item["material_slot_id"] for item in compiled_sd15.get("prompts", []) if item.get("material_slot_id")]
    if len(slots) != len(set(slots)):
        raise ValueError(f"Duplicate material slots in compiled prompts: {slots}")
    if not slots:
        raise ValueError("No material slots found in compiled prompts.")
    return slots


def display_labels_from_slots(material_slots: list[str], compiled_sd15: dict[str, Any], dynamic_evidence: dict[str, Any]) -> dict[str, str]:
    labels = {item["material_slot_id"]: item.get("display_label") or clean_label(item["material_slot_id"]) for item in compiled_sd15.get("prompts", [])}
    evidence = {slot["material_slot_id"]: slot for slot in dynamic_evidence.get("material_slots", [])}
    for slot_id in material_slots:
        labels.setdefault(slot_id, clean_label(slot_id))
        if labels[slot_id] == slot_id and slot_id in evidence:
            labels[slot_id] = clean_label(evidence[slot_id].get("canonical_material_id", slot_id))
    return labels


def clean_label(value: str) -> str:
    text = str(value)
    if text.startswith("mat_"):
        text = text[4:]
    if text.endswith("_material"):
        text = text[:-9]
    return text


def build_sd15_plan_a(compiled_sd15: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in compiled_sd15.get("prompts", []):
        row = dict(item)
        row["plan_id"] = "plan_a"
        row["negative_policy"] = "fix3_as_is"
        row["source"] = "d6f_a4_fresh_fix3_compiled_sd15_prompts_v4"
        rows.append(row)
    return {"schema_version": "compiled_sd15_prompts_d6f_a4_plan_a_v1", "backend": "sd15_a1111_txt2img", "plan_id": "plan_a", "prompts": rows}


def build_sd15_requests(sd15_prompts: dict[str, Any], material_slots: list[str], display_labels: dict[str, str], seeds: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prompts = {item["material_slot_id"]: item for item in sd15_prompts.get("prompts", [])}
    for slot_id in material_slots:
        prompt = prompts[slot_id]
        for seed in seeds:
            rows.append(
                {
                    "request_id": f"sd15_plan_a_{slot_id}_seed_{seed}",
                    "backend": "sd15_a1111_txt2img",
                    "plan_id": "plan_a",
                    "material_slot_id": slot_id,
                    "display_label": display_labels[slot_id],
                    "seed": seed,
                    "positive_prompt": prompt["positive_prompt"],
                    "negative_prompt": prompt.get("negative_prompt", ""),
                    "settings": {**SD15_SETTINGS, "seed": seed},
                }
            )
    return rows


def build_stablematerials_requests(stable_prompts: dict[str, Any], material_slots: list[str], display_labels: dict[str, str], seeds: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prompts = {item["material_slot_id"]: item for item in stable_prompts.get("prompts", [])}
    for slot_id in material_slots:
        prompt = prompts[slot_id]
        for seed in seeds:
            rows.append(
                {
                    "request_id": f"stablematerials_{slot_id}_seed_{seed}",
                    "backend": "stablematerials_lcm",
                    "material_slot_id": slot_id,
                    "display_label": display_labels[slot_id],
                    "seed": seed,
                    "positive_prompt": prompt["positive_prompt"],
                    "negative_prompt": None,
                    "relative_output_dir": f"{slot_id}/seed_{seed}",
                    "settings": {**STABLEMATERIALS_SETTINGS, "seed": seed},
                }
            )
    return rows


def build_generation_diagnostics(
    material_slots: list[str],
    seeds: list[int],
    sd15_outputs: list[dict[str, Any]],
    sm_outputs: list[dict[str, Any]],
    failed_items: list[dict[str, Any]],
    non_blocking_failed_items: list[dict[str, Any]],
    timings: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    expected_sd15 = len(material_slots) * len(seeds)
    expected_sm = len(material_slots) * len(seeds)
    sd_keys = {(item.get("material_slot_id"), int(item.get("seed", -1))) for item in sd15_outputs if item.get("plan_id") == "plan_a"}
    sm_keys = {(item.get("material_slot_id"), int(item.get("seed", -1))) for item in sm_outputs}
    missing = []
    non_blocking_missing = []
    for slot_id in material_slots:
        for seed in seeds:
            if not dry_run and (slot_id, seed) not in sd_keys:
                missing.append({"backend": "sd15_a1111_txt2img", "plan_id": "plan_a", "material_slot_id": slot_id, "seed": seed})
            if not dry_run and (slot_id, seed) not in sm_keys:
                non_blocking_missing.append({"backend": "stablematerials_lcm", "material_slot_id": slot_id, "seed": seed, "non_blocking": True})
    generation_summary = {
        "schema_version": "d6f_a4_generation_summary_v1",
        "dry_run": dry_run,
        "stablematerials_non_blocking": True,
        "expected_sd15_images": expected_sd15,
        "expected_sd15_plan_a_images": expected_sd15,
        "expected_stablematerials_sets": expected_sm,
        "sd15_images_generated": len(sd15_outputs),
        "sd15_plan_a_images_generated": sum(1 for item in sd15_outputs if item.get("plan_id") == "plan_a"),
        "stablematerials_sets_generated": len(sm_outputs),
        "failed_item_count": len(failed_items),
        "non_blocking_failed_item_count": len(non_blocking_failed_items),
        "passed": dry_run or (len(sd15_outputs) == expected_sd15 and len(sm_outputs) == expected_sm and not failed_items and not missing),
        "blocking_passed": dry_run or (len(sd15_outputs) == expected_sd15 and not failed_items and not missing),
        "failed_items": failed_items,
        "non_blocking_failed_items": non_blocking_failed_items,
    }
    generation_summary["passed"] = generation_summary["blocking_passed"]
    return {
        "generation_summary": generation_summary,
        "missing_outputs": {
            "schema_version": "d6f_a4_missing_outputs_v1",
            "rows": missing,
            "non_blocking_rows": non_blocking_missing,
        },
        "non_blocking_failed_items": {
            "schema_version": "d6f_a4_non_blocking_failed_items_v1",
            "rows": non_blocking_failed_items,
            "non_blocking_missing_outputs": non_blocking_missing,
        },
        "timing_summary": {"schema_version": "d6f_a4_timing_summary_v1", "rows": timings},
    }


def build_analysis(
    material_slots: list[str],
    display_labels: dict[str, str],
    sd15_outputs: list[dict[str, Any]],
    sm_outputs: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    rows = []
    notes = []
    groups = (
        ("sd15_a1111_txt2img", "plan_a", sd15_outputs, "output"),
        ("stablematerials_lcm", "stablematerials", sm_outputs, "basecolor"),
    )
    for backend, plan_id, outputs, _image_key in groups:
        for slot_id in material_slots:
            group = [item for item in outputs if item.get("material_slot_id") == slot_id]
            if not group:
                notes.append({"material_slot_id": slot_id, "display_label": display_labels[slot_id], "backend": backend, "plan_id": plan_id, "status": "pending_or_missing", "note": "No generated images available for analysis."})
                continue
            brightness = [item["image_metrics"]["brightness_mean"] for item in group]
            saturation = [item["image_metrics"]["saturation_mean"] for item in group]
            seam = [item["image_metrics"]["seam_jump_mean"] for item in group]
            rows.append(
                {
                    "backend": backend,
                    "plan_id": plan_id,
                    "material_slot_id": slot_id,
                    "display_label": display_labels[slot_id],
                    "image_count": len(group),
                    "brightness_mean_range": [round(min(brightness), 3), round(max(brightness), 3)],
                    "saturation_mean_range": [round(min(saturation), 3), round(max(saturation), 3)],
                    "seam_jump_mean_range": [round(min(seam), 3), round(max(seam), 3)],
                    "heuristic_note": "Metrics are low-level image statistics only; manual visual review is still required.",
                }
            )
            notes.append(
                {
                    "material_slot_id": slot_id,
                    "display_label": display_labels[slot_id],
                    "backend": backend,
                    "plan_id": plan_id,
                    "status": "generated",
                    "note": initial_material_note(display_labels[slot_id], backend, plan_id, group),
                }
            )
    return {
        "stability_observation": {"schema_version": "d6f_a4_stability_observation_v1", "dry_run": dry_run, "rows": rows},
        "per_material_notes": {"schema_version": "d6f_a4_per_material_notes_v1", "rows": notes},
    }


def create_contact_sheets(
    paths: dict[str, Path],
    material_slots: list[str],
    display_labels: dict[str, str],
    seeds: list[int],
    sd15_outputs: list[dict[str, Any]],
    sm_outputs: list[dict[str, Any]],
) -> dict[str, str]:
    sheets = {
        "sd15_plan_a_by_material": paths["contact_sheets"] / "contact_sheet_sd15_plan_a_by_material.png",
        "stablematerials_by_material": paths["contact_sheets"] / "contact_sheet_stablematerials_by_material.png",
        "cross_backend_comparison": paths["contact_sheets"] / "contact_sheet_cross_backend_comparison.png",
        "cross_variation_overview": paths["contact_sheets"] / "contact_sheet_cross_variation_overview.png",
    }
    make_grid_sheet(material_slots, display_labels, seeds, sd15_outputs, "output", sheets["sd15_plan_a_by_material"], "D6F-A4 SD1.5 Plan A")
    make_grid_sheet(material_slots, display_labels, seeds, sm_outputs, "basecolor", sheets["stablematerials_by_material"], "D6F-A4 StableMaterials")
    make_backend_comparison_sheet(material_slots, display_labels, seeds, sd15_outputs, sm_outputs, sheets["cross_backend_comparison"])
    make_cross_variation_sheet(display_labels, sd15_outputs, sm_outputs, sheets["cross_variation_overview"])
    return {key: str(path) for key, path in sheets.items()}


def make_grid_sheet(material_slots: list[str], display_labels: dict[str, str], seeds: list[int], outputs: list[dict[str, Any]], image_key: str, output_path: Path, title: str) -> None:
    thumb = 160
    label_h = 42
    margin = 16
    rows = len(material_slots)
    cols = len(seeds)
    canvas = Image.new("RGB", (margin * 2 + cols * thumb, margin * 2 + 34 + rows * (thumb + label_h)), (28, 30, 30))
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, margin), title, fill=(235, 235, 230))
    by_key = {(item["material_slot_id"], int(item["seed"])): item for item in outputs}
    y0 = margin + 34
    for row, slot_id in enumerate(material_slots):
        for col, seed in enumerate(seeds):
            x = margin + col * thumb
            y = y0 + row * (thumb + label_h)
            item = by_key.get((slot_id, seed))
            if item:
                with Image.open(item[image_key]) as image:
                    canvas.paste(image.convert("RGB").resize((thumb, thumb), Image.Resampling.LANCZOS), (x, y))
            else:
                draw.rectangle((x, y, x + thumb - 1, y + thumb - 1), fill=(70, 32, 32))
                draw.text((x + 8, y + 70), "missing", fill=(255, 220, 220))
            draw.text((x + 4, y + thumb + 4), f"{display_labels[slot_id]}\nseed {seed}", fill=(230, 230, 220))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def make_backend_comparison_sheet(material_slots: list[str], display_labels: dict[str, str], seeds: list[int], sd15_outputs: list[dict[str, Any]], sm_outputs: list[dict[str, Any]], output_path: Path) -> None:
    thumb = 150
    label_w = 130
    label_h = 24
    margin = 16
    cols = len(seeds) * 2
    canvas = Image.new("RGB", (margin * 2 + label_w + cols * thumb, margin * 2 + 44 + len(material_slots) * (thumb + label_h)), (28, 30, 30))
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, margin), "D6F-A4 backend comparison: SD1.5 Plan A vs StableMaterials", fill=(235, 235, 230))
    sd_by_key = {(item["material_slot_id"], int(item["seed"])): item for item in sd15_outputs}
    sm_by_key = {(item["material_slot_id"], int(item["seed"])): item for item in sm_outputs}
    y0 = margin + 44
    for row, slot_id in enumerate(material_slots):
        y = y0 + row * (thumb + label_h)
        draw.text((margin, y + 50), display_labels[slot_id], fill=(235, 235, 230))
        for seed_index, seed in enumerate(seeds):
            for backend_index, (label, data, key) in enumerate((("SD", sd_by_key, "output"), ("SM", sm_by_key, "basecolor"))):
                col = seed_index * 2 + backend_index
                x = margin + label_w + col * thumb
                item = data.get((slot_id, seed))
                if item:
                    with Image.open(item[key]) as image:
                        canvas.paste(image.convert("RGB").resize((thumb, thumb), Image.Resampling.LANCZOS), (x, y))
                else:
                    draw.rectangle((x, y, x + thumb - 1, y + thumb - 1), fill=(70, 32, 32))
                draw.text((x + 4, y + thumb + 4), f"{label} {seed}", fill=(230, 230, 220))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def make_cross_variation_sheet(display_labels: dict[str, str], sd15_outputs: list[dict[str, Any]], sm_outputs: list[dict[str, Any]], output_path: Path) -> None:
    rows = [{**item, "image_key": "output"} for item in sd15_outputs] + [{**item, "image_key": "basecolor"} for item in sm_outputs]
    thumb = 128
    cols = 8
    margin = 16
    label_h = 28
    height_rows = (len(rows) + cols - 1) // cols
    canvas = Image.new("RGB", (margin * 2 + cols * thumb, margin * 2 + 30 + height_rows * (thumb + label_h)), (28, 30, 30))
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, margin), "D6F-A4 cross variation overview", fill=(235, 235, 230))
    for index, item in enumerate(rows):
        col = index % cols
        row = index // cols
        x = margin + col * thumb
        y = margin + 30 + row * (thumb + label_h)
        with Image.open(item[item["image_key"]]) as image:
            canvas.paste(image.convert("RGB").resize((thumb, thumb), Image.Resampling.LANCZOS), (x, y))
        backend = "SD" if item["backend"].startswith("sd15") else "SM"
        label = display_labels.get(item["material_slot_id"], item["material_slot_id"])
        draw.text((x + 3, y + thumb + 3), f"{backend} {label} {item['seed']}", fill=(230, 230, 220))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def build_direct_visual_review(analysis: dict[str, Any], dry_run: bool) -> str:
    lines = [
        "# Direct Visual Review",
        "",
        f"- Dry run: {dry_run}",
        "- SD1.5 uses Plan A only: Fix3 positive prompts with Fix3 negative prompts as-is.",
        "- StableMaterials runs as the parallel preview backend.",
        "- This file is initialized automatically; inspect contact sheets for final visual judgement.",
        "",
        "## Per-Material Notes",
        "",
    ]
    for note in analysis.get("per_material_notes", {}).get("rows", []):
        lines.append(f"- `{note.get('backend')}` / `{note.get('display_label')}`: {note.get('note')}")
    return "\n".join(lines) + "\n"


def initial_material_note(display_label: str, backend: str, plan_id: str, group: list[dict[str, Any]]) -> str:
    seam_values = [item["image_metrics"]["seam_jump_mean"] for item in group]
    seam_mean = sum(seam_values) / max(1, len(seam_values))
    return f"{len(group)} previews generated for {display_label} via {backend} / {plan_id}; mean edge seam jump {seam_mean:.2f}. Manual contact-sheet review required."


def build_prior_leak_audit(llm1_result: dict[str, Any], llm2_result: dict[str, Any], positive_audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "d6f_a4_prior_leak_audit_v1",
        "passed": bool(
            llm1_result["llm1_request_audit"]["passed"]
            and llm1_result["prompt_input_audit"]["passed"]
            and llm2_result["prior_usage_audit"]["passed"]
        ),
        "old_material_slot_rules_used": False,
        "old_material_slot_evidence_used": False,
        "suggested_prompt_hint_used": False,
        "decorative_symbols_passed_to_llm2": False,
        "python_context_classifier_added": False,
        "new_backend_failure_negative_library_added": False,
        "new_hardcoded_material_prompt_hints_added": False,
        "llm1_request_audit": llm1_result["llm1_request_audit"],
        "prompt_input_audit": llm1_result["prompt_input_audit"],
        "llm2_prior_usage_audit": llm2_result["prior_usage_audit"],
        "positive_side_audit_reported_only": positive_audit,
    }


def build_summary(
    run_dir: Path,
    command: str,
    map_id: str,
    dry_run: bool,
    material_slots: list[str],
    display_labels: dict[str, str],
    generation_seed_config: dict[str, Any],
    llm1_result: dict[str, Any],
    llm2_result: dict[str, Any],
    preflight: dict[str, Any],
    diagnostics: dict[str, Any],
    analysis: dict[str, Any],
    contact_sheets: dict[str, str],
    prior_audit: dict[str, Any],
    elapsed_seconds: float,
) -> dict[str, Any]:
    generation_summary = diagnostics["generation_summary"]
    prompt_validation_passed = llm2_result["prompt_validation"]["summary"]["passed"]
    native_passed = bool(
        (dry_run or generation_summary["passed"])
        and llm1_result["llm1_validation"]["summary"]["passed"]
        and llm1_result["evidence_validation"]["passed"]
        and llm1_result["prompt_input_audit"]["passed"]
        and prompt_validation_passed
        and prior_audit["passed"]
    )
    status = "dry_run_planned" if dry_run else ("passed" if native_passed else "failed")
    return {
        "schema_version": "d6f_a4_summary_v1",
        "status": status,
        "round_id": ROUND_ID,
        "map_id": map_id,
        "dry_run": dry_run,
        "created_at": timestamp_iso(),
        "command": command,
        "output_run": str(run_dir),
        "material_slots": material_slots,
        "display_labels": display_labels,
        "seeds": generation_seed_config["seeds"],
        "images_per_material": generation_seed_config["images_per_material"],
        "generation_seed_config": generation_seed_config,
        "llm1_called": not dry_run,
        "llm2_called": not dry_run,
        "llm1_attempts": llm1_result["llm1_attempts"].get("llm_call_count", 0),
        "llm2_attempts": llm2_result["llm2_attempts"].get("attempts", 0),
        "llm1_retry_count": llm1_result["llm1_attempts"].get("retry_count", 0),
        "llm2_retry_count": llm2_result["llm2_attempts"].get("retry_count", 0),
        "llm2_prompt_normalization": llm2_result.get("prompt_normalization", {}).get("summary", {}),
        "llm2_prompt_normalization_applied": llm2_result.get("prompt_normalization", {}).get("summary", {}).get("applied", False),
        "sd_webui_called": not dry_run,
        "stablematerials_called": not dry_run,
        "image_generation_called": not dry_run,
        "sd15_images_generated": generation_summary["sd15_images_generated"],
        "sd15_plan_a_images_generated": generation_summary["sd15_plan_a_images_generated"],
        "stablematerials_sets_generated": generation_summary["stablematerials_sets_generated"],
        "failed_item_count": generation_summary["failed_item_count"],
        "failed_items": generation_summary["failed_items"],
        "stablematerials_non_blocking": generation_summary.get("stablematerials_non_blocking", True),
        "non_blocking_failed_item_count": generation_summary.get("non_blocking_failed_item_count", 0),
        "non_blocking_failed_items": generation_summary.get("non_blocking_failed_items", []),
        "runtime_data_exported": False,
        "generated_package_exported": False,
        "ue_modified": False,
        "old_material_slot_rules_used": False,
        "old_material_slot_evidence_used": False,
        "suggested_prompt_hint_used": False,
        "fix1_llm1_resolver_reused": True,
        "fix3_prompt_contract_reused": True,
        "fix3_patched_validator_reused": True,
        "fix4_plan_a_generation_reused": True,
        "plan_b_run": False,
        "native_run_passed_without_posthoc_recheck": (not dry_run and status == "passed"),
        "llm1_validation_passed": llm1_result["llm1_validation"]["summary"]["passed"],
        "dynamic_evidence_validation_passed": llm1_result["evidence_validation"]["passed"],
        "prompt_input_audit_passed": llm1_result["prompt_input_audit"]["passed"],
        "llm2_prompt_validation_passed": prompt_validation_passed,
        "prior_leak_audit_passed": prior_audit["passed"],
        "preflight": preflight,
        "generation_summary": generation_summary,
        "missing_outputs": diagnostics["missing_outputs"],
        "analysis": analysis,
        "contact_sheets": contact_sheets,
        "elapsed_seconds": elapsed_seconds,
        "summary_path": str(run_dir / "10_reports" / "d6f_a4_full_two_llm_material_generation_preview_summary.json"),
        "report_path": str(run_dir / "10_reports" / "d6f_a4_full_two_llm_material_generation_preview_report.md"),
        "key_files": {
            "map_facts": str(run_dir / "01_map_facts" / "map_facts_v2.json"),
            "llm1_plan": str(run_dir / "02_llm1_material_plan" / "llm_tile_material_plan_v2.json"),
            "resolved_tileset": str(run_dir / "03_python_resolver" / "resolved_tileset_v2.json"),
            "resolved_materials": str(run_dir / "03_python_resolver" / "resolved_materials_v2.json"),
            "dynamic_evidence": str(run_dir / "04_dynamic_material_evidence" / "dynamic_material_slot_evidence_v3.json"),
            "prompt_llm_input": str(run_dir / "04_dynamic_material_evidence" / "prompt_llm_input_v3.json"),
            "llm2_briefs": str(run_dir / "05_llm2_prompt_briefs" / "material_prompt_briefs_v4.json"),
            "llm2_briefs_before_normalization": str(run_dir / "05_llm2_prompt_briefs" / "material_prompt_briefs_v4_before_normalization.json"),
            "llm2_prompt_normalization_report": str(run_dir / "05_llm2_prompt_briefs" / "prompt_normalization_report.json"),
            "compiled_sd15": str(run_dir / "06_compiled_prompts" / "compiled_sd15_prompts_v4.json"),
            "compiled_stablematerials": str(run_dir / "06_compiled_prompts" / "compiled_stablematerials_prompts_v4.json"),
            "prior_leak_audit": str(run_dir / "09_analysis" / "prior_leak_audit.json"),
        },
    }


def build_run_manifest(run_dir: Path, command: str, dry_run: bool, summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "d6f_a4_run_manifest_v1",
        "round_id": ROUND_ID,
        "created_at": timestamp_iso(),
        "run_dir": str(run_dir),
        "command": command,
        "dry_run": dry_run,
        "status": summary["status"],
        "no_runtime_data_refresh": True,
        "no_generated_package_export": True,
        "no_ue_modification": True,
        "plan_b_not_run": True,
    }


def build_key_outputs_index(paths: dict[str, Path], summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "d6f_a4_key_outputs_index_v1",
        "summary": str(paths["reports"] / "d6f_a4_full_two_llm_material_generation_preview_summary.json"),
        "report": str(paths["reports"] / "d6f_a4_full_two_llm_material_generation_preview_report.md"),
        "map_facts": str(paths["map_facts"]),
        "llm1": str(paths["llm1"]),
        "resolver": str(paths["resolver"]),
        "dynamic_material_evidence": str(paths["evidence"]),
        "llm2": str(paths["llm2"]),
        "compiled_prompts": str(paths["compiled"]),
        "generation": str(paths["generation"]),
        "contact_sheets": summary.get("contact_sheets", {}),
        "analysis": str(paths["analysis"]),
    }


def copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


