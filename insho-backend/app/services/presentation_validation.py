"""Section expectations and live checks over immutable presentation evidence."""

from uuid import UUID

from fastapi import HTTPException

from app.schemas.presentation import Section, SectionContent
from app.services.presentation_sources import ASSET_SECTIONS, IncompatibleSource, UnavailableSource


_CONCEPT = {"concept_story", "design_dna", "moodboard", "hero_visual"}
_DESIGN = _CONCEPT | {"spatial_plan", "journey", "material_palette"}
REQUIRED = {"concept": _CONCEPT, "design_review": _DESIGN,
            "production_review": _DESIGN | {"views_3d", "budget_summary", "production_confidence"}}

# Presentation ordering only. These profiles make no claims about the client's preferences.
EMPHASIS = {
    "luxury": ("concept_story", "hero_visual", "material_palette"),
    "corporate": ("concept_story", "budget_summary", "production_confidence"),
    "bride_family": ("concept_story", "journey", "moodboard"),
    "hotel": ("spatial_plan", "journey", "production_confidence"),
    "agency": ("concept_story", "hero_visual", "alternatives"),
    "brand": ("concept_story", "design_dna", "hero_visual"),
    "government": ("production_confidence", "spatial_plan", "budget_summary"),
}
_MISSING = {
    "concept_story": ("missing", "Supply the concept story or link a Style or Vision narrative."),
    "design_dna": ("unavailable", "Link a populated Design DNA record from this project."),
    "moodboard": ("unavailable", "Link a Mood artifact or managed moodboard visuals."),
    "hero_visual": ("unavailable", "Select a managed hero visual or link a Vision artifact with a generated visual."),
    "spatial_plan": ("unavailable", "Link a scene with a 2D plan, a structured Plan artifact, or a managed floor plan."),
    "views_3d": ("unavailable", "Link a canonical scene with renderable geometry or associate its managed render assets."),
    "journey": ("missing", "Author the guest journey or link a Plan artifact with guest-flow information."),
    "material_palette": ("unavailable", "Link material assignments, authored design selections, or palette visuals."),
    "led_visual_preview": ("unavailable", "Link a measured or placed LED surface and a managed preview asset."),
    "budget_summary": ("unavailable", "Link a current budget with an approved amount or recorded costs."),
    "production_confidence": ("unavailable", "Link a production record with a reviewed technical package and BOQ."),
    "alternatives": ("missing", "Select a specific revision of an alternative with associated design evidence."),
}


def section_order(context):
    focus = list(EMPHASIS[context])
    return focus + [section for section in Section.__args__ if section not in focus]


def capture(sheet, sources):
    result = {}
    for key, section in sheet.sections.items():
        if section.applicability == "not_applicable" and key in REQUIRED[sheet.purpose]:
            raise HTTPException(422, detail="Required sections cannot be marked not applicable for this purpose.")
        if section.asset_ids and key not in ASSET_SECTIONS:
            raise HTTPException(422, detail="Attach the asset to a visual presentation section.")
        try:
            records, links = [], []
            for asset_id in section.asset_ids:
                links.append({"id": str(asset_id), "role": "selected", "artifact_id": None,
                              "metadata": sources.asset(asset_id, key)})
            for ref in section.sources:
                evidence, inherited = sources.resolve(ref, key)
                records.append(evidence)
                links.extend(inherited)
        except UnavailableSource as exc:
            raise HTTPException(404, detail="Presentation source or asset not found") from exc
        except IncompatibleSource as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        result[key] = {"sources": records, "assets": links}
    return result


def _issue(code, message, *, blocking=True, reference_id=None):
    return {"code": code, "message": message, "blocking": blocking, "reference_id": reference_id}


def _live_evidence(key, content, captured, sources):
    issues, live_records, public_sources, inherited = [], [], [], set()
    records = captured.get("sources", [])
    for ref, pin in zip(content.sources, records):
        data = pin["data"]
        try:
            live, links = sources.resolve(ref, key)
        except (UnavailableSource, IncompatibleSource):
            status, data = "unavailable", None
            issues.append(_issue("source_unavailable", "This linked source is no longer accessible in this presentation scope.",
                                 reference_id=ref.id))
        else:
            live_records.append(live)
            inherited.update((item["id"], item["role"], item["artifact_id"]) for item in links)
            status = "changed" if live != pin or live["stale"] else "current"
            if status == "changed":
                issues.append(_issue("source_changed", "Review the current source and save a new presentation revision.",
                                     reference_id=ref.id))
        public_sources.append({"id": ref.id, "kind": ref.kind, "version": pin["version"], "status": status, "data": data})

    public_assets = []
    for link in captured.get("assets", []):
        key_id = UUID(link["id"])
        metadata = link["metadata"]
        try:
            current = sources.asset(key_id, key)
            if link["role"] != "selected" and (link["id"], link["role"], link["artifact_id"]) not in inherited:
                raise UnavailableSource
        except (UnavailableSource, IncompatibleSource):
            status, metadata = "unavailable", None
            issues.append(_issue("asset_unavailable", "Replace the deleted, inaccessible, or detached asset.", reference_id=key_id))
        else:
            status = "current" if metadata == current else "changed"
            if status == "changed":
                issues.append(_issue("asset_changed", "Review the asset changes and save a new presentation revision.",
                                     reference_id=key_id))
        public_assets.append({**link, "status": status, "metadata": metadata})
    return live_records, public_sources, public_assets, issues


def _has_content(key, text, records, assets):
    ready = [item for item in records if item["ready"]]
    visual = any(item["status"] != "unavailable" for item in assets)
    if key in {"concept_story", "journey"}:
        return bool(text) or any(item["kind"] == "design_artifact" for item in ready)
    if key == "hero_visual":
        return visual
    if key == "views_3d":
        return any(item["kind"] == "spatial_scene" and (item["ready"] or visual) for item in records)
    if key == "led_visual_preview":
        return visual and any(item["kind"] == "led_quantity" or item["data"].get("contains_led") for item in ready)
    return bool(ready) or visual


def inspect_sheet(revision, sheet, evidence, sources):
    sections = []
    live = {key: _live_evidence(key, content, evidence.get(key, {}), sources) for key, content in sheet.sections.items()}
    plan_scenes = {record["id"] for record in live.get("spatial_plan", ([],))[0] if record["kind"] == "spatial_scene"}
    has_led = any(record["kind"] == "led_quantity" or record["data"].get("contains_led") for key in (
        "spatial_plan", "views_3d", "led_visual_preview",
    ) for record in live.get(key, ([],))[0])

    for key in section_order(sheet.client_context):
        content = sheet.sections.get(key, SectionContent())
        records, public_sources, assets, issues = live.get(key, ([], [], [], []))
        required = key in REQUIRED[sheet.purpose] or content.applicability == "required" or (key == "led_visual_preview" and has_led)
        requirement = "required" if required else ("not_applicable" if content.applicability == "not_applicable" else "optional")
        if key == "views_3d" and plan_scenes and any(record["kind"] == "spatial_scene" and record["id"] not in plan_scenes
                                                    for record in records):
            issues.append(_issue("scene_mismatch", "Use the same canonical scene as the Spatial Plan."))
        if key == "led_visual_preview" and plan_scenes and any(record["data"].get("scene_id") not in plan_scenes
                                                             for record in records):
            issues.append(_issue("scene_mismatch", "Use LED evidence from the selected Spatial Plan."))
        if key == "hero_visual" and len(content.asset_ids) > 1:
            issues.append(_issue("multiple_hero_assets", "Select one primary hero visual."))

        if requirement == "not_applicable":
            status = "not_applicable"
        elif _has_content(key, content.text, records, assets) and not issues:
            status = "complete"
        elif issues:
            status = "unavailable"
        else:
            status, message = _MISSING[key]
            issues.append(_issue("missing_section_content" if status == "missing" else "source_data_required",
                                 message, blocking=required))
        sections.append({"section": key, "requirement": requirement, "status": status, "issues": issues,
                         "text": content.text, "sources": public_sources, "assets": assets})
    validation = {"revision": revision, "complete": not any(issue["blocking"] for item in sections for issue in item["issues"]),
                  "sections": [{key: item[key] for key in ("section", "requirement", "status", "issues")} for item in sections]}
    return sections, validation
