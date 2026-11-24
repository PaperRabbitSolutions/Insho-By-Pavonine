"""Resolve learning inputs through the existing scoped production, design and asset records."""

from datetime import datetime, timezone
from decimal import Decimal, localcontext
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.models.asset import Asset
from app.models.design_audit import DesignAudit
from app.models.design_version import DesignVersion, DesignVersionReview
from app.models.learning import LearningActual
from app.models.presentation import PresentationCheckpoint
from app.models.production import ProductionItem, ProductionRecord, ProductionWorkflowRevision
from app.models.production_bom import MaterialDefinition, ProductionBOMLine
from app.models.production_procurement import ProductionProjectSupplier, ProductionSupplier
from app.models.spatial import SpatialScene
from app.schemas.learning import Source, Window
from app.services import design_snapshot, design_version
from app.services.asset import AssetService
from app.services.production_costing import convert_quantity, decimal_text
from app.services.spatial import _missing, _validate_event_project


def invalid(message):
    raise HTTPException(422, detail=message)


def reference(kind, key, revision=None, parent=None):
    return Source(kind=kind, id=key, revision=revision, parent_id=parent)


def elapsed(window):
    delta = window.end - window.start
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds).scaleb(-6)


def distinct(sources):
    return list({(item.kind, item.id, item.revision, item.parent_id): item for item in sources}.values())


class Context:
    def __init__(self, db, project, event_id, version_id, user):
        self.db, self.project, self.user = db, project, user
        self.event = _validate_event_project(db, user, project, event_id)
        self.version = None
        self.state = {}
        if version_id is not None:
            self.version = design_version.owned(db, DesignVersion, project.id, version_id)
            if self.version.event_id != event_id:
                _missing()
            self.state = design_version._snapshot(db, self.version).state
        self.lines = {key: (bom_id, bom, line) for bom_id, bom in self.state.get("boms", {}).items()
                      for key, line in bom["lines"].items()}

    def require_version(self):
        if self.version is None:
            invalid("This outcome requires an exact event design version.")

    def asset(self, key):
        row = AssetService.get(self.db, key, self.user, read_lock=True)
        if not design_snapshot.asset_scope(row, self.project, self.event.id):
            _missing()
        return row

    def supplier(self, key):
        row = self.db.get(ProductionSupplier, key)
        link = self.db.get(ProductionProjectSupplier, (self.project.id, key))
        if row is None or row.organization_id != self.project.organization_id or link is None:
            _missing()
        return reference("supplier", row.id, row.version)

    def material(self, key):
        row = self.db.get(MaterialDefinition, key)
        if row is None or row.organization_id != self.project.organization_id:
            _missing()
        return reference("material", key, row.current_version)

    def component(self, key):
        row = self.db.get(ProductionItem, key)
        scene = self.db.get(SpatialScene, row.scene_id) if row is not None else None
        if scene is None or scene.project_id != self.project.id or scene.event_id != self.event.id:
            _missing()
        return reference("component", key, row.version, row.record_id)

    def audit(self, key):
        self.require_version()
        row = design_version.owned(self.db, DesignAudit, self.project.id, key)
        if row.organization_id != self.project.organization_id or row.design_version_id != self.version.id or row.event_id != self.event.id:
            _missing()
        if row.status not in {"completed", "completed_with_warnings"}:
            design_version.conflict("learning_audit_incomplete", "Select a finalized audit for this design version.")
        return reference("design_audit", key)

    def artifact(self, key):
        self.require_version()
        artifact = self.state.get("artifacts", {}).get(str(key))
        if artifact is None:
            _missing()
        return artifact, reference("design_artifact", key, artifact["version"])

    def workflow(self, key):
        record = self.db.get(ProductionRecord, key.record_id)
        scene = self.db.get(SpatialScene, record.scene_id) if record else None
        plan = self.db.get(ProductionWorkflowRevision, (key.record_id, key.revision))
        if scene is None or scene.project_id != self.project.id or scene.event_id != self.event.id or plan is None:
            _missing()
        task = next((item for item in plan.tasks if item["id"] == str(key.task_id)), None)
        if task is None:
            _missing()
        return task, reference("workflow_task", key.task_id, key.revision, key.record_id)

    def incident(self, key):
        row = self.db.get(LearningActual, key)
        if row is None or row.event_id != self.event.id or row.project_id != self.project.id or row.organization_id != self.project.organization_id:
            _missing()
        if row.kind not in {"weather_incident", "production_incident", "delay"}:
            invalid("A vendor incident reference must identify an incident or delay outcome.")
        return row

    def bom_line(self, key):
        self.require_version()
        saved = self.lines.get(str(key))
        if saved is None:
            _missing()
        bom_id, bom, line = saved
        # Earlier snapshots lack the catalogue identity and category. Recover them from the same immutable Phase 10 line.
        if "category" not in line:
            row = self.db.get(ProductionBOMLine, key)
            if row is not None and str(row.bom_id) == bom_id:
                dependencies = row.inputs.get("dependencies") or {}
                line = {**line, "category": row.category, "dependencies": dependencies}
        return bom, line, reference("bom_line", key, bom["version"], bom_id)

    def cost_basis(self, selector):
        self.require_version()
        if selector.kind == "budget":
            budget = self.state.get("budgets", {}).get(str(selector.id))
            if budget is None:
                _missing()
            summary = budget["summary"]["estimated"]
            return baseline("cost", reference("budget", selector.id, budget["version"]), summary.get("total"),
                            budget["currency"], "budget_total", budget["currency"])
        bom, line, source = self.bom_line(selector.id)
        if line["basis"] != "estimated":
            invalid("Use an estimated BOM line as the cost basis.")
        return baseline("cost", source, line["total"], bom["currency"], line.get("category", "other"), bom["currency"])

    def quantity_basis(self, key):
        _, line, source = self.bom_line(key)
        if line["basis"] != "estimated":
            invalid("Use an estimated BOM line as the quantity basis.")
        dependencies = line.get("dependencies", {})
        group_id = ((dependencies.get("material") or {}).get("definition_id")
                    or (dependencies.get("component") or {}).get("id") or source.id)
        return baseline("quantity", source, line["quantity"], line["unit"], "consumption", str(group_id))

    def installation_basis(self, activity):
        self.require_version()
        task, source = self.workflow(activity)
        if task["kind"] != "installation":
            invalid("Select an installation task for installation actuals.")
        planned = plan_window(task)
        duration = task.get("duration_minutes")
        value = Decimal(duration) * 60 if duration is not None else elapsed(planned) if planned else None
        return baseline("installation", source, value, "s", "installation", "installation")


def plan_window(task):
    return Window(start=task["window_start"], end=task["window_end"]) if task.get("window_start") and task.get("window_end") else None


def baseline(metric, source, value, unit, category, group, *, variant=""):
    key = "/".join(str(field) for field in (metric, source.kind, source.parent_id or "", source.id, source.revision or "", variant))
    return {"metric": metric, "source": source.model_dump(mode="json"), "key": key,
            "estimate": decimal_text(Decimal(value)) if value is not None else None, "estimate_unit": unit,
            "category": category, "comparison_group": group}


def _window_actual(window, task):
    planned = plan_window(task)
    result = {"duration_seconds": decimal_text(elapsed(window))}
    if planned is not None:
        end_delay = max(Decimal(0), elapsed(Window(start=planned.end, end=window.end))) if window.end > planned.end else Decimal(0)
        start_delay = max(Decimal(0), elapsed(Window(start=planned.start, end=window.start))) if window.start > planned.start else Decimal(0)
        result.update(planned_window=planned.model_dump(mode="json"), start_delay_seconds=decimal_text(start_delay),
                      completion_delay_seconds=decimal_text(end_delay))
    else:
        result.update(planned_window=None, start_delay_seconds=None, completion_delay_seconds=None)
    return {**result, "depends_on": task["depends_on"]}


def _actual(metric, value, unit):
    return {"comparison": metric, "actual": decimal_text(value), "actual_unit": unit}


def _cost(ctx, data):
    basis = ctx.cost_basis(data.baseline)
    if basis["category"] != data.category:
        invalid("The cost category must match the selected estimate. Budget totals use budget_total.")
    suppliers = [ctx.supplier(data.supplier_id)] if data.supplier_id else []
    return _actual(basis, data.amount, data.currency), [Source.model_validate(basis["source"]), *suppliers], data.spent_on


def _quantity(ctx, data):
    _, line, source = ctx.bom_line(data.bom_line_id)
    basis = ctx.quantity_basis(data.bom_line_id)
    links = [source]
    canonical = line.get("dependencies", {})
    for kind in ("material", "component"):
        saved = canonical.get(kind) or {}
        id_field, revision_field = ("definition_id", "definition_version") if kind == "material" else ("id", "version")
        key = saved.get(id_field)
        supplied = getattr(data, kind + "_id")
        if key and supplied and str(supplied) != key:
            invalid("The supplied material or component differs from the captured BOM line.")
        if key:
            links.append(reference(kind, key, saved.get(revision_field)))
        elif supplied:
            links.append(getattr(ctx, kind)(supplied))
    if len(links) == 1:
        invalid("A quantity outcome requires a canonical material or component.")
    if data.wastage is not None:
        try:
            waste = convert_quantity(data.wastage.value, data.wastage.unit, data.consumed.unit)
        except ValueError:
            invalid("Wastage and consumed quantity require compatible units.")
        if waste > data.consumed.value:
            invalid("Wastage cannot exceed the recorded consumed quantity.")
    result = _actual(basis, data.consumed.value, data.consumed.unit)
    return result, links, ctx.event.event_date


def _labour(ctx, data):
    ctx.require_version()
    _, source = ctx.workflow(data.activity)
    plan = data.planned_team_hours
    metric = baseline("labour", source, plan.value if plan else None, "h", data.category, data.category.casefold(),
                      variant=data.category.casefold())
    with localcontext() as arithmetic:
        arithmetic.prec = 50
        hours = data.hours * data.worker_count if data.hours_basis == "per_worker" else data.hours
    links = [source, ctx.supplier(data.supplier_id)] if data.supplier_id else [source]
    return _actual(metric, hours, "h"), links, data.work_window.end.date()


def _installation(ctx, data):
    task, source = ctx.workflow(data.activity)
    metric = ctx.installation_basis(data.activity)
    result = {**_actual(metric, elapsed(data.installed), "s"), **_window_actual(data.installed, task)}
    return result, [source], data.installed.end.date()


def _wastage(ctx, data):
    ctx.require_version()
    material = ctx.material(data.material_id)
    sources = [material]
    variant = ""
    if data.activity:
        _, activity = ctx.workflow(data.activity)
        sources.append(activity)
        variant = "/".join(str(value) for value in (activity.parent_id, activity.id, activity.revision))
    plan = data.expected
    basis = baseline("wastage", material, plan.value if plan else None, data.expected_unit,
                     data.category, str(data.material_id), variant=variant)
    return _actual(basis, data.wasted.value, data.wasted.unit), sources, ctx.event.event_date


def _vendor(ctx, data):
    links = [ctx.supplier(data.supplier_id)]
    for key in sorted(set(data.incident_ids)):
        row = ctx.incident(key)
        links.append(reference("actual", key, row.revision if row.status == "verified" else None))
    return {}, links, data.period.end.date()


def _feedback(ctx, data):
    ctx.require_version()
    pin = ctx.state.get("presentation")
    sources = [reference("presentation", pin["id"], pin["revision"])] if pin else []
    visible = {key: artifact["version"] for artifact in ctx.state.get("artifacts", {}).values() for key in artifact["elements"]}
    for key in sorted(set(data.element_ids)):
        if str(key) not in visible:
            _missing()
        sources.append(reference("design_element", key, visible[str(key)]))
    return {}, sources, None


def _concept(ctx, data):
    ctx.require_version()
    pin = ctx.state.get("presentation")
    binding = ctx.db.get(DesignVersionReview, ctx.version.id)
    row = ctx.db.get(PresentationCheckpoint, data.checkpoint_id, populate_existing=True)
    if row is None or not binding or not pin or (row.presentation_id, row.revision) != (binding.presentation_id, binding.presentation_revision):
        _missing()
    if row.decided_at is None or row.status not in {"approved", "rejected"}:
        design_version.conflict("learning_decision_not_final", "Select a final approval or rejection for the version.")
    sources = [reference("presentation", row.presentation_id, row.revision),
               reference("presentation_checkpoint", row.id, row.revision, row.presentation_id)]
    if data.artifact_id:
        _, source = ctx.artifact(data.artifact_id)
        sources.append(source)
    if data.memory_item_id:
        from app.services.project_memory_item import ProjectMemoryItemService

        item = ProjectMemoryItemService.get_by_id(ctx.db, data.memory_item_id, ctx.user)
        if item.project_id != ctx.project.id or item.event_id != ctx.event.id:
            _missing()
        if (item.status, item.verification, item.linked_version_id, item.decision_id, item.category) != (
            "active", "verified", ctx.version.id, row.id, row.status
        ):
            design_version.conflict("learning_memory_decision_mismatch", "Select a verified Project Memory item linked to this decision and version.")
        sources.append(reference("project_memory", item.id, item.version))
    outcome = {"decision": row.status, "decided_at": row.decided_at.isoformat(),
               "decided_by_id": str(row.decided_by_user_id) if row.decided_by_user_id else None, "feedback": row.feedback}
    return outcome, sources, row.decided_at.date()


def _weather(ctx, data):
    captured = ctx.state.get("context", {}).get("venue") or {}
    if str(data.venue_id) not in {str(ctx.event.venue_id), captured.get("id")}:
        _missing()
    links = [reference("venue", data.venue_id), *(ctx.workflow(item)[1] for item in data.activities)]
    return {}, links, data.occurred_at.date()


def _incident(ctx, data):
    return {}, [ctx.workflow(item)[1] for item in data.activities], data.occurred_at.date()


def _delay(ctx, data):
    task, source = ctx.workflow(data.activity)
    return _window_actual(data.actual, task), [source], data.actual.end.date()


def _change(ctx, data):
    ctx.require_version()
    return {}, [ctx.workflow(item)[1] for item in data.activities], data.requested_at.date()


def _media(ctx, data):
    asset = ctx.asset(data.asset_id)
    if not asset.mime_type.startswith(("image/", "video/")):
        invalid("Final media must refer to an image or video asset.")
    links = [reference("asset", asset.id)]
    if data.scene_id:
        scene = ctx.state.get("scenes", {}).get(str(data.scene_id))
        if scene is None:
            _missing()
        revision = scene["scene"]["current_revision"]
        links.append(reference("scene", data.scene_id, revision))
        if data.scene_object_id:
            if str(data.scene_object_id) not in scene["objects"]:
                _missing()
            links.append(reference("scene_object", data.scene_object_id, revision, data.scene_id))
    metadata = {"mime_type": asset.mime_type, "category": asset.category, "uploaded_at": asset.created_at.isoformat(),
                "uploaded_by_id": str(asset.uploaded_by_user_id) if asset.uploaded_by_user_id else None}
    return {"asset_metadata": metadata}, links, data.captured_at.date() if data.captured_at else None


_CAPTURE = {"cost": _cost, "quantity": _quantity, "labour": _labour, "installation": _installation, "wastage": _wastage,
            "vendor": _vendor, "client_feedback": _feedback, "concept_decision": _concept, "weather_incident": _weather,
            "production_incident": _incident, "delay": _delay, "change_order": _change, "final_media": _media}


def gather(ctx, payload, *, verify=False):
    actual = payload.data
    assets = [ctx.asset(key) for key in sorted(set(payload.asset_ids))]
    sources = [reference("asset", row.id) for row in assets]
    if ctx.version is not None:
        sources.append(reference("design_version", ctx.version.id))
    if payload.audit_id is not None:
        sources.append(ctx.audit(payload.audit_id))
    derived, links, occurred = _CAPTURE[actual.kind](ctx, actual)
    reason = getattr(actual, "reason", None) or getattr(actual, "cause", None)
    if reason is not None:
        derived["reason_source_status"] = "attributed" if reason.source_reference else "unconfirmed"
    if verify:
        documentary = any(not item.mime_type.startswith(("image/", "video/")) for item in assets)
        if actual.kind in {"cost", "quantity", "labour", "wastage"} and not (payload.source_reference or documentary):
            invalid("Verification requires a documentary reference for financial and measured consumption records.")
        if not (payload.source_reference or assets or actual.kind in {"concept_decision", "final_media", "client_feedback"}):
            invalid("Verification requires a source reference or accessible supporting asset.")
        if actual.kind == "client_feedback" and ctx.event.status != "completed":
            design_version.conflict("learning_event_not_completed", "Finalize client feedback after the event is completed.")
        if actual.kind == "vendor" and any(ctx.incident(key).status != "verified" for key in actual.incident_ids):
            design_version.conflict("learning_incident_unverified", "Verify linked vendor incidents before verifying the vendor observation.")
    if getattr(actual, "planned_team_hours", None) or getattr(actual, "expected", None):
        derived["baseline_origin"] = "recorded_plan"
    occurred = occurred or datetime.now(timezone.utc).date()
    return derived, [item.model_dump(mode="json") for item in distinct([*sources, *links])], occurred


def available_asset_ids(db, project, event_id, sources):
    keys = {UUID(str(item["id"])) for item in sources if item["kind"] == "asset"}
    if not keys:
        return set()
    rows = db.scalars(select(Asset).where(Asset.id.in_(keys), Asset.organization_id == project.organization_id)
                      .execution_options(populate_existing=True))
    return {row.id for row in rows if design_snapshot.asset_scope(row, project, event_id)}
