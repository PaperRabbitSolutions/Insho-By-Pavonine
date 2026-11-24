"""Transactional design versions, branches and Part 1 review associations."""

from hashlib import sha256
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.models.asset import Asset
from app.models.design_version import DesignActivity, DesignBranch, DesignSnapshot, DesignSnapshotAsset, DesignVersion, DesignVersionReview
from app.models.presentation import PresentationCheckpoint
from app.schemas.design_version import BranchRead, PresentationPin, VersionRead
from app.security import ADMIN_ROLES, require_membership, require_project_access
from app.services import design_activity, design_snapshot, presentation
from app.services.spatial import _missing, _validate_event_project


def conflict(code, message, **fields):
    raise HTTPException(409, detail={"code": code, "message": message, **fields})


def _scope(db, project_id, user_id, *, write=False, admin=False):
    project = require_project_access(db, user_id, project_id, write=write)
    if admin and require_membership(db, user_id, project.organization_id).role not in ADMIN_ROLES:
        raise HTTPException(403, detail="Insufficient permissions")
    if write:
        # A project-scoped advisory lock serializes all heads and number allocation, including the first version.
        key = sha256(b"insho-design-versions:" + project.id.bytes).digest()
        db.execute(select(func.pg_advisory_xact_lock(int.from_bytes(key[:8], "big", signed=True))))
        db.refresh(project)
    return project


def _event(db, project, user_id, event_id):
    return _validate_event_project(db, user_id, project, event_id)


def _key(event_id):
    return str(event_id) if event_id else "project"


def owned(db, model, project_id, key):
    row = db.scalar(select(model).where(model.id == key, model.project_id == project_id)
                    .execution_options(populate_existing=True))
    if row is None:
        _missing()
    return row


def _stream(project_id, event_id, branch_id):
    return select(DesignVersion).where(DesignVersion.project_id == project_id, DesignVersion.scope_key == _key(event_id),
                                       DesignVersion.branch_id == branch_id)


def _head(db, project_id, event_id, branch_id=None):
    return db.scalar(_stream(project_id, event_id, branch_id).order_by(DesignVersion.number.desc()).limit(1))


def _expected(actual, expected, name="head"):
    current = actual.id if actual else None
    if current != expected:
        conflict(f"design_{name}_conflict", "Refresh the design history before submitting this change.",
                 current_version_id=str(current) if current else None)


def _active(branch):
    if branch.status != "active":
        conflict("design_branch_closed", "Only an active branch can receive or promote revisions.")


def _status_data(db, rows):
    version_ids = [row.id for row in rows]
    links = {} if not version_ids else {row.version_id: row for row in db.scalars(select(DesignVersionReview).where(
        DesignVersionReview.version_id.in_(version_ids),
    ))}
    pairs = [(link.presentation_id, link.presentation_revision) for link in links.values()]
    return links, presentation._statuses(db, pairs)


def _summaries(db, rows):
    links, states = _status_data(db, rows)
    results = []
    for row in rows:
        result = VersionRead.model_validate(row)
        link = links.get(row.id)
        if link:
            pair = (link.presentation_id, link.presentation_revision)
            result = result.model_copy(update={"status": presentation._approval_status(states[pair]),
                                              "review": PresentationPin(id=pair[0], revision=pair[1])})
        results.append(result)
    return results


def _snapshot(db, row):
    return db.get(DesignSnapshot, row.snapshot_id)


def _unavailable_assets(db, project, event_id, state, *, lock=False):
    keys = {UUID(key) for key in state["assets"]}
    if not keys:
        return []
    query = select(Asset).where(Asset.id.in_(keys)).order_by(Asset.id).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update(read=True)
    permitted = {asset.id for asset in db.scalars(query) if design_snapshot.asset_scope(asset, project, event_id)}
    return sorted(str(key) for key in keys - permitted)


def _asset_guard(db, project, event_id, state):
    if _unavailable_assets(db, project, event_id, state, lock=True):
        conflict("design_snapshot_assets_unavailable", "A referenced asset is no longer available in this scope.")


def _detail(db, project, row):
    state = _snapshot(db, row).state
    unavailable = _unavailable_assets(db, project, row.event_id, state)
    visible_assets = {key: value for key, value in state["assets"].items() if key not in set(unavailable)}
    return {**_summaries(db, [row])[0].model_dump(), "state": {**state, "assets": visible_assets},
            "unavailable_asset_ids": unavailable}


def _new_snapshot(db, project, event_id, state):
    row = DesignSnapshot(id=uuid4(), project_id=project.id, scope_key=_key(event_id), state=state)
    db.add(row)
    db.flush()
    db.add_all(DesignSnapshotAsset(snapshot_id=row.id, asset_id=UUID(key)) for key in state["assets"])
    return row


def _append(db, project, event_id, user_id, note, snapshot, *, action, parent=None, based_on=None, branch=None, request_id=None):
    number = db.scalar(select(func.max(DesignVersion.number)).where(
        DesignVersion.project_id == project.id, DesignVersion.scope_key == _key(event_id),
    )) or 0
    row = DesignVersion(id=uuid4(), project_id=project.id, event_id=event_id, scope_key=_key(event_id), number=number + 1,
                        parent_id=parent.id if parent else None, based_on_id=based_on.id if based_on else None,
                        branch_id=branch.id if branch else None, snapshot_id=snapshot.id, created_by_user_id=user_id,
                        summary=getattr(note, "summary", None) or "Fork " + branch.name, reason=note.reason)
    db.add(row)
    db.flush()
    changes, affected = design_snapshot.compare(_snapshot(db, parent).state if parent else {}, snapshot.state)
    executions = {artifact["execution_id"] for key, artifact in snapshot.state["artifacts"].items()
                  if key in affected and artifact["execution_id"] is not None}
    design_activity.record(db, project.id, event_id, user_id, action, "design_version", row.id, version_id=row.id,
                           branch_id=branch.id if branch else based_on.branch_id if based_on else None,
                           reason=note.reason, request_id=request_id, previous_version_id=str(parent.id) if parent else None,
                           based_on_id=str(based_on.id) if based_on else None, snapshot_id=str(snapshot.id),
                           changes=changes, affected_source_ids=affected, source_execution_ids=sorted(executions))
    return row


def _transaction(db, operation):
    try:
        result = operation()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, detail={"code": "design_relationship_conflict",
                                         "message": "A design version or source changed during this operation."}) from exc
    return result


def write(db, project_id, event_id, user_id, data, *, branch_id=None, request_id=None):
    project = _scope(db, project_id, user_id, write=True)
    branch = owned(db, DesignBranch, project.id, branch_id) if branch_id else None
    if branch:
        _active(branch)
        event_id = branch.event_id
    event = _event(db, project, user_id, event_id)
    head = _head(db, project.id, event_id, branch_id)
    _expected(head, data.expected_head_id)
    existing = _snapshot(db, head) if head else None
    state = design_snapshot.Capture(db, project, event, user_id).build(data.selection, existing.state if existing else None,
                                                                     refresh_context=data.refresh_context)

    def save():
        snapshot = existing if existing and state == existing.state else _new_snapshot(db, project, event_id, state)
        row = _append(db, project, event_id, user_id, data, snapshot, action="version_created", parent=head,
                      branch=branch, request_id=request_id)
        if branch:
            branch.updated_at = row.created_at
        return _detail(db, project, row)

    return _transaction(db, save)


def get(db, project_id, version_id, user_id):
    project = _scope(db, project_id, user_id)
    row = owned(db, DesignVersion, project.id, version_id)
    _event(db, project, user_id, row.event_id)
    return _detail(db, project, row)


def current(db, project_id, event_id, user_id):
    project = _scope(db, project_id, user_id)
    _event(db, project, user_id, event_id)
    row = _head(db, project.id, event_id)
    if row is None:
        _missing()
    return _detail(db, project, row)


def history(db, project_id, event_id, user_id, *, branch_id=None, offset=0, limit=50):
    project = _scope(db, project_id, user_id)
    if branch_id:
        event_id = owned(db, DesignBranch, project.id, branch_id).event_id
    _event(db, project, user_id, event_id)
    rows = db.scalars(_stream(project.id, event_id, branch_id).order_by(DesignVersion.number.desc()).offset(offset).limit(limit)).all()
    return _summaries(db, rows)


def restore(db, project_id, user_id, data, *, branch_id=None, request_id=None):
    project = _scope(db, project_id, user_id, write=True)
    source = owned(db, DesignVersion, project.id, data.source_version_id)
    _event(db, project, user_id, source.event_id)
    branch = owned(db, DesignBranch, project.id, branch_id) if branch_id else None
    if source.branch_id != branch_id:
        conflict("design_restore_stream_mismatch", "Restore a version within its own history. Use promotion to publish a branch.")
    if branch:
        _active(branch)
    head = _head(db, project.id, source.event_id, branch_id)
    _expected(head, data.expected_head_id)
    snapshot = _snapshot(db, source)
    _asset_guard(db, project, source.event_id, snapshot.state)
    def save():
        row = _append(db, project, source.event_id, user_id, data, snapshot, action="version_restored", parent=head,
                      based_on=source, branch=branch, request_id=request_id)
        if branch:
            branch.updated_at = row.created_at
        return _detail(db, project, row)

    return _transaction(db, save)


def _compare(db, first, second):
    if first.scope_key != second.scope_key:
        _missing()
    changes, affected = design_snapshot.compare(_snapshot(db, first).state, _snapshot(db, second).state)
    return {"from_version_id": first.id, "to_version_id": second.id, "changes": changes, "affected_source_ids": affected}


def compare(db, project_id, first_id, second_id, user_id):
    project = _scope(db, project_id, user_id)
    first, second = (owned(db, DesignVersion, project.id, key) for key in (first_id, second_id))
    _event(db, project, user_id, first.event_id)
    return _compare(db, first, second)


def _branch_view(row, head_id):
    return BranchRead.model_validate({**{field: getattr(row, field) for field in BranchRead.model_fields if field != "current_version_id"},
                                     "current_version_id": head_id})


def create_branch(db, project_id, user_id, data, *, request_id=None):
    project = _scope(db, project_id, user_id, write=True)
    source = owned(db, DesignVersion, project.id, data.source_version_id)
    event = _event(db, project, user_id, source.event_id)
    main = _head(db, project.id, source.event_id)
    _expected(main, data.expected_mainline_id, "mainline")
    snapshot = _snapshot(db, source)
    _asset_guard(db, project, source.event_id, snapshot.state)
    parent = owned(db, DesignBranch, project.id, source.branch_id) if source.branch_id else None
    baseline = parent.live_basis if parent else design_snapshot.live_basis(db, project, event, user_id, _snapshot(db, main).state)

    def save():
        row = DesignBranch(id=uuid4(), project_id=project.id, event_id=source.event_id, scope_key=source.scope_key,
                           source_version_id=source.id, base_mainline_id=parent.base_mainline_id if parent else main.id,
                           parent_branch_id=parent.id if parent else None, live_basis=baseline, name=data.name, description=data.description,
                           status="active", created_by_user_id=user_id)
        db.add(row)
        db.flush()
        version = _append(db, project, source.event_id, user_id, data, snapshot, action="branch_created", parent=source,
                          based_on=source, branch=row, request_id=request_id)
        return _branch_view(row, version.id)

    return _transaction(db, save)


def get_branch(db, project_id, branch_id, user_id):
    project = _scope(db, project_id, user_id)
    row = owned(db, DesignBranch, project.id, branch_id)
    _event(db, project, user_id, row.event_id)
    return _branch_view(row, _head(db, project.id, row.event_id, row.id).id)


def branches(db, project_id, event_id, user_id, *, status, offset, limit):
    project = _scope(db, project_id, user_id)
    _event(db, project, user_id, event_id)
    head = aliased(DesignVersion)
    latest = select(head.id).where(head.branch_id == DesignBranch.id).order_by(head.number.desc()).limit(1).scalar_subquery()
    query = select(DesignBranch, latest).where(DesignBranch.project_id == project.id, DesignBranch.scope_key == _key(event_id))
    if status:
        query = query.where(DesignBranch.status == status)
    pairs = db.execute(query.order_by(DesignBranch.created_at.desc(), DesignBranch.id).offset(offset).limit(limit)).all()
    return [_branch_view(row, version_id) for row, version_id in pairs]


def compare_branch(db, project_id, branch_id, user_id):
    project = _scope(db, project_id, user_id)
    row = owned(db, DesignBranch, project.id, branch_id)
    _event(db, project, user_id, row.event_id)
    return _compare(db, owned(db, DesignVersion, project.id, row.source_version_id), _head(db, project.id, row.event_id, row.id))


def promote(db, project_id, branch_id, user_id, data, *, request_id=None):
    project = _scope(db, project_id, user_id, write=True, admin=True)
    row = owned(db, DesignBranch, project.id, branch_id)
    event = _event(db, project, user_id, row.event_id)
    _active(row)
    source, target = _head(db, project.id, row.event_id, row.id), _head(db, project.id, row.event_id)
    _expected(source, data.expected_head_id)
    _expected(target, data.expected_mainline_id, "mainline")
    if target.id != row.base_mainline_id:
        conflict("design_mainline_diverged", "The mainline advanced since the branch was based on it.", current_version_id=str(target.id))
    if design_snapshot.live_basis(db, project, event, user_id, _snapshot(db, target).state) != row.live_basis:
        conflict("design_dependencies_diverged", "A canonical dependency of the mainline changed since the branch was created.")
    snapshot = _snapshot(db, source)
    _asset_guard(db, project, row.event_id, snapshot.state)

    def save():
        version = _append(db, project, row.event_id, user_id, data, snapshot, action="branch_promoted", parent=target,
                          based_on=source, request_id=request_id)
        row.status = "promoted"
        return _detail(db, project, version)

    return _transaction(db, save)


def archive(db, project_id, branch_id, user_id, data, *, request_id=None):
    project = _scope(db, project_id, user_id, write=True)
    row = owned(db, DesignBranch, project.id, branch_id)
    _event(db, project, user_id, row.event_id)
    head = _head(db, project.id, row.event_id, row.id)
    _expected(head, data.expected_head_id)
    if row.status == "archived":
        return _branch_view(row, head.id)

    def save():
        previous = row.status
        row.status = "archived"
        design_activity.record(db, project.id, row.event_id, user_id, "branch_archived", "design_branch", row.id,
                               version_id=head.id, branch_id=row.id, reason=data.reason, request_id=request_id,
                               previous_status=previous, status=row.status)
        db.flush()
        return _branch_view(row, head.id)

    return _transaction(db, save)


def bind_review(db, project_id, version_id, user_id, pin, *, request_id=None):
    project = _scope(db, project_id, user_id, write=True, admin=True)
    row = owned(db, DesignVersion, project.id, version_id)
    _event(db, project, user_id, row.event_id)
    state = _snapshot(db, row).state
    pinned = state["presentation"]
    sheet = presentation._presentation(db, project, pin.id, lock=True)
    if sheet.event_id != row.event_id:
        _missing()
    if pinned is None or (pinned["id"], pinned["revision"]) != (str(pin.id), pin.revision):
        conflict("design_review_snapshot_mismatch", "Use the exact presentation revision captured in the design version.")
    existing = db.get(DesignVersionReview, row.id)
    if existing is not None:
        if (existing.presentation_id, existing.presentation_revision) != (pin.id, pin.revision):
            conflict("design_review_snapshot_mismatch", "The existing review does not match the captured presentation.")
        return _detail(db, project, row)
    current = presentation._latest(db, sheet)
    presentation._check_version(pin.revision, current)
    if db.scalar(select(DesignVersionReview.version_id).where(DesignVersionReview.presentation_id == pin.id,
                                                             DesignVersionReview.presentation_revision == pin.revision)):
        conflict("design_review_already_bound", "This presentation revision already belongs to a design review.")
    if db.scalar(presentation._checkpoints(sheet, pin.revision).where(PresentationCheckpoint.status != "draft").limit(1)):
        conflict("design_review_already_started", "Bind the design version before requesting presentation approval.")
    if pinned["fingerprint"] != design_snapshot.digest({"sheet": current.sheet, "evidence": current.evidence}):
        conflict("design_review_snapshot_mismatch", "The presentation evidence no longer matches the design version.")
    _verify_review_sources(state, current)
    _asset_guard(db, project, row.event_id, state)

    def save():
        db.add(DesignVersionReview(version_id=row.id, project_id=project.id, presentation_id=pin.id, presentation_revision=pin.revision))
        design_activity.record(db, project.id, row.event_id, user_id, "review_bound", "presentation", pin.id, version_id=row.id,
                               branch_id=row.branch_id, request_id=request_id, presentation_revision=pin.revision)
        db.flush()
        return _detail(db, project, row)

    return _transaction(db, save)


def _verify_review_sources(state, sheet):
    display_versions = {"spatial_scene": {key: value["scene"]["current_revision"] for key, value in state["scenes"].items()},
                        "design_artifact": {key: value["version"] for key, value in state["artifacts"].items()},
                        "budget": {key: value["version"] for key, value in state["budgets"].items()},
                        "design_dna": {state["design_dna"]["id"]: state["design_dna"]["version"]} if state["design_dna"] else {}}
    for section in sheet.evidence.values():
        for source in section["sources"]:
            pinned = display_versions.get(source["kind"], {})
            if pinned and (source["id"] not in pinned or pinned[source["id"]] != source["version"]):
                conflict("design_review_source_mismatch", "The presentation and design version pin different source revisions.",
                         source_id=source["id"])


def activity(db, project_id, user_id, *, event_id, version_id, branch_id, offset, limit):
    project = _scope(db, project_id, user_id)
    _event(db, project, user_id, event_id)
    query = select(DesignActivity).where(DesignActivity.project_id == project.id)
    for field, model, key in ((DesignActivity.version_id, DesignVersion, version_id), (DesignActivity.branch_id, DesignBranch, branch_id)):
        if key is not None:
            row = owned(db, model, project.id, key)
            _event(db, project, user_id, row.event_id)
            query = query.where(field == key)
    if event_id:
        query = query.where(DesignActivity.event_id == event_id)
    return db.scalars(query.order_by(DesignActivity.created_at.desc(), DesignActivity.id).offset(offset).limit(limit)).all()
