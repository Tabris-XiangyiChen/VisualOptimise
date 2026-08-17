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

Input paths are configured in `settings/pipeline_defaults.json`:

```json
{
  "paths": {
    "map_root": "data/maps",
    "mesh_catalog": "data/ue_asset_catalogs/mesh_catalog.json"
  }
}
```

The defaults preserve the standard project layout. CLI arguments override them for a run:

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --full --map-root "D:\MyMaps" --mesh-catalog "D:\Catalogs\mesh_catalog.json"
```

`--map-root` must contain one directory per map ID, with `map.txt`, `legend.json`, and
`style.txt` inside each package. The selected mesh catalog is used both for the LLM1
sanitized snapshot and for Python resolver metadata; no legacy material-slot catalog is read.

Mesh entries may declare `surface_orientation` as one of `horizontal_surface`,
`vertical_surface`, `panel_surface`, `liquid_surface`, or `sloped_surface`. This
catalog value is the primary geometry-orientation evidence passed to LLM2. Older
catalogs without the field remain supported through the legacy `shape_type` to
view-mode fallback, and generated evidence records which source was used.

Export an existing successful material run into UE-copyable RuntimeData:

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --export-runtime-data --reuse-materials-from <successful_material_run>
```

Run the full pipeline after WebUI and DeepSeek access are ready. StableMaterials
is optional; use `--no-stablematerials` to keep SD1.5 as the only image backend:

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --full
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --map test_map1_clean --full --no-stablematerials
```

The UE-copyable package is written to `generated/ue_ready/runtime_data`, and
timestamped snapshots are kept in `generated/ue_ready/runtime_data_runs`.
