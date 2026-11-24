"""Derive traceable zone and catalog intents from normalized Phase 8 sources."""

from collections import defaultdict
from datetime import timezone
from uuid import UUID, uuid5

from sqlalchemy import select

from app.models.project import Project
from app.models.spatial import SpatialZone
from app.models.spatial_design_review import SpatialDesignReview
from app.schemas.spatial_design import CatalogMatch, Recommendation, RecommendationReview, RecommendationSource, ZoneMatch
from app.services import object_library


NAMESPACE = UUID("a3b58102-8856-419a-be98-46dd4c5fbaf2")


def _name(value):
    return (value or "").strip().casefold()


def _catalog_key(value):
    return (value or "").strip().lower()


def match_reasons(name, category, definition, style_terms=()):
    reasons = []
    if _catalog_key(name) == _catalog_key(definition.name):
        reasons.append("name")
    if _catalog_key(category) == _catalog_key(definition.category):
        reasons.append("category")
    if _catalog_key(category) == _catalog_key(definition.object_type):
        reasons.append("object_type")
    if reasons and set(style_terms).intersection(definition.tags or ()):
        reasons.append("style_tags")
    return reasons


def _recommendation(scene, entry, *, kind, title, description, path, element_id=None, **data):
    origin = entry.artifact
    source = RecommendationSource(
        module=entry.module, artifact_id=origin.artifact_id, artifact_version=origin.version, event_id=origin.event_id,
        execution_id=origin.execution_id, path=path, element_id=element_id,
    )
    recommendation_id = uuid5(NAMESPACE, f"{scene.id}/{source.artifact_id}/{path}/{element_id or ''}")
    if origin.context_stale or origin.status == "archived":
        data["status"] = "stale"
        data.setdefault("prerequisites", []).append("refresh_source")
    return Recommendation(id=recommendation_id, kind=kind, title=title, description=description, source=source, **data)


def derive(db, scene, user_id, context, *, recommendation_id=None):
    modules = {entry.module: entry for entry in context.modules}
    plan, elements, style = modules["plan"], modules["elements"], modules["style"]
    zones = defaultdict(list)
    for row in db.scalars(select(SpatialZone).where(SpatialZone.scene_id == scene.id, SpatialZone.status != "archived")
                          .order_by(SpatialZone.id)):
        zones[_name(row.name)].append(ZoneMatch(id=row.id, name=row.name))

    results = []
    for index, zone in enumerate(plan.normalized.get("zones", ())):
        title = zone["name"].strip()
        key = _name(title)
        matches = zones[key] if key else []
        valid = bool(title and zone["zone_type"].strip())
        reasons = (["invalid_zone_requirement"] if not valid else ["choose_existing_zone"] if len(matches) > 1
                   else [] if matches else ["provide_measured_dimensions", "provide_position_m"])
        if any(issue.startswith("invalid_field:") for issue in plan.issues):
            reasons.append("repair_source_output")
        status = ("needs_resolution" if not valid or len(matches) > 1 or "repair_source_output" in reasons
                  else "resolved" if matches else "proposed")
        adjacencies = []
        for edge in plan.normalized.get("adjacencies", ()):
            if all(isinstance(edge.get(side), str) and edge[side].strip() for side in ("from", "to", "relationship")):
                if key in {_name(edge["from"]), _name(edge["to"])}:
                    adjacencies.append({"from": edge["from"], "to": edge["to"], "relationship": edge["relationship"],
                                        "from_zone_ids": [str(z.id) for z in zones[_name(edge["from"])]],
                                        "to_zone_ids": [str(z.id) for z in zones[_name(edge["to"])]]})
        results.append(_recommendation(
            scene, plan, kind="zone_requirement", title=title or "Unnamed zone requirement",
            description=zone["purpose"], path=f"zones.{index}", status=status, zone_matches=matches,
            prerequisites=reasons, intent={"zone": zone, "adjacencies": adjacencies},
        ))

    placements = elements.normalized.get("placements", ())
    rows = []
    for item in placements:
        title, path, element_id = item["name"].strip(), item["path"], item["element_id"]
        zone_key = _name(item.get("related_zone"))
        matches = zones[zone_key] if zone_key else []
        reasons = []
        if item.get("invalid") or not title:
            reasons.append("repair_source_output")
        if zone_key and len(matches) != 1:
            reasons.append("choose_target_zone")
        rec = _recommendation(
            scene, elements, kind="element_placement", title=title or "Unnamed element",
            description=item.get("description") or "Review the source element details.", path=path, element_id=element_id,
            status="needs_resolution" if reasons else "ready_for_validation", zone_matches=matches,
            prerequisites=reasons, intent={"element": item},
        )
        if recommendation_id is None or recommendation_id == rec.id:
            rows.append((rec, item))
    if rows:
        eligible = [(rec, item) for rec, item in rows if "repair_source_output" not in rec.prerequisites]
        if eligible:
            project = db.get(Project, scene.project_id)
            candidates = object_library.placement_candidates(
                db, user_id, project, {_catalog_key(item["name"]) for _, item in eligible},
                {_catalog_key(item["category"]) for _, item in eligible},
            )
            by_term = defaultdict(dict)
            for candidate in candidates:
                for term in (_catalog_key(candidate.name), _catalog_key(candidate.category), _catalog_key(candidate.object_type)):
                    by_term[term][candidate.id] = candidate
            preferred = set()
            if style.status == "available":
                for field in ("design_language", "materials_and_finishes", "forms_and_shapes", "motifs"):
                    preferred.update(_catalog_key(value) for value in style.normalized.get(field, ()))
            for rec, item in eligible:
                terms = {_catalog_key(item["name"]), _catalog_key(item["category"])}
                pool = {cid: candidate for term in terms for cid, candidate in by_term[term].items()}
                terms = preferred | {_catalog_key(item.get("style")), *(_catalog_key(v) for v in item.get("materials", ()))}
                ranked = []
                for definition in pool.values():
                    reasons = match_reasons(item["name"], item["category"], definition, terms)
                    if reasons:
                        key = tuple(reason not in reasons for reason in ("name", "category", "object_type", "style_tags"))
                        ranked.append((key, _catalog_key(definition.name), definition.id,
                                       CatalogMatch(definition=definition, reasons=reasons)))
                rec.candidates = [entry[-1] for entry in sorted(ranked)[:5]]
                if not rec.candidates:
                    rec.prerequisites.append("resolve_catalog_definition")
                    if rec.status != "stale":
                        rec.status = "needs_resolution"
    results.extend(rec for rec, _ in rows)
    return [rec for rec in results if recommendation_id is None or rec.id == recommendation_id]


def review_payload(row):
    validation = (row.outcome or {}) if row.status == "validated" else {}
    return RecommendationReview.model_validate({
        "review_id": row.review_id, "status": row.status, "base_revision": row.base_revision,
        "context_version": row.context_version, "reviewed_at": row.reviewed_at.astimezone(timezone.utc),
        "reviewed_by_user_id": row.reviewed_by_user_id,
        "action": row.action, "validation_summary": validation.get("summary"),
        "requires_warning_acceptance": validation.get("requires_warning_acceptance", False),
        "receipt": row.outcome if row.status == "applied" else None,
        "rejection_reason": (row.outcome or {}).get("reason") if row.status == "rejected" else None,
    })


def with_reviews(db, scene, user_id, context, recommendations, *, recommendation_id=None):
    query = select(SpatialDesignReview).where(SpatialDesignReview.scene_id == scene.id)
    if recommendation_id is not None:
        query = query.where(SpatialDesignReview.recommendation_id == recommendation_id)
    reviews = {row.recommendation_id: row for row in db.scalars(query)}
    placed_definitions = {UUID(row.action["definition_id"]) for row in reviews.values()
                          if row.status == "validated" and row.action["kind"] == "place_object"}
    versions = object_library.placement_versions(db, user_id, db.get(Project, scene.project_id), placed_definitions)
    merged = {rec.id: rec for rec in recommendations}
    current_ids = set(merged)
    for rec_id, row in reviews.items():
        rec = merged.get(rec_id)
        if rec is None:
            source = RecommendationSource.model_validate(row.source)
            kind = "zone_requirement" if source.module == "plan" else "element_placement"
            rec = Recommendation(id=rec_id, kind=kind, title="Earlier design recommendation",
                                 description="Open the source artifact for the original intent.", status="stale", source=source)
            merged[rec_id] = rec
        if row.status in {"applied", "rejected"}:
            rec.status = row.status
        elif rec.status != "stale" and rec_id in current_ids:
            definition_current = (
                row.action["kind"] != "place_object"
                or versions.get(UUID(row.action["definition_id"])) == row.action["definition_version"]
            )
            rec.status = ("validated" if row.base_revision == scene.current_revision
                          and row.context_version == context.context_version and definition_current else "stale")
        rec.review = review_payload(row)
    return sorted(merged.values(), key=lambda rec: (rec.source.module, rec.title.casefold(), rec.id))
