# Submission Checklist

Use this checklist before packaging or presenting the final VisualOptimise
submission project.

## Required Checks

- `run_main_pipeline.py` is the documented user-facing entry point.
- Runtime code imports only `visualoptimise.*` and Python standard/library
  dependencies, not old `VisualOptimization/experiments` modules.
- `settings/backend_paths.json` controls WebUI, StableMaterials, and UE copy
  destination paths.
- `VisualOptimization/RuntimeData` is preserved as the UE compatibility virtual
  root.
- D6F/D6G artifact names are documented as compatibility identifiers.
- Prompt contracts, validators, SD1.5 settings, StableMaterials settings, seed
  logic, RuntimeData schema, and MapPackageIndex schema are unchanged during B3.
- Full dry-run passes without LLM or image generation.
- Export-only SD1.5 validation passes from a verified material run.
- StableMaterials backend-switch export uses `--no-refresh-runtime-data` and
  does not overwrite latest RuntimeData.
- UE files are not modified by Python validation.

## Recommended Command

```powershell
I:\MiniConda3\envs\dissertation\python.exe I:\Disertation\VisualOptimise\run_main_pipeline.py --b3-submission-hardening-validation
```

The generated B3 summary should report:

- `status = passed`
- `import_isolation_passed = true`
- `path_hardcode_audit_passed = true`
- `structure_equivalence_passed = true`
- `behavior_equivalence_passed = true`
- `ready_for_submission_review = true`

## Known Limitations

- Some core modules remain large to preserve validated behavior.
- Facade modules are intentionally thin wrappers around migrated implementation.
- Image quality is not optimized by B3.
- Full real generation requires separate approval because it calls DeepSeek,
  A1111 WebUI, and optionally StableMaterials.
