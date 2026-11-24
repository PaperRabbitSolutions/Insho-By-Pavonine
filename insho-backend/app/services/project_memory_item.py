"""Versioned writes over the Phase 5 structured memory model."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.ai_execution import AIExecution
from app.models.design_version import DesignVersion, DesignVersionReview
from app.models.presentation import Presentation, PresentationCheckpoint
from app.models.project_memory_item import ProjectMemoryItem, ProjectMemoryRevision
from app.repositories.project_memory_item import ProjectMemoryItemRepository
from app.schemas.project_memory import ProjectMemoryItemResponse
from app.security import ADMIN_ROLES, require_membership, require_project_access
from app.services import design_activity, procurement_scope
from app.services.design_snapshot import execution_in_scope, in_scope
from app.services.design_version import conflict
from app.services.spatial import _missing, _row_state, _validate_event_project


CATEGORY_KINDS = {"must_have": "requirement", "must_avoid": "constraint", "approved": "decision", "rejected": "decision",
                  "client_preferences": "preference", "design_dna": "fact", "budget": "constraint", "venue_dimensions": "fact",
                  "guest_count": "fact", "previous_decisions": "decision", "vendor_constraints": "constraint",
                  "production_constraints": "constraint", "safety_constraints": "constraint"}
_LEGACY_CATEGORIES = {"preference": "client_preferences", "decision": "previous_decisions"}
_OUTCOMES = {"approved", "rejected"}


def _classify(values):
    if values.get("category") is None:
        values["category"] = _LEGACY_CATEGORIES.get(values["kind"])
    elif values.get("kind") is None:
        values["kind"] = CATEGORY_KINDS[values["category"]]
    elif CATEGORY_KINDS[values["category"]] != values["kind"]:
        raise HTTPException(422, detail="The memory kind does not match its category.")


def _structured_value(category, content):
    if category == "guest_count" and "count" in content:
        if type(content["count"]) is not int or content["count"] < 0:
            raise HTTPException(422, detail="Guest count must be a non-negative integer.")
    if category == "budget" and "amount" in content:
        try:
            amount = Decimal(str(content["amount"]))
            currency = content.get("currency")
            if not amount.is_finite() or amount < 0 or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError
        except (InvalidOperation, ValueError) as exc:
            raise HTTPException(422, detail="Supply a non-negative amount and a three-letter uppercase currency.") from exc


def _verify_links(db, project, user_id, values, *, verifying):
    event_id, category = values["event_id"], values["category"]
    _validate_event_project(db, user_id, project, event_id, write=True)
    if values.get("linked_version_id") is not None:
        row = db.get(DesignVersion, values["linked_version_id"])
        if not in_scope(row, project, event_id):
            _missing()
    if values.get("decision_id") is not None:
        decision = db.get(PresentationCheckpoint, values["decision_id"], populate_existing=True)
        sheet = db.get(Presentation, decision.presentation_id) if decision else None
        if not in_scope(sheet, project, event_id):
            _missing()
        if decision.decided_at is None or (category in _OUTCOMES and category != decision.status):
            conflict("memory_decision_mismatch", "Select a final decision that matches the memory category.")
        bound = db.scalar(select(DesignVersionReview.version_id).where(
            DesignVersionReview.presentation_id == decision.presentation_id,
            DesignVersionReview.presentation_revision == decision.revision,
        ))
        if values.get("linked_version_id") is not None and bound != values["linked_version_id"]:
            conflict("memory_version_decision_mismatch", "The decision must refer to the linked design version.")
        values["linked_version_id"] = bound
    if values.get("execution_id") is not None and not execution_in_scope(db.get(AIExecution, values["execution_id"]), project, event_id):
        _missing()
    if verifying:
        if require_membership(db, user_id, project.organization_id).role not in ADMIN_ROLES:
            raise HTTPException(403, detail="Insufficient permissions")
        if category in _OUTCOMES and values.get("decision_id") is None:
            conflict("memory_decision_required", "Link the matching presentation decision to verify an approval or rejection.")


def _record(db, row, action, actor, *, reason=None, previous=None, request_id=None, event_id=None):
    fields = _row_state(row)
    db.add(ProjectMemoryRevision(item_id=row.id, version=row.version, state=fields, action=action,
                                 reason=reason, changed_by_user_id=actor))
    design_activity.record(db, row.project_id, event_id or row.event_id, actor, "memory_" + action, "memory_item", row.id,
                           version_id=row.linked_version_id, execution_id=row.execution_id, reason=reason, request_id=request_id,
                           previous_revision=previous, revision=row.version, category=row.category,
                           decision_id=str(row.decision_id) if row.decision_id else None)


def _initial_history(db, row):
    if db.get(ProjectMemoryRevision, (row.id, row.version)) is None:
        db.add(ProjectMemoryRevision(item_id=row.id, version=row.version, state=_row_state(row), action="baseline",
                                     changed_by_user_id=row.created_by_user_id, created_at=row.updated_at))


def _commit(db):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, detail={"code": "memory_relationship_conflict", "message": "A linked memory source changed."}) from exc


class ProjectMemoryItemService:
    @staticmethod
    def create(db, data, user_id, *, request_id=None):
        project = procurement_scope.project_scope(db, data.project_id, user_id, write=True)
        payload = data.model_dump(exclude={"reason"})
        _classify(payload)
        _structured_value(payload["category"], payload["content"])
        _verify_links(db, project, user_id, payload, verifying=data.verification == "verified")
        row = ProjectMemoryItem(id=uuid4(), created_by_user_id=user_id, **payload)
        db.add(row)
        procurement_scope.flush(db)
        _record(db, row, "created", user_id, reason=data.reason, request_id=request_id)
        response = ProjectMemoryItemResponse.model_validate(row)
        _commit(db)
        return response

    @staticmethod
    def get_by_id(db, item_id, user_id):
        row = ProjectMemoryItemRepository.get_by_id(db, item_id)
        if row is None:
            _missing()
        project = require_project_access(db, user_id, row.project_id)
        _validate_event_project(db, user_id, project, row.event_id)
        return row

    @staticmethod
    def get_by_project(db, project_id, user_id, *, include_archived=False, category=None, event_id=None, offset=0, limit=None):
        project = require_project_access(db, user_id, project_id)
        _validate_event_project(db, user_id, project, event_id)
        return ProjectMemoryItemRepository.get_by_project(db, project_id, include_archived=include_archived,
                                                          category=category, event_id=event_id, offset=offset, limit=limit)

    @staticmethod
    def history(db, item_id, user_id, *, offset, limit):
        row = ProjectMemoryItemService.get_by_id(db, item_id, user_id)
        return db.scalars(select(ProjectMemoryRevision).where(ProjectMemoryRevision.item_id == row.id)
                          .order_by(ProjectMemoryRevision.version.desc()).offset(offset).limit(limit)).all()

    @staticmethod
    def update(db, item_id, data, user_id, *, request_id=None):
        existing = ProjectMemoryItemService.get_by_id(db, item_id, user_id)
        project = procurement_scope.project_scope(db, existing.project_id, user_id, write=True)
        row = ProjectMemoryItemRepository.get_by_id(db, item_id, lock=True)
        if row is None:
            _missing()
        if data.expected_version is not None and data.expected_version != row.version:
            conflict("memory_version_conflict", "Refresh the current memory revision.", current_version=row.version)
        payload = data.model_dump(exclude_unset=True, exclude={"expected_version", "reason"})
        for field in {"kind", "title", "content", "source_type", "status", "verification"} & payload.keys():
            if payload[field] is None:
                raise HTTPException(422, detail=f"{field} cannot be null")
        _preserve_provenance(row, payload)
        fields = ("event_id", "kind", "category", "content", "verification", "linked_version_id", "decision_id", "execution_id")
        values = {**{field: getattr(row, field) for field in fields}, **payload}
        if "category" in payload and "kind" not in payload and payload["category"]:
            values["kind"] = None
        _classify(values)
        if row.category in _OUTCOMES and values["category"] != row.category:
            conflict("memory_decision_category_immutable", "Record a separate memory item for a different decision.")
        _structured_value(values["category"], values["content"])
        _verify_links(db, project, user_id, values, verifying=values["verification"] == "verified" and (
            row.verification != "verified" or any(field in payload for field in ("category", "content", "decision_id", "linked_version_id"))
        ))
        changes = {key: value for key, value in values.items() if getattr(row, key) != value}
        if changes:
            if row.status == "archived":
                conflict("memory_archived", "Archive history is read-only. Create a new item for a new constraint or decision.")
            _initial_history(db, row)
            previous = row.version
            for key, value in changes.items():
                setattr(row, key, value)
            row.version += 1
            action = "archived" if row.status == "archived" else "updated"
            if action == "archived":
                row.archived_at = datetime.now(timezone.utc)
            procurement_scope.flush(db)
            _record(db, row, action, user_id, reason=data.reason, previous=previous, request_id=request_id)
        response = ProjectMemoryItemResponse.model_validate(row)
        _commit(db)
        return response

    @staticmethod
    def archive_for_event(db, project_id, event_id, user_id):
        # The event service holds the same project transaction lock as memory writes.
        rows = db.scalars(select(ProjectMemoryItem).where(ProjectMemoryItem.project_id == project_id,
                          ProjectMemoryItem.event_id == event_id).order_by(ProjectMemoryItem.id).with_for_update()).all()
        for row in rows:
            _initial_history(db, row)
            row.status, row.event_id = "archived", None
            row.archived_at = row.archived_at or datetime.now(timezone.utc)
            row.version += 1
            db.flush()
            _record(db, row, "event_detached", user_id, reason="The associated event was deleted.",
                    previous=row.version - 1, event_id=event_id)


def _preserve_provenance(row, payload):
    for field in ("source_reference", "source_type", "linked_version_id", "decision_id"):
        previous = getattr(row, field)
        if field in payload and previous is not None and payload[field] != previous:
            conflict("memory_source_immutable", "Create a new memory item for a different source or decision.", field=field)
    if "event_id" in payload and payload["event_id"] != row.event_id and (row.linked_version_id or row.decision_id or row.execution_id):
        conflict("memory_source_immutable", "The event scope of a linked memory item is fixed.", field="event_id")
