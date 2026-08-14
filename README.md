# VisualOptimise

`VisualOptimise` is the clean submission-oriented extraction of the current
Visual Optimization material pipeline. It keeps the validated two-LLM material
planning and preview generation flow, plus UE RuntimeData export, inside this
folder instead of depending on archived experiment packages.

Typical dry-run for the default map package:

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --full --dry-run
```

Run multiple map packages in sequence:

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --maps test_map1_clean test_map1 --full
```

Export an existing successful material run into UE-copyable RuntimeData:

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --export-runtime-data --reuse-materials-from <successful_material_run>
```

Run the full pipeline only after WebUI, StableMaterials, and DeepSeek access are
ready:

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --full
```

The UE-copyable package is written to `generated/ue_ready/runtime_data`, and
timestamped snapshots are kept in `generated/ue_ready/runtime_data_runs`.
