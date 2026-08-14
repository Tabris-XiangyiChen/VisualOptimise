"""Markdown report builder for D6F-A4."""

from __future__ import annotations

from typing import Any


def build_markdown_report(summary: dict[str, Any]) -> str:
    preflight = summary.get("preflight", {})
    sd15_preflight = preflight.get("sd15", {})
    sm_preflight = preflight.get("stablematerials", {})
    contact_sheets = summary.get("contact_sheets", {})
    generation = summary.get("generation_summary", {})
    analysis = summary.get("analysis", {})
    notes = analysis.get("per_material_notes", {}).get("rows", [])
    stability_rows = analysis.get("stability_observation", {}).get("rows", [])

    lines: list[str] = [
        "# D6F-A4 Full Two-LLM Material Generation Preview",
        "",
        "## Overview",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Output run: `{summary.get('output_run')}`",
        f"- Map: `{summary.get('map_id')}`",
        f"- LLM1 called/attempts/retries: `{summary.get('llm1_called')}` / `{summary.get('llm1_attempts')}` / `{summary.get('llm1_retry_count')}`",
        f"- LLM2 called/attempts/retries: `{summary.get('llm2_called')}` / `{summary.get('llm2_attempts')}` / `{summary.get('llm2_retry_count')}`",
        f"- RuntimeData exported: `{summary.get('runtime_data_exported')}`",
        f"- Generated package exported: `{summary.get('generated_package_exported')}`",
        f"- UE modified: `{summary.get('ue_modified')}`",
        f"- Native pass without posthoc recheck: `{summary.get('native_run_passed_without_posthoc_recheck')}`",
        "",
        "## Component Reuse",
        "",
        f"- Fix1 LLM1/resolver reused: `{summary.get('fix1_llm1_resolver_reused')}`",
        f"- Fix3 prompt contract reused: `{summary.get('fix3_prompt_contract_reused')}`",
        f"- Fix3 patched validator reused: `{summary.get('fix3_patched_validator_reused')}`",
        f"- Fix4 Plan A generation reused: `{summary.get('fix4_plan_a_generation_reused')}`",
        f"- Plan B run: `{summary.get('plan_b_run')}`",
        "",
        "## Validation",
        "",
        f"- LLM1 validation passed: `{summary.get('llm1_validation_passed')}`",
        f"- Dynamic evidence validation passed: `{summary.get('dynamic_evidence_validation_passed')}`",
        f"- Prompt input audit passed: `{summary.get('prompt_input_audit_passed')}`",
        f"- LLM2 prompt validation passed: `{summary.get('llm2_prompt_validation_passed')}`",
        f"- Prior leak audit passed: `{summary.get('prior_leak_audit_passed')}`",
        "",
        "## Generation Counts",
        "",
        f"- Material slots: `{len(summary.get('material_slots', []))}`",
        f"- Seeds: `{summary.get('seeds')}`",
        f"- SD1.5 expected/generated: `{generation.get('expected_sd15_images')}` / `{summary.get('sd15_images_generated')}`",
        f"- StableMaterials expected/generated: `{generation.get('expected_stablematerials_sets')}` / `{summary.get('stablematerials_sets_generated')}`",
        f"- Failed item count: `{summary.get('failed_item_count')}`",
        "",
        "## Backend Preflight",
        "",
        f"- SD1.5 preflight passed: `{sd15_preflight.get('passed')}`",
        f"- SD1.5 active checkpoint: `{sd15_preflight.get('webui', {}).get('active_checkpoint')}`",
        f"- SD1.5 required checkpoint: `{sd15_preflight.get('webui', {}).get('required_checkpoint')}`",
        f"- StableMaterials preflight passed: `{sm_preflight.get('passed')}`",
        f"- StableMaterials Python: `{sm_preflight.get('python')}`",
        "",
    ]

    failed_items = summary.get("failed_items", [])
    if failed_items:
        lines.extend(["## Failed Items", ""])
        for item in failed_items:
            lines.append(
                f"- `{item.get('backend')}` `{item.get('request_id')}` "
                f"slot=`{item.get('material_slot_id')}` seed=`{item.get('seed')}`: {item.get('error')}"
            )
        lines.append("")

    lines.extend(["## Contact Sheets", ""])
    if contact_sheets:
        for label, path in contact_sheets.items():
            lines.append(f"- {label}: `{path}`")
    else:
        lines.append("- Contact sheets pending until real generation completes.")
    lines.append("")

    lines.extend(["## Low-Level Diagnostics", ""])
    if stability_rows:
        lines.append("| Backend | Plan | Material | Count | Brightness Range | Saturation Range | Seam Jump Range |")
        lines.append("| --- | --- | --- | ---: | --- | --- | --- |")
        for row in stability_rows:
            lines.append(
                f"| {row.get('backend')} | {row.get('plan_id')} | {row.get('display_label')} | {row.get('image_count')} | "
                f"{row.get('brightness_mean_range')} | {row.get('saturation_mean_range')} | {row.get('seam_jump_mean_range')} |"
            )
    else:
        lines.append("- Diagnostics pending until image generation runs.")
    lines.append("")

    lines.extend(["## Per-Material Notes", ""])
    if notes:
        for note in notes:
            lines.append(f"- `{note.get('backend')}` / `{note.get('display_label')}`: {note.get('note')}")
    else:
        lines.append("- Per-material notes pending.")
    lines.append("")

    key_files = summary.get("key_files", {})
    lines.extend(["## Key Files", ""])
    for label, path in key_files.items():
        lines.append(f"- {label}: `{path}`")
    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "This is an integration preview, not a prompt tuning round. SD1.5 uses Plan A only: Fix3 positive prompts and Fix3 negative prompts as-is. StableMaterials is included as a parallel backend preview. Manual visual contact-sheet review remains required for art-direction conclusions.",
            "",
            "## Report Files",
            "",
            f"- Summary JSON: `{summary.get('summary_path')}`",
            f"- Report: `{summary.get('report_path')}`",
        ]
    )
    return "\n".join(lines) + "\n"
