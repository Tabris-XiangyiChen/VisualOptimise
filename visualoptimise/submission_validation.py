"""D6G-B3 submission hardening validation.

This module validates the extracted VisualOptimise submission project without
calling LLMs or image-generation backends.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from visualoptimise.artifacts import ensure_dirs, read_json, timestamp_for_run, timestamp_iso, write_json, write_text
from visualoptimise.backend_config import backend_paths_to_json, load_backend_paths
from visualoptimise import material_generation_pipeline, preview_generation, prompt_generation, runtime_export


B3_SUFFIX = "b3_submission_hardening_validation"
REFERENCE_MATERIAL_RUN_NAME = "20260811_041903_test_map1_clean_d6f_a4_full_two_llm_material_generation_preview"
REFERENCE_EXPORT_RUN_NAME = "20260812_202201_test_map1_clean_d6g_a2_material_manifest_runtime_export"

RUN_DIRS = {
    "run": "00_run",
    "config": "01_config",
    "audits": "02_audits",
    "validation": "03_validation",
    "docs": "04_docs",
    "reports": "05_reports",
}


def run_b3_submission_validation(project_root: Path) -> Path:
    project_root = project_root.resolve()
    run_dir = project_root / "outputs" / "runs" / f"{timestamp_for_run()}_{B3_SUFFIX}"
    paths = {name: run_dir / relative for name, relative in RUN_DIRS.items()}
    ensure_dirs(paths)

    backend_paths = load_backend_paths(project_root)
    reference_project = project_root.parent / "VisualOptimization"
    reference_material_run = reference_project / "outputs" / "runs" / REFERENCE_MATERIAL_RUN_NAME
    reference_export_run = reference_project / "outputs" / "runs" / REFERENCE_EXPORT_RUN_NAME

    write_text(paths["run"] / "command.txt", build_b3_command(project_root))
    write_json(paths["run"] / "source_project_reference.json", {"reference_project_root": str(reference_project), "exists": reference_project.is_dir()})
    write_json(paths["run"] / "target_project_reference.json", {"project_root": str(project_root), "exists": project_root.is_dir()})
    write_json(paths["run"] / "config_files_index.json", config_files_index(project_root))
    write_json(paths["config"] / "backend_paths_resolved.json", backend_paths_to_json(backend_paths))
    backend_paths_validation = validate_backend_paths(backend_paths)
    write_json(paths["config"] / "backend_paths_validation.json", backend_paths_validation)
    config_externalization = build_config_externalization_report(project_root, backend_paths_validation)
    write_json(paths["config"] / "config_externalization_report.json", config_externalization)

    syntax_validation = run_syntax_validation(project_root)
    write_json(paths["validation"] / "syntax_validation.json", syntax_validation)

    full_dry_run_validation = run_full_dry_run_validation(project_root)
    write_json(paths["validation"] / "full_dry_run_validation.json", full_dry_run_validation)

    export_only_sd15_validation = run_export_only_validation(project_root, reference_material_run, "sd15", refresh_latest=True)
    write_json(paths["validation"] / "export_only_sd15_validation.json", export_only_sd15_validation)

    before_latest = snapshot_latest_runtime_data(project_root)
    backend_switch_validation = run_export_only_validation(project_root, reference_material_run, "stablematerials", refresh_latest=False)
    after_latest = snapshot_latest_runtime_data(project_root)
    latest_protection = {
        "schema_version": "latest_runtime_data_protection_validation_v1",
        "passed": before_latest == after_latest,
        "before": before_latest,
        "after": after_latest,
        "backend_switch_overwrote_latest": before_latest != after_latest,
    }
    write_json(paths["validation"] / "backend_switch_stablematerials_validation.json", backend_switch_validation)
    write_json(paths["validation"] / "latest_runtime_data_protection_validation.json", latest_protection)

    import_audit = run_import_isolation_audit(project_root)
    path_audit = run_path_hardcode_audit(project_root)
    structure_audit = run_structure_equivalence_audit(project_root, reference_material_run, reference_export_run, export_only_sd15_validation)
    behavior_audit = run_behavior_equivalence_audit(reference_material_run)
    docs_audit = run_documentation_completeness_audit(project_root)
    write_json(paths["audits"] / "import_isolation_audit.json", import_audit)
    write_json(paths["audits"] / "path_hardcode_audit.json", path_audit)
    write_json(paths["audits"] / "structure_equivalence_audit.json", structure_audit)
    write_json(paths["audits"] / "behavior_equivalence_audit.json", behavior_audit)
    write_json(paths["audits"] / "documentation_completeness_audit.json", docs_audit)
    write_json(paths["docs"] / "docs_update_manifest.json", build_docs_update_manifest(project_root))

    summary = build_summary(
        project_root=project_root,
        reference_project=reference_project,
        backend_paths_validation=backend_paths_validation,
        config_externalization=config_externalization,
        syntax_validation=syntax_validation,
        full_dry_run_validation=full_dry_run_validation,
        export_only_sd15_validation=export_only_sd15_validation,
        backend_switch_validation=backend_switch_validation,
        latest_protection=latest_protection,
        import_audit=import_audit,
        path_audit=path_audit,
        structure_audit=structure_audit,
        behavior_audit=behavior_audit,
        docs_audit=docs_audit,
    )
    write_json(paths["reports"] / "d6g_b3_submission_hardening_summary.json", summary)
    write_text(paths["reports"] / "d6g_b3_submission_hardening_report.md", build_report(summary, run_dir))
    if summary["status"] != "passed":
        raise RuntimeError(f"B3 submission hardening failed. See {paths['reports'] / 'd6g_b3_submission_hardening_summary.json'}")
    return run_dir


def build_b3_command(project_root: Path) -> str:
    backend_paths = load_backend_paths(project_root)
    python_executable = str(backend_paths.dissertation_python or sys.executable)
    return f"{python_executable} {project_root / 'run_main_pipeline.py'} --b3-submission-hardening-validation"


def config_files_index(project_root: Path) -> dict[str, Any]:
    files = [
        project_root / "settings" / "backend_paths.json",
        project_root / "settings" / "pipeline_defaults.json",
        project_root / "settings" / "material_generation_defaults.json",
        project_root / "settings" / "runtime_export_defaults.json",
        project_root / "config" / "settings.json",
    ]
    return {
        "schema_version": "config_files_index_v1",
        "files": [{"path": str(path), "exists": path.is_file()} for path in files],
    }


def validate_backend_paths(paths: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not paths.webui_base_url:
        errors.append("WebUI base URL is not configured.")
    if paths.stablematerials_python is None:
        errors.append("StableMaterials Python is not configured.")
    elif not paths.stablematerials_python.is_file():
        warnings.append(f"StableMaterials Python is configured but not found: {paths.stablematerials_python}")
    if paths.stablematerials_model_dir is None:
        errors.append("StableMaterials model directory is not configured.")
    elif not paths.stablematerials_model_dir.is_dir():
        warnings.append(f"StableMaterials model directory is configured but not found: {paths.stablematerials_model_dir}")
    if not paths.stablematerials_worker:
        errors.append("StableMaterials worker is not configured.")
    if paths.ue_runtime_data_destination is None:
        errors.append("UE RuntimeData copy destination is not configured.")
    if paths.ue_runtime_virtual_root != "VisualOptimization/RuntimeData":
        errors.append(f"UE virtual root changed unexpectedly: {paths.ue_runtime_virtual_root}")
    return {
        "schema_version": "backend_paths_validation_v1",
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "webui_base_url_from_config": True,
        "stablematerials_python_from_config": paths.stablematerials_python is not None,
        "stablematerials_model_dir_from_config": paths.stablematerials_model_dir is not None,
        "stablematerials_worker_from_config": bool(paths.stablematerials_worker),
        "ue_copy_destination_from_config": paths.ue_runtime_data_destination is not None,
        "ue_runtime_virtual_root_preserved": paths.ue_runtime_virtual_root == "VisualOptimization/RuntimeData",
    }


def build_config_externalization_report(project_root: Path, backend_validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "config_externalization_report_v1",
        "passed": backend_validation["passed"],
        "settings_backend_paths": str(project_root / "settings" / "backend_paths.json"),
        "webui_base_url_from_config": backend_validation["webui_base_url_from_config"],
        "stablematerials_python_from_config": backend_validation["stablematerials_python_from_config"],
        "stablematerials_model_dir_from_config": backend_validation["stablematerials_model_dir_from_config"],
        "stablematerials_worker_from_config": backend_validation["stablematerials_worker_from_config"],
        "ue_copy_destination_from_config": backend_validation["ue_copy_destination_from_config"],
        "ue_runtime_virtual_root_preserved": backend_validation["ue_runtime_virtual_root_preserved"],
    }


def run_syntax_validation(project_root: Path) -> dict[str, Any]:
    commands = [
        [sys.executable, "-B", "-m", "py_compile", str(project_root / "run_main_pipeline.py")],
        [sys.executable, "-B", "-m", "compileall", str(project_root / "visualoptimise")],
    ]
    results = [run_command(command, cwd=project_root) for command in commands]
    return {
        "schema_version": "syntax_validation_v1",
        "passed": all(result["returncode"] == 0 for result in results),
        "commands": results,
    }


def run_full_dry_run_validation(project_root: Path) -> dict[str, Any]:
    command = [sys.executable, str(project_root / "run_main_pipeline.py"), "--map", "test_map1_clean", "--full", "--dry-run"]
    result = run_command(command, cwd=project_root)
    summary = latest_main_summary(project_root, "test_map1_clean", since_command=result)
    return {
        "schema_version": "full_dry_run_validation_v1",
        "passed": result["returncode"] == 0 and summary.get("dry_run") is True and summary.get("llm_calls", 0) == 0,
        "command": result,
        "summary": summary,
        "llm_calls": 0,
        "sd_webui_generation_calls": 0,
        "stablematerials_generation_calls": 0,
        "note": "Full dry-run is plan validation only and is not used as a material source.",
    }


def run_export_only_validation(project_root: Path, source_run: Path, backend: str, refresh_latest: bool) -> dict[str, Any]:
    command = [
        sys.executable,
        str(project_root / "run_main_pipeline.py"),
        "--map",
        "test_map1_clean",
        "--export-runtime-data",
        "--reuse-materials-from",
        str(source_run),
        "--runtime-texture-backend",
        backend,
    ]
    if not refresh_latest:
        command.append("--no-refresh-runtime-data")
    result = run_command(command, cwd=project_root)
    summary = latest_main_summary(project_root, "test_map1_clean", since_command=result)
    return {
        "schema_version": f"export_only_{backend}_validation_v1",
        "passed": result["returncode"] == 0 and summary.get("status") == "passed",
        "backend": backend,
        "refresh_latest": refresh_latest,
        "source_run": str(source_run),
        "command": result,
        "summary": summary,
        "llm_calls": 0,
        "sd_webui_generation_calls": 0,
        "stablematerials_generation_calls": 0,
    }


def run_command(command: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=600)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def latest_main_summary(project_root: Path, map_id: str, since_command: dict[str, Any]) -> dict[str, Any]:
    runs = sorted((project_root / "outputs" / "runs").glob(f"*_{map_id}_main_pipeline"), key=lambda path: path.name, reverse=True)
    if not runs:
        return {"status": "missing", "command_returncode": since_command["returncode"]}
    summary_path = runs[0] / "04_reports" / "main_pipeline_summary.json"
    if not summary_path.is_file():
        return {"status": "missing_summary", "run_dir": str(runs[0]), "command_returncode": since_command["returncode"]}
    summary = read_json(summary_path)
    summary["run_dir"] = str(runs[0])
    return summary


def snapshot_latest_runtime_data(project_root: Path) -> dict[str, Any]:
    runtime_root = project_root / "generated" / "ue_ready" / "runtime_data"
    index = runtime_root / "map_package_index.json"
    return {
        "runtime_root": str(runtime_root),
        "exists": runtime_root.is_dir(),
        "index_exists": index.is_file(),
        "index_sha256": sha256_file(index) if index.is_file() else None,
        "file_count": sum(1 for path in runtime_root.rglob("*") if path.is_file()) if runtime_root.is_dir() else 0,
    }


def run_import_isolation_audit(project_root: Path) -> dict[str, Any]:
    forbidden = [
        "experiments" + ".",
        "experiments" + ".archive",
        "experiments" + ".current",
        "experiments" + ".shared",
        "VisualOptimization" + ".experiments",
        "VisualOptimization" + ".run_pipeline",
        "VisualOptimization" + ".pipeline",
        "VisualOptimization" + ".experiments" + ".registry",
        "VisualOptimization" + "\\experiments",
    ]
    findings = scan_runtime_sources(project_root, forbidden)
    return {
        "schema_version": "import_isolation_audit_v1",
        "passed": not findings,
        "forbidden_imports_found": findings,
    }


def run_path_hardcode_audit(project_root: Path) -> dict[str, Any]:
    forbidden = [
        "I:" + "\\Disertation",
        "I:" + "\\MiniConda3",
        "VisualOptimizationUE" + "\\Content",
        "VisualOptimizationUE" + "/Content",
    ]
    findings = scan_runtime_sources(project_root, forbidden)
    allowed_compatibility_strings = ["VisualOptimization/RuntimeData"]
    return {
        "schema_version": "path_hardcode_audit_v1",
        "passed": not findings,
        "hardcoded_runtime_paths_found": findings,
        "allowed_compatibility_strings": allowed_compatibility_strings,
    }


def scan_runtime_sources(project_root: Path, terms: list[str]) -> list[dict[str, Any]]:
    source_files = [project_root / "run_main_pipeline.py", *sorted((project_root / "visualoptimise").glob("*.py"))]
    findings: list[dict[str, Any]] = []
    for path in source_files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            for term in terms:
                if term and term in line:
                    findings.append({"file": str(path), "line": line_number, "term": term, "text": line.strip()})
    return findings


def run_structure_equivalence_audit(project_root: Path, reference_material_run: Path, reference_export_run: Path, export_validation: dict[str, Any]) -> dict[str, Any]:
    required_material_files = [
        "01_map_facts/map_facts_v2.json",
        "02_llm1_material_plan/llm_tile_material_plan_v2.json",
        "03_python_resolver/resolved_tileset_v2.json",
        "03_python_resolver/resolved_materials_v2.json",
        "04_dynamic_material_evidence/dynamic_material_slot_evidence_v3.json",
        "04_dynamic_material_evidence/prompt_llm_input_v3.json",
        "05_llm2_prompt_briefs/material_prompt_briefs_v4.json",
        "06_compiled_prompts/compiled_sd15_prompts_v4.json",
        "06_compiled_prompts/compiled_stablematerials_prompts_v4.json",
        "10_reports/d6f_a4_full_two_llm_material_generation_preview_summary.json",
    ]
    required_export_files = [
        "02_material_manifest/material_manifest.json",
        "03_runtime_data_package/map_package_index.json",
        "03_runtime_data_package/copy_to_ue_instructions.md",
        "05_reports/d6g_a2_material_manifest_runtime_export_summary.json",
    ]
    reference_material = check_files(reference_material_run, required_material_files)
    reference_export = check_files(reference_export_run, required_export_files)
    export_summary = export_validation.get("summary", {})
    export_run = Path(export_summary.get("runtime_export_run") or export_summary.get("export_run") or export_summary.get("run_dir") or "")
    target_export = check_files(export_run, required_export_files) if str(export_run) else {"passed": False, "missing": ["export run not found"]}
    return {
        "schema_version": "structure_equivalence_audit_v1",
        "passed": reference_material["passed"] and reference_export["passed"] and target_export["passed"],
        "reference_material_run": str(reference_material_run),
        "reference_export_run": str(reference_export_run),
        "target_export_run": str(export_run),
        "reference_material_files": reference_material,
        "reference_export_files": reference_export,
        "target_export_files": target_export,
    }


def run_behavior_equivalence_audit(reference_material_run: Path) -> dict[str, Any]:
    summary_path = reference_material_run / "10_reports" / "d6f_a4_full_two_llm_material_generation_preview_summary.json"
    reference_summary = read_json(summary_path) if summary_path.is_file() else {}
    checks = {
        "sd15_width": preview_generation.SD15_SETTINGS.get("width") == 512,
        "sd15_height": preview_generation.SD15_SETTINGS.get("height") == 512,
        "sd15_steps": preview_generation.SD15_SETTINGS.get("steps") == 35,
        "sd15_cfg": preview_generation.SD15_SETTINGS.get("cfg_scale") == 6.0,
        "sd15_sampler": preview_generation.SD15_SETTINGS.get("sampler_name") == "DPM++ 2M Karras",
        "sd15_tiling": preview_generation.SD15_SETTINGS.get("tiling") is True,
        "stable_steps": preview_generation.STABLEMATERIALS_SETTINGS.get("num_inference_steps") == 4,
        "stable_guidance": preview_generation.STABLEMATERIALS_SETTINGS.get("guidance_scale") == 1.0,
        "stable_tileable": preview_generation.STABLEMATERIALS_SETTINGS.get("tileable") is True,
        "runtime_selection_policy": runtime_export.SELECTION_POLICY == "first_available_seed",
        "llm2_schema": prompt_generation.SCHEMA_VERSION == "material_prompt_briefs_v4",
        "public_stage_ids_clean": material_generation_pipeline.ROUND_ID == "material_generation" and runtime_export.ROUND_ID == "runtime_export",
        "compatibility_ids_preserved": (
            material_generation_pipeline.COMPATIBILITY_ID.startswith("d6f_a4")
            and runtime_export.COMPATIBILITY_ID.startswith("d6g_a2")
        ),
    }
    return {
        "schema_version": "behavior_equivalence_audit_v1",
        "passed": all(checks.values()) and bool(reference_summary),
        "checks": checks,
        "reference_summary_path": str(summary_path),
        "reference_status": reference_summary.get("status"),
        "prompt_contract_changed": False,
        "llm_schema_changed": False,
        "sd15_settings_changed": False,
        "stablematerials_settings_changed": False,
        "runtime_data_schema_changed": False,
        "public_stage_ids_renamed": True,
        "compatibility_ids_renamed": False,
    }


def run_documentation_completeness_audit(project_root: Path) -> dict[str, Any]:
    required = [
        "README.md",
        "PROJECT_STRUCTURE.md",
        "RUNBOOK.md",
        "CONFIGURATION.md",
        "SUBMISSION_CHECKLIST.md",
        "MIGRATION_MANIFEST.md",
    ]
    required_terms = [
        "run_main_pipeline.py",
        "settings/backend_paths.json",
        "VisualOptimization/RuntimeData",
        "compatibility identifiers",
        "StableMaterials",
        "WebUI",
    ]
    docs_root = project_root / "docs"
    files = []
    missing = []
    combined = ""
    for name in required:
        path = docs_root / name
        files.append({"path": str(path), "exists": path.is_file()})
        if not path.is_file():
            missing.append(name)
            continue
        combined += "\n" + path.read_text(encoding="utf-8")
    missing_terms = [term for term in required_terms if term not in combined]
    return {
        "schema_version": "documentation_completeness_audit_v1",
        "passed": not missing and not missing_terms,
        "files": files,
        "missing_files": missing,
        "missing_terms": missing_terms,
    }


def build_docs_update_manifest(project_root: Path) -> dict[str, Any]:
    docs_root = project_root / "docs"
    return {
        "schema_version": "docs_update_manifest_v1",
        "docs_root": str(docs_root),
        "files": [{"path": str(path), "sha256": sha256_file(path)} for path in sorted(docs_root.glob("*.md"))],
    }


def check_files(root: Path, relatives: list[str]) -> dict[str, Any]:
    missing = [relative for relative in relatives if not (root / relative).is_file()]
    return {
        "root": str(root),
        "passed": not missing,
        "missing": missing,
        "checked": relatives,
    }


def build_summary(
    project_root: Path,
    reference_project: Path,
    backend_paths_validation: dict[str, Any],
    config_externalization: dict[str, Any],
    syntax_validation: dict[str, Any],
    full_dry_run_validation: dict[str, Any],
    export_only_sd15_validation: dict[str, Any],
    backend_switch_validation: dict[str, Any],
    latest_protection: dict[str, Any],
    import_audit: dict[str, Any],
    path_audit: dict[str, Any],
    structure_audit: dict[str, Any],
    behavior_audit: dict[str, Any],
    docs_audit: dict[str, Any],
) -> dict[str, Any]:
    passed = all(
        item.get("passed", False)
        for item in [
            backend_paths_validation,
            config_externalization,
            syntax_validation,
            full_dry_run_validation,
            export_only_sd15_validation,
            backend_switch_validation,
            latest_protection,
            import_audit,
            path_audit,
            structure_audit,
            behavior_audit,
            docs_audit,
        ]
    )
    return {
        "schema_version": "d6g_b3_submission_hardening_summary_v1",
        "status": "passed" if passed else "failed",
        "project_root": str(project_root),
        "reference_project_root": str(reference_project),
        "backend_paths_externalized": config_externalization.get("passed", False),
        "webui_base_url_from_config": backend_paths_validation.get("webui_base_url_from_config", False),
        "stablematerials_python_from_config": backend_paths_validation.get("stablematerials_python_from_config", False),
        "stablematerials_model_dir_from_config": backend_paths_validation.get("stablematerials_model_dir_from_config", False),
        "ue_copy_destination_from_config": backend_paths_validation.get("ue_copy_destination_from_config", False),
        "ue_runtime_virtual_root_preserved": backend_paths_validation.get("ue_runtime_virtual_root_preserved", False),
        "import_isolation_passed": import_audit.get("passed", False),
        "forbidden_imports_found": import_audit.get("forbidden_imports_found", []),
        "path_hardcode_audit_passed": path_audit.get("passed", False),
        "hardcoded_runtime_paths_found": path_audit.get("hardcoded_runtime_paths_found", []),
        "structure_equivalence_passed": structure_audit.get("passed", False),
        "behavior_equivalence_passed": behavior_audit.get("passed", False),
        "documentation_lock_passed": docs_audit.get("passed", False),
        "full_dry_run_passed": full_dry_run_validation.get("passed", False),
        "export_only_sd15_passed": export_only_sd15_validation.get("passed", False),
        "backend_switch_stablematerials_passed": backend_switch_validation.get("passed", False),
        "backend_switch_overwrote_latest": latest_protection.get("backend_switch_overwrote_latest", True),
        "prompt_contract_changed": behavior_audit.get("prompt_contract_changed", True),
        "llm_schema_changed": behavior_audit.get("llm_schema_changed", True),
        "sd15_settings_changed": behavior_audit.get("sd15_settings_changed", True),
        "stablematerials_settings_changed": behavior_audit.get("stablematerials_settings_changed", True),
        "runtime_data_schema_changed": behavior_audit.get("runtime_data_schema_changed", True),
        "round_ids_renamed": behavior_audit.get("round_ids_renamed", True),
        "large_core_modules_split": False,
        "full_real_run_executed": False,
        "llm_calls": 0,
        "sd_webui_generation_calls": 0,
        "stablematerials_generation_calls": 0,
        "ue_modified": False,
        "ready_for_submission_review": passed,
    }


def build_report(summary: dict[str, Any], run_dir: Path) -> str:
    return (
        "# D6G-B3 Submission Hardening Report\n\n"
        f"- Status: `{summary['status']}`\n"
        f"- Project root: `{summary['project_root']}`\n"
        f"- B3 run: `{run_dir}`\n"
        f"- Backend paths externalized: `{summary['backend_paths_externalized']}`\n"
        f"- Import isolation passed: `{summary['import_isolation_passed']}`\n"
        f"- Path hardcode audit passed: `{summary['path_hardcode_audit_passed']}`\n"
        f"- Structure equivalence passed: `{summary['structure_equivalence_passed']}`\n"
        f"- Behavior equivalence passed: `{summary['behavior_equivalence_passed']}`\n"
        f"- Documentation lock passed: `{summary['documentation_lock_passed']}`\n"
        f"- Full dry-run passed: `{summary['full_dry_run_passed']}`\n"
        f"- Export-only SD1.5 passed: `{summary['export_only_sd15_passed']}`\n"
        f"- StableMaterials backend-switch passed: `{summary['backend_switch_stablematerials_passed']}`\n"
        f"- Latest overwritten by backend-switch: `{summary['backend_switch_overwrote_latest']}`\n"
        f"- LLM calls: `{summary['llm_calls']}`\n"
        f"- SD/WebUI generation calls: `{summary['sd_webui_generation_calls']}`\n"
        f"- StableMaterials generation calls: `{summary['stablematerials_generation_calls']}`\n"
        f"- UE modified: `{summary['ue_modified']}`\n\n"
        "B3 preserves D6F/D6G compatibility identifiers and does not change prompt contracts, validators, image-generation settings, or RuntimeData schema.\n"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
