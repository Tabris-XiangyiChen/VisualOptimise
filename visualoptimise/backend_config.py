"""Backend and export path configuration for VisualOptimise.

Machine-specific paths are intentionally read from settings/backend_paths.json,
not embedded in runtime modules. The defaults here are relative or compatibility
labels only, so the package can be moved to another machine and reconfigured.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from visualoptimise.config_loader import read_json_if_exists


BACKEND_PATHS_SCHEMA = "backend_paths_v1"
DEFAULT_WEBUI_BASE_URL = "http://127.0.0.1:7860"
DEFAULT_UE_RUNTIME_VIRTUAL_ROOT = "VisualOptimization/RuntimeData"


@dataclass(frozen=True)
class BackendPaths:
    project_root: Path
    webui_base_url: str
    dissertation_python: Path | None
    stablematerials_python: Path | None
    stablematerials_model_dir: Path | None
    stablematerials_worker: str
    stablematerials_worker_kind: str
    stablematerials_working_dir: Path
    ue_runtime_data_destination: Path | None
    ue_runtime_virtual_root: str


def load_backend_paths(project_root: Path) -> BackendPaths:
    """Load backend path settings, supporting the current nested JSON layout."""
    project_root = project_root.resolve()
    raw = read_json_if_exists(project_root / "settings" / "backend_paths.json")
    sd15 = _object(raw.get("sd15"))
    stable = _object(raw.get("stablematerials"))
    ue = _object(raw.get("ue_runtime"))

    webui_base_url = str(sd15.get("webui_base_url") or raw.get("webui_base_url") or DEFAULT_WEBUI_BASE_URL)
    dissertation_python = _optional_path(sd15.get("python") or raw.get("python") or raw.get("dissertation_python"))
    stablematerials_python = _optional_path(stable.get("python") or raw.get("stablematerials_python"))
    stablematerials_model_dir = _optional_path(stable.get("model_dir") or raw.get("stablematerials_model_dir"))
    worker = str(stable.get("worker") or raw.get("stablematerials_worker") or "visualoptimise.stablematerials_worker")
    worker_kind = _worker_kind(worker)
    working_dir = _optional_path(stable.get("working_dir") or raw.get("stablematerials_working_dir")) or project_root.parent
    ue_destination = _optional_path(ue.get("copy_destination") or raw.get("ue_runtime_data_destination"))
    ue_virtual_root = str(ue.get("virtual_root") or raw.get("ue_runtime_virtual_root") or DEFAULT_UE_RUNTIME_VIRTUAL_ROOT)
    return BackendPaths(
        project_root=project_root,
        webui_base_url=webui_base_url.rstrip("/"),
        dissertation_python=dissertation_python,
        stablematerials_python=stablematerials_python,
        stablematerials_model_dir=stablematerials_model_dir,
        stablematerials_worker=worker,
        stablematerials_worker_kind=worker_kind,
        stablematerials_working_dir=working_dir,
        ue_runtime_data_destination=ue_destination,
        ue_runtime_virtual_root=ue_virtual_root,
    )


def backend_paths_to_json(paths: BackendPaths) -> dict[str, Any]:
    return {
        "schema_version": BACKEND_PATHS_SCHEMA,
        "project_root": str(paths.project_root),
        "webui_base_url": paths.webui_base_url,
        "dissertation_python": str(paths.dissertation_python) if paths.dissertation_python else None,
        "stablematerials_python": str(paths.stablematerials_python) if paths.stablematerials_python else None,
        "stablematerials_model_dir": str(paths.stablematerials_model_dir) if paths.stablematerials_model_dir else None,
        "stablematerials_worker": paths.stablematerials_worker,
        "stablematerials_worker_kind": paths.stablematerials_worker_kind,
        "stablematerials_working_dir": str(paths.stablematerials_working_dir),
        "ue_runtime_data_destination": str(paths.ue_runtime_data_destination) if paths.ue_runtime_data_destination else None,
        "ue_runtime_virtual_root": paths.ue_runtime_virtual_root,
    }


def webui_settings(paths: BackendPaths) -> dict[str, Any]:
    return {"webui": {"base_url": paths.webui_base_url}}


def stablematerials_generation_settings(base_settings: dict[str, Any], paths: BackendPaths) -> dict[str, Any]:
    settings = dict(base_settings)
    if paths.stablematerials_model_dir is not None:
        settings["custom_pipeline"] = str(paths.stablematerials_model_dir)
    return settings


def stablematerials_worker_command(paths: BackendPaths) -> list[str]:
    if paths.stablematerials_python is None:
        raise FileNotFoundError("StableMaterials Python is not configured in settings/backend_paths.json.")
    command = [str(paths.stablematerials_python)]
    if paths.stablematerials_worker_kind == "module":
        command.extend(["-m", paths.stablematerials_worker])
    else:
        command.append(paths.stablematerials_worker)
    return command


def ue_copy_destination(paths: BackendPaths, project_root: Path) -> Path:
    if paths.ue_runtime_data_destination is not None:
        return paths.ue_runtime_data_destination
    return project_root / "generated" / "ue_ready" / "runtime_data_copy_destination_not_configured"


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_path(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value))


def _worker_kind(worker: str) -> str:
    suffix = Path(worker).suffix.lower()
    if suffix == ".py" or "\\" in worker or "/" in worker:
        return "script"
    return "module"
