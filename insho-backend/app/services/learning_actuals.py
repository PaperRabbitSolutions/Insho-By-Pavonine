"""Capture, verify and retrieve event outcome records."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.models.learning import LearningActual
from app.schemas.learning import ActualInput, ActualRead
from app.security import require_project_access
from app.services import design_activity, design_version, procurement_scope
from app.services.learning_sources import Context, available_asset_ids, gather, invalid
from app.services.spatial import _missing, _validate_event_project


def rollback_conflict(db, exc):
    db.rollback()
    raise HTTPException(409, detail={"code": "learning_relationship_conflict",
                                    "message": "A related record or learning revision changed. Refresh before retrying."}) from exc


def commit(db):
    try:
        db.commit()
    except IntegrityError as exc:
        rollback_conflict(db, exc)


def active_actuals(project, event_ids):
    correction = aliased(LearningActual)
    return select(LearningActual).where(
        LearningActual.project_id == project.id, LearningActual.organization_id == project.organization_id,
        LearningActual.event_id.in_(event_ids),
        ~exists(select(1).where(correction.supersedes_id == LearningActual.id, correction.status == "verified")),
    )


def owned(db, project, key, *, lock=False):
    query = select(LearningActual).where(LearningActual.id == key, LearningActual.project_id == project.id,
                                         LearningActual.organization_id == project.organization_id)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        _missing()
    return row


def view(db, project, row, *, page_assets=None):
    available = (available_asset_ids(db, project, row.event_id, row.sources) if page_assets is None
                 else {UUID(item["id"]) for item in row.sources if item["kind"] == "asset"} & page_assets)
    data, derived = dict(row.data), dict(row.derived)
    if row.kind == "final_media" and UUID(data["asset_id"]) not in available:
        data.pop("asset_id")
        derived.pop("asset_metadata", None)
    visible = [item for item in row.sources if item["kind"] != "asset" or UUID(item["id"]) in available]
    fields = {name: getattr(row, name) for name in ActualRead.model_fields if name not in {"available_assets", "data", "derived", "sources"}}
    return ActualRead(**fields, data=data, derived=derived, sources=visible, available_assets=sorted(available))


def _log(db, row, user, action, request_id):
    design_activity.record(db, row.project_id, row.event_id, user, "learning_actual_" + action, "learning_actual", row.id,
                           version_id=row.design_version_id, request_id=request_id, kind=row.kind, revision=row.revision)


def _draft(row, expected):
    if row.status != "draft":
        design_version.conflict("learning_actual_final", "Create a correction for a verified outcome.")
    if row.revision != expected:
        design_version.conflict("learning_revision_conflict", "Refresh the current outcome revision.", current_revision=row.revision)


def _predecessor(db, project, ctx, key, kind, derived, *, lock=False):
    prior = owned(db, project, key, lock=lock)
    if prior.event_id != ctx.event.id or prior.design_version_id != (ctx.version.id if ctx.version else None):
        _missing()
    if prior.kind != kind or prior.status != "verified":
        invalid("A correction must replace a verified outcome of the same kind.")
    if prior.derived.get("comparison", {}).get("key") != derived.get("comparison", {}).get("key"):
        invalid("A correction must retain the comparison baseline.")
    if db.scalar(select(LearningActual.id).where(LearningActual.supersedes_id == key, LearningActual.status == "verified")):
        design_version.conflict("learning_actual_corrected", "Select the current verified outcome for correction.")


def create(db, project_id, user, payload, *, request_id=None):
    project = require_project_access(db, user, project_id, write=True)
    ctx = Context(db, project, payload.event_id, payload.design_version_id, user)
    derived, sources, occurred = gather(ctx, payload)
    if payload.supersedes_id is not None:
        _predecessor(db, project, ctx, payload.supersedes_id, payload.data.kind, derived)
    row = LearningActual(id=uuid4(), organization_id=project.organization_id, project_id=project.id, event_id=ctx.event.id,
                         design_version_id=payload.design_version_id, snapshot_id=ctx.version.snapshot_id if ctx.version else None,
                         supersedes_id=payload.supersedes_id, status="draft", kind=payload.data.kind, revision=1, origin=payload.origin,
                         source_reference=payload.source_reference, data=payload.data.model_dump(mode="json"), derived=derived,
                         sources=sources, occurred_on=occurred, recorded_by_id=user)
    db.add(row)
    _log(db, row, user, "recorded", request_id)
    commit(db)
    return view(db, project, row)


def edit(db, project_id, key, user, payload, *, request_id=None):
    project = require_project_access(db, user, project_id, write=True)
    row = owned(db, project, key, lock=True)
    _draft(row, payload.expected_revision)
    if payload.data.kind != row.kind:
        invalid("The outcome kind is fixed when the draft is created.")
    ctx = Context(db, project, row.event_id, row.design_version_id, user)
    derived, sources, occurred = gather(ctx, payload)
    if row.supersedes_id:
        _predecessor(db, project, ctx, row.supersedes_id, row.kind, derived)
    row.data, row.derived, row.sources, row.occurred_on = payload.data.model_dump(mode="json"), derived, sources, occurred
    row.source_reference, row.origin = payload.source_reference, payload.origin
    row.revision += 1
    _log(db, row, user, "edited", request_id)
    commit(db)
    return view(db, project, row)


def verify(db, project_id, key, user, payload, *, request_id=None):
    project = require_project_access(db, user, project_id, write=True)
    procurement_scope.require_approver(db, user, project)
    row = owned(db, project, key, lock=True)
    _draft(row, payload.expected_revision)
    ctx = Context(db, project, row.event_id, row.design_version_id, user)
    assets = [item["id"] for item in row.sources if item["kind"] == "asset"
              and (row.kind != "final_media" or item["id"] != row.data["asset_id"])]
    audit = next((item["id"] for item in row.sources if item["kind"] == "design_audit"), None)
    body = ActualInput(origin=row.origin, source_reference=row.source_reference, asset_ids=assets, audit_id=audit, data=row.data)
    row.derived, row.sources, row.occurred_on = gather(ctx, body, verify=True)
    if row.supersedes_id:
        _predecessor(db, project, ctx, row.supersedes_id, row.kind, row.derived, lock=True)
    row.status, row.revision = "verified", row.revision + 1
    row.verified_by_id, row.verified_at, row.review_note = user, datetime.now(timezone.utc), payload.review_note
    _log(db, row, user, "verified", request_id)
    commit(db)
    return view(db, project, row)


def discard(db, project_id, key, user, expected, *, request_id=None):
    project = require_project_access(db, user, project_id, write=True)
    row = owned(db, project, key, lock=True)
    _validate_event_project(db, user, project, row.event_id)
    _draft(row, expected)
    _log(db, row, user, "discarded", request_id)
    db.delete(row)
    commit(db)


def read(db, project_id, key, user):
    project = require_project_access(db, user, project_id)
    row = owned(db, project, key)
    _validate_event_project(db, user, project, row.event_id)
    return view(db, project, row)


def history(db, project_id, user, *, event_id, kind, status, supplier_id, offset, limit):
    project = require_project_access(db, user, project_id)
    _validate_event_project(db, user, project, event_id)
    query = select(LearningActual).where(LearningActual.project_id == project.id,
                                         LearningActual.organization_id == project.organization_id, LearningActual.event_id == event_id)
    if kind:
        query = query.where(LearningActual.kind == kind)
    if status:
        query = query.where(LearningActual.status == status)
    if supplier_id is not None:
        Context(db, project, event_id, None, user).supplier(supplier_id)
        query = query.where(LearningActual.data["supplier_id"].astext == str(supplier_id))
    rows = db.scalars(query.order_by(LearningActual.created_at.desc(), LearningActual.id).offset(offset).limit(limit)).all()
    page_assets = available_asset_ids(db, project, event_id, (item for row in rows for item in row.sources))
    return [view(db, project, row, page_assets=page_assets) for row in rows]
