"""Summary and report helpers for the self-contained main pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SUMMARY_SCHEMA = "visualoptimise_main_pipeline_summary_v1"


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def material_generation_summary(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return read_json_if_exists(run_dir / "10_reports" / "d6f_a4_full_two_llm_material_generation_preview_summary.json")


def runtime_export_summary(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {}
    return read_json_if_exists(run_dir / "05_reports" / "d6g_a2_material_manifest_runtime_export_summary.json")


def build_summary(
    *,
    status: str,
    command: str,
    map_id: str,
    mode: str,
    dry_run: bool,
    runtime_texture_backend: str,
    material_run: Path | None,
    export_run: Path | None,
    material_summary: dict[str, Any],
    export_summary: dict[str, Any],
    stage_plan: dict[str, Any],
    copy_to_ue_path: Path,
) -> dict[str, Any]:
    source_llm_calls = int(bool(material_summary.get("llm1_called"))) + int(bool(material_summary.get("llm2_called")))
    source_sd_calls = int(material_summary.get("sd15_images_generated", 0) or 0)
    source_stablematerials_calls = int(material_summary.get("stablematerials_sets_generated", 0) or 0)
    if mode == "export-only":
        llm_calls = 0
        sd_calls = 0
        stablematerials_calls = 0
        webui_probed = False
        stablematerials_probed = False
    else:
        llm_calls = source_llm_calls
        sd_calls = source_sd_calls
        stablematerials_calls = source_stablematerials_calls
        webui_probed = bool(material_summary.get("preflight", {}).get("sd15")) if material_summary else False
        stablematerials_probed = bool(material_summary.get("preflight", {}).get("stablematerials")) if material_summary else False
    return {
        "schema_version": SUMMARY_SCHEMA,
        "status": status,
        "command": command,
        "map_id": map_id,
        "mode": mode,
        "material_generation_run": str(material_run) if material_run else None,
        "runtime_export_run": str(export_run) if export_run else None,
        "runtime_texture_backend": runtime_texture_backend,
        "runtime_data_path": export_summary.get("generated_runtime_data_path"),
        "runtime_data_snapshot_path": export_summary.get("runtime_data_snapshot_path"),
        "generated_runtime_data_refreshed": bool(export_summary.get("generated_runtime_data_refreshed", False)),
        "llm_calls": llm_calls,
        "sd_webui_generation_calls": sd_calls,
        "stablematerials_generation_calls": stablematerials_calls,
        "dry_run": dry_run,
        "webui_preflight_allowed": True,
        "stablematerials_preflight_allowed": True,
        "webui_probed": webui_probed,
        "stablematerials_probed": stablematerials_probed,
        "source_material_generation_counts": {
            "llm_calls": source_llm_calls,
            "sd_webui_generation_calls": source_sd_calls,
            "stablematerials_generation_calls": source_stablematerials_calls,
            "note": "For export-only mode these are historical counts from the reused source run, not calls made by this invocation.",
        },
        "runtime_data_exported": bool(export_summary.get("runtime_data_package_created", False)),
        "ue_modified": False,
        "ready_for_ue_copy": (not dry_run and bool(export_summary.get("validation_passed", False))),
        "copy_to_ue_path": str(copy_to_ue_path),
        "stage_plan": stage_plan,
    }


def build_report(summary: dict[str, Any]) -> str:
    return (
        "# VisualOptimise Main Pipeline Report\n\n"
        f"- Status: `{summary['status']}`\n"
        f"- Mode: `{summary['mode']}`\n"
        f"- Map: `{summary['map_id']}`\n"
        f"- Dry-run: `{summary['dry_run']}`\n"
        f"- Material generation run: `{summary['material_generation_run']}`\n"
        f"- Runtime export run: `{summary['runtime_export_run']}`\n"
        f"- Runtime texture backend: `{summary['runtime_texture_backend']}`\n"
        f"- RuntimeData path: `{summary['runtime_data_path']}`\n"
        f"- RuntimeData snapshot: `{summary['runtime_data_snapshot_path']}`\n"
        f"- RuntimeData latest refreshed: `{summary['generated_runtime_data_refreshed']}`\n"
        f"- LLM calls: `{summary['llm_calls']}`\n"
        f"- SD/WebUI generation calls: `{summary['sd_webui_generation_calls']}`\n"
        f"- StableMaterials generation calls: `{summary['stablematerials_generation_calls']}`\n"
        f"- WebUI probed: `{summary['webui_probed']}`\n"
        f"- StableMaterials probed: `{summary['stablematerials_probed']}`\n"
        f"- UE modified: `{summary['ue_modified']}`\n"
        f"- Ready for UE copy: `{summary['ready_for_ue_copy']}`\n"
        f"- UE copy destination: `{summary['copy_to_ue_path']}`\n\n"
        "## Stage Plan\n\n"
        "```json\n"
        + json.dumps(summary["stage_plan"], ensure_ascii=False, indent=2)
        + "\n```\n"
    )
