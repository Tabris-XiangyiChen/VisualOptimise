# Configuration

The final submission project keeps machine-specific paths in configuration,
not in runtime source code.

## Backend Paths

Edit:

```text
settings/backend_paths.json
```

Current fields:

- `sd15.webui_base_url`: A1111 WebUI API URL.
- `sd15.python`: Python executable used in generated command records.
- `stablematerials.python`: Python executable for the local StableMaterials environment.
- `stablematerials.model_dir`: local StableMaterials model directory.
- `stablematerials.worker`: Python module or script used as the offline worker.
- `ue_runtime.copy_destination`: UE project RuntimeData copy destination.
- `ue_runtime.virtual_root`: UE-facing virtual content root.

The default UE virtual root remains:

```text
VisualOptimization/RuntimeData
```

This is intentional. `VisualOptimise` is the Python submission project name,
while `VisualOptimization/RuntimeData` is the UE content path expected by the
existing loader.

## WebUI

Start A1111 WebUI with API enabled, then set:

```json
{
  "sd15": {
    "webui_base_url": "http://127.0.0.1:7860"
  }
}
```

## StableMaterials

StableMaterials remains local/offline. The worker preserves:

- `HF_HUB_OFFLINE=1`
- `TRANSFORMERS_OFFLINE=1`
- `local_files_only=True`
- `trust_remote_code=False`
- `unet_subfolder=unet_lcm`
- `LCMScheduler`
- `float16`

Only the Python executable and model directory are configurable.

StableMaterials is optional for the default SD1.5 RuntimeData path. To disable
it by default, set this in `settings/pipeline_defaults.json`:

```json
{
  "stablematerials_enabled": false
}
```

For a single run, pass `--no-stablematerials`. The LLM still produces
StableMaterials prompt briefs for audit compatibility, but no StableMaterials
worker is launched and no StableMaterials candidate structure is packaged when
there are no generated StableMaterials files.

## RuntimeData Export

The Python pipeline writes the latest UE-copyable package to:

```text
generated/ue_ready/runtime_data
```

It also writes timestamped snapshots to:

```text
generated/ue_ready/runtime_data_runs
```

The configured `ue_runtime.copy_destination` tells users where to copy this
package inside the UE project. The Python export does not modify UE files.
