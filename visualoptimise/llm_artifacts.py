"""One-call JSON LLM helpers and artifact persistence."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests

from .artifacts import write_json, write_text


class LLMArtifactError(RuntimeError):
    """Raised when one-call LLM planning fails."""


PROVIDER_DEFAULT_API_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def _append_unique(values: list[str], value: str | None) -> None:
    if value and value not in values:
        values.append(value)


def _configured_env_names(llm_settings: dict[str, Any]) -> list[str]:
    names: list[str] = []
    configured = llm_settings.get("api_key_env")
    if isinstance(configured, str):
        _append_unique(names, configured)
    elif isinstance(configured, list):
        for item in configured:
            if isinstance(item, str):
                _append_unique(names, item)
    provider = str(llm_settings.get("provider", "")).lower()
    _append_unique(names, PROVIDER_DEFAULT_API_KEY_ENV.get(provider))
    _append_unique(names, "DEEPSEEK_API_KEY")
    return names


def _resolve_secret_file(project_root: Path, configured: str) -> Path:
    path = Path(configured)
    if path.is_absolute():
        return path
    return project_root / path


def _configured_key_files(project_root: Path, llm_settings: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    configured = llm_settings.get("api_key_file")
    if isinstance(configured, str):
        paths.append(_resolve_secret_file(project_root, configured))
    elif isinstance(configured, list):
        for item in configured:
            if isinstance(item, str):
                paths.append(_resolve_secret_file(project_root, item))

    provider = str(llm_settings.get("provider", "deepseek")).lower() or "deepseek"
    workspace_root = project_root.parent
    for candidate in (
        project_root / "config" / "secrets" / f"{provider}_api_key.txt",
        project_root / "config" / "secrets" / "deepseek_api_key.txt",
        workspace_root / "config" / "secrets" / "deepseek_api_key.txt",
    ):
        if candidate not in paths:
            paths.append(candidate)
    return paths


def build_json_chat_payload(settings: dict, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    llm_settings = settings.get("llm", {})
    payload = {
        "model": llm_settings.get("model", "deepseek-chat"),
        "temperature": llm_settings.get("temperature", 0.2),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if llm_settings.get("json_mode", True):
        payload["response_format"] = {"type": "json_object"}
    return payload


def parse_json_response(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise LLMArtifactError("LLM response JSON must be an object.")
    return parsed


def read_api_key(project_root: Path, llm_settings: dict[str, Any] | None = None) -> str:
    llm_settings = llm_settings or {}
    env_names = _configured_env_names(llm_settings)
    for env_name in env_names:
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value.strip()

    candidates = _configured_key_files(project_root, llm_settings)
    for candidate in candidates:
        if candidate.exists():
            key = candidate.read_text(encoding="utf-8").strip()
            if key and not key.startswith("replace_"):
                return key
    raise LLMArtifactError(
        "LLM API key not found. Set one of these environment variables: "
        f"{', '.join(env_names)}; or create one configured key file such as "
        f"{candidates[0]} with the key on a single line."
    )


def call_one_json_llm(settings: dict, project_root: Path, request_payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    llm_settings = settings.get("llm", {})
    api_key = read_api_key(project_root, llm_settings)
    base_url = llm_settings.get("base_url", "https://api.deepseek.com").rstrip("/")
    endpoint = str(llm_settings.get("endpoint", "/chat/completions"))
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    response = requests.post(
        f"{base_url}{endpoint}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=request_payload,
        timeout=llm_settings.get("timeout_seconds", 60),
    )
    response.raise_for_status()
    payload = response.json()
    raw = payload["choices"][0]["message"]["content"]
    return raw, parse_json_response(raw)


def save_llm_artifacts(
    llm_dir: Path,
    system_prompt: str,
    user_prompt: str,
    request_payload: dict[str, Any],
    raw_response: str,
    parsed_response: dict[str, Any],
    validation: dict[str, Any],
    shared_style: dict[str, Any],
    prompt_candidates: dict[str, Any],
) -> None:
    write_text(llm_dir / "llm_system_prompt.txt", system_prompt)
    write_text(llm_dir / "llm_user_prompt.txt", user_prompt)
    write_json(llm_dir / "llm_request_payload.json", request_payload)
    write_text(llm_dir / "llm_raw_response.txt", raw_response)
    write_json(llm_dir / "llm_parsed_response.json", parsed_response)
    write_json(llm_dir / "llm_schema_validation.json", validation)
    write_json(llm_dir / "shared_style_locked.json", shared_style)
    write_json(llm_dir / "material_prompt_candidates.json", prompt_candidates)
