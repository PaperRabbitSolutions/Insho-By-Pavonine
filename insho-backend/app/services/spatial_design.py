"""Review and apply one authorized design recommendation through canonical scene services."""

from datetime import datetime, timezone
from uuid import uuid4, uuid5

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm.util import identity_key

from app.models.project import Project
from app.models.spatial import SpatialObject, SpatialZone
from app.models.spatial_design_review import SpatialDesignReview
from app.schemas.object_library import InstantiateDefinition
from app.schemas.spatial import SpatialZoneCreate
from app.schemas.spatial_design import ApplicationReceipt, RecommendationPage, ValidateRecommendation, ZoneAction
from app.schemas.spatial_validation import RuleOptions, ValidationConfig, ValidationReportRequest
from app.services import object_library, spatial_validation
from app.services.design_intelligence import lock_design_sources
from app.services.spatial import SpatialService, _commit, _get_scene, _missing
from app.services.spatial_design_context import load_context
from app.services.spatial_design_recommendations import NAMESPACE, derive, match_reasons, review_payload, with_reviews
from app.services.spatial_policy import scene_row


ESSENTIAL_RULES = ("geometry", "transforms", "hierarchy", "venue_boundary", "consistency", "object_library")


def _scene(db, scene_id, user_id, *, write=False):
    scene = _get_scene(db, scene_id, user_id, write=write, lock=True if write else "read")
    lock_design_sources(db, scene.project_id)
    return scene


def context(db, scene_id, user_id):
    return load_context(db, _scene(db, scene_id, user_id), user_id)


def recommendations(db, scene_id, user_id, *, source, kind, status, offset, limit):
    scene = _scene(db, scene_id, user_id)
    current = load_context(db, scene, user_id)
    items = with_reviews(db, scene, user_id, current, derive(db, scene, user_id, current))
    filtered = [item for item in items if (source is None or item.source.module == source)
                and (kind is None or item.kind == kind) and (status is None or item.status == status)]
    return RecommendationPage(scene_revision=scene.current_revision, context_version=current.context_version,
                              items=filtered[offset:offset + limit], total=len(filtered), offset=offset, limit=limit)


def recommendation(db, scene_id, recommendation_id, user_id):
    scene = _scene(db, scene_id, user_id)
    current = load_context(db, scene, user_id)
    items = with_reviews(db, scene, user_id, current, derive(db, scene, user_id, current, recommendation_id=recommendation_id),
                         recommendation_id=recommendation_id)
    if not items:
        _missing()
    return items[0]


def _error(status, code, message, **data):
    raise HTTPException(status, detail={"code": code, "message": message, **data})


def _check_snapshot(scene, current, data):
    if scene.current_revision != data.base_revision:
        _error(409, "scene_revision_conflict", "Reload the canonical scene and revalidate the recommendation.",
               current_revision=scene.current_revision)
    if current.context_version != data.context_version:
        _error(409, "design_context_changed", "Refresh design context before reviewing this recommendation.",
               context_version=current.context_version)


def _review(db, scene, recommendation_id):
    return db.get(SpatialDesignReview, (scene.id, recommendation_id), populate_existing=True)


def _current_intent(db, scene, user_id, current, recommendation_id):
    items = derive(db, scene, user_id, current, recommendation_id=recommendation_id)
    if not items:
        if _review(db, scene, recommendation_id) is not None:
            _error(409, "stale_recommendation", "This recommendation's source has been superseded.")
        _missing()
    return items[0]


def _stage(db, scene, user_id, current, recommendation, action):
    if recommendation.status == "stale":
        _error(409, "stale_recommendation", "Refresh the Phase 8 source before using its recommendation.")
    if {"repair_source_output", "invalid_zone_requirement"}.intersection(recommendation.prerequisites):
        _error(422, "source_output_invalid", "Repair the source requirement before proposing scene changes.")
    zone_action = isinstance(action, ZoneAction)
    if zone_action != (recommendation.kind == "zone_requirement"):
        _error(422, "action_mismatch", "Use the action supported by this recommendation type.")
    entity_id = uuid5(NAMESPACE, f"{scene.id}/{recommendation.id}/placement")
    provenance = {"recommendation_id": str(recommendation.id), "context_version": current.context_version,
                  "source": recommendation.source.model_dump(mode="json")}
    base_revision = scene.current_revision
    catalog_validation = None
    if zone_action:
        if recommendation.zone_matches:
            _error(409, "zone_already_exists", "Review the existing matching zones before creating another.")
        zone = recommendation.intent["zone"]
        data = SpatialZoneCreate(name=recommendation.title, category=zone["zone_type"].strip(),
                                 description=zone["purpose"], transform=action.transform, dimensions=action.dimensions,
                                 metadata={"design_intelligence": provenance})
        SpatialService.create_zone(db, scene.id, data, user_id, commit=False, entity_id=entity_id)
        entity_type = "zone"
    else:
        element = recommendation.intent["element"]
        if action.zone_id is None and (element.get("related_zone") or "").strip():
            _error(422, "zone_required", "Select the existing scene zone for this planning requirement.")
        if action.zone_id is not None and scene_row(db, SpatialZone, scene.id, action.zone_id).status == "archived":
            _error(422, "zone_archived", "Choose a zone that is available for placement.")
        definition = object_library.scene_definition(db, user_id, db.get(Project, scene.project_id), action.definition_id)
        if not match_reasons(element["name"], element["category"], definition):
            _error(422, "catalog_match_required", "Choose a definition that matches the element's name, category, or object type.")
        args = action.model_dump(exclude_unset=True, exclude={"kind", "definition_id"})
        placement = InstantiateDefinition(**args, base_revision=base_revision, metadata={"design_intelligence": provenance})
        result = object_library.instantiate(db, user_id, scene.id, action.definition_id, placement,
                                            commit=False, entity_id=entity_id)
        catalog_validation = result["validation"]
        entity_type = "object"
    policy = ValidationConfig(rules={
        **{rule: RuleOptions(enabled=True) for rule in ESSENTIAL_RULES},
        "venue_boundary": RuleOptions(enabled=True, severity="error"),
        "zone_containment": RuleOptions(enabled=True),
    })
    report = spatial_validation.validate(
        db, scene.id, user_id, ValidationReportRequest(config=policy, limit=500),
        boundary_zone_ids=(entity_id,) if zone_action else (),
    )
    return entity_id, entity_type, report, catalog_validation


def _blocked(report):
    return report["summary"]["blocking"] > 0 or not report["complete"]


def _warnings(report, catalog):
    return (report["summary"]["warnings"] > 0
            or (catalog is not None and any(issue["severity"] == "warning" for issue in catalog["issues"])))


def _store_review(db, scene, rec, data, user_id, row, **values):
    if row is None:
        row = SpatialDesignReview(scene_id=scene.id, recommendation_id=rec.id)
        db.add(row)
    row.review_id, row.reviewed_at = uuid4(), datetime.now(timezone.utc)
    row.source = rec.source.model_dump(mode="json")
    row.reviewed_by_user_id = user_id
    row.base_revision, row.context_version = data.base_revision, data.context_version
    for key, value in values.items():
        setattr(row, key, value)
    return row


def validate_recommendation(db, scene_id, recommendation_id, user_id, data):
    scene = _scene(db, scene_id, user_id, write=True)
    current = load_context(db, scene, user_id)
    _check_snapshot(scene, current, data)
    previous = _review(db, scene, recommendation_id)
    if previous is not None and previous.status in {"rejected", "applied"}:
        _error(409, "recommendation_finalized", "This recommendation already has a final decision.")
    rec = _current_intent(db, scene, user_id, current, recommendation_id)
    with db.begin_nested() as preview:
        entity_id, entity_type, report, catalog = _stage(db, scene, user_id, current, rec, data.action)
        preview.rollback()
    # A compiler read can reload the staged entity after the original ORM instance
    # is released. Discard that read instance along with the rolled-back row.
    model = SpatialZone if entity_type == "zone" else SpatialObject
    staged = db.identity_map.get(identity_key(model, entity_id))
    if staged is not None:
        db.expunge(staged)
    blocked, warnings = _blocked(report), _warnings(report, catalog)
    review_id = None
    if not blocked:
        row = _store_review(
            db, scene, rec, data, user_id, previous, status="validated",
            action=data.action.model_dump(mode="json", exclude_unset=True),
            policy_fingerprint=report["configuration_fingerprint"],
            outcome={"findings_fingerprint": report["findings_fingerprint"], "catalog": jsonable_encoder(catalog),
                     "summary": report["summary"], "requires_warning_acceptance": warnings},
        )
        review_id = row.review_id
        _commit(db)
    return {
        "recommendation_id": rec.id, "review_id": review_id, "base_revision": data.base_revision,
        "context_version": current.context_version, "proposed_entity_id": entity_id,
        "status": "blocked" if blocked else "validated_with_warnings" if warnings else "validated",
        "requires_warning_acceptance": warnings and not blocked, "validation": report, "catalog_validation": catalog,
    }


def apply_recommendation(db, scene_id, recommendation_id, user_id, data):
    scene = _scene(db, scene_id, user_id, write=True)
    row = _review(db, scene, recommendation_id)
    if row is None:
        current = load_context(db, scene, user_id)
        _current_intent(db, scene, user_id, current, recommendation_id)
        _error(409, "review_required", "Validate the recommendation before applying it.")
    if row.review_id != data.review_id or row.status not in {"validated", "applied"}:
        _error(409, "review_not_current", "Validate the recommendation again to obtain a current review ID.")
    if row.status == "applied":
        return ApplicationReceipt.model_validate(row.outcome)

    current = load_context(db, scene, user_id)
    _check_snapshot(scene, current, row)
    rec = _current_intent(db, scene, user_id, current, recommendation_id)
    reviewed = ValidateRecommendation.model_validate({
        "action": row.action, "base_revision": row.base_revision, "context_version": row.context_version,
    })
    try:
        with db.begin_nested():
            entity_id, entity_type, report, catalog = _stage(db, scene, user_id, current, rec, reviewed.action)
            details = {"validation": jsonable_encoder(report), "catalog_validation": jsonable_encoder(catalog)}
            if _blocked(report):
                _error(409, "spatial_validation_blocked", "The prospective scene has blocking or incomplete checks.", **details)
            if (row.policy_fingerprint != report["configuration_fingerprint"]
                    or row.outcome["findings_fingerprint"] != report["findings_fingerprint"]
                    or row.outcome["catalog"] != details["catalog_validation"]):
                _error(409, "validation_changed", "The validation results changed. Review a new preview before applying.", **details)
            if _warnings(report, catalog) and not data.accept_warnings:
                _error(409, "warning_acceptance_required", "Review and explicitly accept the current validation warnings.", **details)
            receipt = ApplicationReceipt(
                scene_id=scene.id, recommendation_id=rec.id, review_id=row.review_id, scene_revision=scene.current_revision,
                entity_id=entity_id, entity_type=entity_type, applied_at=datetime.now(timezone.utc), applied_by_user_id=user_id,
            )
            row.status, row.outcome = "applied", receipt.model_dump(mode="json")
        _commit(db)
    except Exception:
        db.rollback()
        raise
    return receipt


def reject_recommendation(db, scene_id, recommendation_id, user_id, data):
    scene = _scene(db, scene_id, user_id, write=True)
    previous = _review(db, scene, recommendation_id)
    if previous is not None and previous.status == "rejected":
        return review_payload(previous)
    if previous is not None and previous.status == "applied":
        _error(409, "recommendation_finalized", "An applied recommendation remains in canonical scene history.")
    current = load_context(db, scene, user_id)
    _check_snapshot(scene, current, data)
    rec = _current_intent(db, scene, user_id, current, recommendation_id)
    row = _store_review(db, scene, rec, data, user_id, previous, status="rejected", action=None,
                        policy_fingerprint=None, outcome={"reason": data.reason})
    result = review_payload(row)
    _commit(db)
    return result
