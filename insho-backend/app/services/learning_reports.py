"""Save event reconciliation and learning reports, independent of design writes."""

import logging
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.models.ai_execution import AIExecution
from app.models.event import Event
from app.models.learning import LearningDisposition, LearningReport
from app.schemas.learning import DispositionRead, Insight, ReportDetail, ReportRead
from app.security import require_project_access
from app.services import design_activity, design_snapshot, design_version, learning_actuals, learning_insights, learning_reconciliation, procurement_scope
from app.services.learning_sources import Context, invalid
from app.services.spatial import _missing, _validate_event_project


logger = logging.getLogger("insho.learning")


def _verify_events(db, project, events):
    keys = {UUID(str(key)) for key in events}
    permitted = set(db.scalars(select(Event.id).where(Event.id.in_(keys), Event.project_id == project.id,
                                                     Event.organization_id == project.organization_id)))
    if permitted != keys:
        _missing()


def _query(project, kind):
    return select(LearningReport).where(LearningReport.project_id == project.id, LearningReport.organization_id == project.organization_id,
                                         LearningReport.kind == kind)


def _owned(db, project, kind, key):
    row = db.scalar(_query(project, kind).where(LearningReport.id == key))
    if row is None:
        _missing()
    return row


def _view(row):
    header = ReportRead.model_validate(row)
    return ReportDetail(**header.model_dump(), **row.content)


def _idempotent(db, project, kind, key, request_hash):
    if key is None:
        return None
    row = db.scalar(_query(project, kind).where(LearningReport.idempotency_key == key))
    if row and row.request_fingerprint != request_hash:
        design_version.conflict("learning_idempotency_mismatch", "This idempotency key was used with a different report request.")
    return row


def _save(db, project, user, kind, request_id, request_hash, key, *, events, content, fingerprint, warnings=(),
          version=None, execution_id=None, started):
    project = procurement_scope.project_scope(db, project.id, user, write=True)
    _verify_events(db, project, events)
    row_id = uuid4()
    version_id = version.id if version is not None else None
    event_id = version.event_id if version is not None else None
    ai = db.get(AIExecution, UUID(str(execution_id))) if execution_id is not None else None
    if ai is not None and (ai.project_id != project.id or ai.organization_id != project.organization_id
                           or (ai.metadata_json or {}).get("provenance", {}).get("source") != "learning_insights"):
        ai = None
    values = {"id": row_id, "organization_id": project.organization_id, "project_id": project.id, "kind": kind,
              "idempotency_key": key, "request_fingerprint": request_hash, "input_fingerprint": fingerprint,
              "event_id": event_id, "event_ids": list(map(str, events)), "design_version_id": version_id,
              "snapshot_id": version.snapshot_id if version is not None else None, "content": content, "warnings": list(warnings),
              "initiated_by_id": user, "method_version": learning_reconciliation.METHOD_VERSION,
              "ai_execution_id": ai.id if ai is not None else None}
    statement = insert(LearningReport).values(**values).on_conflict_do_nothing(constraint="uq_learning_report_idempotency")
    log_fields = {"request_id": str(request_id), "project_id": str(project.id), "event_ids": values["event_ids"],
                  "actor_id": str(user), "report_id": str(row_id), "report_kind": kind,
                  "provider": ai.provider if ai else None, "model": ai.model if ai else None}
    try:
        saved = db.scalar(statement.returning(LearningReport.id))
    except IntegrityError as exc:
        logger.warning("learning_report_conflict", extra={**log_fields, "error_code": "learning_relationship_conflict",
                                                          "duration_ms": int((monotonic() - started) * 1000)})
        learning_actuals.rollback_conflict(db, exc)
    if saved is None:
        existing = _idempotent(db, project, kind, key, request_hash)
        db.rollback()
        return _view(existing)
    design_activity.record(db, project.id, event_id, user, "learning_" + kind + "_generated", "learning_report", row_id,
                           version_id=version_id, request_id=request_id, execution_id=values["ai_execution_id"],
                           event_ids=values["event_ids"], method_version=values["method_version"], warning_codes=list(warnings))
    learning_actuals.commit(db)
    logger.info("learning_report_saved", extra={**log_fields, "outcome": "saved", "warning_codes": list(warnings),
                                                "duration_ms": int((monotonic() - started) * 1000)})
    return _view(db.get(LearningReport, row_id))


def reconcile(db, project_id, user, payload, *, request_id=None):
    started, request_id = monotonic(), request_id or uuid4()
    project = require_project_access(db, user, project_id, write=True)
    ctx = Context(db, project, payload.event_id, payload.design_version_id, user)
    content, fingerprint = learning_reconciliation.build(db, ctx, payload)
    existing = _idempotent(db, project, "reconciliation", fingerprint, fingerprint)
    if existing:
        return _view(existing)
    warnings = ["no_comparison_sources_selected"] if not content["comparisons"] else []
    return _save(db, project, user, "reconciliation", request_id, fingerprint, fingerprint, events=[ctx.event.id],
                 content=content, fingerprint=fingerprint, warnings=warnings, version=ctx.version, started=started)


async def generate(db, project_id, user, payload, *, request_id=None):
    started, request_id = monotonic(), request_id or uuid4()
    project = require_project_access(db, user, project_id, write=True)
    events = sorted(set(payload.event_ids))
    if len(events) != len(payload.event_ids):
        invalid("Each event may be selected once.")
    _verify_events(db, project, events)
    report_keys = sorted(payload.reconciliation_ids)
    reports = db.scalars(_query(project, "reconciliation").where(LearningReport.id.in_(report_keys))
                         .order_by(LearningReport.id)).all() if report_keys else []
    if len(reports) != len(set(report_keys)):
        _missing()
    report_events = [row.event_id for row in reports]
    if not set(report_events) <= set(events):
        _missing()
    if len(set(report_events)) != len(report_keys):
        invalid("Select at most one reconciliation per event in a learning report.")
    request_hash = design_snapshot.digest({"events": list(map(str, events)), "reports": [str(row.id) for row in reports],
                                           "ai_assisted": payload.ai_assisted})
    key = str(payload.idempotency_key) if payload.idempotency_key else None
    existing = _idempotent(db, project, "insights", key, request_hash)
    if existing:
        return _view(existing)
    insights, evidence, warnings, fingerprint = learning_insights.build(db, project, events, reports)
    execution_id = None
    if payload.ai_assisted:
        provider_key = design_snapshot.digest(["learning", str(project.id), key, fingerprint]) if key else None
        insights, execution_id, ai_warnings = await learning_insights.interpret(
            db, project, user, request_id, provider_key, insights,
        )
        warnings.extend(ai_warnings)
    content = {"insights": [item.model_dump(mode="json") for item in insights],
               "evidence": [item.model_dump(mode="json") for item in evidence]}
    return _save(db, project, user, "insights", request_id, request_hash, key, events=events, content=content,
                 fingerprint=fingerprint, warnings=warnings, execution_id=execution_id, started=started)


def history(db, project_id, user, kind, *, event_id, offset, limit):
    project = require_project_access(db, user, project_id)
    query = _query(project, kind)
    if event_id is not None:
        _validate_event_project(db, user, project, event_id)
        query = query.where(LearningReport.event_ids.contains([str(event_id)]))
    return [ReportRead.model_validate(row) for row in db.scalars(
        query.order_by(LearningReport.created_at.desc(), LearningReport.id).offset(offset).limit(limit))]


def read(db, project_id, kind, key, user):
    project = require_project_access(db, user, project_id)
    row = _owned(db, project, kind, key)
    _verify_events(db, project, row.event_ids)
    return _view(row)


def _insight(db, project, report_id, insight_id):
    row = _owned(db, project, "insights", report_id)
    _verify_events(db, project, row.event_ids)
    result = next((item for item in row.content["insights"] if item["id"] == str(insight_id)), None)
    if result is None:
        _missing()
    return Insight.model_validate(result)


def insight(db, project_id, report_id, insight_id, user):
    project = require_project_access(db, user, project_id)
    return _insight(db, project, report_id, insight_id)


def record_disposition(db, project_id, report_id, insight_id, user, payload, *, request_id=None):
    project = require_project_access(db, user, project_id, write=True)
    _insight(db, project, report_id, insight_id)
    row = LearningDisposition(id=uuid4(), project_id=project.id, report_id=report_id, insight_id=insight_id, actor_id=user,
                              **payload.model_dump())
    db.add(row)
    design_activity.record(db, project.id, None, user, "learning_recommendation_reviewed", "learning_disposition", row.id,
                           request_id=request_id, report_id=str(report_id), insight_id=str(insight_id), decision=row.decision)
    learning_actuals.commit(db)
    return DispositionRead.model_validate(row)


def dispositions(db, project_id, report_id, insight_id, user, *, offset, limit):
    project = require_project_access(db, user, project_id)
    _insight(db, project, report_id, insight_id)
    return [DispositionRead.model_validate(row) for row in db.scalars(select(LearningDisposition).where(
        LearningDisposition.project_id == project.id, LearningDisposition.report_id == report_id, LearningDisposition.insight_id == insight_id,
    ).order_by(LearningDisposition.created_at.desc(), LearningDisposition.id).offset(offset).limit(limit))]
