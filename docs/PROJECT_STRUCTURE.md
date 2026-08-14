# Project Structure

`VisualOptimise` is a self-contained production-facing extraction of the latest
working material pipeline.

## Runtime Entry

- `run_main_pipeline.py`: single command-line entry point.
- `visualoptimise/cli.py`: argument parsing and mode selection.
- `visualoptimise/orchestrator.py`: local stage orchestration and pipeline context.

## Core Pipeline

- `visualoptimise/semantic_planning.py`: map package validation, map facts, LLM1 material planning, dynamic resolver helpers.
- `visualoptimise/prompt_generation.py`: LLM2 D6E-style prompt brief generation, validation, bounded post-retry prompt-contract normalization, and material-identity-source-aware negative prompt conflict validation.
- `visualoptimise/material_generation_pipeline.py`: complete material preview pipeline using the extracted helpers.
- `visualoptimise/preview_generation.py`: SD1.5 and StableMaterials preview generation helpers.
- `visualoptimise/runtime_export.py`: export-only RuntimeData integration.
- `visualoptimise/runtime_export_base.py`: UE RuntimeData package writer.
- `visualoptimise/runtime_validation.py`: RuntimeData validation.

## Facade Modules

The following modules provide stable engineering names around extracted logic:

- `map_loader.py`
- `mesh_catalog.py`
- `tileset_resolver.py`
- `material_evidence.py`
- `prompt_validation.py`
- `prompt_compiler.py`
- `sd15_backend.py`
- `stablematerials_backend.py`

## Data And Outputs

- `data/maps/<map_id>`: authoring map packages containing `map.txt`, `legend.json`, and `style.txt`.
- `data/maps/test_map1_clean`: default example map used by the current settings.
- `data/maps/test_map1`: original example map retained as an additional package for validation.
- `data/ue_asset_catalogs/mesh_catalog.json`: available UE logical mesh catalog.
- `outputs/runs`: timestamped run reports.
- `generated/ue_ready/runtime_data`: refreshable UE-copyable latest package.
- `generated/ue_ready/runtime_data_runs`: non-overwritten RuntimeData snapshots.
