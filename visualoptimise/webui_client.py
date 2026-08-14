"""Stable Diffusion WebUI API client for scene and material generation."""

from __future__ import annotations

import base64
import copy
import io
import json
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageChops


class WebUIClientError(RuntimeError):
    """Raised for WebUI connectivity or payload errors."""


class WebUIClient:
    """Small client for AUTOMATIC1111 Stable Diffusion WebUI APIs."""

    def __init__(self, settings: dict):
        webui = settings.get("webui", {})
        self.settings = settings
        self.base_url = webui.get("base_url", "http://127.0.0.1:7860").rstrip("/")
        self.timeout = webui.get("timeout_seconds", 180)

    def health_check(self) -> dict[str, Any]:
        response = requests.get(f"{self.base_url}/sdapi/v1/sd-models", timeout=10)
        response.raise_for_status()
        models = response.json()
        return {
            "ok": True,
            "base_url": self.base_url,
            "model_count": len(models),
            "models": models,
        }

    def list_models(self) -> list[str]:
        health = self.health_check()
        names = []
        for model in health["models"]:
            names.append(model.get("title") or model.get("model_name") or str(model))
        return names

    def get_options(self) -> dict[str, Any]:
        response = requests.get(f"{self.base_url}/sdapi/v1/options", timeout=10)
        response.raise_for_status()
        return response.json()

    def get_active_checkpoint(self) -> str:
        return str(self.get_options().get("sd_model_checkpoint", "unknown"))

    def set_checkpoint(self, checkpoint: str) -> str:
        """Set the active checkpoint through the WebUI options API and return the active value."""
        response = requests.post(
            f"{self.base_url}/sdapi/v1/options",
            json={"sd_model_checkpoint": checkpoint},
            timeout=max(30, self.timeout),
        )
        response.raise_for_status()
        return self.get_active_checkpoint()

    def list_checkpoints(self) -> list[dict[str, Any]]:
        health = self.health_check()
        checkpoints = []
        for model in health["models"]:
            checkpoints.append(
                {
                    "title": model.get("title") or "unknown",
                    "model_name": model.get("model_name") or "unknown",
                    "filename": model.get("filename") or "unknown",
                    "hash": model.get("hash") or "unknown",
                    "sha256": model.get("sha256") or "unknown",
                    "architecture": "unknown",
                }
            )
        return checkpoints

    def list_controlnet_models(self) -> list[str]:
        response = requests.get(f"{self.base_url}/controlnet/model_list", timeout=10)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            models = payload.get("model_list", [])
        else:
            models = payload
        return [str(model) for model in models]

    def list_controlnet_modules(self) -> list[str]:
        response = requests.get(f"{self.base_url}/controlnet/module_list", timeout=10)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            modules = payload.get("module_list", [])
        else:
            modules = payload
        return [str(module) for module in modules]

    def controlnet_model_payload(self) -> Any:
        response = requests.get(f"{self.base_url}/controlnet/model_list", timeout=10)
        response.raise_for_status()
        return response.json()

    def controlnet_module_payload(self) -> Any:
        response = requests.get(f"{self.base_url}/controlnet/module_list", timeout=10)
        response.raise_for_status()
        return response.json()

    def generate_scene(self, prompt_data: dict, control_image: Image.Image) -> Image.Image:
        generation = self.settings["scene_generation"]
        payload = {
            "prompt": prompt_data["scene_positive_prompt"],
            "negative_prompt": prompt_data["scene_negative_prompt"],
            "width": generation["width"],
            "height": generation["height"],
            "steps": generation["steps"],
            "cfg_scale": generation["cfg_scale"],
            "sampler_name": generation["sampler_name"],
            "seed": generation["seed"],
            "alwayson_scripts": self._controlnet_script(generation, control_image),
        }
        return self._post_image("/sdapi/v1/txt2img", payload)

    def generate_scene_img2img(
        self,
        prompt_data: dict,
        init_image: Image.Image,
        control_image: Image.Image,
    ) -> Image.Image:
        generation = self.settings["round3_scene"]
        payload = self.build_scene_img2img_payload(prompt_data, init_image, control_image, generation)
        return self._post_image("/sdapi/v1/img2img", payload)

    def build_txt2img_payload(self, prompt_data: dict, generation: dict) -> dict:
        """Build a plain txt2img payload without ControlNet or image conditioning."""
        return {
            "prompt": prompt_data["scene_positive_prompt"],
            "negative_prompt": prompt_data["scene_negative_prompt"],
            "width": generation["width"],
            "height": generation["height"],
            "steps": generation["steps"],
            "cfg_scale": generation["cfg_scale"],
            "sampler_name": generation["sampler_name"],
            "seed": generation["seed"],
            "tiling": generation.get("tiling", False),
            "batch_size": 1,
            "n_iter": 1,
            "send_images": True,
            "save_images": False,
        }

    def build_material_txt2img_payload(self, prompt_data: dict, generation: dict) -> dict:
        """Build a plain txt2img payload for one tileable material."""
        return {
            "prompt": prompt_data["positive_prompt"],
            "negative_prompt": prompt_data["negative_prompt"],
            "width": generation["width"],
            "height": generation["height"],
            "steps": generation["steps"],
            "cfg_scale": generation["cfg_scale"],
            "sampler_name": generation["sampler_name"],
            "seed": generation["seed"],
            "tiling": generation.get("tiling", True),
            "batch_size": 1,
            "n_iter": 1,
            "send_images": True,
            "save_images": False,
        }

    def build_material_txt2img_controlnet_payload(
        self,
        prompt_data: dict,
        generation: dict,
        control_image: Image.Image,
    ) -> dict:
        """Build a txt2img material payload with one optional ControlNet unit."""
        payload = self.build_material_txt2img_payload(prompt_data, generation)
        controlnet_payload = self._controlnet_script(generation, control_image)
        if controlnet_payload:
            payload["alwayson_scripts"] = controlnet_payload
        return payload

    def post_txt2img_diagnostic(
        self,
        payload: dict,
        response_path: Path,
        output_filename: str,
    ) -> Image.Image:
        """Post txt2img payload and save a compact response diagnostic summary."""
        endpoint = "/sdapi/v1/txt2img"
        try:
            response = requests.post(f"{self.base_url}{endpoint}", json=payload, timeout=self.timeout)
            status_code = response.status_code
            response.raise_for_status()
            data = response.json()
            images = data.get("images") or []
            if not images:
                raise WebUIClientError(f"WebUI returned no images for {endpoint}.")
            image = _base64_to_pil(images[0])
            info = str(data.get("info", ""))
            summary = {
                "endpoint": endpoint,
                "http_status": status_code,
                "images_returned": len(images),
                "decoded_image_size": [image.width, image.height],
                "output_filename": output_filename,
                "info_excerpt": info[:4000],
                "error_keywords_found": _response_error_keywords(info),
            }
        except Exception as exc:
            summary = {
                "endpoint": endpoint,
                "http_status": locals().get("status_code"),
                "images_returned": 0,
                "decoded_image_size": None,
                "output_filename": output_filename,
                "error": str(exc),
            }
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            raise WebUIClientError(f"WebUI request failed at {endpoint}: {exc}") from exc
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return image

    def build_scene_img2img_payload(
        self,
        prompt_data: dict,
        init_image: Image.Image,
        control_image: Image.Image | None,
        generation: dict,
    ) -> dict:
        """Build the exact WebUI img2img payload used for scene diagnostics."""
        payload = {
            "init_images": [_pil_to_base64(init_image)],
            "prompt": prompt_data["scene_positive_prompt"],
            "negative_prompt": prompt_data["scene_negative_prompt"],
            "width": generation["width"],
            "height": generation["height"],
            "steps": generation["steps"],
            "cfg_scale": generation["cfg_scale"],
            "sampler_name": generation["sampler_name"],
            "seed": generation["seed"],
            "denoising_strength": generation["denoising_strength"],
            "tiling": generation.get("tiling", False),
            "resize_mode": 0,
            "batch_size": 1,
            "n_iter": 1,
            "send_images": True,
            "save_images": False,
            "alwayson_scripts": self._controlnet_script(generation, control_image),
        }
        return payload

    def build_material_img2img_payload(
        self,
        prompt_data: dict,
        init_image: Image.Image,
        control_image: Image.Image | None,
        generation: dict,
    ) -> dict:
        """Build the exact WebUI img2img payload for one tileable material."""
        payload = {
            "init_images": [_pil_to_base64(init_image)],
            "prompt": prompt_data["positive_prompt"],
            "negative_prompt": prompt_data["negative_prompt"],
            "width": generation["width"],
            "height": generation["height"],
            "steps": generation["steps"],
            "cfg_scale": generation["cfg_scale"],
            "sampler_name": generation["sampler_name"],
            "seed": generation["seed"],
            "denoising_strength": generation["denoising_strength"],
            "tiling": generation.get("tiling", True),
            "resize_mode": 0,
            "batch_size": 1,
            "n_iter": 1,
            "send_images": True,
            "save_images": False,
            "alwayson_scripts": self._controlnet_script(generation, control_image),
        }
        return payload

    def save_sanitized_payload(self, endpoint: str, payload: dict, path: Path) -> dict:
        """Save a diagnostic copy of a WebUI payload without base64 image data."""
        sanitized = _sanitize_payload(endpoint, payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8")
        return sanitized

    def post_scene_img2img_diagnostic(
        self,
        payload: dict,
        init_image: Image.Image,
        response_path: Path,
        output_filename: str,
    ) -> Image.Image:
        """Post img2img payload and save a compact response diagnostic summary."""
        return self.post_img2img_diagnostic(payload, init_image, response_path, output_filename)

    def post_img2img_diagnostic(
        self,
        payload: dict,
        init_image: Image.Image,
        response_path: Path,
        output_filename: str,
    ) -> Image.Image:
        """Post img2img payload and save a compact response diagnostic summary."""
        endpoint = "/sdapi/v1/img2img"
        try:
            response = requests.post(f"{self.base_url}{endpoint}", json=payload, timeout=self.timeout)
            status_code = response.status_code
            response.raise_for_status()
            data = response.json()
            images = data.get("images") or []
            if not images:
                raise WebUIClientError(f"WebUI returned no images for {endpoint}.")
            image = _base64_to_pil(images[0])
            summary = {
                "endpoint": endpoint,
                "http_status": status_code,
                "images_returned": len(images),
                "decoded_image_size": [image.width, image.height],
                "result_differs_from_init_image": _images_differ(init_image, image),
                "output_filename": output_filename,
            }
        except Exception as exc:
            summary = {
                "endpoint": endpoint,
                "http_status": locals().get("status_code"),
                "images_returned": 0,
                "decoded_image_size": None,
                "result_differs_from_init_image": None,
                "output_filename": output_filename,
                "error": str(exc),
            }
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            raise WebUIClientError(f"WebUI request failed at {endpoint}: {exc}") from exc
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return image

    def generate_wall_basecolor(
        self,
        prompt_data: dict,
        prototype_image: Image.Image,
        control_image: Image.Image,
    ) -> Image.Image:
        generation = self.settings["wall_generation"]
        payload = {
            "init_images": [_pil_to_base64(prototype_image)],
            "prompt": prompt_data.get("positive_prompt", prompt_data.get("wall_positive_prompt", "")),
            "negative_prompt": prompt_data.get("negative_prompt", prompt_data.get("wall_negative_prompt", "")),
            "width": generation["width"],
            "height": generation["height"],
            "steps": generation["steps"],
            "cfg_scale": generation["cfg_scale"],
            "sampler_name": generation["sampler_name"],
            "seed": generation["seed"],
            "denoising_strength": generation["denoising_strength"],
            "tiling": generation.get("tiling", True),
            "alwayson_scripts": self._controlnet_script(generation, control_image),
        }
        return self._post_image("/sdapi/v1/img2img", payload)

    def inpaint(
        self,
        base_image: Image.Image,
        mask_image: Image.Image,
        positive_prompt: str,
        negative_prompt: str,
        settings: dict,
    ) -> Image.Image:
        """Run WebUI img2img inpainting and return the complete output image."""
        base = base_image.convert("RGB")
        mask = mask_image.convert("L")
        payload = self.build_inpaint_payload(base, mask, positive_prompt, negative_prompt, settings)
        return self._post_image("/sdapi/v1/img2img", payload)

    def build_inpaint_payload(
        self,
        base_image: Image.Image,
        mask_image: Image.Image,
        positive_prompt: str,
        negative_prompt: str,
        settings: dict,
        control_image: Image.Image | None = None,
    ) -> dict:
        """Build the exact WebUI img2img inpainting payload."""
        base = base_image.convert("RGB")
        mask = mask_image.convert("L")
        payload = {
            "init_images": [_pil_to_base64(base)],
            "mask": _pil_to_base64(mask),
            "prompt": positive_prompt,
            "negative_prompt": negative_prompt,
            "width": base.width,
            "height": base.height,
            "steps": settings["steps"],
            "cfg_scale": settings["cfg_scale"],
            "sampler_name": settings["sampler_name"],
            "seed": settings["seed"],
            "denoising_strength": settings["denoising_strength"],
            "mask_blur": settings["mask_blur"],
            "inpainting_fill": settings["inpainting_fill"],
            "inpaint_full_res": settings["inpaint_full_res"],
            "inpaint_full_res_padding": settings["inpaint_full_res_padding"],
            "inpainting_mask_invert": 0,
            "resize_mode": 0,
            "batch_size": 1,
            "n_iter": 1,
            "send_images": True,
            "save_images": False,
        }
        if "tiling" in settings:
            payload["tiling"] = settings["tiling"]
        controlnet_payload = self._controlnet_script(settings, control_image)
        if controlnet_payload:
            payload["alwayson_scripts"] = controlnet_payload
        return payload

    def _post_image(self, endpoint: str, payload: dict) -> Image.Image:
        try:
            response = requests.post(f"{self.base_url}{endpoint}", json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise WebUIClientError(f"WebUI request failed at {endpoint}: {exc}") from exc
        images = data.get("images") or []
        if not images:
            raise WebUIClientError(f"WebUI returned no images for {endpoint}.")
        return _base64_to_pil(images[0])

    def _controlnet_script(self, generation: dict, image: Image.Image | None) -> dict:
        controlnet = generation.get("controlnet", {})
        if not controlnet.get("enabled", False):
            return {}
        if image is None:
            raise WebUIClientError("ControlNet is enabled but no control image was provided.")
        model = controlnet.get("model", "")
        if not model:
            raise WebUIClientError(
                "ControlNet is enabled but the model name is blank in settings.json. "
                "Fill it with an installed local ControlNet model before full generation."
            )
        return {
            "controlnet": {
                "args": [
                    {
                        "enabled": True,
                        "image": _pil_to_base64(image),
                        "module": controlnet.get("module", "none"),
                        "model": model,
                        "weight": controlnet.get("weight", 0.8),
                        "guidance_start": controlnet.get("guidance_start", 0.0),
                        "guidance_end": controlnet.get("guidance_end", 1.0),
                        "resize_mode": "Crop and Resize",
                        "pixel_perfect": controlnet.get("pixel_perfect", False),
                    }
                ]
            }
        }


def _pil_to_base64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    if image.mode == "L":
        image.save(buffer, format="PNG")
    else:
        image.convert("RGB").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _base64_to_pil(value: str) -> Image.Image:
    if "," in value:
        value = value.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(value))).convert("RGB")


def _sanitize_payload(endpoint: str, payload: dict) -> dict:
    sanitized = copy.deepcopy(payload)
    if "init_images" in sanitized:
        sanitized["init_images"] = ["<base64 omitted>" for _ in sanitized.get("init_images", [])]
    if "mask" in sanitized:
        sanitized["mask"] = "<base64 omitted>"
    controlnet_args = (
        sanitized.get("alwayson_scripts", {})
        .get("controlnet", {})
        .get("args", [])
    )
    for item in controlnet_args:
        if isinstance(item, dict) and "image" in item:
            item["image"] = "<base64 omitted>"
    controlnet = controlnet_args[0] if controlnet_args else {}
    return {
        "endpoint": endpoint,
        "payload": sanitized,
        "diagnostic_summary": {
            "width": sanitized.get("width"),
            "height": sanitized.get("height"),
            "steps": sanitized.get("steps"),
            "cfg_scale": sanitized.get("cfg_scale"),
            "sampler_name": sanitized.get("sampler_name"),
            "seed": sanitized.get("seed"),
            "denoising_strength": sanitized.get("denoising_strength"),
            "tiling": sanitized.get("tiling"),
            "mask_present": "mask" in sanitized,
            "mask_blur": sanitized.get("mask_blur"),
            "inpainting_fill": sanitized.get("inpainting_fill"),
            "inpaint_full_res": sanitized.get("inpaint_full_res"),
            "inpaint_full_res_padding": sanitized.get("inpaint_full_res_padding"),
            "inpainting_mask_invert": sanitized.get("inpainting_mask_invert"),
            "controlnet_enabled": bool(controlnet.get("enabled", False)),
            "module": controlnet.get("module"),
            "model": controlnet.get("model"),
            "weight": controlnet.get("weight"),
            "guidance_start": controlnet.get("guidance_start"),
            "guidance_end": controlnet.get("guidance_end"),
            "pixel_perfect": controlnet.get("pixel_perfect"),
            "resize_mode": controlnet.get("resize_mode"),
        },
    }


def _images_differ(a: Image.Image, b: Image.Image) -> bool:
    left = a.convert("RGB").resize(b.size, Image.Resampling.NEAREST)
    diff = ImageChops.difference(left, b.convert("RGB"))
    return diff.getbbox() is not None


def _response_error_keywords(info: str) -> list[str]:
    lowered = info.lower()
    keywords = [
        "error",
        "traceback",
        "exception",
        "controlnet error",
        "model mismatch",
        "incompatible",
        "nan",
    ]
    return [keyword for keyword in keywords if keyword in lowered]
