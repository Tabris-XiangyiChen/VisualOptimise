# VisualOptimise Documentation

This directory documents the extracted submission project. The runtime code is
contained under `visualoptimise/`, while historical experiments remain in the
original `VisualOptimization` project.

`run_main_pipeline.py` is the final user-facing entry point. The project is
designed to run from its own `VisualOptimise` folder without importing old
`experiments/current`, `experiments/archive`, or `experiments/shared` code.

Key documents:

- `SUPERVISOR_PROJECT_OVERVIEW.md`: English end-to-end architecture, data flow,
  responsibilities, failure boundaries, and Mermaid diagrams for project
  presentations.
- `visualoptimise_pipeline_overview.png`: concise English flowchart for
  supervisor presentations.
- `PROJECT_STRUCTURE.md`: final project layout and module ownership.
- `RUNBOOK.md`: commands for dry-run, material generation, and RuntimeData export.
- `CONFIGURATION.md`: editable backend paths for WebUI, StableMaterials, and UE copy destination.
- `SUBMISSION_CHECKLIST.md`: final verification checklist before submission.
- `MIGRATION_MANIFEST.md`: copied modules, preserved logic, and required edits.

Internal D6F/D6G schema names and summary filenames are compatibility
identifiers from validated research stages. They do not mean the runtime depends
on old experiment packages.
