"""Read and normalize saved Phase 8 sources without invoking a provider."""

from collections import defaultdict
from hashlib import sha256
import json

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import and_, or_, select

from app.models.ai_execution import AIExecution
from app.models.asset import Asset
from app.models.design_artifact import DesignArtifact, DesignArtifactAsset, DesignElement
from app.models.project import Project
from app.schemas.context import ProjectContextResponse
from app.schemas.design_intelligence import DesignElementSuggestion
from app.schemas.spatial_design import ArtifactSource, IntegrationContext, ModuleContext, Reference
from app.services.context import ContextService
from app.services.design_intelligence import (
    DesignIntelligenceService, MODULE_SCHEMAS, PROJECT_EVENT_SCOPE, UPSTREAM, design_fingerprint,
    design_scope_context, reference_in_scope, reference_metadata,
)
from app.services.spatial_runtime import _clean_json


def content_hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


_FIELD_ADAPTERS = {
    module: {field: TypeAdapter(info.rebuild_annotation()) for field, info in schema.model_fields.items()}
    for module, schema in MODULE_SCHEMAS.items()
}


def _normalize(artifact):
    schema = MODULE_SCHEMAS[artifact.module]
    content, _ = _clean_json(artifact.content)
    try:
        return schema.model_validate(content).model_dump(mode="json"), []
    except ValidationError as exc:
        fields = sorted({".".join(map(str, e["loc"])) or "content" for e in exc.errors()})
        normalized = {}
        if isinstance(content, dict):
            for field, adapter in _FIELD_ADAPTERS[artifact.module].items():
                if field in content:
                    try:
                        normalized[field] = adapter.dump_python(adapter.validate_python(content[field]), mode="json")
                    except ValidationError:
                        continue
        return normalized, fields


def _latest_artifacts(db, scene, scopes):
    event = DesignArtifact.event_id
    condition = and_(DesignArtifact.event_scope_key == PROJECT_EVENT_SCOPE, event.is_(None))
    if scene.event_id is not None:
        condition = or_(condition, and_(DesignArtifact.event_scope_key == str(scene.event_id), event == scene.event_id))
    rows = db.scalars(select(DesignArtifact).where(
        DesignArtifact.project_id == scene.project_id, DesignArtifact.event_scope_key.in_(scopes), condition,
    ).distinct(DesignArtifact.event_scope_key, DesignArtifact.module).order_by(
        DesignArtifact.event_scope_key, DesignArtifact.module, DesignArtifact.version.desc(),
    )).all()
    return {(row.event_scope_key, row.module): row for row in rows}


def _latest_executions(db, project, scope):
    tasks = [f"design_{module}" for module in MODULE_SCHEMAS]
    rows = db.scalars(select(AIExecution).where(
        AIExecution.project_id == project.id, AIExecution.organization_id == project.organization_id,
        AIExecution.workspace_id == project.workspace_id, AIExecution.task.in_(tasks),
        AIExecution.metadata_json["provenance"]["source"].astext == "design_intelligence",
        AIExecution.metadata_json["provenance"]["event_scope"].astext == scope,
    ).distinct(AIExecution.task).order_by(AIExecution.task, AIExecution.created_at.desc(), AIExecution.id)).all()
    return {row.task.removeprefix("design_"): row for row in rows}


def _references(db, selected, project):
    links = defaultdict(list)
    if selected:
        rows = db.execute(select(DesignArtifactAsset, Asset).outerjoin(Asset, Asset.id == DesignArtifactAsset.asset_id)
                          .where(DesignArtifactAsset.artifact_id.in_(selected))
                          .order_by(DesignArtifactAsset.asset_id)).all()
        for link, asset in rows:
            artifact = selected[link.artifact_id]
            available = reference_in_scope(asset, project, artifact.event_id)
            links[link.artifact_id].append((link.role, asset if available else None))
    return links


def _elements(db, artifact, normalized):
    if "elements" not in normalized:
        return []
    rows = db.scalars(select(DesignElement).where(
        DesignElement.artifact_id == artifact.id, DesignElement.project_id == artifact.project_id,
        DesignElement.event_id.is_not_distinct_from(artifact.event_id),
    ).order_by(DesignElement.created_at, DesignElement.id)).all()
    if not rows:
        return [{**item, "path": f"elements.{i}", "element_id": None, "source": "ai_suggested"}
                for i, item in enumerate(normalized["elements"])]
    values = []
    for row in rows:
        details, _ = _clean_json(row.details)
        try:
            value = DesignElementSuggestion.model_validate({
                **(details if isinstance(details, dict) else {}), "name": row.name, "category": row.category,
            }).model_dump(mode="json")
        except ValidationError:
            value = {"name": row.name, "category": row.category, "invalid": True}
        values.append({**value, "path": "elements", "element_id": str(row.id), "source": row.source})
    return values


def load_context(db, scene, user_id):
    scope = DesignIntelligenceService._scope(scene.event_id)
    scopes = {scope, PROJECT_EVENT_SCOPE}
    artifacts = _latest_artifacts(db, scene, scopes)
    selected = {module: (artifacts.get((scope, module)) or artifacts.get((PROJECT_EVENT_SCOPE, module)))
                for module in MODULE_SCHEMAS}
    project = db.get(Project, scene.project_id)
    executions = _latest_executions(db, project, scope)
    sources = {artifact.id: artifact for artifact in selected.values() if artifact is not None}
    references = _references(db, sources, project)
    base_context = (ProjectContextResponse.model_validate(
        ContextService.get_project_context(db, project.id, user_id),
    ).model_dump(mode="json") if sources else None)
    scoped_contexts = {key: design_scope_context(base_context, None if key == PROJECT_EVENT_SCOPE else scene.event_id)
                       for key in scopes} if base_context is not None else {}
    modules = {}
    for module, artifact in selected.items():
        execution = executions.get(module)
        entry = ModuleContext(module=module, status="missing")
        latest_failed = execution is not None and execution.status in {"failed", "cancelled"}
        if execution is not None:
            entry.latest_execution_id, entry.latest_execution_status = execution.id, execution.status
            entry.error_code = execution.error_code
        if artifact is None:
            if latest_failed:
                entry.status = "failed"
            elif execution is not None and execution.status in {"running", "pending"}:
                entry.status = "running"
            elif execution is not None:
                entry.status = "partial"
                entry.issues = ["output_not_saved"]
            modules[module] = entry
            continue

        normalized, issues = _normalize(artifact)
        entry.issues = [f"invalid_field:{field}" for field in issues]
        newer_execution = (execution is not None and execution.id != artifact.ai_execution_id
                           and execution.created_at >= artifact.created_at)
        if newer_execution:
            entry.issues.append("newer_execution_without_artifact")
        links = references[artifact.id]
        entry.references = [Reference(asset_id=asset.id, name=asset.display_name, role=role) for role, asset in links if asset is not None]
        entry.unavailable_reference_count = sum(asset is None for _, asset in links)
        refs = reference_metadata(asset for _, asset in links if asset is not None)
        basis = artifact.based_on if isinstance(artifact.based_on, dict) else {}
        upstream = {}
        upstream_changed = False
        for name in UPSTREAM[module]:
            current = artifacts.get((artifact.event_scope_key, name))
            stored = basis.get(name)
            if current is not None:
                content, _ = _clean_json(current.content)
                upstream[name] = {"artifact_id": str(current.id), "version": current.version, "content": content}
            if isinstance(stored, dict) and stored.get("artifact_id") != (str(current.id) if current is not None else None):
                upstream_changed = True
        stale = (upstream_changed or entry.unavailable_reference_count > 0 or
                 design_fingerprint(scoped_contexts[artifact.event_scope_key], refs, upstream) != artifact.context_fingerprint)
        entry.artifact = ArtifactSource(
            artifact_id=artifact.id, version=artifact.version, event_id=artifact.event_id, source=artifact.source,
            status=artifact.status, execution_id=artifact.ai_execution_id, context_stale=stale, based_on=basis,
        )
        if module == "elements":
            normalized["placements"] = _elements(db, artifact, normalized)
            if any(item.get("invalid") for item in normalized["placements"]):
                entry.issues.append("invalid_element_details")
        entry.normalized = normalized
        if artifact.status == "archived":
            entry.status = "stale"
            entry.issues.append("artifact_archived")
        elif issues:
            entry.status = "partial" if normalized else "invalid"
        elif stale:
            entry.status = "stale"
        elif entry.issues:
            entry.status = "partial"
        else:
            entry.status = "available"
        modules[module] = entry

    # Vision is a text view plan. Link only an exact, unambiguous name to known Elements.
    elements = defaultdict(list)
    for item in modules["elements"].normalized.get("placements", ()):
        if not item.get("invalid"):
            elements[item["name"].strip().casefold()].append(item)
    observations = []
    for view in modules["vision"].normalized.get("views", ()):
        for focal in view["focal_points"]:
            matches = elements[focal.strip().casefold()]
            if len(matches) == 1:
                observations.append({"view": view["name"], "focal_point": focal, "category": matches[0]["category"],
                                     "element_id": matches[0]["element_id"], "basis": "exact_element_name"})
    if observations:
        modules["vision"].normalized["element_links"] = observations
    entries = list(modules.values())
    any_output = any(entry.normalized for entry in entries)
    status = ("available" if all(entry.status == "available" for entry in entries)
              else "partial" if any_output else "unavailable")
    return IntegrationContext(
        scene_id=scene.id, project_id=scene.project_id, event_id=scene.event_id, scene_revision=scene.current_revision,
        context_version=content_hash([entry.model_dump(mode="json") for entry in entries]), status=status, modules=entries,
    )
