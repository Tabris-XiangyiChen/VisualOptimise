# Migration Manifest

## Source And Target

- Source project: `I:\Disertation\VisualOptimization`
- Submission extraction: `I:\Disertation\VisualOptimise`

## Preserved Logic

Function bodies were copied from the latest working D6F/D6G implementation and
kept as close as possible. Changes were limited to local import paths, command
strings, default source-run removal, and self-contained orchestration.

## Copied Runtime Modules

- `experiments/shared/run_artifacts.py` -> `visualoptimise/artifacts.py`
- `experiments/shared/llm_artifacts.py` -> `visualoptimise/llm_artifacts.py`
- `experiments/shared/material_planning/d6f_mainline.py` -> `visualoptimise/semantic_planning.py`
- `experiments/shared/prompting/d6e_style_prompt_briefs.py` -> `visualoptimise/prompt_generation.py`
- `experiments/shared/material_runtime/preview_generation.py` -> `visualoptimise/preview_generation.py`
- `experiments/shared/material_runtime/generation_backend.py` -> `visualoptimise/generation_backend.py`
- `experiments/shared/material_runtime/stablematerials_worker.py` -> `visualoptimise/stablematerials_worker.py`
- `experiments/shared/runtime_data/exporter.py` -> `visualoptimise/runtime_export_base.py`
- `experiments/shared/runtime_data/validation.py` -> `visualoptimise/runtime_validation.py`
- `experiments/current/round_d6f_a4_full_two_llm_material_generation_preview/experiment.py` -> `visualoptimise/material_generation_pipeline.py`
- `experiments/current/round_d6f_a4_full_two_llm_material_generation_preview/report.py` -> `visualoptimise/material_generation_report.py`
- `experiments/current/round_d6g_a2_material_manifest_runtime_export/experiment.py` -> `visualoptimise/runtime_export.py`

## Required Edits

- Replaced imports from `experiments.shared.*` with `visualoptimise.*`.
- Replaced old `run_pipeline.py` command strings with `VisualOptimise\run_main_pipeline.py`.
- Removed the old hardcoded default material source run from `runtime_export.py`; export now requires an explicit source run or a locally generated source run.
- Moved StableMaterials worker resolution to the local `visualoptimise/stablematerials_worker.py`.
- Added `ProductionPipelineContext` so runtime modules no longer require `VisualGenerationPipeline`.
- Added facade modules for production-facing names without changing extracted behavior.
- Added root `README.md` and docs.

## Intentional Non-Copies

- Historical `outputs/runs` were not copied.
- Existing `generated/ue_ready/runtime_data` was not copied as the default result; export-only validation regenerates it.
- Archived experiment packages were not copied as dependencies.
