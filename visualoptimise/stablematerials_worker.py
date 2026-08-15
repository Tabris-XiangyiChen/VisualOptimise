"""Offline StableMaterials LCM worker for shared material generation.

This script is launched with the `stablematerials` Conda environment. It reads a
small JSON request and writes only into the requested experiment output directory.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import inspect
import json
import os
import platform
import socket
import sys
import time
import traceback
from pathlib import Path
from typing import Any

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import torch
from diffusers import DiffusionPipeline, LCMScheduler, UNet2DConditionModel
from PIL import Image, ImageStat
from safetensors import safe_open


MAP_NAMES = ["basecolor", "normal", "height", "roughness", "metallic"]
RGB_MAPS = {"basecolor", "normal"}
GRAYSCALE_MAPS = {"height", "roughness", "metallic"}
REQUIRED_FILES = [
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


class NetworkBlocker:
    def __init__(self) -> None:
        self.attempts: list[dict[str, str]] = []
        self._original_create_connection = None
        self._original_connect = None

    def __enter__(self) -> "NetworkBlocker":
        self._original_create_connection = socket.create_connection
        self._original_connect = socket.socket.connect

        def blocked_create_connection(address: object, *args: object, **kwargs: object) -> None:
            self.attempts.append({"function": "socket.create_connection", "address": repr(address)})
            raise RuntimeError(f"Network access blocked during StableMaterials local run: {address!r}")

        def blocked_connect(sock: socket.socket, address: object) -> None:
            self.attempts.append({"function": "socket.socket.connect", "address": repr(address)})
            raise RuntimeError(f"Network access blocked during StableMaterials local run: {address!r}")

        socket.create_connection = blocked_create_connection
        socket.socket.connect = blocked_connect
        return self

    def __exit__(self, exc_type: object, exc_value: object, exc_tb: object) -> bool:
        socket.create_connection = self._original_create_connection
        socket.socket.connect = self._original_connect
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shared StableMaterials LCM worker")
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-dir", help="Local StableMaterials model directory. Overrides request generation custom_pipeline.")
    args = parser.parse_args(argv)
    request_path = Path(args.request)
    output_root = Path(args.output)
    report_path = output_root / "stablematerials_worker_report.json"
    output_root.mkdir(parents=True, exist_ok=True)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    model_path = resolve_model_path(args.model_dir, request)
    pipeline_path = model_path / "pipeline.py"
    report: dict[str, Any] = {
        "status": "started",
        "python_executable": sys.executable,
        "python_version": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
        "model_path": str(model_path),
        "custom_pipeline": str(model_path),
        "trust_remote_code": False,
        "local_files_only": True,
        "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"),
        "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE"),
        "scheduler": "LCMScheduler",
        "unet_subfolder": "unet_lcm",
        "dtype": "float16",
        "cpu_offload": False,
        "attention_slicing": False,
        "network_attempts": [],
        "materials": {},
        "errors": [],
        "warnings": [],
    }
    started = time.perf_counter()
    try:
        report["preflight_model_files"] = validate_model_files(model_path)
        report["local_pipeline_sha256"] = sha256_file(pipeline_path)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available for StableMaterials LCM generation.")
        torch.cuda.reset_peak_memory_stats()
        with NetworkBlocker() as network:
            unet = UNet2DConditionModel.from_pretrained(
                model_path,
                subfolder="unet_lcm",
                local_files_only=True,
                torch_dtype=torch.float16,
            )
            pipe = DiffusionPipeline.from_pretrained(
                model_path,
                custom_pipeline=str(model_path),
                local_files_only=True,
                trust_remote_code=False,
                unet=unet,
                torch_dtype=torch.float16,
            )
            loaded_source = inspect.getsourcefile(pipe.__class__)
            report["loaded_pipeline_class"] = pipe.__class__.__name__
            report["loaded_pipeline_module"] = pipe.__class__.__module__
            report["loaded_pipeline_source"] = str(Path(loaded_source)) if loaded_source else None
            if loaded_source and Path(loaded_source).is_file():
                report["loaded_pipeline_sha256"] = sha256_file(Path(loaded_source))
                report["local_pipeline_sha256_matches_loaded_source"] = report["loaded_pipeline_sha256"] == report["local_pipeline_sha256"]
            pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
            pipe.enable_attention_slicing()
            report["attention_slicing"] = True
            pipe.enable_model_cpu_offload()
            report["cpu_offload"] = True
            generation = request["generation"]
            for material in request["materials"]:
                material_id = material["material_id"]
                material_dir = output_root / material_id
                material_dir.mkdir(parents=True, exist_ok=False)
                material_seed = material.get("seed", generation.get("seed", 0))
                generator = torch.Generator(device="cuda").manual_seed(int(material_seed))
                material_started = time.perf_counter()
                with torch.inference_mode():
                    result = pipe(
                        prompt=material["prompt"],
                        width=int(generation["width"]),
                        height=int(generation["height"]),
                        tileable=bool(generation["tileable"]),
                        num_images_per_prompt=1,
                        num_inference_steps=int(generation["num_inference_steps"]),
                        guidance_scale=float(generation["guidance_scale"]),
                        negative_prompt=None,
                        generator=generator,
                        output_type="pil",
                    )
                maps = save_material_maps(result.images[0], material_dir, int(generation["width"]), int(generation["height"]))
                report["materials"][material_id] = {
                    "prompt": material["prompt"],
                    "seed": int(material_seed),
                    "maps": maps,
                    "generation_seconds": round(time.perf_counter() - material_started, 3),
                }
                del result
            report["network_attempts"] = network.attempts
            if network.attempts:
                raise RuntimeError(f"StableMaterials attempted network access: {network.attempts}")
            del pipe, unet
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append({"exception": repr(exc), "traceback": traceback.format_exc()})
    finally:
        if torch.cuda.is_available():
            report["cuda_peak_allocated_gib"] = round(torch.cuda.max_memory_allocated() / 1024**3, 3)
            report["cuda_peak_reserved_gib"] = round(torch.cuda.max_memory_reserved() / 1024**3, 3)
            torch.cuda.empty_cache()
        gc.collect()
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(report_path)}, indent=2))
    return 0 if report["status"] == "passed" else 1


def resolve_model_path(cli_model_dir: str | None, request: dict[str, Any]) -> Path:
    value = (
        cli_model_dir
        or request.get("generation", {}).get("custom_pipeline")
        or os.environ.get("STABLEMATERIALS_MODEL_DIR")
    )
    if not value:
        raise ValueError("StableMaterials model directory is not configured.")
    return Path(value).resolve()


def validate_model_files(model_path: Path) -> list[dict[str, Any]]:
    missing = []
    checked = []
    if not model_path.is_dir():
        missing.append(str(model_path))
    for relative_file in REQUIRED_FILES:
        path = model_path / relative_file
        if not path.is_file() or path.stat().st_size <= 0:
            missing.append(relative_file)
            continue
        entry: dict[str, Any] = {"relative_path": relative_file, "size_bytes": path.stat().st_size}
        if path.suffix == ".safetensors":
            with safe_open(path, framework="pt", device="cpu") as handle:
                entry["tensor_count"] = len(list(handle.keys()))
        checked.append(entry)
    if missing:
        raise FileNotFoundError("Missing local StableMaterials files: " + json.dumps(missing, indent=2))
    return checked


def save_material_maps(material_output: Any, material_dir: Path, width: int, height: int) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    for map_name in MAP_NAMES:
        image = getattr(material_output, map_name)
        if not isinstance(image, Image.Image):
            raise TypeError(f"{map_name} output is {type(image)}, expected PIL.Image.Image")
        expected_mode = "RGB" if map_name in RGB_MAPS else "L"
        if image.mode != expected_mode:
            raise ValueError(f"{material_dir.name} {map_name} mode is {image.mode}, expected {expected_mode}")
        if image.size != (width, height):
            raise ValueError(f"{material_dir.name} {map_name} size is {image.size}, expected {(width, height)}")
        path = material_dir / f"{map_name}.png"
        image.save(path)
        saved[map_name] = {
            "path": str(path),
            "mode": image.mode,
            "dimensions": [image.width, image.height],
            "stats": image_stats(image),
        }
    return saved


def image_stats(image: Image.Image) -> dict[str, Any]:
    converted = image.convert("RGB") if image.mode != "RGB" else image
    stat = ImageStat.Stat(converted)
    values = [channel for pixel in converted.getdata() for channel in pixel]
    mean = sum(values) / max(1, len(values))
    variance = sum((value - mean) ** 2 for value in values) / max(1, len(values))
    return {
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "std": variance ** 0.5,
        "channel_mean": stat.mean,
        "channel_std": stat.stddev,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
