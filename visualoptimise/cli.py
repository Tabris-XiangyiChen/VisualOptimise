"""Command-line interface for the self-contained VisualOptimise pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from visualoptimise.config_loader import load_defaults
from visualoptimise.orchestrator import MainPipelineRequest, run_main_pipeline
from visualoptimise.submission_validation import run_b3_submission_validation


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_seed_values(raw_seeds: list[str] | None) -> list[int] | None:
    if raw_seeds is None:
        return None
    parsed: list[int] = []
    for item in raw_seeds:
        for part in str(item).split(","):
            value = part.strip()
            if not value:
                continue
            seed = int(value)
            if seed < 0:
                raise ValueError("Seed values must be non-negative integers.")
            parsed.append(seed)
    return parsed or None


def main(argv: list[str] | None = None) -> int:
    defaults = load_defaults(PROJECT_ROOT)
    parser = argparse.ArgumentParser(description="Run the VisualOptimise production material pipeline.")
    parser.add_argument("--map", dest="map_id", default=defaults.get("default_map", "test_map1_clean"))
    parser.add_argument("--maps", nargs="*", help="Run the selected mode for multiple map packages in sequence.")
    parser.add_argument("--full", action="store_true", help="Run material generation followed by RuntimeData export.")
    parser.add_argument("--generate-materials", action="store_true", help="Run material generation only.")
    parser.add_argument("--export-runtime-data", action="store_true", help="Export RuntimeData from an existing material run.")
    parser.add_argument("--b3-submission-hardening-validation", action="store_true", help="Run submission hardening, config, audit, and equivalence validation.")
    parser.add_argument("--reuse-materials-from", help="Existing successful material generation run for export-only mode.")
    parser.add_argument("--runtime-texture-backend", choices=["sd15", "stablematerials"], default=defaults.get("runtime_texture_backend", "sd15"))
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not call LLMs, image backends, or refresh RuntimeData.")
    parser.add_argument("--semantic-mode", default=defaults.get("semantic_mode", "llm"))
    parser.add_argument("--material-mode", default=defaults.get("material_mode", "preview-only"))
    parser.add_argument("--llm-max-attempts", type=int, default=int(defaults.get("llm_max_attempts", 2)))
    parser.add_argument("--prompt-llm-max-attempts", type=int, default=int(defaults.get("prompt_llm_max_attempts", 2)))
    parser.add_argument("--seeds", nargs="*", help="Generation seeds for material generation mode.")
    parser.add_argument("--images-per-material", type=int, help="Random images per material when seeds are not specified.")
    parser.add_argument("--no-refresh-runtime-data", action="store_true", help="Do not refresh generated/ue_ready/runtime_data.")
    args = parser.parse_args(argv)

    selected_modes = [args.full, args.generate_materials, args.export_runtime_data, args.b3_submission_hardening_validation]
    if sum(1 for value in selected_modes if value) != 1:
        parser.error("Choose exactly one of --full, --generate-materials, --export-runtime-data, or --b3-submission-hardening-validation.")
    if args.b3_submission_hardening_validation:
        try:
            run_dir = run_b3_submission_validation(PROJECT_ROOT)
        except Exception as exc:
            print(f"VisualOptimise B3 submission hardening failed: {exc}")
            return 1
        print(f"VisualOptimise B3 submission hardening complete: {run_dir}")
        return 0
    map_ids = args.maps if args.maps else [args.map_id]
    if not map_ids:
        parser.error("At least one map id is required.")
    mode = "full" if args.full else "generate-materials" if args.generate_materials else "export-only"
    reuse_materials_from = Path(args.reuse_materials_from).resolve() if args.reuse_materials_from else None
    if len(map_ids) > 1 and reuse_materials_from is not None:
        parser.error("--reuse-materials-from is single-map only. Omit it for multi-map export so each map can auto-select its own source run.")
    seeds = parse_seed_values(args.seeds)
    run_dirs: list[Path] = []
    try:
        for map_id in map_ids:
            request = MainPipelineRequest(
                project_root=PROJECT_ROOT,
                map_id=map_id,
                mode=mode,
                dry_run=args.dry_run,
                semantic_mode=args.semantic_mode,
                material_mode=args.material_mode,
                llm_max_attempts=args.llm_max_attempts,
                prompt_llm_max_attempts=args.prompt_llm_max_attempts,
                runtime_texture_backend=args.runtime_texture_backend,
                refresh_runtime_data=bool(defaults.get("refresh_runtime_data", True)) and not args.no_refresh_runtime_data,
                reuse_materials_from=reuse_materials_from,
                seeds=seeds,
                images_per_material=args.images_per_material,
            )
            run_dirs.append(run_main_pipeline(request))
    except Exception as exc:
        print(f"VisualOptimise pipeline failed: {exc}")
        return 1
    mode_label = "dry-run" if args.dry_run else mode
    if len(run_dirs) == 1:
        print(f"VisualOptimise pipeline {mode_label} complete: {run_dirs[0]}")
    else:
        print(f"VisualOptimise pipeline {mode_label} complete for {len(run_dirs)} maps:")
        for run_dir in run_dirs:
            print(f"  {run_dir}")
    return 0
