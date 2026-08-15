"""Shared D6F preview generation helpers extracted from D6F-A3-Fix4.

This module reuses the existing material_runtime backend constants and preserves
Fix4 Plan-A preview behavior without importing archived round modules.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from visualoptimise.generation_backend import (
    REQUIRED_CHECKPOINT,
    STABLEMATERIALS_GENERATION_SETTINGS,
    missing_stablematerials_files,
)
from visualoptimise.artifacts import read_json, write_json, write_text
from visualoptimise.backend_config import load_backend_paths, stablematerials_generation_settings, stablematerials_worker_command
from visualoptimise.image_tools import create_tiled_preview, save_image
from visualoptimise.webui_client import WebUIClient

SD15_SETTINGS = {
    "endpoint": "/sdapi/v1/txt2img",
    "checkpoint": REQUIRED_CHECKPOINT,
    "width": 512,
    "height": 512,
    "steps": 35,
    "cfg_scale": 6.0,
    "sampler_name": "DPM++ 2M Karras",
    "tiling": True,
    "batch_size": 1,
    "n_iter": 1,
    "send_images": True,
    "save_images": False,
}

STABLEMATERIALS_SETTINGS = {**STABLEMATERIALS_GENERATION_SETTINGS, "seed": None}

DISPLAY_LABELS = {
    "mat_stone_wall_material": "stone_wall",
    "mat_stone_floor_material": "stone_floor",
    "mat_wood_planks_material": "wood_planks",
    "mat_wooden_door_material": "wooden_door",
    "mat_grass_ground_material": "grass_ground",
    "mat_shallow_water_material": "water",
    "mat_water_material": "water",
}

POSITIVE_AUDIT_CONTEXT_TERMS = {
    "ascii marker",
    "symbol marker",
    "small dot marker",
    "small dot markers",
    "dot marker",
    "dot markers",
    "marker",
    "markers",
    "walkable",
    "interactive",
    "bridge",
    "walkway",
    "gate",
    "complete door",
    "door frame",
    "arch",
    "arched",
    "iron strap",
    "hinges",
    "handle",
    "knob",
    "dungeon",
    "ruin boundary",
    "ruin scene",
    "complete scene",
    "object prop",
    "perspective view",
    "forest scene",
    "tree trunk",
    "branch",
    "river bank",
    "ocean",
    "shore",
    "boundary",
    "vertical structure",
}

MATERIAL_ALLOWED_CONTEXT_WORDS = {
    "wall",
    "floor",
    "ground",
    "water",
    "wood",
    "wooden",
    "stone",
    "moss",
    "mossy",
    "wet",
    "damp",
    "shallow",
    "blue",
    "green",
    "surface",
    "texture",
    "liquid",
}

def audit_positive_side_pollution(prompt_briefs: dict[str, Any], dynamic_evidence: dict[str, Any]) -> dict[str, Any]:
    evidence = {slot["material_slot_id"]: slot for slot in dynamic_evidence.get("material_slots", [])}
    findings: list[dict[str, Any]] = []
    for item in prompt_briefs.get("backend_prompt_briefs", []):
        slot_id = item["material_slot_id"]
        slot_evidence = evidence.get(slot_id, {})
        candidate_terms = set(POSITIVE_AUDIT_CONTEXT_TERMS)
        for clue in slot_evidence.get("context_clues_for_prompt_llm", []):
            term = normalized_phrase(clue.get("term", ""))
            if term and not is_material_allowed_context_term(term):
                candidate_terms.add(term)
        fields = positive_fields(item)
        for field_name, values in fields.items():
            for value in values:
                text = normalized_phrase(value)
                for term in sorted(candidate_terms):
                    if term and phrase_in_text(term, text):
                        findings.append(
                            {
                                "material_slot_id": slot_id,
                                "display_label": DISPLAY_LABELS.get(slot_id),
                                "field": field_name,
                                "term": term,
                                "value": value,
                                "severity": "warning",
                                "action": "reported_only_no_prompt_edit",
                            }
                        )
    return {
        "schema_version": "positive_side_symbolic_context_audit_v1",
        "passed": not findings,
        "findings_count": len(findings),
        "findings": findings,
        "prompt_editing_applied": False,
        "note": "This audit intentionally reports positive-side symbolic/context leakage without modifying finalized D6F-A3-Fix1 prompts.",
    }

def positive_fields(item: dict[str, Any]) -> dict[str, list[str]]:
    stable = item.get("stablematerials", {})
    return {
        "prompt_ready_surface_terms": [str(value) for value in item.get("prompt_ready_surface_terms", [])],
        "sd15.positive_tags": [str(value) for value in item.get("sd15", {}).get("positive_tags", [])],
        "stablematerials.positive_phrase": [str(stable.get("positive_phrase", ""))],
        "stablematerials.surface_structure": [str(stable.get("surface_structure", ""))],
    }

def normalized_phrase(value: Any) -> str:
    return " ".join(str(value).lower().replace("-", " ").split())

def phrase_in_text(phrase: str, text: str) -> bool:
    phrase = normalized_phrase(phrase)
    text = normalized_phrase(text)
    if not phrase or not text:
        return False
    return f" {phrase} " in f" {text} "

def is_material_allowed_context_term(term: str) -> bool:
    words = set(normalized_phrase(term).split())
    return bool(words) and words <= MATERIAL_ALLOWED_CONTEXT_WORDS

def preflight_backends(pipeline: Any) -> dict[str, Any]:
    backend_paths = load_backend_paths(pipeline.root)
    client = WebUIClient(pipeline._runtime_settings(False))
    sd_errors: list[str] = []
    sd_warnings: list[str] = []
    webui: dict[str, Any] = {"base_url": client.base_url}
    try:
        models = client.list_models()
        active = client.get_active_checkpoint()
        webui.update({"model_count": len(models), "active_checkpoint": active, "required_checkpoint": REQUIRED_CHECKPOINT})
        if REQUIRED_CHECKPOINT not in models:
            sd_errors.append(f"Required checkpoint not installed in WebUI model list: {REQUIRED_CHECKPOINT}")
        if active != REQUIRED_CHECKPOINT:
            sd_warnings.append(f"Active checkpoint is {active!r}; generation will switch to {REQUIRED_CHECKPOINT!r} and restore afterward.")
    except Exception as exc:
        sd_errors.append(f"A1111 API unavailable: {exc}")
    sm_errors: list[str] = []
    if backend_paths.stablematerials_python is None or not backend_paths.stablematerials_python.is_file():
        sm_errors.append(f"StableMaterials Python not found: {backend_paths.stablematerials_python}")
    if backend_paths.stablematerials_model_dir is None or not backend_paths.stablematerials_model_dir.is_dir():
        sm_errors.append(f"StableMaterials model directory not found: {backend_paths.stablematerials_model_dir}")
    if backend_paths.stablematerials_worker_kind == "script" and not Path(backend_paths.stablematerials_worker).is_file():
        sm_errors.append(f"StableMaterials worker not found: {backend_paths.stablematerials_worker}")
    missing = missing_stablematerials_files(backend_paths.stablematerials_model_dir)
    if missing:
        sm_errors.append("StableMaterials local files missing: " + json.dumps(missing[:20], ensure_ascii=False))
    return {
        "schema_version": "d6f_a3_fix4_backend_preflight_v1",
        "sd15": {"passed": not sd_errors, "errors": sd_errors, "warnings": sd_warnings, "webui": webui},
        "stablematerials": {
            "passed": not sm_errors,
            "errors": sm_errors,
            "warnings": [],
            "python": str(backend_paths.stablematerials_python),
            "model_dir": str(backend_paths.stablematerials_model_dir),
            "worker": backend_paths.stablematerials_worker,
        },
    }

def run_sd15_generation(
    pipeline: Any,
    paths: dict[str, Path],
    requests_table: list[dict[str, Any]],
    preflight: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    outputs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    timings: list[dict[str, Any]] = []
    client = WebUIClient(pipeline._runtime_settings(False))
    original_checkpoint = preflight["sd15"]["webui"].get("active_checkpoint")
    checkpoint_changed = False
    try:
        if original_checkpoint != REQUIRED_CHECKPOINT:
            client.set_checkpoint(REQUIRED_CHECKPOINT)
            checkpoint_changed = True
        for request in requests_table:
            slot_id = request["material_slot_id"]
            seed = request["seed"]
            plan_id = request.get("plan_id", "plan_unknown")
            slot_dir = paths["sd15"] / plan_id / slot_id
            slot_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "prompt": request["positive_prompt"],
                "negative_prompt": request["negative_prompt"],
                "width": SD15_SETTINGS["width"],
                "height": SD15_SETTINGS["height"],
                "steps": SD15_SETTINGS["steps"],
                "cfg_scale": SD15_SETTINGS["cfg_scale"],
                "sampler_name": SD15_SETTINGS["sampler_name"],
                "seed": seed,
                "tiling": SD15_SETTINGS["tiling"],
                "batch_size": SD15_SETTINGS["batch_size"],
                "n_iter": SD15_SETTINGS["n_iter"],
                "send_images": SD15_SETTINGS["send_images"],
                "save_images": SD15_SETTINGS["save_images"],
            }
            payload_path = paths["compiled"] / "generation_payloads" / "sd15" / plan_id / slot_id / f"seed_{seed}_payload.json"
            response_path = paths["compiled"] / "generation_payloads" / "sd15" / plan_id / slot_id / f"seed_{seed}_response_summary.json"
            output_path = slot_dir / f"seed_{seed}.png"
            tiled_path = paths["previews"] / "sd15" / plan_id / slot_id / f"seed_{seed}_tiled_preview.png"
            started = time.perf_counter()
            try:
                payload_summary = client.save_sanitized_payload("/sdapi/v1/txt2img", payload, payload_path)
                image = client.post_txt2img_diagnostic(payload, response_path, output_path.name)
                save_image(image, output_path)
                save_image(create_tiled_preview(image), tiled_path)
                elapsed = round(time.perf_counter() - started, 3)
                record = {
                    **request,
                    "output": str(output_path),
                    "tiled_preview": str(tiled_path),
                    "dimensions": [image.width, image.height],
                    "payload": str(payload_path),
                    "response_summary": str(response_path),
                    "payload_summary": payload_summary.get("diagnostic_summary", {}),
                    "image_metrics": image_metrics(image),
                    "generation_seconds": elapsed,
                    "checkpoint_used": REQUIRED_CHECKPOINT,
                }
                write_json(slot_dir / f"seed_{seed}_metadata.json", record)
                outputs.append(record)
                timings.append({"backend": "sd15_a1111_txt2img", "plan_id": plan_id, "request_id": request["request_id"], "seconds": elapsed})
            except Exception as exc:
                elapsed = round(time.perf_counter() - started, 3)
                failure = {"backend": "sd15_a1111_txt2img", "plan_id": plan_id, "request_id": request["request_id"], "material_slot_id": slot_id, "seed": seed, "error": str(exc), "seconds": elapsed}
                failures.append(failure)
                timings.append({"backend": "sd15_a1111_txt2img", "plan_id": plan_id, "request_id": request["request_id"], "seconds": elapsed, "failed": True})
    finally:
        if checkpoint_changed and original_checkpoint:
            try:
                client.set_checkpoint(original_checkpoint)
            except Exception as exc:
                failures.append({"backend": "sd15_a1111_txt2img", "request_id": "restore_checkpoint", "error": str(exc), "original_checkpoint": original_checkpoint})
    return outputs, failures, timings

def run_stablematerials_generation(pipeline: Any, paths: dict[str, Path], requests_table: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    output_root = paths["stablematerials"]
    backend_paths = load_backend_paths(pipeline.root)
    if backend_paths.stablematerials_model_dir is None:
        raise FileNotFoundError("StableMaterials model directory is not configured in settings/backend_paths.json.")
    stable_settings = stablematerials_generation_settings(STABLEMATERIALS_GENERATION_SETTINGS, backend_paths)
    request = {
        "schema_version": "d6f_a3_fix4_stablematerials_preview_request_v1",
        "generation": {
            **stable_settings,
        },
        "materials": [
            {
                "material_id": row["request_id"],
                "material_slot_id": row["material_slot_id"],
                "seed": row["seed"],
                "prompt": row["positive_prompt"],
                "relative_output_dir": row["relative_output_dir"],
            }
            for row in requests_table
        ],
    }
    request_path = paths["compiled"] / "stablematerials_worker_request.json"
    write_json(request_path, request)
    env = os.environ.copy()
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    command = stablematerials_worker_command(backend_paths) + [
        "--request",
        str(request_path),
        "--output",
        str(output_root),
        "--model-dir",
        str(backend_paths.stablematerials_model_dir),
    ]
    started = time.perf_counter()
    result = subprocess.run(command, cwd=str(backend_paths.stablematerials_working_dir), env=env, capture_output=True, text=True, timeout=2400)
    elapsed = round(time.perf_counter() - started, 3)
    write_text(paths["compiled"] / "stablematerials_worker_stdout.txt", result.stdout)
    write_text(paths["compiled"] / "stablematerials_worker_stderr.txt", result.stderr)
    report_path = output_root / "stablematerials_worker_report.json"
    failures: list[dict[str, Any]] = []
    timings: list[dict[str, Any]] = [{"backend": "stablematerials_lcm", "request_id": "stablematerials_worker_all", "seconds": elapsed, "returncode": result.returncode}]
    if result.returncode != 0:
        failures.append({"backend": "stablematerials_lcm", "request_id": "stablematerials_worker_all", "error": result.stderr[-2000:], "seconds": elapsed})
        return [], failures, timings
    report = read_json(report_path)
    if report.get("status") != "passed":
        failures.append({"backend": "stablematerials_lcm", "request_id": "stablematerials_worker_all", "error": json.dumps(report.get("errors", []), ensure_ascii=False), "seconds": elapsed})
        return [], failures, timings
    outputs: list[dict[str, Any]] = []
    by_request = {row["request_id"]: row for row in requests_table}
    for material_id, record in report.get("materials", {}).items():
        request_row = by_request.get(material_id, {})
        maps = record.get("maps", {})
        basecolor = Path(maps["basecolor"]["path"])
        tiled = paths["previews"] / "stablematerials" / request_row["material_slot_id"] / f"seed_{request_row['seed']}_tiled_preview.png"
        with Image.open(basecolor) as image:
            rgb = image.convert("RGB")
            save_image(create_tiled_preview(rgb), tiled)
            metrics = image_metrics(rgb)
        output = {
            **request_row,
            "output_dir": str(basecolor.parent),
            "basecolor": str(basecolor),
            "normal": maps["normal"]["path"],
            "height": maps["height"]["path"],
            "roughness": maps["roughness"]["path"],
            "metallic": maps["metallic"]["path"],
            "tiled_preview": str(tiled),
            "dimensions": maps["basecolor"]["dimensions"],
            "image_metrics": metrics,
            "generation_seconds": record.get("generation_seconds"),
        }
        write_json(basecolor.parent / "metadata.json", output)
        outputs.append(output)
    return outputs, failures, timings

def image_metrics(image: Image.Image) -> dict[str, Any]:
    rgb = image.convert("RGB")
    stat = ImageStat.Stat(rgb)
    hsv = rgb.convert("HSV")
    hsv_stat = ImageStat.Stat(hsv)
    left = crop_edge(rgb, "left")
    right = crop_edge(rgb, "right")
    top = crop_edge(rgb, "top")
    bottom = crop_edge(rgb, "bottom")
    seam_lr = mean_abs_difference(left, right.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    seam_tb = mean_abs_difference(top, bottom.transpose(Image.Transpose.FLIP_TOP_BOTTOM))
    return {
        "dimensions": [rgb.width, rgb.height],
        "brightness_mean": sum(stat.mean) / 3.0,
        "brightness_std_mean": sum(stat.stddev) / 3.0,
        "saturation_mean": hsv_stat.mean[1],
        "seam_left_right": seam_lr,
        "seam_top_bottom": seam_tb,
        "seam_jump_mean": (seam_lr + seam_tb) / 2.0,
    }

def crop_edge(image: Image.Image, edge: str, width: int = 8) -> Image.Image:
    if edge == "left":
        return image.crop((0, 0, width, image.height))
    if edge == "right":
        return image.crop((image.width - width, 0, image.width, image.height))
    if edge == "top":
        return image.crop((0, 0, image.width, width))
    if edge == "bottom":
        return image.crop((0, image.height - width, image.width, image.height))
    raise ValueError(edge)

def mean_abs_difference(a: Image.Image, b: Image.Image) -> float:
    a_rgb = a.convert("RGB")
    b_rgb = b.convert("RGB").resize(a_rgb.size)
    total = 0.0
    count = 0
    for p1, p2 in zip(a_rgb.getdata(), b_rgb.getdata()):
        total += sum(abs(int(x) - int(y)) for x, y in zip(p1, p2)) / 3.0
        count += 1
    return total / max(1, count)

