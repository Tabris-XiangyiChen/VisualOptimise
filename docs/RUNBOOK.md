# Runbook

All Python commands should use:

```powershell
I:\MiniConda3\envs\dissertation\python.exe
```

## Plan Validation Only

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --full --dry-run
```

This does not call DeepSeek, WebUI, StableMaterials, or refresh RuntimeData.

## Submission Hardening Validation

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --b3-submission-hardening-validation
```

This creates a B3 validation run under `outputs/runs`. It performs syntax
validation, import isolation audit, path hardcode audit, structure equivalence
audit, behavior equivalence audit, full dry-run validation, export-only SD1.5
validation, and a StableMaterials backend-switch export validation using
`--no-refresh-runtime-data`. It does not call DeepSeek, does not generate new
images, and does not modify UE files.

## Generate Materials Only

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --generate-materials
```

This requires DeepSeek, A1111 WebUI with API enabled, and StableMaterials local
availability.

To run more than one map package, pass multiple map IDs. Each map is processed
independently; RuntimeData export updates `maps/<map_id>` entries and rebuilds
`map_package_index.json`.

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --maps test_map1_clean test_map1 --full
```

## Export RuntimeData From Existing Material Run

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --export-runtime-data --reuse-materials-from <successful_material_run>
```

By default, this refreshes:

```text
I:\Disertation\VisualOptimise\generated\ue_ready\runtime_data
```

Use `--no-refresh-runtime-data` for backend-switch validation that should not
overwrite the default latest package.

To export using StableMaterials candidates without replacing latest:

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --export-runtime-data --reuse-materials-from <successful_material_run> --runtime-texture-backend stablematerials --no-refresh-runtime-data
```

## Full Pipeline

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --full
```

This runs material generation first, then exports RuntimeData from the newly
created material run.

LLM2 output is validated before image generation. After configured LLM2 retries,
the pipeline may apply bounded, reported normalization for missing/misplaced
tileability tags and removable weak meta tags; the original and normalized
prompt briefs are both saved in the material run.

Negative prompt conflict validation uses only hard material identity sources:
`canonical_material_id`, `material_identity_coarse`, and current context clues
explicitly marked as `material_identity`. Full legend descriptions remain
available to LLM2 as evidence, but are not treated as protected material tokens
because they may contain placement context such as nearby walls or water.

StableMaterials prompts are constrained in the LLM2 contract and validated by
Python for length only. Python does not semantically trim StableMaterials text.
If StableMaterials prompt length remains over budget after LLM2 retries, the
StableMaterials backend is downgraded to a warning while SD1.5 remains eligible
for default RuntimeData export.

## Copy RuntimeData To UE

RuntimeData is generated first into:

```text
I:\Disertation\VisualOptimise\generated\ue_ready\runtime_data
```

The configured UE copy destination is:

```text
I:\Disertation\VisualOptimizationUE\Content\VisualOptimization\RuntimeData
```

The `VisualOptimization/RuntimeData` content path is retained for compatibility
with the existing UE loader. Edit `settings/backend_paths.json` if the UE project
is moved.
