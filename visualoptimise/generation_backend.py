"""Material generation backends for authoring-to-runtime rounds."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image

from visualoptimise.artifacts import read_json, write_json, write_text
from visualoptimise.image_tools import create_tiled_preview, save_image
from visualoptimise.webui_client import WebUIClient


TEXTURE_TYPES = ("basecolor", "normal", "roughness", "height", "metallic")
REQUIRED_CHECKPOINT = "v1-5-pruned-emaonly.safetensors [6ce0161689]"
STABLEMATERIALS_PYTHON = Path(r"I:\MiniConda3\envs\stablematerials\python.exe")
STABLEMATERIALS_MODEL_DIR = Path(r"I:\Disertation\StableMaterials")
STABLEMATERIALS_WORKER = Path(__file__).with_name("stablematerials_worker.py")

SD15_GENERATION_SETTINGS = {
    "endpoint": "/sdapi/v1/txt2img",
    "checkpoint": REQUIRED_CHECKPOINT,
    "width": 512,
    "height": 512,
    "steps": 35,
    "cfg_scale": 6.0,
    "sampler_name": "DPM++ 2M Karras",
    "seed": 2060,
    "tiling": True,
    "batch_size": 1,
    "n_iter": 1,
    "send_images": True,
    "save_images": False,
    "negative_prompt": "",
}

STABLEMATERIALS_GENERATION_SETTINGS = {
    "width": 512,
    "height": 512,
    "num_inference_steps": 4,
    "guidance_scale": 1.0,
    "seed": 2060,
    "tileable": True,
    "negative_prompt": None,
    "custom_pipeline": str(STABLEMATERIALS_MODEL_DIR),
    "local_files_only": True,
    "trust_remote_code": False,
    "unet_subfolder": "unet_lcm",
    "scheduler": "LCMScheduler",
    "dtype": "float16",
}

STABLEMATERIALS_REQUIRED_FILES = [
    "model_index.json",
    "pipeline.py",
    "scheduler/scheduler_config.json",
    "tokenizer/tokenizer_config.json",
    "tokenizer/special_tokens_map.json",
    "tokenizer/merges.txt",
    "tokenizer/vocab.json",
    "tokenizer/tokenizer.json",
    "processor/preprocessor_config.json",
    "processor/tokenizer_config.json",
    "processor/special_tokens_map.json",
    "processor/merges.txt",
    "processor/vocab.json",
    "processor/tokenizer.json",
    "text_encoder/config.json",
    "text_encoder/model.safetensors",
    "vision_encoder/config.json",
    "vision_encoder/model.safetensors",
    "vae/config.json",
    "vae/diffusion_pytorch_model.safetensors",
    "unet_lcm/config.json",
    "unet_lcm/diffusion_pytorch_model.safetensors",
]


def preflight_generation_backends(pipeline: Any) -> dict[str, Any]:
    """Check the two local generation backends without changing models."""
    errors: list[str] = []
    warnings: list[str] = []
    webui_info: dict[str, Any] = {}
    try:
        client = WebUIClient(pipeline._runtime_settings(False))
        webui_info["base_url"] = client.base_url
        webui_info["model_count"] = len(client.list_models())
        webui_info["active_checkpoint"] = client.get_active_checkpoint()
        if webui_info["active_checkpoint"] != REQUIRED_CHECKPOINT:
            errors.append(
                f"A1111 active checkpoint must be {REQUIRED_CHECKPOINT!r}; "
                f"got {webui_info['active_checkpoint']!r}."
            )
    except Exception as exc:
        errors.append(f"A1111 API unavailable: {exc}")
    if not STABLEMATERIALS_PYTHON.is_file():
        errors.append(f"StableMaterials Python not found: {STABLEMATERIALS_PYTHON}")
    if not STABLEMATERIALS_WORKER.is_file():
        errors.append(f"StableMaterials worker not found: {STABLEMATERIALS_WORKER}")
    missing = missing_stablematerials_files()
    if missing:
        errors.append("StableMaterials local files missing: " + json.dumps(missing[:20], indent=2))
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "sd15": {
            "backend": "sd15_a1111_txt2img",
            "required_checkpoint": REQUIRED_CHECKPOINT,
            "settings": SD15_GENERATION_SETTINGS,
            "webui": webui_info,
        },
        "stablematerials_lcm": {
            "backend": "stablematerials_lcm",
            "python": str(STABLEMATERIALS_PYTHON),
            "model_dir": str(STABLEMATERIALS_MODEL_DIR),
            "worker": str(STABLEMATERIALS_WORKER),
            "settings": STABLEMATERIALS_GENERATION_SETTINGS,
        },
    }


def generate_sd15_materials(client: WebUIClient, output_root: Path, compiled: dict[str, Any]) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    for prompt in compiled.get("prompts", []):
        slot_id = prompt["material_slot_id"]
        slot_dir = output_root / slot_id
        slot_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "prompt": prompt["positive_prompt"],
            "negative_prompt": "",
            "width": SD15_GENERATION_SETTINGS["width"],
            "height": SD15_GENERATION_SETTINGS["height"],
            "steps": SD15_GENERATION_SETTINGS["steps"],
            "cfg_scale": SD15_GENERATION_SETTINGS["cfg_scale"],
            "sampler_name": SD15_GENERATION_SETTINGS["sampler_name"],
            "seed": SD15_GENERATION_SETTINGS["seed"],
            "tiling": SD15_GENERATION_SETTINGS["tiling"],
            "batch_size": SD15_GENERATION_SETTINGS["batch_size"],
            "n_iter": SD15_GENERATION_SETTINGS["n_iter"],
            "send_images": SD15_GENERATION_SETTINGS["send_images"],
            "save_images": SD15_GENERATION_SETTINGS["save_images"],
        }
        payload_summary = client.save_sanitized_payload("/sdapi/v1/txt2img", payload, slot_dir / "payload.json")
        started = time.perf_counter()
        image = client.post_txt2img_diagnostic(payload, slot_dir / "response_summary.json", "basecolor.png")
        elapsed = round(time.perf_counter() - started, 3)
        basecolor = slot_dir / "basecolor.png"
        tiled = slot_dir / "tiled_preview.png"
        save_image(image, basecolor)
        save_image(create_tiled_preview(image), tiled)
        response_summary = read_json(slot_dir / "response_summary.json")
        record = {
            "material_slot_id": slot_id,
            "backend": "sd15_a1111_txt2img",
            "positive_prompt": prompt["positive_prompt"],
            "negative_prompt": "",
            "basecolor": str(basecolor),
            "tiled_preview": str(tiled),
            "dimensions": [image.width, image.height],
            "generation_seconds": elapsed,
            "payload": str(slot_dir / "payload.json"),
            "response_summary": str(slot_dir / "response_summary.json"),
            "http_status": response_summary.get("http_status"),
            "payload_summary": payload_summary.get("diagnostic_summary", {}),
        }
        write_json(slot_dir / "metadata.json", record)
        outputs[slot_id] = record
    return outputs


def generate_stablematerials_lcm(output_root: Path, request_dir: Path, compiled: dict[str, Any]) -> dict[str, Any]:
    request = {
        "generation": STABLEMATERIALS_GENERATION_SETTINGS,
        "materials": [
            {"material_id": item["material_slot_id"], "prompt": item["positive_prompt"]}
            for item in compiled.get("prompts", [])
        ],
    }
    request_path = request_dir / "stablematerials_request.json"
    write_json(request_path, request)
    output_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    command = [
        str(STABLEMATERIALS_PYTHON),
        str(STABLEMATERIALS_WORKER),
        "--request",
        str(request_path),
        "--output",
        str(output_root),
    ]
    result = subprocess.run(command, cwd=str(Path(r"I:\Disertation")), env=env, capture_output=True, text=True, timeout=1800)
    write_text(request_dir / "stablematerials_worker_stdout.txt", result.stdout)
    write_text(request_dir / "stablematerials_worker_stderr.txt", result.stderr)
    report_path = output_root / "stablematerials_worker_report.json"
    if result.returncode != 0:
        error = f"StableMaterials worker failed with exit code {result.returncode}. See {report_path}."
        if result.stderr:
            error += f" stderr: {result.stderr[-1200:]}"
        raise RuntimeError(error)
    report = read_json(report_path)
    if report.get("status") != "passed":
        raise RuntimeError(f"StableMaterials worker report status is {report.get('status')}; see {report_path}.")
    outputs: dict[str, Any] = {}
    for slot_id, record in report.get("materials", {}).items():
        basecolor = Path(record["maps"]["basecolor"]["path"])
        tiled = basecolor.with_name("tiled_preview.png")
        with Image.open(basecolor) as image:
            save_image(create_tiled_preview(image.convert("RGB")), tiled)
        outputs[slot_id] = {
            "material_slot_id": slot_id,
            "backend": "stablematerials_lcm",
            "positive_prompt": record["prompt"],
            "negative_prompt": None,
            "basecolor": str(basecolor),
            "normal": record["maps"]["normal"]["path"],
            "height": record["maps"]["height"]["path"],
            "roughness": record["maps"]["roughness"]["path"],
            "metallic": record["maps"]["metallic"]["path"],
            "tiled_preview": str(tiled),
            "dimensions": record["maps"]["basecolor"]["dimensions"],
            "generation_seconds": record["generation_seconds"],
        }
    return outputs


def build_generated_material_dispatch(
    material_slots: dict[str, Any],
    generated_outputs: dict[str, dict[str, Any]],
    target_textures_dir: Path,
    material_mode: str,
    dry_run: bool,
    runtime_source_backend: str = "stablematerials_lcm",
) -> dict[str, Any]:
    records = []
    errors: list[str] = []
    warnings: list[str] = []
    sd_outputs = generated_outputs.get("sd15", {})
    runtime_outputs = generated_outputs.get(runtime_source_backend, {})
    for slot in material_slots.get("unique_material_slots", []):
        slot_id = slot["material_slot_id"]
        target_slot_dir = target_textures_dir / slot_id
        if not dry_run:
            target_slot_dir.mkdir(parents=True, exist_ok=True)
        textures: dict[str, str] = {}
        source_textures: dict[str, str] = {}
        available_maps: list[str] = []
        missing_maps: list[str] = []
        copy_records = []
        if dry_run:
            for texture_type in TEXTURE_TYPES:
                textures[texture_type] = f"textures/{slot_id}/{texture_type}.png"
            available_maps = list(TEXTURE_TYPES)
        else:
            source_record = runtime_outputs.get(slot_id)
            if not source_record:
                errors.append(f"{slot_id}: missing {runtime_source_backend} generated output.")
                missing_maps.extend(TEXTURE_TYPES)
            else:
                for texture_type in TEXTURE_TYPES:
                    source_value = source_record.get(texture_type)
                    relative = f"textures/{slot_id}/{texture_type}.png"
                    if not source_value:
                        missing_maps.append(texture_type)
                        errors.append(f"{slot_id}: missing generated {texture_type} from {runtime_source_backend}.")
                        continue
                    source = Path(source_value)
                    if not source.is_file():
                        missing_maps.append(texture_type)
                        errors.append(f"{slot_id}: generated {texture_type} file does not exist: {source}")
                        continue
                    target = target_slot_dir / f"{texture_type}.png"
                    shutil.copyfile(source, target)
                    textures[texture_type] = relative
                    source_textures[texture_type] = str(source)
                    available_maps.append(texture_type)
                    copy_records.append(
                        {
                            "texture_type": texture_type,
                            "source": str(source),
                            "target": str(target),
                            "copied": True,
                        }
                    )
        records.append(
            {
                "material_slot_id": slot_id,
                "backend_mode": material_mode,
                "source_backend_hint": runtime_source_backend,
                "reuse_decision": "generated_new_textures" if not dry_run else "dry_run_generation_planned",
                "textures": textures,
                "source_textures": source_textures,
                "available_maps": sorted(available_maps),
                "missing_maps": missing_maps,
                "records": copy_records,
                "generated_backends": sorted(generated_outputs.keys()) if not dry_run else ["sd15_a1111_txt2img", "stablematerials_lcm"],
                "sd15_basecolor": sd_outputs.get(slot_id, {}).get("basecolor"),
            }
        )
    return {
        "schema_version": "material_backend_dispatch_report_v1",
        "material_mode": material_mode,
        "dry_run": dry_run,
        "runtime_source_backend": runtime_source_backend,
        "records": records,
        "errors": errors,
        "warnings": warnings,
        "llm_calls": 0,
        "sd_calls": 0 if dry_run else len(sd_outputs),
        "stablematerials_calls": 0 if dry_run else len(generated_outputs.get("stablematerials_lcm", {})),
        "new_image_generation": not dry_run,
    }


def missing_stablematerials_files() -> list[str]:
    missing: list[str] = []
    if not STABLEMATERIALS_MODEL_DIR.is_dir():
        missing.append(str(STABLEMATERIALS_MODEL_DIR))
        return missing
    for relative in STABLEMATERIALS_REQUIRED_FILES:
        path = STABLEMATERIALS_MODEL_DIR / relative
        if not path.is_file() or path.stat().st_size <= 0:
            missing.append(relative)
    return missing
