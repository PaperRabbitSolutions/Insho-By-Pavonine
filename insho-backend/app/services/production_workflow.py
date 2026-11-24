"""Section 25 lifecycle and approved package checks on the existing scene record."""

from uuid import UUID

from sqlalchemy import select

from app.models.production import ProductionWorkflowRevision
from app.schemas.production_workflow import WorkflowStatus
from app.services import production, production_bom, production_suppliers
from app.services.procurement_scope import check_version, flush


_STATUSES = WorkflowStatus.__args__
_TRANSITIONS = {status: [following] for status, following in zip(_STATUSES, _STATUSES[1:])}
_TRANSITIONS["production_ready"].append("technical_review")
_EVIDENCE_REQUIRED = {"design_approved", "inspected", "archived"}
_PLAN_EDITABLE = {"draft", "design_approved", "technical_review"}


def _history(db, record):
    return (select(ProductionWorkflowRevision).where(ProductionWorkflowRevision.record_id == record.id)
            .order_by(ProductionWorkflowRevision.version.desc()))


def _revision(row):
    if row is None:
        return {"version": 0, "status": "draft", "tasks": [], "readiness_basis": None,
                "changed_by_user_id": None, "changed_at": None, "evidence_reference": None}
    return {key: getattr(row, key) for key in (
        "version", "status", "tasks", "readiness_basis", "changed_by_user_id", "changed_at", "evidence_reference",
    )}


def critical_path(tasks):
    if not tasks or any(task["duration_minutes"] is None for task in tasks):
        return None, None
    paths = {}
    for task in tasks:
        duration, path = max((paths[key] for key in task["depends_on"]), key=lambda entry: entry[0], default=(0, []))
        paths[task["id"]] = duration + task["duration_minutes"], [*path, task["id"]]
    duration, path = max(paths.values(), key=lambda entry: entry[0])
    return path, duration


def _ready_sheet(sheet):
    technical = sheet["technical"]
    return (sheet["status"] == "approved" and not sheet["stale"] and sheet["superseded_by_id"] is None
            and technical is not None and any(technical.get(key) for key in (
                "dimensions", "parameters", "fabrication_notes", "installation_requirements",
            )))


def _check_package(db, scene, record, project, tasks, *, lock_rates=False):
    sources = production_bom.Sources(db, scene, record, project.organization_id, lock_rates=lock_rates)
    issues, specifications = [], []
    bom = production_bom._head(db, record)
    estimated = []
    if bom is None or bom.approved_at is None:
        issues.append({"code": "approved_bom_required"})
    if bom is not None:
        bom_view = production_bom._view(bom, production_bom._lines(db, bom.id), sources, latest=True)
        if bom_view["stale"] or any(line["issues"] or line["total"] is None for line in bom_view["lines"]):
            issues.append({"code": "bom_unready"})
        estimated = [line for line in bom_view["lines"] if line["basis"] == "estimated"]
    if not estimated or not sources.items:
        issues.append({"code": "boq_requirements_missing"})
    if scene.deleted_at is not None:
        issues.append({"code": "scene_deleted"})

    covered = {line["inputs"].get("item_id") for line in estimated}
    linked_sheets = {}
    for line in estimated:
        technical = line["inputs"].get("dependencies", {}).get("specification")
        if technical is not None:
            linked_sheets.setdefault(line["inputs"]["item_id"], set()).add(technical["id"])
    grouping_items = {item.parent_item_id for item in sources.items.values()}
    for key, item in sorted(sources.items.items()):
        if key in grouping_items and str(key) not in covered:
            continue
        if str(key) not in covered:
            issues.append({"code": "item_missing_from_boq", "item_id": str(key)})
        sheets = [sheet for sheet in sources.sheets(item) if _ready_sheet(sheet)]
        required = linked_sheets.get(str(key), set())
        if required:
            sheets = [sheet for sheet in sheets if str(sheet["id"]) in required]
        if not sheets or len(sheets) < len(required):
            issues.append({"code": "technical_package_incomplete", "item_id": str(key)})
        specifications.extend({"id": str(sheet["id"]), "version": sheet["version"], "item_id": str(key)}
                              for sheet in sorted(sheets, key=lambda sheet: sheet["id"]))

    vendor_status = {}
    for task in tasks:
        if task["item_id"] is not None and UUID(task["item_id"]) not in sources.items:
            issues.append({"code": "task_item_missing", "task_id": task["id"]})
        vendor = task["supplier_id"]
        if vendor is not None:
            if vendor not in vendor_status:
                vendor_status[vendor] = production_suppliers.available_for_project(db, project, UUID(vendor))
            if not vendor_status[vendor]:
                issues.append({"code": "task_supplier_unavailable", "task_id": task["id"]})
    if issues:
        return None, issues
    return {"bom_id": str(bom.id), "bom_version": bom.version, "record_version": record.version,
            "scene_revision": scene.current_revision, "specifications": specifications}, []


def _view(scene, record, revision, basis, blockers):
    tasks = revision["tasks"]
    path, duration = critical_path(tasks)
    warnings = []
    if not tasks:
        warnings.append("production_sequence_missing")
    elif path is None:
        warnings.append("critical_path_requires_durations")
    if any(task["kind"] == "installation" and task["window_start"] is None for task in tasks):
        warnings.append("installation_window_missing")
    pinned = revision["readiness_basis"]
    return {**revision, "record_id": record.id, "scene_id": scene.id, "project_id": scene.project_id,
            "event_id": scene.event_id, "allowed_transitions": _TRANSITIONS.get(revision["status"], []),
            "readiness_blockers": blockers, "warnings": warnings, "readiness_stale": pinned is not None and pinned != basis,
            "critical_path": path, "critical_path_minutes": duration}


def get(db, scene_id, record_id, user_id):
    scene, record, project = production_bom._scope(db, scene_id, record_id, user_id)
    revision = _revision(db.scalar(_history(db, record).limit(1)))
    return _view(scene, record, revision, *_check_package(db, scene, record, project, revision["tasks"]))


def history(db, scene_id, record_id, user_id, *, offset, limit):
    scene = production._scene(db, scene_id, user_id)
    record = production._record(db, scene, record_id)
    return [_revision(row) for row in db.scalars(_history(db, record).offset(offset).limit(limit))]


def _save(db, scene, record, user_id, version, status, tasks, pinned, evidence, basis, blockers):
    row = ProductionWorkflowRevision(record_id=record.id, version=version + 1, status=status, tasks=tasks,
                                     readiness_basis=pinned, evidence_reference=evidence, changed_by_user_id=user_id)
    db.add(row)
    flush(db)
    result = _view(scene, record, _revision(row), basis, blockers)
    production._commit_changes(db)
    production._log("workflow_revision_saved", record_id=str(result["record_id"]), version=version + 1, status=status)
    return result


def save_plan(db, scene_id, record_id, user_id, data):
    scene, record, project = production_bom._scope(db, scene_id, record_id, user_id, write=True)
    current = _revision(db.scalar(_history(db, record).limit(1)))
    check_version(data.expected_version, current["version"], "workflow")
    if current["status"] not in _PLAN_EDITABLE:
        production._conflict("workflow_plan_locked", "Revise the production sequence during technical review.")
    for item_id in {task.item_id for task in data.tasks} - {None}:
        production._item(db, record, item_id)
    for supplier_id in sorted({task.supplier_id for task in data.tasks} - {None}):
        production_suppliers.supplier_for_project(db, project, supplier_id)
    tasks = [task.model_dump(mode="json") for task in data.tasks]
    basis, blockers = _check_package(db, scene, record, project, tasks)
    if tasks == current["tasks"]:
        return _view(scene, record, current, basis, blockers)
    return _save(db, scene, record, user_id, current["version"], current["status"], tasks, None, None, basis, blockers)


def transition(db, scene_id, record_id, user_id, data):
    scene, record, project = production_bom._scope(db, scene_id, record_id, user_id, write=True)
    production._require_approver(db, scene, user_id)
    current = _revision(db.scalar(_history(db, record).limit(1)))
    check_version(data.expected_version, current["version"], "workflow")
    if data.status not in _TRANSITIONS.get(current["status"], []):
        production._conflict("workflow_transition_invalid", "The requested production transition is unavailable.")
    if data.status in _EVIDENCE_REQUIRED and data.evidence_reference is None:
        production._invalid("Supply an evidence reference for this status change.")
    tasks = current["tasks"]
    gate = data.status in ("production_ready", "in_fabrication")
    basis, blockers = _check_package(db, scene, record, project, tasks, lock_rates=gate)
    pinned = current["readiness_basis"]
    if gate and blockers:
        production._conflict("production_package_unready", "Resolve the production package blockers.", blockers=blockers)
    if data.status == "in_fabrication" and pinned != basis:
        production._conflict("production_readiness_stale", "Return to technical review and approve the current package.")
    if data.status == "production_ready":
        pinned = basis
    elif data.status == "technical_review":
        pinned = None
    return _save(db, scene, record, user_id, current["version"], data.status, tasks, pinned, data.evidence_reference,
                 basis, blockers)
