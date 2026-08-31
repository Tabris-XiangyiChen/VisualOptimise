"""Shared D6F mainline D6E-style prompt brief helpers extracted from D6F-A3-Fix3.

The prompt contract, validator, and compilers are preserved for behavior parity.
"""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any

from visualoptimise.llm_artifacts import call_one_llm_raw, parse_json_response
from visualoptimise.artifacts import read_json, write_json, write_text

FIX2_SUFFIX = "d6f_a3_fix2_preview_image_generation"

D6E_SUFFIX = "d6e_fix1_retest_dualpass_keyword_surface_appearance"

SCHEMA_VERSION = "material_prompt_briefs_v4"
STABLEMATERIALS_POSITIVE_MIN_WORDS = 8
STABLEMATERIALS_POSITIVE_MAX_WORDS = 16
STABLEMATERIALS_COMPONENT_MAX_WORDS = 8
STABLEMATERIALS_RUNTIME_TOKEN_LIMIT = 55

GENERIC_FIRST_TAGS = {
    "front facing closeup surface",
    "front-facing close-up surface",
    "top down closeup surface",
    "top-down close-up surface",
    "front facing panel surface",
    "front-facing panel surface",
    "tileable material texture",
    "tileable surface texture",
}

WEAK_META_TAGS = {
    "solid_stone material surface",
    "ground_organic material surface",
    "water_like material surface",
    "reusable game material",
    "map-derived restrained palette",
    "coherent palette",
    "natural color variation",
    "restrained surface variation",
}

RICHNESS_CATEGORIES = {
    "surface_structure",
    "edge_or_joint_detail",
    "wear_or_weathering",
    "moisture_or_moss",
    "color_variation",
    "fine_surface_detail",
    "roughness",
    "grain",
    "ripples",
    "mineral_variation",
    "organic_microdetail",
}

GENERIC_IDENTITY_STOPWORDS = {
    "material",
    "surface",
    "texture",
    "solid",
    "like",
    "basic",
    "mat",
    "tile",
    "tiles",
    "closeup",
    "close",
    "front",
    "facing",
    "top",
    "down",
    "and",
}

GENERIC_CARRIER_IDENTITY_TOKENS = {
    "ground",
    "surface",
    "texture",
    "material",
    "floor",
    "wall",
    "panel",
    "tile",
    "tiles",
}

def resolve_runs_dir(output_dir: Path) -> Path:
    return output_dir if output_dir.name == "runs" else output_dir / "runs"

def build_llm2_system_prompt() -> str:
    return "\n".join(
        [
            "You are a backend-specific prompt-brief generator for a procedural game visual asset pipeline.",
            "Return exactly one valid JSON object and no markdown.",
            "",
            "Your job is to convert the provided dynamic map-derived material evidence into prompt briefs for two generation backends:",
            "1. SD1.5 generic image generation.",
            "2. StableMaterials material-map generation.",
            "",
            "General rules:",
            "- Use only the material evidence provided in the user payload.",
            "- Do not use knowledge from previous experiments, hidden project history, old material slot lists, old prompt hints, or implementation round names.",
            "- Do not mention internal experiment names.",
            "- Do not output final compiled prompts.",
            "- Do not output UE paths, mesh IDs, texture paths, generation parameters, or file paths.",
            "- Do not change symbol semantics, tile roles, mesh roles, material grouping, or material slot assignments.",
            "- Do not describe map location, gameplay role, object placement, story context, or complete scenes.",
            "- Do not invent unsupported material identities.",
            "- If source evidence contains contextual, symbolic, object-role, mesh-role, gameplay, or non-surface terms, do not place them in SD1.5 positive tags or runtime negative terms. Record them only in audit-only or rejected fields when necessary.",
            "- Never copy an object, structure, prop, landmark, or container term into SD1.5 positive_tags merely because it appears in the legend, material category, raw clue, or mesh description.",
            "- Object or structure terms such as bridge, stairs, steps, door, gate, frame, river, channel, wall block, complete structure, room, or prop belong in context or rejected fields, not as positive material identity.",
            "- Describe reusable surface appearance using substance, colour, grain, mineral variation, roughness, joints, cracks, wear, moisture, ripples, or fine surface detail.",
            "",
            "SD1.5 brief rules:",
            "- SD1.5 output is a keyword-list prompt brief for a visual surface appearance image.",
            "- Do not write full sentences.",
            "- Do not write explanatory clauses.",
            "- Do not write appearance_goal sentences.",
            "- Do not write 'A view of', 'The image should', or similar sentence forms.",
            "- Do not use semicolons.",
            "- Do not use 'or' alternatives in positive_tags.",
            "- Do not put negative wording inside positive_tags.",
            "- Do not include 'no', 'not', or 'without' in positive_tags.",
            "- positive_tags must contain only short desired visual tags.",
            "- Each positive tag should be 2 to 8 words.",
            "- Use 6 to 10 positive tags per material.",
            "- surface_orientation is geometry evidence, not a literal SD1.5 prompt phrase and not a camera tag.",
            "- Use surface_orientation only to choose natural material-capture wording. Do not copy or paraphrase enum wording such as horizontal surface, vertical surface, panel surface, liquid surface, or sloped surface into positive_tags.",
            "- The first positive tag must combine natural material-capture wording with the specific material identity from the evidence.",
            "- For horizontal materials, prefer natural capture wording such as top-down close-up worn stone surface when compatible with the evidence.",
            "- For vertical materials, prefer natural capture wording such as front-facing close-up natural stone wall surface when compatible with the evidence.",
            "- For panel materials, prefer natural capture wording such as front-facing close-up aged wooden panel surface when compatible with the evidence.",
            "- For liquid materials, prefer natural capture wording such as top-down close-up shallow blue-green water surface when compatible with the evidence.",
            "- For sloped materials, describe the worn material surface without naming stairs, steps, slope objects, or complete structural geometry.",
            "- The first positive tag must contain at least one exact whole-word material identity token copied from the current material evidence, preferably from material_identity_coarse.",
            "- If material_identity_coarse is too generic, use an exact material-bearing token from the current canonical material identity evidence.",
            "- Do not rely only on synonyms or morphological variants in the first positive tag. For example, if the evidence contains 'wood', the first tag must contain the exact word 'wood', not only 'wooden'.",
            "- The first positive tag must not be a generic orientation-only phrase or a generic texture phrase.",
            "- Do not copy enum text such as horizontal_surface, vertical_surface, panel_surface, liquid_surface, or sloped_surface literally into positive_tags.",
            "- Do not invent a surface orientation that conflicts with the catalog-provided surface_orientation.",
            "- If the material is meant to repeat on a simple surface, include a repeat-friendly tag such as tileable or seamless only when compatible with the provided target.",
            "- Tileable or seamless tags must not appear as tag 1 or tag 2. Prefer placing them after the primary material identity and concrete surface detail tags.",
            "- Include 2 to 4 intrinsic visual richness tags per material. These should describe surface structure, edge or joint detail, wear, weathering, moisture, moss, colour variation, fine detail, roughness, grain, ripples, mineral variation, or organic microdetail when supported by the evidence or style.",
            "- negative_terms must be short terms only, not full negative sentences.",
            "- negative_terms should be sparse and conservative.",
            "- negative_terms should describe unwanted visual failure modes only when clearly useful.",
            "- Do not use a bare target material identity word as a negative term. A multi-word state or contrast phrase may contain an identity token when it excludes a visual variant without removing the material itself.",
            "- Do not put mesh role, gameplay role, map usage, or context-only terms into runtime negative_terms. Put uncertain context terms into audit_only_context_terms instead.",
            "- Every exact term from context_clues_for_prompt_llm is audit-only evidence. Never copy it, paraphrase it, or derive a structural noun from it into runtime negative_terms or stablematerials.avoid_terms.",
            "- Runtime negative terms must describe visual failure states, not the absence of the target object, structure, tile, role, or map context.",
            "",
            "StableMaterials brief rules:",
            "- StableMaterials output remains material-oriented.",
            "- It should be more material-specific than SD1.5, but still concise.",
            "- Keep the combined StableMaterials runtime prompt within the configured token budget. Individual fields may vary in word count; prefer concise material-only phrases.",
            "- The combined StableMaterials runtime prompt made from positive_phrase, surface_structure, and color_palette must stay under 55 CLIP tokens.",
            "- Write compact material-only phrases, not full sentences.",
            "- Include only material identity, main surface structure, colour or condition, and one fine detail.",
            "- Do not reuse the SD1.5 tag list verbatim.",
            "- Do not add scene, camera, map-location, gameplay, or object-placement language.",
            "- Do not include negative prompt text unless the schema asks for avoid_terms.",
            "",
            "Source evidence rules:",
            "- A material slot may cover multiple symbols, but the base prompt should use only primary_prompt_symbol and prompt_source_symbols.",
            "- Excluded detail symbols may share the material slot, but must not enter the base SD1.5 positive tags.",
            "- Context clues are for understanding only. Convert them into reusable material-surface language only when they clearly describe intrinsic surface appearance.",
            "- Secondary descriptors such as damp, worn, dirty, cracked, mossy, rusty, or aged may be used as subtle surface details, not as the primary material identity unless the material evidence says so.",
            "- If the evidence is ambiguous, choose one clean material interpretation and record the ambiguity in warnings. Do not use 'or' inside positive_tags.",
            "- Do not filter, remove, or reject small dot markers solely because they are symbolic. If used, keep them as ordinary source evidence and let validation report them separately.",
            "",
            "Python is the final resolver and compiler.",
            "Your output is a structured prompt brief only.",
        ]
    )

def build_llm2_user_prompt(prompt_input: dict[str, Any]) -> str:
    schema = {
        "schema_version": SCHEMA_VERSION,
        "map_id": "...",
        "backend_prompt_briefs": [
            {
                "material_slot_id": "...",
                "canonical_material_id": "...",
                "prompt_ready_surface_terms": ["..."],
                "context_terms_used_for_understanding": ["..."],
                "audit_only_context_terms": ["..."],
                "rejected_prompt_terms": ["..."],
                "sd15": {
                    "target": "surface_appearance_image",
                    "positive_tags": ["..."],
                    "richness_tags": [
                        {
                            "tag": "...",
                            "category": "surface_structure | edge_or_joint_detail | wear_or_weathering | moisture_or_moss | color_variation | fine_surface_detail | roughness | grain | ripples | mineral_variation | organic_microdetail",
                        }
                    ],
                    "negative_terms": ["..."],
                    "audit_only_context_terms": ["..."],
                    "rejected_source_terms": [{"term": "...", "reason": "context_or_non_surface_term"}],
                    "phrase_style": "keyword_list_surface_appearance",
                },
                "stablematerials": {
                    "positive_phrase": "...",
                    "surface_structure": "...",
                    "color_palette": "...",
                    "detail_scale": "fine | small | small to medium | medium",
                    "avoid_terms": ["..."],
                    "phrase_style": "detailed_material_only_phrase",
                },
                "confidence": 0.0,
            }
        ],
        "warnings": [],
    }
    return "\n".join(
        [
            "Produce structured backend prompt briefs after Python has already resolved map structure, mesh selections, material groups, and dynamic material slots.",
            "",
            "Use only the provided dynamic material evidence.",
            "Do not alter material slot assignments.",
            "Do not create new material slots.",
            "Do not remove material slots.",
            "Do not output final prompts, generation parameters, mesh IDs, UE paths, texture paths, or file paths.",
            "",
            "Output JSON with this structure:",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "",
            "Hard rules:",
            "- Return schema_version exactly material_prompt_briefs_v4.",
            "- Return map_id exactly as provided.",
            "- backend_prompt_briefs must be an array.",
            "- Include exactly one backend_prompt_briefs entry for every material slot in dynamic_material_slot_evidence_v3.",
            "- Copy material_slot_id and canonical_material_id exactly.",
            "- Treat surface_orientation as read-only catalog evidence. Do not echo it as an SD1.5 output field.",
            "- SD1.5 positive_tags must contain 6 to 10 tags.",
            "- SD1.5 positive_tags must be desired visual tags only.",
            "- SD1.5 positive_tags must not contain no, not, without, semicolons, final sentence punctuation, or 'or' alternatives.",
            "- surface_orientation is read-only geometry evidence, not a literal output phrase or camera tag.",
            "- The first SD1.5 positive tag must combine natural material-capture wording with an exact material identity from the current evidence.",
            "- Do not start the first tag with horizontal surface, vertical surface, panel surface, liquid surface, sloped surface, or a close paraphrase of an enum label.",
            "- Use natural capture wording appropriate to the supplied orientation, such as top-down close-up for horizontal or liquid surfaces and front-facing close-up for vertical or panel surfaces.",
            "- Do not use stairs, steps, bridge, door, gate, frame, river, channel, or complete prop as the positive material identity. Move object or structure terms to context_terms_used_for_understanding, audit_only_context_terms, or rejected_prompt_terms.",
            "- The first SD1.5 positive tag must contain at least one exact whole-word material identity token copied from the current slot evidence.",
            "- Prefer an exact token from material_identity_coarse. Do not use only a synonym or morphological variant of that token.",
            "- The first SD1.5 positive tag must not be a generic view-only phrase.",
            "- Tileable or seamless must not appear in the first or second positive tag.",
            "- Each material must have 2 to 4 richness_tags.",
            "- richness_tags must also appear in positive_tags.",
            "- Negative terms must be sparse and conservative.",
            "- Negative terms must not duplicate positive_tags.",
            "- Negative terms must not exactly equal the target material identity or a complete multi-word identity phrase. A multi-word state or contrast phrase may contain an identity token when it excludes a visual variant without removing the material itself.",
            "- Do not put current evidence context-only source terms into runtime negative_terms when uncertain; place them in audit_only_context_terms.",
            "- Treat every exact context clue and every structural noun derived from a context clue as audit-only. Do not place it in sd15.negative_terms or stablematerials.avoid_terms.",
            "- If retry feedback reports a context-clue conflict, remove that source term from runtime negatives and keep it only in audit_only_context_terms or rejected_source_terms.",
            "- Keep the combined StableMaterials runtime prompt within the configured token budget. Do not optimize by isolated word counts for individual fields.",
            "- The combined StableMaterials runtime prompt made from positive_phrase, surface_structure, and color_palette must stay under 55 approximate CLIP tokens.",
            "- Do not use old material_slot_evidence, old suggested_prompt_hint, fixed old material slots, or previous experiment knowledge.",
            "- Do not use original decorative symbols that are not present in the provided clean-map evidence.",
            "- Do not filter, remove, or reject small dot markers solely because of the phrase small dot markers.",
            "",
            "Input payload:",
            json.dumps(prompt_input, ensure_ascii=False, indent=2),
        ]
    )

def call_llm2_until_valid(
    pipeline: Any,
    request_payload: dict[str, Any],
    prompt_input: dict[str, Any],
    paths: dict[str, Path],
    max_attempts: int,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    base_messages = list(request_payload["messages"])
    last_raw = ""
    last_parsed: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        payload = dict(request_payload)
        payload["messages"] = list(base_messages)
        if attempts:
            feedback = {
                "previous_validation_errors": attempts[-1].get("validation", {}).get("summary", {}).get("errors", []),
                "instruction": "Repair the JSON and satisfy all validation rules. If first_positive_tag_failed, preserve the required view phrase and copy at least one exact whole-word material identity token from the current slot evidence into the first positive tag. Do not use only a synonym or morphological variant. Return only the corrected JSON object.",
            }
            write_json(paths["response"] / f"llm2_retry_feedback_after_attempt_{attempt - 1}.json", feedback)
            payload["messages"] = payload["messages"] + [{"role": "user", "content": json.dumps(feedback, ensure_ascii=False, indent=2)}]
        write_json(paths["request"] / f"llm2_request_attempt_{attempt}.json", payload)
        raw_received = False
        parse_succeeded = False
        response_started = time.perf_counter()
        response_elapsed_seconds: float | None = None
        try:
            raw = call_one_llm_raw(pipeline.settings, pipeline.root, payload)
            response_elapsed_seconds = round(time.perf_counter() - response_started, 6)
            raw_received = True
            write_text(paths["response"] / f"llm2_raw_response_attempt_{attempt}.txt", raw)
            parsed = parse_json_response(raw)
            parse_succeeded = True
        except Exception as exc:
            if response_elapsed_seconds is None:
                response_elapsed_seconds = round(time.perf_counter() - response_started, 6)
            attempts.append({"attempt": attempt, "response_elapsed_seconds": response_elapsed_seconds, "ok": False, "error": str(exc)})
            write_text(paths["response"] / f"llm2_error_attempt_{attempt}.txt", str(exc))
            if raw_received and not parse_succeeded:
                write_json(
                    paths["response"] / f"llm2_parse_audit_attempt_{attempt}.json",
                    {
                        "schema_version": "llm_parse_audit_v1",
                        "stage": "llm2",
                        "attempt": attempt,
                        "raw_response_saved": True,
                        "parse_passed": False,
                        "validator_called": False,
                        "errors": [f"{type(exc).__name__}: {exc}"],
                    },
                )
            continue
        last_raw = raw
        last_parsed = parsed
        write_json(paths["response"] / f"llm2_parsed_response_attempt_{attempt}.json", parsed)
        validation = validate_prompt_briefs(parsed, prompt_input)
        write_json(paths["response"] / f"llm2_validation_attempt_{attempt}.json", validation)
        attempts.append({"attempt": attempt, "response_elapsed_seconds": response_elapsed_seconds, "ok": validation["summary"]["passed"], "validation": validation})
        if validation["summary"]["passed"]:
            attempt_summary = {"llm2_called": True, "attempts": attempt, "retry_count": attempt - 1, "attempt_log": attempts}
            write_json(paths["response"] / "llm2_stage_summary.json", build_llm2_stage_summary(attempts, "passed"))
            return parsed, raw, attempt_summary
    attempt_summary = {"llm2_called": True, "attempts": len(attempts), "retry_count": max(0, len(attempts) - 1), "attempt_log": attempts}
    write_json(paths["response"] / "llm2_attempts_summary.json", attempt_summary)
    write_json(paths["response"] / "llm2_stage_summary.json", build_llm2_stage_summary(attempts, "failed"))
    if last_parsed:
        return last_parsed, last_raw, attempt_summary
    raise RuntimeError("LLM2 failed before returning parseable JSON.")


def build_llm2_stage_summary(attempts: list[dict[str, Any]], status: str) -> dict[str, Any]:
    first_passed = bool(attempts and attempts[0].get("ok"))
    final_passed = bool(attempts and attempts[-1].get("ok"))
    return {
        "schema_version": "llm_stage_attempt_summary_v1",
        "stage": "llm2",
        "status": status,
        "attempt_count": len(attempts),
        "retry_count": max(0, len(attempts) - 1),
        "first_attempt_passed": first_passed,
        "final_passed": final_passed,
        "attempts": attempts,
    }

def build_dry_run_briefs(source_files: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for slot in source_files["evidence"].get("material_slots", []):
        label = display_label(slot)
        first = natural_view_identity(slot)
        previous_terms = previous_terms_for_slot(source_files.get("previous_briefs", {}), slot["material_slot_id"])
        current_context_terms = {normalize_phrase(c.get("term", "")) for c in slot.get("context_clues_for_prompt_llm", [])}
        positive_tags = [first]
        for term in previous_terms:
            if (
                term.lower() not in {first.lower(), "tileable material texture", "tileable surface texture"}
                and normalize_phrase(term) not in current_context_terms
            ):
                positive_tags.append(term)
            if len(positive_tags) >= 4:
                break
        fallback_detail_tags = [
            f"fine {label.replace('_', ' ')} surface structure",
            f"subtle {label.replace('_', ' ')} wear",
        ]
        for tag in fallback_detail_tags:
            if len(positive_tags) >= 3:
                break
            if tag not in positive_tags:
                positive_tags.append(tag)
        positive_tags.append(f"tileable {label.replace('_', ' ')}")
        trailing_tags = [
            f"restrained {label.replace('_', ' ')} weathering",
            f"small scale {label.replace('_', ' ')} detail",
            f"natural {label.replace('_', ' ')} texture variation",
        ]
        for tag in trailing_tags:
            if len(positive_tags) >= 7:
                break
            if tag not in positive_tags:
                positive_tags.append(tag)
        richness = [{"tag": tag, "category": "surface_structure"} for tag in positive_tags[1:3]]
        items.append(
            {
                "material_slot_id": slot["material_slot_id"],
                "canonical_material_id": slot["canonical_material_id"],
                "prompt_ready_surface_terms": previous_terms[:6],
                "context_terms_used_for_understanding": [c.get("term", "") for c in slot.get("context_clues_for_prompt_llm", [])],
                "audit_only_context_terms": [c.get("term", "") for c in slot.get("context_clues_for_prompt_llm", [])],
                "rejected_prompt_terms": [],
                "sd15": {
                    "target": "surface_appearance_image",
                    "positive_tags": positive_tags[:8],
                    "richness_tags": richness[:3],
                    "negative_terms": [],
                    "audit_only_context_terms": [c.get("term", "") for c in slot.get("context_clues_for_prompt_llm", [])],
                    "rejected_source_terms": [],
                    "phrase_style": "keyword_list_surface_appearance",
                },
                "stablematerials": {
                    "positive_phrase": ", ".join(previous_terms[:4]) or first,
                    "surface_structure": previous_terms[0] if previous_terms else first,
                    "color_palette": "muted map-derived material tones",
                    "detail_scale": "small to medium",
                    "avoid_terms": [],
                    "phrase_style": "detailed_material_only_phrase",
                },
                "confidence": 1.0,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "map_id": source_files["prompt_input"]["map_id"], "backend_prompt_briefs": items, "warnings": ["dry_run_template_only"]}

def previous_terms_for_slot(previous_briefs: dict[str, Any], slot_id: str) -> list[str]:
    for item in previous_briefs.get("backend_prompt_briefs", []):
        if item.get("material_slot_id") == slot_id:
            terms = item.get("prompt_ready_surface_terms") or item.get("sd15", {}).get("positive_tags", [])
            return [str(term) for term in terms if str(term).strip()]
    return []

def validate_prompt_briefs(briefs: dict[str, Any], prompt_input: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []
    richness_rows: list[dict[str, Any]] = []
    negative_rows: list[dict[str, Any]] = []
    context_rows: list[dict[str, Any]] = []
    stablematerials_rows: list[dict[str, Any]] = []
    marker_rows: list[dict[str, Any]] = []
    source_slots = {slot["material_slot_id"]: slot for slot in prompt_input.get("material_slot_evidence", [])}
    items = briefs.get("backend_prompt_briefs", [])

    if briefs.get("schema_version") != SCHEMA_VERSION:
        errors.append({"error": "schema_version_mismatch", "actual": briefs.get("schema_version")})
    if briefs.get("map_id") != prompt_input.get("map_id"):
        errors.append({"error": "map_id_mismatch", "actual": briefs.get("map_id"), "expected": prompt_input.get("map_id")})
    if not isinstance(items, list):
        errors.append({"error": "backend_prompt_briefs must be an array"})
        items = []
    by_id = {item.get("material_slot_id"): item for item in items if isinstance(item, dict)}
    if set(by_id) != set(source_slots):
        errors.append({"error": "material_slot_set_mismatch", "actual": sorted(k for k in by_id if k), "expected": sorted(source_slots)})

    for slot_id, source in source_slots.items():
        item = by_id.get(slot_id)
        if not item:
            continue
        sd15 = item.get("sd15", {})
        tags = [str(tag).strip() for tag in sd15.get("positive_tags", []) if str(tag).strip()]
        lowered_tags = [normalize_phrase(tag) for tag in tags]
        negatives = [str(term).strip() for term in sd15.get("negative_terms", []) if str(term).strip()]
        context_terms = [str(c.get("term", "")).strip() for c in source.get("context_clues_for_prompt_llm", []) if str(c.get("term", "")).strip()]

        if item.get("canonical_material_id") != source.get("canonical_material_id"):
            errors.append({"material_slot_id": slot_id, "error": "canonical_material_id_mismatch"})
        if not (6 <= len(tags) <= 10):
            errors.append({"material_slot_id": slot_id, "error": "positive_tags must contain 6 to 10 tags", "count": len(tags)})

        first = tags[0] if tags else ""
        first_check = validate_first_tag(first, source)
        order_rows.append({"material_slot_id": slot_id, **first_check, "first_tag": first})
        if not first_check["passed"]:
            errors.append({"material_slot_id": slot_id, "error": "first_positive_tag_failed", "details": first_check})

        tile_positions = [index for index, tag in enumerate(lowered_tags) if "tileable" in tag or "seamless" in tag]
        early_tile_positions = [index for index in tile_positions if index < 2]
        tile_present = bool(tile_positions)
        tile_ok = tile_present and not early_tile_positions
        order_rows[-1]["tileability_positions"] = tile_positions
        order_rows[-1]["tileability_present"] = tile_present
        order_rows[-1]["tileability_passed"] = tile_ok
        if not tile_present:
            order_rows[-1]["tileability_missing_nonblocking"] = True
            order_rows[-1]["warnings"] = ["tileability_missing_will_be_added_by_compiler"]
        elif early_tile_positions:
            order_rows[-1]["tileability_missing_nonblocking"] = False
            errors.append(
                {
                    "material_slot_id": slot_id,
                    "error": "tileability_missing_or_too_early",
                    "positions": tile_positions,
                }
            )
        else:
            order_rows[-1]["tileability_missing_nonblocking"] = False

        weak_hits = sorted(set(lowered_tags) & WEAK_META_TAGS)
        if weak_hits:
            errors.append({"material_slot_id": slot_id, "error": "weak_meta_positive_tags", "hits": weak_hits})

        richness = sd15.get("richness_tags", [])
        richness_check = validate_richness_tags(richness, tags)
        richness_rows.append({"material_slot_id": slot_id, **richness_check})
        if not richness_check["passed"]:
            errors.append({"material_slot_id": slot_id, "error": "richness_tag_validation_failed", "details": richness_check})

        negative_check = validate_negative_terms(negatives, tags, source)
        negative_rows.append({"material_slot_id": slot_id, **negative_check})
        if not negative_check["passed"]:
            errors.append({"material_slot_id": slot_id, "error": "negative_prompt_validation_failed", "details": negative_check})

        context_check = validate_context_audit(negatives, context_terms)
        context_rows.append({"material_slot_id": slot_id, **context_check})
        if not context_check["passed"]:
            errors.append({"material_slot_id": slot_id, "error": "context_terms_in_runtime_negative", "details": context_check})

        stablematerials_check = validate_stablematerials_brief(item)
        stablematerials_rows.append({"material_slot_id": slot_id, **stablematerials_check})
        if not stablematerials_check["passed"]:
            errors.append({"material_slot_id": slot_id, "error": "stablematerials_length_validation_failed", "details": stablematerials_check})

        marker_hits = [tag for tag in tags if "small dot marker" in tag.lower() or normalize_phrase(tag) in {"dot markers", "markers"}]
        marker_rows.append({"material_slot_id": slot_id, "small_dot_marker_terms": marker_hits, "reported_only": True, "edited_or_rejected": False})

    summary = {
        "schema_version": "d6f_a3_fix3_validation_summary_v1",
        "passed": not errors,
        "error_count": len(errors),
        "errors": errors,
        "material_slot_count": len(source_slots),
        "llm1_called": False,
        "sd_webui_called": False,
        "stablematerials_called": False,
        "image_generation_called": False,
        "runtime_data_exported": False,
        "generated_package_exported": False,
        "ue_modified": False,
    }
    return {
        "summary": summary,
        "prompt_order": {
            "passed": not any(
                row.get("passed") is False
                or (row.get("tileability_passed") is False and not row.get("tileability_missing_nonblocking"))
                for row in order_rows
            ),
            "rows": order_rows,
        },
        "richness": {"passed": not any(row.get("passed") is False for row in richness_rows), "rows": richness_rows},
        "negative": {"passed": not any(row.get("passed") is False for row in negative_rows), "rows": negative_rows},
        "context_audit": {"passed": not any(row.get("passed") is False for row in context_rows), "rows": context_rows},
        "stablematerials_length": {"passed": not any(row.get("passed") is False for row in stablematerials_rows), "rows": stablematerials_rows},
        "symbolic_marker_audit": {"passed": True, "rows": marker_rows, "note": "small dot markers are reported only and do not cause rejection"},
    }

def validate_first_tag(first: str, source: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_phrase(first)
    identity_tokens = material_identity_tokens(source)
    has_identity = any(re.search(rf"\b{re.escape(token)}\b", normalized) for token in identity_tokens)
    generic = normalized in GENERIC_FIRST_TAGS or normalized.replace("-", " ") in GENERIC_FIRST_TAGS
    return {
        "passed": bool(first and has_identity and not generic),
        "surface_orientation": source.get("surface_orientation"),
        "surface_orientation_source": source.get("surface_orientation_source"),
        "surface_orientation_blocking": False,
        "warnings": [],
        "has_identity": has_identity,
        "generic_view_only": generic,
        "identity_tokens_checked": sorted(identity_tokens),
    }

def validate_richness_tags(richness: Any, positive_tags: list[str]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(richness, list):
        return {"passed": False, "errors": [{"error": "richness_tags must be array"}], "count": 0}
    if not (2 <= len(richness) <= 4):
        errors.append({"error": "richness_tags must contain 2 to 4 items", "count": len(richness)})
    positive_norm = {normalize_phrase(tag) for tag in positive_tags}
    rows = []
    for item in richness:
        if not isinstance(item, dict):
            errors.append({"error": "richness tag item must be object", "item": item})
            continue
        tag = str(item.get("tag", "")).strip()
        category = str(item.get("category", "")).strip()
        in_positive = normalize_phrase(tag) in positive_norm
        rows.append({"tag": tag, "category": category, "in_positive_tags": in_positive})
        if category not in RICHNESS_CATEGORIES:
            errors.append({"tag": tag, "error": "invalid_richness_category", "category": category})
        if not in_positive:
            errors.append({"tag": tag, "error": "richness tag is not present in positive_tags"})
    return {"passed": not errors, "errors": errors, "count": len(richness), "rows": rows}

def validate_negative_terms(negatives: list[str], positives: list[str], source: dict[str, Any]) -> dict[str, Any]:
    conflicts: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    positive_norm = [normalize_phrase(tag) for tag in positives]
    identity_signal = material_identity_signal(source)
    identity_phrases = identity_signal["phrases"]
    core_token_sources = identity_signal["core_tokens"]
    carrier_token_sources = identity_signal["carrier_tokens"]
    for term in negatives:
        norm = normalize_phrase(term)
        if norm in positive_norm:
            conflicts.append(
                {
                    "term": term,
                    "status": "blocked",
                    "error": "negative_duplicates_positive_tag",
                    "phrase_conflict_checked": sorted(identity_phrases),
                    "token_conflict_checked": {
                        "core_tokens": sorted(core_token_sources),
                        "carrier_tokens": sorted(carrier_token_sources),
                    },
                }
            )
            continue

        exact_phrase_hits = [phrase for phrase in sorted(identity_phrases) if norm == phrase]
        if exact_phrase_hits:
            conflicts.append(
                {
                    "term": term,
                    "status": "blocked",
                    "error": "negative_equals_material_identity_phrase",
                    "matched_identity_phrases": exact_phrase_hits,
                    "phrase_sources": {phrase: identity_phrases[phrase] for phrase in exact_phrase_hits},
                    "phrase_conflict_checked": sorted(identity_phrases),
                    "token_conflict_checked": {
                        "core_tokens": sorted(core_token_sources),
                        "carrier_tokens": sorted(carrier_token_sources),
                    },
                }
            )
            continue

        multiword_phrase_hits = [
            phrase
            for phrase in sorted(identity_phrases)
            if len(tokenize_identity_text(phrase)) > 1 and phrase_in_text(phrase, norm)
        ]
        if multiword_phrase_hits:
            conflicts.append(
                {
                    "term": term,
                    "status": "blocked",
                    "error": "negative_contains_multiword_material_identity_phrase",
                    "matched_identity_phrases": multiword_phrase_hits,
                    "phrase_sources": {phrase: identity_phrases[phrase] for phrase in multiword_phrase_hits},
                    "phrase_conflict_checked": sorted(identity_phrases),
                    "token_conflict_checked": {
                        "core_tokens": sorted(core_token_sources),
                        "carrier_tokens": sorted(carrier_token_sources),
                    },
                }
            )
            continue

        core_hits = [token for token in sorted(core_token_sources) if token_in_text(token, norm)]
        if core_hits:
            if len(tokenize_identity_text(norm)) == 1:
                conflicts.append(
                    {
                        "term": term,
                        "status": "blocked",
                        "error": "negative_contains_core_material_identity_token",
                        "matched_core_tokens": core_hits,
                        "identity_token_sources": {token: core_token_sources[token] for token in core_hits},
                        "phrase_conflict_checked": sorted(identity_phrases),
                        "token_conflict_checked": {
                            "core_tokens": sorted(core_token_sources),
                            "carrier_tokens": sorted(carrier_token_sources),
                        },
                    }
                )
            else:
                audits.append(
                    {
                        "term": term,
                        "status": "allowed_compound_negative",
                        "reason": "Contains core identity token(s) inside a different multi-word state or contrast phrase.",
                        "matched_identity_tokens": core_hits,
                        "ignored_tokens": core_hits,
                        "identity_token_sources": {token: core_token_sources[token] for token in core_hits},
                        "phrase_conflict_checked": sorted(identity_phrases),
                        "token_conflict_checked": {
                            "core_tokens": sorted(core_token_sources),
                            "carrier_tokens": sorted(carrier_token_sources),
                        },
                    }
                )
            continue

        carrier_hits = [token for token in sorted(carrier_token_sources) if token_in_text(token, norm)]
        if carrier_hits:
            audits.append(
                {
                    "term": term,
                    "status": "allowed",
                    "reason": "contains only generic carrier identity token, not core material token",
                    "ignored_tokens": carrier_hits,
                    "identity_token_sources": {token: carrier_token_sources[token] for token in carrier_hits},
                    "phrase_conflict_checked": sorted(identity_phrases),
                    "token_conflict_checked": {
                        "core_tokens": sorted(core_token_sources),
                        "carrier_tokens": sorted(carrier_token_sources),
                    },
                }
            )
    return {
        "passed": not conflicts,
        "conflicts": conflicts,
        "allowed_carrier_token_audits": audits,
        "identity_signal": identity_signal,
        "negative_terms": negatives,
    }

def validate_context_audit(negatives: list[str], context_terms: list[str]) -> dict[str, Any]:
    hits: list[dict[str, str]] = []
    negative_norm = [normalize_phrase(term) for term in negatives]
    for term in context_terms:
        norm = normalize_phrase(term)
        if norm and norm in negative_norm:
            hits.append({"term": term, "error": "current_evidence_context_term_in_runtime_negative"})
    return {"passed": not hits, "hits": hits, "current_context_terms_checked": context_terms}


def validate_stablematerials_brief(item: dict[str, Any]) -> dict[str, Any]:
    stablematerials = item.get("stablematerials", {})
    positive_phrase = str(stablematerials.get("positive_phrase", "")).strip()
    surface_structure = str(stablematerials.get("surface_structure", "")).strip()
    color_palette = str(stablematerials.get("color_palette", "")).strip()
    runtime_prompt = stablematerials_runtime_prompt(stablematerials)
    errors: list[dict[str, Any]] = []

    positive_words = count_english_words(positive_phrase)
    surface_words = count_english_words(surface_structure)
    palette_words = count_english_words(color_palette)
    runtime_tokens = approximate_clip_token_count(runtime_prompt)

    if runtime_tokens > STABLEMATERIALS_RUNTIME_TOKEN_LIMIT:
        errors.append(
            {
                "error": "stablematerials_runtime_prompt_token_limit_exceeded",
                "approx_token_count": runtime_tokens,
                "max_approx_tokens": STABLEMATERIALS_RUNTIME_TOKEN_LIMIT,
            }
        )

    return {
        "passed": not errors,
        "errors": errors,
        "positive_phrase_word_count": positive_words,
        "surface_structure_word_count": surface_words,
        "color_palette_word_count": palette_words,
        "runtime_prompt_approx_token_count": runtime_tokens,
        "runtime_prompt_max_approx_tokens": STABLEMATERIALS_RUNTIME_TOKEN_LIMIT,
        "runtime_prompt": runtime_prompt,
        "note": "Only the combined runtime token budget is blocking. Individual word counts are retained for diagnostics and do not block SD1.5 or StableMaterials.",
    }


def stablematerials_runtime_prompt(stablematerials: dict[str, Any]) -> str:
    parts = [
        stablematerials.get("positive_phrase"),
        stablematerials.get("surface_structure"),
        stablematerials.get("color_palette"),
    ]
    return ", ".join(str(part).strip() for part in parts if str(part or "").strip())


def count_english_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?", text))


def approximate_clip_token_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+|[^\sA-Za-z0-9]", text))


def normalize_prompt_briefs_for_contract(briefs: dict[str, Any], prompt_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply bounded, reportable prompt-contract fixes after LLM retries.

    This is intentionally narrow: it only fixes tileability placement/missing
    anchors and removes weak meta positive tags when removal keeps the existing
    Fix3 validation shape valid.
    """
    normalized = copy.deepcopy(briefs)
    source_slots = {slot["material_slot_id"]: slot for slot in prompt_input.get("material_slot_evidence", [])}
    items = normalized.get("backend_prompt_briefs", [])
    if not isinstance(items, list):
        return normalized, {
            "schema_version": "bounded_prompt_contract_normalization_v1",
            "applied": False,
            "summary": {"applied": False, "slot_count": 0, "action_count": 0},
            "slot_reports": [],
            "errors": ["backend_prompt_briefs is not an array; normalization skipped"],
        }

    reports: list[dict[str, Any]] = []
    action_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        slot_id = str(item.get("material_slot_id", ""))
        source = source_slots.get(slot_id)
        sd15 = item.get("sd15")
        if not source or not isinstance(sd15, dict):
            continue
        before_tags = [str(tag).strip() for tag in sd15.get("positive_tags", []) if str(tag).strip()]
        before_richness = copy.deepcopy(sd15.get("richness_tags", []))
        tags = list(before_tags)
        actions: list[dict[str, Any]] = []

        tags, richness, weak_actions = remove_weak_meta_tags_when_safe(tags, before_richness)
        actions.extend(weak_actions)
        tags, tile_actions = normalize_tileability_tags(tags, source)
        actions.extend(tile_actions)
        richness, richness_actions = remove_uncompiled_richness_tags_when_safe(tags, richness)
        actions.extend(richness_actions)

        if actions:
            action_count += len(actions)
            sd15["positive_tags"] = tags
            sd15["richness_tags"] = richness
            reports.append(
                {
                    "material_slot_id": slot_id,
                    "canonical_material_id": item.get("canonical_material_id"),
                    "actions": actions,
                    "before_positive_tags": before_tags,
                    "after_positive_tags": tags,
                    "before_richness_tags": before_richness,
                    "after_richness_tags": richness,
                }
            )

    report = {
        "schema_version": "bounded_prompt_contract_normalization_v1",
        "applied": action_count > 0,
        "summary": {
            "applied": action_count > 0,
            "slot_count": len(source_slots),
            "modified_slot_count": len(reports),
            "action_count": action_count,
            "policy": [
                "Remove weak meta positive tags only when tag and richness counts remain valid.",
                "Move tileability tags out of positions 0 and 1.",
                "Add one material-specific tileability tag after concrete material tags when missing.",
                "Remove richness metadata tags that were not compiled into positive_tags when enough richness metadata remains.",
                "Do not add new material richness or context semantics.",
            ],
        },
        "slot_reports": reports,
        "errors": [],
    }
    return normalized, report


def remove_weak_meta_tags_when_safe(tags: list[str], richness: Any) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    richness_items = [item for item in richness if isinstance(item, dict)]
    weak_norms = {normalize_phrase(tag) for tag in tags if normalize_phrase(tag) in WEAK_META_TAGS}
    if not weak_norms:
        return tags, richness_items, []

    candidate_tags = [tag for tag in tags if normalize_phrase(tag) not in weak_norms]
    candidate_richness = [item for item in richness_items if normalize_phrase(item.get("tag", "")) not in weak_norms]
    removed = [tag for tag in tags if normalize_phrase(tag) in weak_norms]
    if 6 <= len(candidate_tags) <= 10 and 2 <= len(candidate_richness) <= 4:
        return candidate_tags, candidate_richness, [
            {
                "action": "removed_weak_meta_positive_tags",
                "removed_tags": removed,
                "reason": "Weak metadata-style tags are too abstract for SD1.5 and removal preserved valid tag/richness counts.",
            }
        ]
    return tags, richness_items, [
        {
            "action": "weak_meta_positive_tags_not_removed",
            "weak_tags": removed,
            "reason": "Removal would break the required 6-10 positive tags or 2-4 richness tags contract.",
        }
    ]


def remove_uncompiled_richness_tags_when_safe(tags: list[str], richness: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    positive_norm = {normalize_phrase(tag) for tag in tags}
    uncompiled = [
        item
        for item in richness
        if normalize_phrase(item.get("tag", "")) not in positive_norm
    ]
    if not uncompiled:
        return richness, []

    candidate = [
        item
        for item in richness
        if normalize_phrase(item.get("tag", "")) in positive_norm
    ]
    if 2 <= len(candidate) <= 4:
        return candidate, [
            {
                "action": "removed_uncompiled_richness_tags",
                "removed_tags": [item.get("tag") for item in uncompiled],
                "reason": "These richness metadata tags were not present in positive_tags and therefore would not affect image generation; removal preserved the 2-4 richness tag contract.",
            }
        ]

    return richness, [
        {
            "action": "uncompiled_richness_tags_not_removed",
            "uncompiled_tags": [item.get("tag") for item in uncompiled],
            "reason": "Removal would break the required 2-4 richness tag contract.",
        }
    ]


def normalize_tileability_tags(tags: list[str], source: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    actions: list[dict[str, Any]] = []
    tile_indices = [index for index, tag in enumerate(tags) if is_tileability_tag(tag)]
    if tile_indices and any(index < 2 for index in tile_indices):
        moved = [tags[index] for index in tile_indices if index < 2]
        remaining = [tag for index, tag in enumerate(tags) if index not in tile_indices or index >= 2]
        tag_to_move = moved[0]
        insert_index = min(3, len(remaining))
        remaining.insert(insert_index, tag_to_move)
        tags = remaining[:10]
        actions.append(
            {
                "action": "moved_tileability_tag_after_tag_2",
                "moved_tag": tag_to_move,
                "original_positions": tile_indices,
                "new_position": insert_index,
            }
        )
        tile_indices = [index for index, tag in enumerate(tags) if is_tileability_tag(tag)]

    if not tile_indices:
        tag = material_specific_tileability_tag(source)
        if len(tags) < 10:
            tags.append(tag)
            actions.append({"action": "added_missing_tileability_tag", "added_tag": tag, "position": len(tags) - 1})
        else:
            tags[-1] = tag
            actions.append(
                {
                    "action": "replaced_last_tag_with_missing_tileability_tag",
                    "added_tag": tag,
                    "position": len(tags) - 1,
                    "reason": "positive_tags already had the maximum count of 10",
                }
            )
    return tags, actions


def is_tileability_tag(tag: str) -> bool:
    normalized = normalize_phrase(tag)
    return "tileable" in normalized or "seamless" in normalized


def material_specific_tileability_tag(source: dict[str, Any]) -> str:
    """Return the generic SD1.5 fallback for a missing tileability cue.

    The source is intentionally not inspected here: material identity remains
    LLM-provided, while this phrase is only a backend repeatability anchor.
    """
    return "tileable material texture"


def material_identity_tokens(source: dict[str, Any]) -> set[str]:
    return set(material_identity_token_sources(source))


def material_identity_signal(source: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    token_sources = material_identity_token_sources(source)
    core_tokens = {
        token: sources
        for token, sources in token_sources.items()
        if token not in GENERIC_CARRIER_IDENTITY_TOKENS
    }
    carrier_tokens = {
        token: sources
        for token, sources in token_sources.items()
        if token in GENERIC_CARRIER_IDENTITY_TOKENS
    }
    return {
        "phrases": material_identity_phrase_sources(source),
        "tokens": token_sources,
        "core_tokens": core_tokens,
        "carrier_tokens": carrier_tokens,
    }


def material_identity_phrase_sources(source: dict[str, Any]) -> dict[str, list[str]]:
    phrase_sources: dict[str, set[str]] = {}
    for source_name, chunk in material_identity_chunks(source):
        phrase = normalize_identity_phrase(chunk)
        if not phrase:
            continue
        tokens = set(tokenize_identity_text(phrase))
        if tokens and tokens.issubset(GENERIC_CARRIER_IDENTITY_TOKENS):
            continue
        phrase_sources.setdefault(phrase, set()).add(source_name)
    return {phrase: sorted(sources) for phrase, sources in sorted(phrase_sources.items())}


def material_identity_token_sources(source: dict[str, Any]) -> dict[str, list[str]]:
    """Return hard material-identity tokens used by prompt conflict checks.

    The validator intentionally avoids full legend descriptions here: those
    often contain placement context such as "set into stone walls" or
    "near water", which should not make `stone` or `water` protected identity
    terms for an unrelated material.
    """
    token_sources: dict[str, set[str]] = {}
    for source_name, chunk in material_identity_chunks(source):
        for token in tokenize_identity_text(chunk):
            token_sources.setdefault(token, set()).add(source_name)
    return {token: sorted(sources) for token, sources in sorted(token_sources.items())}


def material_identity_chunks(source: dict[str, Any]) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = [
        ("canonical_material_id", str(source.get("canonical_material_id", ""))),
        ("material_identity_coarse", str(source.get("material_identity_coarse", ""))),
    ]
    for clue_key in ("context_clues_for_prompt_llm", "context_clues"):
        for clue in source.get(clue_key, []):
            if not isinstance(clue, dict):
                continue
            if str(clue.get("reason", "")).strip().lower() == "material_identity":
                chunks.append((f"{clue_key}.material_identity", str(clue.get("term", ""))))
    return chunks


def normalize_identity_phrase(value: str) -> str:
    return " ".join(tokenize_identity_text(value))


def tokenize_identity_text(value: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z-]+", str(value).replace("_", " ")):
        lowered = token.lower()
        if lowered not in GENERIC_IDENTITY_STOPWORDS and len(lowered) > 2:
            tokens.append(lowered)
    return tokens


def phrase_in_text(phrase: str, text: str) -> bool:
    return bool(phrase and re.search(rf"\b{re.escape(phrase)}\b", text))


def token_in_text(token: str, text: str) -> bool:
    return bool(token and re.search(rf"\b{re.escape(token)}\b", text))

def compile_sd15_prompts(briefs: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    source_by_id = {slot["material_slot_id"]: slot for slot in evidence.get("material_slots", [])}
    rows = []
    for item in briefs.get("backend_prompt_briefs", []):
        slot_id = item["material_slot_id"]
        sd15 = item.get("sd15", {})
        positives = [str(tag).strip() for tag in sd15.get("positive_tags", []) if str(tag).strip()]
        negatives = [str(term).strip() for term in sd15.get("negative_terms", []) if str(term).strip()]
        rows.append(
            {
                "material_slot_id": slot_id,
                "display_label": display_label(source_by_id.get(slot_id, item)),
                "canonical_material_id": item.get("canonical_material_id"),
                "positive_prompt": ", ".join(positives),
                "negative_prompt": ", ".join(negatives),
                "positive_tags": positives,
                "negative_terms": negatives,
                "richness_tags": sd15.get("richness_tags", []),
                "source": "d6f_a3_fix3_llm2_prompt_briefs_v4",
            }
        )
    return {"schema_version": "compiled_sd15_prompts_v4", "backend": "sd15_a1111_txt2img", "prompts": rows}

def compile_stablematerials_prompts(briefs: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    source_by_id = {slot["material_slot_id"]: slot for slot in evidence.get("material_slots", [])}
    rows = []
    for item in briefs.get("backend_prompt_briefs", []):
        slot_id = item["material_slot_id"]
        sm = item.get("stablematerials", {})
        rows.append(
            {
                "material_slot_id": slot_id,
                "display_label": display_label(source_by_id.get(slot_id, item)),
                "canonical_material_id": item.get("canonical_material_id"),
                "positive_prompt": stablematerials_runtime_prompt(sm),
                "negative_prompt": None,
                "avoid_terms": sm.get("avoid_terms", []),
                "source": "d6f_a3_fix3_llm2_prompt_briefs_v4",
            }
        )
    return {"schema_version": "compiled_stablematerials_prompts_v4", "backend": "stablematerials_lcm", "prompts": rows}

def build_prompt_comparison(output_dir: Path, map_id: str, fix3_compiled: dict[str, Any]) -> dict[str, Any]:
    fix2 = load_latest_compiled(output_dir, map_id, FIX2_SUFFIX, "02_compiled_prompts", "compiled_sd15_prompts.json")
    d6e = load_latest_compiled(output_dir, map_id, D6E_SUFFIX, "02_llm/pass_b/compiled_prompts", "compiled_sd15_keyword_list_prompts.json")
    rows = []
    for fix3 in fix3_compiled.get("prompts", []):
        label = fix3.get("display_label")
        fix2_match = find_prompt_by_label(fix2, label)
        d6e_match = find_prompt_by_label(d6e, label)
        rows.append(
            {
                "display_label": label,
                "material_slot_id": fix3.get("material_slot_id"),
                "d6e_positive_prompt": d6e_match.get("positive_prompt") if d6e_match else None,
                "fix2_positive_prompt": fix2_match.get("positive_prompt") if fix2_match else None,
                "fix3_positive_prompt": fix3.get("positive_prompt"),
                "d6e_negative_prompt": d6e_match.get("negative_prompt") if d6e_match else None,
                "fix2_negative_prompt": fix2_match.get("negative_prompt") if fix2_match else None,
                "fix3_negative_prompt": fix3.get("negative_prompt"),
                "fix3_first_tag": fix3.get("positive_tags", [""])[0],
                "fix3_tileability_index": next((i for i, tag in enumerate(fix3.get("positive_tags", [])) if "tileable" in tag.lower() or "seamless" in tag.lower()), None),
                "fix3_richness_count": len(fix3.get("richness_tags", [])),
            }
        )
    return {
        "schema_version": "d6f_a3_fix3_prompt_comparison_v1",
        "map_id": map_id,
        "d6e_source_found": d6e is not None,
        "fix2_source_found": fix2 is not None,
        "rows": rows,
    }

def load_latest_compiled(output_dir: Path, map_id: str, suffix: str, rel_dir: str, filename: str) -> dict[str, Any] | None:
    candidates = sorted(resolve_runs_dir(output_dir).glob(f"*_{map_id}_{suffix}"))
    for run in reversed(candidates):
        path = run / rel_dir / filename
        if path.exists():
            return read_json(path)
    return None

def find_prompt_by_label(compiled: dict[str, Any] | None, label: str | None) -> dict[str, Any] | None:
    if not compiled or not label:
        return None
    for item in compiled.get("prompts", []):
        if item.get("display_label") == label or item.get("slot_id") == label or item.get("material_slot_id") == label:
            return item
    if label == "water":
        for item in compiled.get("prompts", []):
            if item.get("material_slot_id") in {"water", "mat_shallow_water_material"}:
                return item
    return None

def build_comparison_report(comparison: dict[str, Any]) -> str:
    lines = [
        "# D6F-A3-Fix3 Prompt Comparison",
        "",
        f"- D6E source found: `{comparison['d6e_source_found']}`",
        f"- Fix2 source found: `{comparison['fix2_source_found']}`",
        "",
        "| Material | D6E positive | Fix2 positive | Fix3 positive | Fix3 tileability index | Richness count |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for row in comparison["rows"]:
        lines.append(
            "| {display_label} | {d6e} | {fix2} | {fix3} | {tile} | {rich} |".format(
                display_label=row["display_label"],
                d6e=md_cell(row.get("d6e_positive_prompt")),
                fix2=md_cell(row.get("fix2_positive_prompt")),
                fix3=md_cell(row.get("fix3_positive_prompt")),
                tile=row.get("fix3_tileability_index"),
                rich=row.get("fix3_richness_count"),
            )
        )
    return "\n".join(lines) + "\n"

def build_prior_usage_audit(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    text = "\n".join([system_prompt, user_prompt])
    return {
        "schema_version": "d6f_a3_fix3_prior_usage_audit_v1",
        "passed": True,
        "old_material_slot_rules_used_as_evidence": False,
        "old_material_slot_evidence_file_used": False,
        "old_suggested_prompt_hint_used": False,
        "decorative_symbols_reintroduced": False,
        "note": "The request may mention old evidence names only as forbidden sources; no old files are read as source evidence.",
        "instruction_mentions": {
            "material_slot_rules": "material_slot_rules" in text,
            "material_slot_evidence": "material_slot_evidence" in text,
            "suggested_prompt_hint": "suggested_prompt_hint" in text,
        },
    }

def display_label(slot: dict[str, Any]) -> str:
    entries = slot.get("prompt_source_legend_entries") or []
    if entries and entries[0].get("name"):
        return str(entries[0]["name"])
    canonical = str(slot.get("canonical_material_id") or slot.get("material_slot_id") or "material")
    for prefix in ("mat_",):
        if canonical.startswith(prefix):
            canonical = canonical[len(prefix) :]
    if canonical.endswith("_material"):
        canonical = canonical[: -len("_material")]
    if canonical == "shallow_water":
        return "water"
    return canonical

def natural_view_identity(slot: dict[str, Any]) -> str:
    label = display_label(slot).replace("_", " ")
    orientation = str(slot.get("surface_orientation") or "material_surface").replace("_", " ")
    return f"{orientation} {label}"

def normalize_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower().replace("-", " "))

def md_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")

