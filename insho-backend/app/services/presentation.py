"""Scoped Client Presentation Sheet revisions and checkpoint decisions."""

from collections import defaultdict
from datetime import datetime, timezone
import logging
from time import monotonic
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.models.presentation import Presentation, PresentationAsset, PresentationCheckpoint, PresentationRevision
from app.schemas.presentation import CheckpointRead, SheetInput
from app.security import ADMIN_ROLES, require_membership, require_project_access
from app.services import presentation_validation as validation
from app.services import design_activity
from app.services.presentation_sources import Sources
from app.services.spatial import _missing, _validate_event_project


logger = logging.getLogger("insho.presentation")


def _conflict(code, message, **details):
    raise HTTPException(409, detail={"code": code, "message": message, **details})


def _presentation(db, project, presentation_id, *, lock=False):
    query = select(Presentation).where(Presentation.id == presentation_id, Presentation.project_id == project.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        _missing()
    return row


def _scope(db, project_id, presentation_id, user_id, *, write=False):
    project = require_project_access(db, user_id, project_id, write=write)
    row = _presentation(db, project, presentation_id, lock=write)
    _validate_event_project(db, user_id, project, row.event_id, write=write)
    return project, row


def _revisions(row):
    return select(PresentationRevision).where(PresentationRevision.presentation_id == row.id)


def _latest(db, row):
    return db.scalar(_revisions(row).order_by(PresentationRevision.version.desc()).limit(1))


def _revision(db, row, latest, version):
    if version is None or version == latest.version:
        return latest
    result = db.get(PresentationRevision, (row.id, version))
    if result is None:
        _missing()
    return result


def _check_version(expected, latest):
    if expected != latest.version:
        _conflict("presentation_revision_conflict", "Review the current presentation revision.", current_revision=latest.version)


def _checkpoints(row, revision=None):
    query = select(PresentationCheckpoint).where(PresentationCheckpoint.presentation_id == row.id)
    if revision is not None:
        query = query.where(PresentationCheckpoint.revision == revision)
    return query


def _approval_status(statuses):
    if not statuses:
        return "draft"
    if statuses == {"approved"}:
        return "approved"
    for status in ("rejected", "revision_requested", "pending"):
        if status in statuses:
            return status
    return "pending" if "approved" in statuses else "draft"


def _statuses(db, pairs):
    states = defaultdict(set)
    model = PresentationCheckpoint
    if pairs:
        for key, version, status in db.execute(select(model.presentation_id, model.revision, model.status).where(
            tuple_(model.presentation_id, model.revision).in_(pairs),
        )):
            states[(key, version)].add(status)
    return states


def _summary(project, row, revision, latest, statuses):
    return {"id": row.id, "organization_id": project.organization_id, "workspace_id": project.workspace_id,
            "project_id": project.id, "event_id": row.event_id, "alternative_of_id": row.alternative_of_id,
            "revision": revision.version, "current_revision": latest,
            **{key: revision.sheet[key] for key in ("title", "description", "purpose", "client_context")},
            "approval_status": _approval_status(statuses), "created_by_user_id": row.created_by_user_id,
            "created_at": row.created_at, "updated_by_user_id": revision.created_by_user_id, "updated_at": revision.created_at}


def _inspect(db, project, row, revision, user_id, *, sources=None):
    sheet = SheetInput.model_validate(revision.sheet)
    sections, report = validation.inspect_sheet(revision.version, sheet, revision.evidence,
                                                sources or Sources(db, project, row, user_id))
    return sheet, sections, report


def _detail(db, project, row, revision, latest, user_id, *, sources=None):
    sheet, sections, report = _inspect(db, project, row, revision, user_id, sources=sources)
    statuses = _statuses(db, [(row.id, revision.version)])
    return {**_summary(project, row, revision, latest, statuses[(row.id, revision.version)]),
            "sheet": sheet, "sections": sections, "validation": report,
            "section_order": validation.section_order(sheet.client_context), "emphasis": validation.EMPHASIS[sheet.client_context]}


def _commit(db, row, user_id, revision, action, request_id, started):
    metadata = {"request_id": str(request_id), "project_id": str(row.project_id),
                "event_id": str(row.event_id) if row.event_id is not None else None, "actor_id": str(user_id),
                "presentation_id": str(row.id), "revision": revision}
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning(action, extra={**metadata, "outcome": "conflict", "error_code": "presentation_write_conflict",
                                      "duration_ms": int((monotonic() - started) * 1000)})
        raise HTTPException(409, detail={"code": "presentation_write_conflict",
                                        "message": "The presentation or a linked record changed during the write."}) from exc
    logger.info(action, extra={**metadata, "outcome": "saved", "duration_ms": int((monotonic() - started) * 1000)})


def _write_revision(db, project, row, data, user_id, *, previous=None, request_id=None):
    started, request_id = monotonic(), request_id or uuid4()
    sheet = SheetInput.model_validate({key: getattr(data, key) for key in SheetInput.model_fields})
    sources = Sources(db, project, row, user_id, lock=True)
    evidence = validation.capture(sheet, sources)
    authored = sheet.model_dump(mode="json")
    if previous is not None and previous.sheet == authored and previous.evidence == evidence:
        return _detail(db, project, row, previous, previous.version, user_id, sources=sources)
    number = previous.version + 1 if previous else 1
    revision = PresentationRevision(presentation_id=row.id, version=number, sheet=authored, evidence=evidence,
                                    created_by_user_id=user_id)
    asset_ids = {UUID(link["id"]) for section in evidence.values() for link in section["assets"]}
    try:
        if previous is None:
            db.add(row)
            db.flush()
        db.add(revision)
        db.flush()
        db.add_all(PresentationAsset(presentation_id=row.id, revision=number, asset_id=key) for key in sorted(asset_ids))
        result = _detail(db, project, row, revision, number, user_id, sources=sources)
        design_activity.record(db, row.project_id, row.event_id, user_id, "presentation_revision_saved", "presentation",
                               row.id, request_id=request_id, presentation_revision=number)
        _commit(db, row, user_id, number, "presentation_revision_saved", request_id, started)
    except IntegrityError as exc:
        db.rollback()
        logger.warning("presentation_transaction_conflict", extra={"request_id": str(request_id),
                       "project_id": str(project.id), "actor_id": str(user_id), "error_code": "presentation_write_conflict"})
        raise HTTPException(409, detail={"code": "presentation_write_conflict",
                                        "message": "The presentation or a linked record changed during the write."}) from exc
    return result


def create(db, project_id, user_id, data, *, request_id=None):
    project = require_project_access(db, user_id, project_id, write=True)
    _validate_event_project(db, user_id, project, data.event_id, write=True)
    if data.alternative_of_id is not None:
        parent = _presentation(db, project, data.alternative_of_id, lock=True)
        if parent.event_id != data.event_id:
            _missing()
        if parent.alternative_of_id is not None:
            _conflict("presentation_alternative_parent_invalid", "Create alternatives under the main presentation.")
    row = Presentation(id=uuid4(), project_id=project.id, event_id=data.event_id,
                       alternative_of_id=data.alternative_of_id, created_by_user_id=user_id)
    return _write_revision(db, project, row, data, user_id, request_id=request_id)


def revise(db, project_id, presentation_id, user_id, data, *, request_id=None):
    project, row = _scope(db, project_id, presentation_id, user_id, write=True)
    latest = _latest(db, row)
    _check_version(data.expected_revision, latest)
    return _write_revision(db, project, row, data, user_id, previous=latest, request_id=request_id)


def get(db, project_id, presentation_id, user_id, *, version=None):
    project, row = _scope(db, project_id, presentation_id, user_id)
    latest = _latest(db, row)
    revision = _revision(db, row, latest, version)
    return _detail(db, project, row, revision, latest.version, user_id)


def list_presentations(db, project_id, user_id, *, event_id, offset, limit, alternative_of_id=None):
    project = require_project_access(db, user_id, project_id)
    _validate_event_project(db, user_id, project, event_id)
    newer = aliased(PresentationRevision)
    latest = select(func.max(newer.version)).where(newer.presentation_id == Presentation.id).scalar_subquery()
    query = select(Presentation, PresentationRevision).join(PresentationRevision, (
        PresentationRevision.presentation_id == Presentation.id) & (PresentationRevision.version == latest)
    ).where(Presentation.project_id == project.id)
    if event_id is not None:
        query = query.where(Presentation.event_id == event_id)
    if alternative_of_id is not None:
        query = query.where(Presentation.alternative_of_id == alternative_of_id)
    rows = db.execute(query.order_by(Presentation.created_at.desc(), Presentation.id).offset(offset).limit(limit)).all()
    statuses = _statuses(db, [(row.id, revision.version) for row, revision in rows])
    return [_summary(project, row, revision, revision.version, statuses[(row.id, revision.version)]) for row, revision in rows]


def alternatives(db, project_id, presentation_id, user_id, *, offset, limit):
    _, row = _scope(db, project_id, presentation_id, user_id)
    return list_presentations(db, project_id, user_id, event_id=row.event_id, offset=offset, limit=limit,
                              alternative_of_id=row.id)


def history(db, project_id, presentation_id, user_id, *, offset, limit):
    project, row = _scope(db, project_id, presentation_id, user_id)
    latest = _latest(db, row).version
    revisions = db.scalars(_revisions(row).order_by(PresentationRevision.version.desc()).offset(offset).limit(limit)).all()
    statuses = _statuses(db, [(row.id, revision.version) for revision in revisions])
    return [_summary(project, row, revision, latest, statuses[(row.id, revision.version)]) for revision in revisions]


def validate(db, project_id, presentation_id, user_id, *, version=None):
    project, row = _scope(db, project_id, presentation_id, user_id)
    revision = _revision(db, row, _latest(db, row), version)
    return _inspect(db, project, row, revision, user_id)[2]


def _require_complete(db, project, row, revision, user_id):
    sources = Sources(db, project, row, user_id, lock=True)
    _, _, report = _inspect(db, project, row, revision, user_id, sources=sources)
    if not report["complete"]:
        blocked = [part["section"] for part in report["sections"] if any(item["blocking"] for item in part["issues"])]
        _conflict("presentation_incomplete", "Resolve presentation validation issues before approval.", sections=blocked)


def add_checkpoint(db, project_id, presentation_id, user_id, data, *, request_id=None):
    started, request_id = monotonic(), request_id or uuid4()
    _, row = _scope(db, project_id, presentation_id, user_id, write=True)
    latest = _latest(db, row)
    _check_version(data.revision, latest)
    if db.scalar(_checkpoints(row, latest.version).where(PresentationCheckpoint.status != "draft").limit(1)):
        _conflict("presentation_review_started", "Define all checkpoints before requesting review of this revision.")
    duplicates = _checkpoints(row, latest.version).where(func.lower(PresentationCheckpoint.title) == data.title.lower())
    if db.scalar(duplicates.limit(1)):
        _conflict("presentation_checkpoint_exists", "A checkpoint with this title already exists on the revision.")
    checkpoint = PresentationCheckpoint(id=uuid4(), presentation_id=row.id, revision=latest.version, title=data.title,
                                         status="draft", created_by_user_id=user_id)
    db.add(checkpoint)
    design_activity.record(db, row.project_id, row.event_id, user_id, "presentation_checkpoint_created",
                           "presentation_checkpoint", checkpoint.id, request_id=request_id,
                           presentation_id=str(row.id), presentation_revision=latest.version)
    _commit(db, row, user_id, latest.version, "presentation_checkpoint_created", request_id, started)
    return CheckpointRead.model_validate(checkpoint)


def review(db, project_id, presentation_id, checkpoint_id, user_id, data, *, decide=False, request_id=None):
    started, request_id = monotonic(), request_id or uuid4()
    project, row = _scope(db, project_id, presentation_id, user_id, write=True)
    if decide and require_membership(db, user_id, project.organization_id).role not in ADMIN_ROLES:
        raise HTTPException(403, detail="Insufficient permissions")
    checkpoint = db.scalar(_checkpoints(row).where(PresentationCheckpoint.id == checkpoint_id)
                           .execution_options(populate_existing=True))
    if checkpoint is None:
        _missing()
    latest = _latest(db, row)
    _check_version(data.revision, latest)
    if checkpoint.revision != data.revision:
        _conflict("presentation_checkpoint_revision_mismatch", "The checkpoint belongs to a different presentation revision.",
                  checkpoint_revision=checkpoint.revision)
    expected_status = "pending" if decide else "draft"
    if checkpoint.status != expected_status:
        _conflict("presentation_checkpoint_transition_invalid", "This checkpoint cannot make that transition.")

    if not decide or data.decision == "approved":
        _require_complete(db, project, row, latest, user_id)
    now = datetime.now(timezone.utc)
    if decide:
        checkpoint.status, checkpoint.feedback = data.decision, data.feedback
        checkpoint.decided_by_user_id, checkpoint.decided_at = user_id, now
    else:
        checkpoint.status, checkpoint.request_comment = "pending", data.comment
        checkpoint.requested_by_user_id, checkpoint.requested_at = user_id, now
    design_activity.presentation_review(db, row, latest.version, checkpoint, user_id, request_id)
    _commit(db, row, user_id, latest.version, "presentation_checkpoint_" + checkpoint.status, request_id, started)
    return CheckpointRead.model_validate(checkpoint)


def approvals(db, project_id, presentation_id, user_id, *, version, offset, limit):
    _, row = _scope(db, project_id, presentation_id, user_id)
    if version is not None:
        _revision(db, row, _latest(db, row), version)
    return db.scalars(_checkpoints(row, version).order_by(PresentationCheckpoint.revision.desc(),
                      PresentationCheckpoint.created_at.desc(), PresentationCheckpoint.id).offset(offset).limit(limit)).all()
