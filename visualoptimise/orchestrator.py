"""Self-contained production pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from visualoptimise import material_generation_pipeline, runtime_export
from visualoptimise.artifacts import ensure_dirs, read_json, timestamp_for_run, timestamp_iso, write_json, write_text
from visualoptimise.config_loader import load_settings
from visualoptimise.reports import build_report, build_summary, material_generation_summary, runtime_export_summary


RUN_DIRS = {
    "run": "00_run",
    "material": "01_material_generation",
    "export": "02_runtime_export",
    "validation": "03_validation",
    "reports": "04_reports",
}


@dataclass(frozen=True)
class MainPipelineRequest:
    project_root: Path
    map_id: str
    mode: str
    dry_run: bool
    semantic_mode: str
    material_mode: str
    llm_max_attempts: int
    prompt_llm_max_attempts: int
    runtime_texture_backend: str
    refresh_runtime_data: bool
    reuse_materials_from: Path | None
    seeds: list[int] | None
    images_per_material: int | None


class ProductionPipelineContext:
    """Minimal context object expected by the extracted pipeline modules."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.output_dir = self.root / "outputs" / "runs"
        self.config_dir = self.root / "config"
        self.data_dir = self.root / "data"
        self.settings = load_settings(self.root)

    def _runtime_settings(self, dry_run: bool = False) -> dict[str, Any]:
        settings = dict(self.settings)
        settings["_dry_run"] = dry_run
        return settings


def run_main_pipeline(request: MainPipelineRequest) -> Path:
    pipeline = ProductionPipelineContext(request.project_root)
    run_dir = pipeline.output_dir / f"{timestamp_for_run()}_{request.map_id}_main_pipeline"
    paths = {name: run_dir / rel for name, rel in RUN_DIRS.items()}
    ensure_dirs(paths)

    command = build_command(request)
    stage_plan = build_stage_plan(request)
    write_text(paths["run"] / "command.txt", command)
    write_json(paths["run"] / "main_pipeline_config.json", request_to_json(request))
    write_json(paths["run"] / "stage_plan.json", stage_plan)

    material_run: Path | None = None
    export_run: Path | None = None
    status = "passed"
    errors: list[str] = []

    if request.dry_run:
        validation = build_dry_run_validation(request, pipeline)
        write_json(paths["material"] / "material_generation_run_reference.json", {"planned": stage_plan["material_generation"], "run": None})
        write_json(paths["export"] / "runtime_export_run_reference.json", {"planned": stage_plan["runtime_export"], "run": None})
    else:
        try:
            if request.mode in {"full", "generate-materials"}:
                material_run = material_generation_pipeline.run_experiment(
                    pipeline,
                    request.map_id,
                    [request.map_id],
                    False,
                    request.semantic_mode,
                    request.material_mode,
                    request.llm_max_attempts,
                    request.prompt_llm_max_attempts,
                    request.seeds,
                    request.images_per_material,
                )
            elif request.reuse_materials_from:
                material_run = request.reuse_materials_from

            if request.mode in {"full", "export-only"}:
                source_run = material_run if material_run is not None else request.reuse_materials_from
                export_run = runtime_export.run_experiment(
                    pipeline,
                    request.map_id,
                    [request.map_id],
                    False,
                    source_run,
                    request.refresh_runtime_data,
                    request.runtime_texture_backend,
                    True,
                )
        except Exception as exc:
            status = "failed"
            errors.append(str(exc))

        write_json(paths["material"] / "material_generation_run_reference.json", build_reference(material_run, "material_generation"))
        write_json(paths["export"] / "runtime_export_run_reference.json", build_reference(export_run, "runtime_export"))
        validation = {
            "schema_version": "visualoptimise_main_pipeline_validation_v1",
            "passed": status == "passed",
            "dry_run": False,
            "errors": errors,
            "warnings": [],
        }

    write_json(paths["validation"] / "main_pipeline_validation.json", validation)
    material_summary = material_generation_summary(material_run)
    export_summary = runtime_export_summary(export_run)
    if not request.dry_run and status == "passed":
        if request.mode in {"full", "generate-materials"} and material_summary.get("status") != "passed":
            status = "failed"
        if request.mode in {"full", "export-only"} and export_summary.get("status") != "passed":
            status = "failed"

    summary = build_summary(
        status=status,
        command=command,
        map_id=request.map_id,
        mode=request.mode,
        dry_run=request.dry_run,
        runtime_texture_backend=request.runtime_texture_backend,
        material_run=material_run,
        export_run=export_run,
        material_summary=material_summary,
        export_summary=export_summary,
        stage_plan=stage_plan,
        copy_to_ue_path=request.project_root.parent / "VisualOptimizationUE" / "Content" / "VisualOptimization" / "RuntimeData",
    )
    write_json(paths["reports"] / "main_pipeline_summary.json", summary)
    write_text(paths["reports"] / "main_pipeline_report.md", build_report(summary))

    if status != "passed":
        raise RuntimeError(f"Main pipeline failed. See {paths['reports'] / 'main_pipeline_summary.json'}")
    return run_dir


def build_dry_run_validation(request: MainPipelineRequest, pipeline: ProductionPipelineContext) -> dict[str, Any]:
    errors = []
    warnings = ["Dry-run is plan-only; no LLM calls, image generation, backend calls, or RuntimeData refresh occurred."]
    map_dir = pipeline.root / "data" / "maps" / request.map_id
    for filename in ("map.txt", "legend.json", "style.txt"):
        if not (map_dir / filename).is_file():
            errors.append(f"Missing map package file: {map_dir / filename}")
    mesh_catalog = pipeline.root / "data" / "ue_asset_catalogs" / "mesh_catalog.json"
    if not mesh_catalog.is_file():
        errors.append(f"Missing mesh catalog: {mesh_catalog}")
    else:
        loaded = read_json(mesh_catalog)
        if not isinstance(loaded.get("meshes"), list):
            errors.append("mesh_catalog.json must contain a meshes array.")
    if request.mode == "export-only" and request.reuse_materials_from is None:
        warnings.append("Export-only dry-run will auto-select the latest successful material run for this map at real execution time.")
    return {
        "schema_version": "visualoptimise_main_pipeline_validation_v1",
        "passed": not errors,
        "dry_run": True,
        "errors": errors,
        "warnings": warnings,
    }


def build_stage_plan(request: MainPipelineRequest) -> dict[str, Any]:
    material_stage_enabled = request.mode in {"full", "generate-materials"}
    export_stage_enabled = request.mode in {"full", "export-only"}
    return {
        "schema_version": "visualoptimise_main_pipeline_stage_plan_v1",
        "created_at": timestamp_iso(),
        "map_id": request.map_id,
        "mode": request.mode,
        "dry_run": request.dry_run,
        "material_generation": {
            "enabled": material_stage_enabled,
            "module": "visualoptimise.material_generation_pipeline",
            "semantic_mode": request.semantic_mode,
            "material_mode": request.material_mode,
            "llm_max_attempts": request.llm_max_attempts,
            "prompt_llm_max_attempts": request.prompt_llm_max_attempts,
            "seeds": request.seeds,
            "images_per_material": request.images_per_material,
            "would_call_llm": material_stage_enabled and not request.dry_run,
            "would_generate_images": material_stage_enabled and not request.dry_run,
        },
        "runtime_export": {
            "enabled": export_stage_enabled,
            "module": "visualoptimise.runtime_export",
            "reuse_materials_from": str(request.reuse_materials_from) if request.reuse_materials_from else None,
            "runtime_texture_backend": request.runtime_texture_backend,
            "refresh_runtime_data": request.refresh_runtime_data and not request.dry_run,
            "would_refresh_runtime_data": export_stage_enabled and request.refresh_runtime_data and not request.dry_run,
            "would_modify_ue_project": False,
        },
    }


def request_to_json(request: MainPipelineRequest) -> dict[str, Any]:
    return {
        "schema_version": "visualoptimise_main_pipeline_config_v1",
        "project_root": str(request.project_root),
        "map_id": request.map_id,
        "mode": request.mode,
        "dry_run": request.dry_run,
        "semantic_mode": request.semantic_mode,
        "material_mode": request.material_mode,
        "llm_max_attempts": request.llm_max_attempts,
        "prompt_llm_max_attempts": request.prompt_llm_max_attempts,
        "runtime_texture_backend": request.runtime_texture_backend,
        "refresh_runtime_data": request.refresh_runtime_data,
        "reuse_materials_from": str(request.reuse_materials_from) if request.reuse_materials_from else None,
        "seeds": request.seeds,
        "images_per_material": request.images_per_material,
    }


def build_reference(run_dir: Path | None, stage: str) -> dict[str, Any]:
    return {
        "schema_version": "visualoptimise_main_pipeline_stage_reference_v1",
        "stage": stage,
        "run": str(run_dir) if run_dir else None,
        "exists": bool(run_dir and run_dir.is_dir()),
    }


def build_command(request: MainPipelineRequest) -> str:
    parts = [
        r"I:\MiniConda3\envs\dissertation\python.exe",
        str(request.project_root / "run_main_pipeline.py"),
        "--map",
        request.map_id,
        f"--{request.mode if request.mode != 'export-only' else 'export-runtime-data'}",
        "--runtime-texture-backend",
        request.runtime_texture_backend,
    ]
    if request.reuse_materials_from:
        parts.extend(["--reuse-materials-from", str(request.reuse_materials_from)])
    if request.dry_run:
        parts.append("--dry-run")
    if not request.refresh_runtime_data:
        parts.append("--no-refresh-runtime-data")
    if request.seeds:
        parts.extend(["--seeds", *[str(seed) for seed in request.seeds]])
    if request.images_per_material is not None:
        parts.extend(["--images-per-material", str(request.images_per_material)])
    return " ".join(parts)
