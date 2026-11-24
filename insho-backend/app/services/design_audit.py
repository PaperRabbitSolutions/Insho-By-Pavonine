"""Persist and retrieve Section 28 audits tied to immutable design versions."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.models.ai_execution import AIExecution
from app.models.design_audit import DesignAudit
from app.models.design_version import DesignSnapshot, DesignVersion
from app.schemas.design_audit import AuditOptions, AuditRead, AuditWarning, Dimension, DimensionResult
from app.security import require_project_access
from app.services import design_activity, design_audit_ai, design_audit_rules as rules, design_version
from app.services.design_audit_context import load, spatial_reports
from app.services.spatial import _missing, _validate_event_project


logger = logging.getLogger("insho.design_audit")
_STALE_RUN_AFTER = timedelta(minutes=10)
_COMPLETED = {"completed", "completed_with_warnings"}


def _owned(db, project, key):
    row = db.scalar(select(DesignAudit).where(DesignAudit.id == key, DesignAudit.project_id == project.id,
                                             DesignAudit.organization_id == project.organization_id))
    if row is None:
        _missing()
    return row


def _header(row):
    result = AuditRead.model_validate(row)
    if row.status == "running" and row.started_at < datetime.now(timezone.utc) - _STALE_RUN_AFTER:
        warning = AuditWarning(code="audit_interrupted", message="The request ended before a final audit was recorded.", retryable=True)
        return result.model_copy(update={"status": "interrupted", "warnings": [*result.warnings, warning], "retryable": True})
    return result


def _evaluate_deterministic(ctx, options):
    reports, warnings = spatial_reports(ctx)
    result = {dimension: rules.spatial(ctx, reports, dimension)
              for dimension in ("scale_proportion", "spatial_feasibility", "furniture_decor")}
    result.update(accessibility=rules.accessibility(ctx, reports, options.accessibility_clearance_ids),
                  budget_efficiency=rules.budget(ctx), production_complexity=rules.production(ctx),
                  technical_completeness=rules.production(ctx, technical=True), sustainability=rules.sustainability(ctx),
                  led_suitability=rules.led(ctx), risk_safety_flags=rules.safety(ctx, reports))
    return result, warnings


def _distinct(items):
    return list({(item.kind, item.id, item.revision, item.field): item for item in items}.values())


def _finish(db, audit_id, version, user_id, *, results=None, warnings, execution_id=None, status=None):
    row = db.get(DesignAudit, audit_id, populate_existing=True, with_for_update=True)
    if row.status != "running":
        design_version.conflict("audit_already_finalized", "The audit has already been finalized.")
    row.warnings = [item.model_dump(mode="json") for item in warnings]
    row.completed_at = datetime.now(timezone.utc)
    row.retryable = any(item.retryable for item in warnings)
    if execution_id is not None:
        execution = db.get(AIExecution, UUID(str(execution_id)))
        if (execution is not None and execution.organization_id == row.organization_id and execution.project_id == row.project_id
                and (execution.metadata_json or {}).get("provenance", {}).get("audit_id") == str(row.id)):
            row.ai_execution_id, row.provider, row.model = execution.id, execution.provider, execution.model
    if results is not None:
        ordered = [results[key] for key in Dimension.__args__]
        row.results = [item.model_dump(mode="json") for item in ordered]
        row.evidence_sources = [item.model_dump(mode="json") for item in _distinct(
            item.source for result in ordered for item in result.evidence)]
        count = sum(item.state == "evaluated" for item in ordered)
        row.summary = {"evaluated": count, "not_evaluated": len(ordered) - count,
                       "high_priority_dimensions": [item.dimension for item in ordered if item.severity == "high"],
                       "missing_inputs": sorted({key for item in ordered for key in item.missing_inputs})}
        incomplete = any(item.state == "not_evaluated" and item.dimension != "risk_safety_flags" for item in ordered)
        status = "completed_with_warnings" if warnings or incomplete else "completed"
    row.status = status
    design_activity.record(db, row.project_id, row.event_id, user_id, "design_audit_" + status, "design_audit", row.id,
                           version_id=row.design_version_id, branch_id=version.branch_id, request_id=row.request_id,
                           execution_id=row.ai_execution_id, configuration_version=row.configuration_version,
                           result_dimensions=len(row.results), warning_codes=[item.code for item in warnings])
    details = {"request_id": str(row.request_id), "project_id": str(row.project_id),
               "event_id": str(row.event_id) if row.event_id is not None else None, "actor_id": str(user_id),
               "audit_id": str(row.id), "design_version_id": str(row.design_version_id), "outcome": status,
               "duration_ms": int((row.completed_at - row.started_at).total_seconds() * 1000),
               "provider": row.provider, "model": row.model, "warning_codes": [item.code for item in warnings]}
    db.commit()
    logger.info("design_audit_finalized", extra=details)
    return row


def _detail(db, project, row):
    results = [DimensionResult.model_validate(part) for part in row.results]
    if any(item.source.kind == "asset" for result in results for item in result.evidence):
        capture = db.get(DesignSnapshot, row.snapshot_id).state
        unavailable = set(design_version._unavailable_assets(db, project, row.event_id, capture))
        for result in results:
            for item in result.evidence:
                if item.source.kind == "asset":
                    item.available = str(item.source.id) not in unavailable
                    if not item.available:
                        item.value = None
    return {**_header(row).model_dump(), "results": results, "evidence_sources": row.evidence_sources,
            "score_scale": rules.SCORE_SCALE, "safety_scope": rules.SAFETY_SCOPE}


async def start(db, project_id, version_id, user_id, options, *, request_id=None, retry_of_id=None):
    project = require_project_access(db, user_id, project_id, write=True)
    version = design_version.owned(db, DesignVersion, project.id, version_id)
    _validate_event_project(db, user_id, project, version.event_id)
    ctx = load(db, project, version)
    audit_id, request_id, workspace_id = uuid4(), request_id or uuid4(), project.workspace_id
    row = DesignAudit(id=audit_id, organization_id=project.organization_id, project_id=project.id, event_id=version.event_id,
                      design_version_id=version.id, snapshot_id=version.snapshot_id, initiated_by_user_id=user_id,
                      request_id=request_id, retry_of_id=retry_of_id, status="running", configuration_version=rules.CONFIGURATION_VERSION,
                      options=options.model_dump(mode="json"), input_fingerprint=ctx.fingerprint(), results=[],
                      warnings=[], evidence_sources=[], retryable=False)
    db.add(row)
    design_activity.record(db, project.id, version.event_id, user_id, "design_audit_started", "design_audit", audit_id,
                           version_id=version.id, branch_id=version.branch_id, request_id=request_id,
                           snapshot_id=str(version.snapshot_id), configuration_version=row.configuration_version)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning("design_audit_write_conflict", extra={"request_id": str(request_id), "project_id": str(project_id),
                       "actor_id": str(user_id), "design_version_id": str(version_id), "error_code": "audit_write_conflict"})
        raise HTTPException(409, detail={"code": "audit_write_conflict",
                                        "message": "The audit or a linked record changed during the write. Retry the request."}) from exc
    execution_id = None
    warnings = list(ctx.warnings)
    try:
        deterministic, spatial_warnings = await run_in_threadpool(_evaluate_deterministic, ctx, options)
        warnings.extend(spatial_warnings)
        subjective, ai_warnings, execution_id = await design_audit_ai.evaluate(
            db, ctx, project_id, workspace_id, user_id, audit_id, request_id, enabled=options.ai_assisted,
        )
        warnings.extend(ai_warnings)
        completed = _finish(db, audit_id, version, user_id, results={**deterministic, **subjective}, warnings=warnings,
                            execution_id=execution_id)
    except asyncio.CancelledError:
        db.rollback()
        _finish(db, audit_id, version, user_id, warnings=[*warnings, AuditWarning(
            code="audit_cancelled", message="The evaluation request was cancelled.", retryable=True)], status="cancelled")
        raise
    except Exception as exc:
        db.rollback()
        logger.error("design_audit_failed", extra={"audit_id": str(audit_id), "error_type": type(exc).__name__})
        invalid = isinstance(exc, (KeyError, ValidationError))
        completed = _finish(db, audit_id, version, user_id, warnings=[*warnings, AuditWarning(
            code="audit_captured_input_invalid" if invalid else "audit_execution_failed",
            message="A captured input did not match the audit contract." if invalid else "The audit did not finish. A retry is available.",
            retryable=not invalid)], status="failed", execution_id=execution_id)
    return _detail(db, project, completed)


def get(db, project_id, key, user_id):
    project = require_project_access(db, user_id, project_id)
    row = _owned(db, project, key)
    _validate_event_project(db, user_id, project, row.event_id)
    return _detail(db, project, row)


async def retry(db, project_id, key, user_id, *, request_id=None):
    project = require_project_access(db, user_id, project_id, write=True)
    row = _owned(db, project, key)
    if not _header(row).retryable:
        design_version.conflict("audit_retry_unavailable", "This audit has no retryable outcome. Start a new audit if needed.")
    return await start(db, project.id, row.design_version_id, user_id, AuditOptions.model_validate(row.options),
                       request_id=request_id, retry_of_id=row.id)


def history(db, project_id, user_id, *, version_id, event_id, status, offset, limit):
    project = require_project_access(db, user_id, project_id)
    _validate_event_project(db, user_id, project, event_id)
    query = select(DesignAudit).where(DesignAudit.project_id == project.id, DesignAudit.organization_id == project.organization_id)
    if version_id is not None:
        version = design_version.owned(db, DesignVersion, project.id, version_id)
        _validate_event_project(db, user_id, project, version.event_id)
        query = query.where(DesignAudit.design_version_id == version.id)
    if event_id is not None:
        query = query.where(DesignAudit.event_id == event_id)
    if status in ("running", "interrupted"):
        cutoff = datetime.now(timezone.utc) - _STALE_RUN_AFTER
        age = DesignAudit.started_at < cutoff if status == "interrupted" else DesignAudit.started_at >= cutoff
        query = query.where(and_(DesignAudit.status == "running", age))
    elif status:
        query = query.where(DesignAudit.status == status)
    return [_header(row) for row in db.scalars(query.order_by(DesignAudit.started_at.desc(), DesignAudit.id).offset(offset).limit(limit))]


def results(db, project_id, key, user_id, *, dimension=None):
    result = get(db, project_id, key, user_id)
    if result["status"] not in _COMPLETED:
        design_version.conflict("audit_results_unavailable", "This audit has no finalized results.", status=result["status"])
    return [item for item in result["results"] if dimension is None or item.dimension == dimension]


def evidence_items(db, project_id, key, user_id, *, dimension):
    return list({item.id: item for part in results(db, project_id, key, user_id, dimension=dimension) for item in part.evidence}.values())


def recommendations(db, project_id, key, user_id, *, dimension):
    return [item for part in results(db, project_id, key, user_id, dimension=dimension) for item in part.recommendations]


def compare(db, project_id, first_id, second_id, user_id):
    project = require_project_access(db, user_id, project_id)
    first, second = (_owned(db, project, key) for key in (first_id, second_id))
    if first.event_id != second.event_id:
        _missing()
    _validate_event_project(db, user_id, project, first.event_id)
    if first.status not in _COMPLETED or second.status not in _COMPLETED:
        design_version.conflict("audit_comparison_unavailable", "Select two completed audits.")
    prior, current = ({item["dimension"]: DimensionResult.model_validate(item) for item in row.results} for row in (first, second))
    entries = []
    for dimension in Dimension.__args__:
        left, right = prior[dimension], current[dimension]
        comparable = (first.configuration_version == second.configuration_version and left.state == right.state == "evaluated"
                      and left.evaluator.method == right.evaluator.method == "deterministic"
                      and left.evaluator.criterion == right.evaluator.criterion)
        if comparable and dimension == "accessibility":
            comparable = sorted(first.options["accessibility_clearance_ids"]) == sorted(second.options["accessibility_clearance_ids"])
        if comparable and dimension in {"accessibility", "spatial_feasibility", "scale_proportion", "furniture_decor"}:
            configurations = [{item.value["configuration_fingerprint"] for item in part.evidence
                               if item.value and "configuration_fingerprint" in item.value} for part in (left, right)]
            comparable = configurations[0] == configurations[1]
        entries.append({"dimension": dimension, "from_state": left.state, "to_state": right.state,
                        "from_score": left.score, "to_score": right.score, "from_confidence": left.confidence,
                        "to_confidence": right.confidence, "comparable": comparable,
                        "reason": "Matching deterministic criterion and configuration." if comparable else
                                  "A comparable deterministic score is unavailable. Review the evidence and coverage for each audit.",
                        "from_finding_codes": sorted({item.code for item in left.findings}),
                        "to_finding_codes": sorted({item.code for item in right.findings})})
    return {"from_evaluation_id": first.id, "to_evaluation_id": second.id, "from_design_version_id": first.design_version_id,
            "to_design_version_id": second.design_version_id, "dimensions": entries}
