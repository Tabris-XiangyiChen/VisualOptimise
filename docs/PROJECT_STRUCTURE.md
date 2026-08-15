# Project Structure

`VisualOptimise` is a self-contained production-facing extraction of the latest
working material pipeline.

## Runtime Entry

- `run_main_pipeline.py`: single command-line entry point.
- `visualoptimise/cli.py`: argument parsing and mode selection.
- `visualoptimise/orchestrator.py`: local stage orchestration and pipeline context.
- `visualoptimise/submission_validation.py`: D6G-B3 submission hardening, config, isolation, equivalence, and documentation validation.

## Core Pipeline

- `visualoptimise/semantic_planning.py`: map package validation, map facts, LLM1 material planning, dynamic resolver helpers.
- `visualoptimise/prompt_generation.py`: LLM2 D6E-style prompt brief generation, validation, bounded post-retry prompt-contract normalization, and material-identity-source-aware negative prompt conflict validation.
- `visualoptimise/material_generation_pipeline.py`: complete material preview pipeline using the extracted helpers.
- `visualoptimise/preview_generation.py`: SD1.5 and StableMaterials preview generation helpers.
- `visualoptimise/runtime_export.py`: export-only RuntimeData integration.
- `visualoptimise/runtime_export_base.py`: UE RuntimeData package writer.
- `visualoptimise/runtime_validation.py`: RuntimeData validation.
- `visualoptimise/backend_config.py`: reads `settings/backend_paths.json` so WebUI, StableMaterials, and UE RuntimeData copy paths are configurable.

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

## Compatibility Identifiers

Some artifact schemas and report filenames intentionally retain names such as
`d6f_a4_full_two_llm_material_generation_preview` and
`d6g_a2_material_manifest_runtime_export`. These are compatibility identifiers
for the validated research stages that the submission project preserves. They
are not runtime imports from the old `VisualOptimization/experiments` tree.

## UE RuntimeData Path

The Python project is named `VisualOptimise`, but the UE content path remains
`VisualOptimization/RuntimeData` because the existing UE loader has already been
validated against that content root. The absolute copy destination is configured
in `settings/backend_paths.json`; generated packages still live under
`generated/ue_ready/runtime_data` before being copied to UE.

## Large Core Modules

Several files remain larger than ideal because they preserve validated behavior
from the successful D6F/D6G pipeline. The small facade modules provide cleaner
engineering names, while deeper splitting is intentionally deferred to avoid
changing prompt contracts, validators, or RuntimeData schema during submission
hardening.
