"""Shared scope, identity, and source validation for budget and purchasing workflows."""

from datetime import datetime, timezone
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import String, cast, column, exists, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from app.models.design_version import DesignVersion
from app.models.learning import LearningActual, LearningReport
from app.models.presentation import Presentation
from app.models.production import ProductionItem, ProductionRecord, ProductionSpecification, ProductionWorkflowRevision
from app.models.production_bom import ProductionBOM
from app.models.production_procurement import ProductionBudget, ProductionQuotation
from app.models.project import Project
from app.models.spatial import SpatialScene
from app.security import ADMIN_ROLES, require_membership, require_project_access
from app.services import production, production_bom
from app.services.spatial import _missing, _validate_event_project


def today():
    return datetime.now(timezone.utc).date()


def audit(user_id, action, **details):
    return {"action": action, "user_id": str(user_id), "at": datetime.now(timezone.utc).isoformat(), **details}


def scope_key(event_id):
    return str(event_id) if event_id is not None else "project"


def project_scope(db, project_id, user_id, *, write=False):
    project = require_project_access(db, user_id, project_id, write=write)
    if write:
        # Serialize sourcing mutations without reversing the scene/project lock order used by deletion.
        digest = sha256(b"production-sourcing:" + project.id.bytes).digest()
        db.execute(select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], "big", signed=True))))
        project = db.scalar(select(Project).where(Project.id == project.id)
                            .execution_options(populate_existing=True))
        if project is None:
            _missing()
    return project


def event_scope(db, project, user_id, event_id):
    return _validate_event_project(db, user_id, project, event_id)


def scoped_query(model, project, event_id=None):
    query = select(model).where(model.project_id == project.id)
    return query.where(model.event_id == event_id) if event_id is not None else query


def require_approver(db, user_id, project):
    if require_membership(db, user_id, project.organization_id).role not in ADMIN_ROLES:
        raise HTTPException(403, detail="Insufficient permissions")


def check_version(expected, current, scope):
    if expected != current:
        production._conflict(f"{scope}_version_conflict", f"Reload the current {scope.replace('_', ' ')}.",
                             current_version=current)


def flush(db):
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        production.logger.warning("production_transaction_failed", extra={"operation": "sourcing_flush"})
        production._conflict("procurement_relationship_conflict", "The change conflicts with a sourcing relationship.")


def protect_budget_bom_history(db, project_id, records):
    bom_ids = select(cast(ProductionBOM.id, String)).where(ProductionBOM.record_id.in_(records))
    source = func.jsonb_array_elements(ProductionBudget.sources["boms"]).table_valued(column("value", JSONB))
    matching_source = exists(select(1).select_from(source).where(source.c.value["id"].astext.in_(bom_ids)))
    linked = select(ProductionBudget.id).where(ProductionBudget.project_id == project_id,
                                                ProductionBudget.approved_at.is_not(None), matching_source)
    if db.scalar(linked.limit(1)) is not None:
        production._conflict("approved_budget_history", "An approved budget retains this BOM as a cost source.")


def protect_container_history(db, project, *, event_id=None):
    # The caller serializes with sourcing and locks existing scenes before checking these records.
    scene_ids = select(SpatialScene.id).where(SpatialScene.project_id == project.id)
    if event_id is not None:
        scene_ids = scene_ids.where(SpatialScene.event_id == event_id)
    records = select(ProductionRecord.id).where(ProductionRecord.scene_id.in_(scene_ids))
    items = select(ProductionItem.id).where(ProductionItem.record_id.in_(records))
    checks = (
        records.where(ProductionRecord.approved_at.is_not(None)),
        select(ProductionWorkflowRevision.record_id).where(ProductionWorkflowRevision.record_id.in_(records)),
        select(ProductionSpecification.id).where(ProductionSpecification.item_id.in_(items),
                                                 ProductionSpecification.approved_at.is_not(None)),
        select(ProductionBOM.id).where(ProductionBOM.record_id.in_(records), ProductionBOM.approved_at.is_not(None)),
        scoped_query(ProductionBudget, project, event_id).where(ProductionBudget.approved_at.is_not(None)),
        scoped_query(ProductionQuotation, project, event_id),
    )
    if any(db.scalar(query.limit(1)) is not None for query in checks):
        production._conflict("production_history_retained", "This project or event retains production history.")
    if event_id is not None:
        protect_budget_bom_history(db, project.id, records)
    learning = select(LearningReport.id).where(LearningReport.project_id == project.id)
    if event_id is not None:
        learning = learning.where(LearningReport.event_ids.contains([str(event_id)]))
    if db.scalar(learning.limit(1)) is not None or db.scalar(scoped_query(LearningActual, project, event_id).limit(1)) is not None:
        production._conflict("learning_history_retained", "This project or event retains actuals or learning reports.")
    if db.scalar(scoped_query(Presentation, project, event_id).limit(1)) is not None:
        production._conflict("presentation_history_retained", "This project or event retains presentation revisions.")
    if db.scalar(scoped_query(DesignVersion, project, event_id).limit(1)) is not None:
        production._conflict("design_history_retained", "This project or event retains design versions.")


def validate_bom_event(scene, event_id):
    if event_id is not None and scene.event_id != event_id:
        _missing()


class BOMSources:
    def __init__(self, db, project, user_id, *, lock_rates=False):
        self.db, self.project, self.user_id = db, project, user_id
        self.lock_rates = lock_rates
        self.cache = {}

    def get(self, bom_id):
        if bom_id not in self.cache:
            self.cache[bom_id] = self._get(bom_id)
        return self.cache[bom_id]

    def _get(self, bom_id):
        db, project = self.db, self.project
        result = db.execute(
            select(ProductionBOM, ProductionRecord, SpatialScene)
            .join(ProductionRecord, ProductionRecord.id == ProductionBOM.record_id)
            .join(SpatialScene, SpatialScene.id == ProductionRecord.scene_id)
            .where(ProductionBOM.id == bom_id, SpatialScene.project_id == project.id)
        ).one_or_none()
        if result is None:
            return None
        bom, record, scene = result
        # Use the same scene and aggregate read locks as Part 3.
        scene = production._scene(db, scene.id, self.user_id)
        record = production._record(db, scene, record.id)
        head = production_bom._head(db, record)
        sources = production_bom.Sources(db, scene, record, project.organization_id, lock_rates=self.lock_rates)
        view = production_bom._view(bom, production_bom._lines(db, bom.id), sources, latest=bom.id == head.id)
        return bom, scene, view

    def require(self, bom_id):
        result = self.get(bom_id)
        if result is None:
            _missing()
        return result


def bom_ready(view, *, approved=False):
    return view["latest"] and not view["stale"] and (not approved or view["approved_at"] is not None)
