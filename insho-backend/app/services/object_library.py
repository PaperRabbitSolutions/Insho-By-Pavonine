"""Scoped catalog reads and versioned placement into the canonical scene."""

from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select

from app.models.asset import Asset
from app.models.object_library import ObjectDefinition, ObjectDefinitionAsset
from app.models.spatial import SpatialObject
from app.schemas.object_library import CatalogFilters, DefinitionFields, DefinitionSummary
from app.schemas.spatial import SpatialAssetAttach, SpatialObjectCreate, SpatialTransform
from app.security import require_project_access
from app.services.asset import AssetService
from app.services.spatial import SpatialService, _commit, _dimensions_values, _flush, _get_scene, _missing, _transform_values
from app.services.spatial_geometry import _local_transform, _object_local_footprint
from app.services.spatial_graph import _world
from app.services.spatial_runtime import _bounds
from app.spatial.math3d import compose_transforms, transform_matrix
from app.spatial.projection import project_xy
from app.spatial.runtime3d import compile_geometry
from app.spatial.tolerance import DEFAULT_TOLERANCE
from app.spatial.types import GeometryError, UnsupportedGeometryError


_STEP = Decimal("0.000001")
_DIMENSIONS = ("width_m", "depth_m", "height_m")


def _issue(code, message, field, *, severity="error", asset_id=None):
    return {"code": code, "message": message, "field": field, "severity": severity, "asset_id": asset_id}


def _validation(definition_id, issues, compatible=False, bounds=None):
    return {"definition_id": definition_id, "status": ("invalid" if any(issue["severity"] == "error" for issue in issues)
            else "valid_with_warnings" if issues else "valid"),
            "issues": issues, "plan_compatible": compatible, "world_bounds": bounds}


def _require_valid(validation, *, status=422):
    if validation["status"] == "invalid":
        raise HTTPException(status, detail=jsonable_encoder(validation))


def _rounded(value):
    return value.quantize(_STEP, rounding=ROUND_HALF_UP)


def _dimensions(row):
    return {key: getattr(row, key) for key in _DIMENSIONS}


def _stored_dimensions(values):
    return {key: _rounded(value) for key, value in _dimensions_values(values).items()}


def _default_transform(row):
    try:
        return SpatialTransform.model_validate(row.default_transform)
    except ValidationError as exc:
        validation = _validation(row.id, [
            _issue("invalid_spatial_defaults", "Repair the stored catalog transform before using this definition.", "default_transform")
        ])
        raise HTTPException(422, detail=jsonable_encoder(validation)) from exc


def _merged_transform(base, override=None):
    merged = SpatialTransform.model_validate(base).model_dump()
    if override is not None:
        for key, values in override.model_dump(exclude_unset=True).items():
            merged[key].update(values)
    validated = SpatialTransform.model_validate(merged)
    _transform_values(validated)
    return SpatialTransform.model_validate({
        key: {axis: _rounded(value) for axis, value in values.items()} for key, values in merged.items()
    })


def _geometry_validation(definition_id, spatial, parent_world=None):
    try:
        authored = _stored_dimensions(_dimensions(spatial))
        world = transform_matrix(_local_transform(spatial))
        if parent_world is not None:
            world = compose_transforms(parent_world, world)
    except (GeometryError, HTTPException) as exc:
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _validation(definition_id, [_issue("invalid_spatial_defaults", message, "dimensions_or_transform")])
    try:
        footprint = _object_local_footprint(spatial)
        mesh = compile_geometry(footprint, float(authored["height_m"]), world)
        bounds = _bounds(mesh.world_bounds)
    except UnsupportedGeometryError as exc:
        return _validation(definition_id, [_issue("unsupported_geometry", str(exc), "geometry")])
    except GeometryError as exc:
        return _validation(definition_id, [_issue("invalid_geometry", str(exc), "geometry")])

    issues = []
    try:
        project_xy(footprint, world, height_m=float(authored["height_m"]))
    except GeometryError as exc:
        issues.append(_issue("plan_projection_unavailable", str(exc), "geometry", severity="warning"))
    return _validation(definition_id, issues, not issues, bounds)


def validate_linked_geometry(db, obj):
    """Apply the same shape contract to subsequent canonical overrides."""
    parent_world = _world(db, obj.scene_id, SpatialObject, obj.parent_object_id)
    _require_valid(_geometry_validation(obj.library_definition_id, obj, parent_world))


def _prototype(dimensions, transform, properties):
    return SimpleNamespace(**dimensions, **_transform_values(transform), properties=properties)


def _scope_name(row):
    return "project" if row.project_id else "workspace" if row.workspace_id else "organization"


def _scope(db, user_id, organization_id, workspace_id=None, project_id=None, *, write=False):
    return AssetService.validate_scope(db, user_id, organization_id, workspace_id, project_id, write=write)


def _definition(db, user_id, organization_id, definition_id, *, write=False, lock=False):
    _scope(db, user_id, organization_id, write=write)
    stmt = select(ObjectDefinition).where(ObjectDefinition.id == definition_id,
                                          ObjectDefinition.organization_id == organization_id)
    if lock:
        stmt = stmt.with_for_update(read=not write).execution_options(populate_existing=True)
    row = db.scalars(stmt).first()
    if row is None:
        _missing()
    _scope(db, user_id, organization_id, row.workspace_id, row.project_id, write=write)
    return row


def _links(db, definition_id):
    return db.scalars(select(ObjectDefinitionAsset).where(ObjectDefinitionAsset.definition_id == definition_id)
                      .order_by(ObjectDefinitionAsset.asset_id, ObjectDefinitionAsset.role)).all()


def _asset_available(scope, asset):
    return (asset is not None and asset.organization_id == scope.organization_id
            and asset.status == "active" and asset.deleted_at is None and asset.event_id is None
            and (asset.workspace_id is None or asset.workspace_id == scope.workspace_id)
            and (asset.project_id is None or asset.project_id == scope.project_id))


def _assets(db, scope, refs, *, lock=False):
    ids = {ref.asset_id for ref in refs}
    if not ids:
        return [], []
    stmt = select(Asset).where(Asset.id.in_(ids), Asset.organization_id == scope.organization_id).order_by(Asset.id)
    if lock:
        stmt = stmt.with_for_update(read=True).execution_options(populate_existing=True)
    records = {asset.id: asset for asset in db.scalars(stmt)}
    payloads, issues, seen = [], [], set()
    for ref in refs:
        key = ref.asset_id, ref.role
        if key in seen:
            issues.append(_issue("duplicate_asset_reference", "Each asset and role pair must be unique.",
                                 "assets", asset_id=ref.asset_id))
            continue
        seen.add(key)
        available = _asset_available(scope, records.get(ref.asset_id))
        payloads.append({"asset_id": ref.asset_id, "role": ref.role, "available": available})
        if not available:
            issues.append(_issue("asset_unavailable", "The asset is unavailable for this definition's scope.",
                                 "assets", asset_id=ref.asset_id))
    return payloads, issues


def _summary(row, availability):
    return {
        "id": row.id, "organization_id": row.organization_id, "workspace_id": row.workspace_id, "project_id": row.project_id,
        "scope": _scope_name(row), "name": row.name, "description": row.description, "category": row.category,
        "object_type": row.object_type, "tags": row.tags, "dimensions": _dimensions(row), "geometry_type": row.geometry["type"],
        "asset_availability": availability, "plan_compatible": row.plan_compatible, "status": row.status,
        "version": row.version, "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _combined_validation(geometry, issues):
    return _validation(geometry["definition_id"], [*geometry["issues"], *issues],
                       geometry["plan_compatible"], geometry["world_bounds"])


def _detail(db, row):
    assets, issues = _assets(db, row, _links(db, row.id))
    geometry = _geometry_validation(row.id, _prototype(
        _dimensions(row), _default_transform(row), {"geometry": row.geometry},
    ))
    availability = "unavailable" if issues else "available" if assets else "none"
    return {
        **_summary(row, availability), "default_transform": row.default_transform, "geometry": row.geometry,
        "appearance": row.appearance, "material_refs": row.material_refs, "metadata": row.metadata_json,
        "assets": assets, "validation": _combined_validation(geometry, issues),
    }


def get_definition(db, user_id, organization_id, definition_id):
    return _detail(db, _definition(db, user_id, organization_id, definition_id, lock=True))


def _replace_links(db, row, refs):
    existing = {(ref.asset_id, ref.role): ref for ref in _links(db, row.id)}
    requested = {(ref.asset_id, ref.role) for ref in refs}
    for key, ref in existing.items():
        if key not in requested:
            db.delete(ref)
    for asset_id, role in sorted(requested - existing.keys()):
        db.add(ObjectDefinitionAsset(definition_id=row.id, asset_id=asset_id, role=role))


def _save(db, row, fields, *, advance=False):
    dimensions = _stored_dimensions(fields.dimensions)
    transform = _merged_transform(fields.default_transform)
    geometry = fields.geometry.model_dump(mode="json", exclude_none=True)
    validation = _geometry_validation(row.id, _prototype(dimensions, transform, {"geometry": geometry}))
    _, asset_issues = _assets(db, row, fields.assets, lock=True)
    _require_valid(_combined_validation(validation, asset_issues))

    for key, value in dimensions.items():
        setattr(row, key, value)
    row.name, row.description = fields.name, fields.description
    row.category, row.object_type, row.tags = fields.category, fields.object_type, fields.tags
    row.geometry, row.default_transform = geometry, transform.model_dump(mode="json")
    row.appearance, row.material_refs, row.metadata_json = fields.appearance or {}, fields.material_refs or [], fields.metadata or {}
    row.plan_compatible = validation["plan_compatible"]
    if advance:
        row.version += 1
    db.add(row)
    _flush(db)
    _replace_links(db, row, fields.assets)
    _flush(db)
    db.refresh(row)
    response = _detail(db, row)
    _commit(db)
    return response


def create_definition(db, user_id, organization_id, data):
    scope = _scope(db, user_id, organization_id, data.workspace_id, data.project_id, write=True)
    scope.pop("event_id")
    return _save(db, ObjectDefinition(**scope, created_by_user_id=user_id), data)


def _check_version(row, expected):
    if row.version != expected:
        raise HTTPException(409, detail={"code": "definition_version_conflict", "definition_id": str(row.id),
                                          "current_version": row.version, "message": "Reload the current definition."})


def _fields(db, row):
    return {
        "name": row.name, "description": row.description, "category": row.category, "object_type": row.object_type,
        "tags": row.tags, "dimensions": _dimensions(row), "default_transform": row.default_transform,
        "geometry": row.geometry, "appearance": row.appearance, "material_refs": row.material_refs, "metadata": row.metadata_json,
        "assets": [{"asset_id": ref.asset_id, "role": ref.role} for ref in _links(db, row.id)],
    }


def update_definition(db, user_id, organization_id, definition_id, data):
    row = _definition(db, user_id, organization_id, definition_id, write=True, lock=True)
    _check_version(row, data.expected_version)
    changes = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    status = changes.pop("status", row.status)
    names = {"name", "category", "object_type", "tags", "dimensions", "default_transform", "geometry", "assets"}
    if status is None or any(key in names and value is None for key, value in changes.items()):
        raise HTTPException(422, detail="Required definition fields cannot be null.")
    if "default_transform" in changes:
        changes["default_transform"] = _merged_transform(row.default_transform, data.default_transform)
    if not changes and status == row.status:
        return _detail(db, row)
    try:
        fields = DefinitionFields.model_validate({**_fields(db, row), **changes})
    except ValidationError as exc:
        raise HTTPException(422, detail="The merged definition fields are invalid.") from exc
    row.status = status
    try:
        return _save(db, row, fields, advance=True)
    except Exception:
        db.rollback()
        raise


def archive_definition(db, user_id, organization_id, definition_id, expected_version):
    row = _definition(db, user_id, organization_id, definition_id, write=True, lock=True)
    _check_version(row, expected_version)
    if row.status != "archived":
        row.status = "archived"
        row.version += 1
        _flush(db)
        db.refresh(row)
        response = _detail(db, row)
        _commit(db)
        return response
    return _detail(db, row)


def duplicate_definition(db, user_id, organization_id, definition_id, data):
    source = _definition(db, user_id, organization_id, definition_id, write=True, lock=True)
    _check_version(source, data.expected_version)
    fields = _fields(db, source)
    fields["name"] = data.name or source.name[:195] + " copy"
    try:
        fields = DefinitionFields.model_validate(fields)
    except ValidationError as exc:
        raise HTTPException(422, detail="The duplicate definition fields are invalid.") from exc
    row = ObjectDefinition(organization_id=source.organization_id, workspace_id=source.workspace_id,
                           project_id=source.project_id, created_by_user_id=user_id)
    return _save(db, row, fields)


def _asset_filters():
    links = ObjectDefinitionAsset
    has_assets = select(links.asset_id).where(links.definition_id == ObjectDefinition.id).exists()
    unavailable = select(links.asset_id).select_from(links).outerjoin(Asset, Asset.id == links.asset_id).where(
        links.definition_id == ObjectDefinition.id,
        or_(Asset.id.is_(None), Asset.organization_id != ObjectDefinition.organization_id, Asset.deleted_at.is_not(None),
            Asset.status != "active", Asset.event_id.is_not(None),
            and_(Asset.workspace_id.is_not(None), Asset.workspace_id.is_distinct_from(ObjectDefinition.workspace_id)),
            and_(Asset.project_id.is_not(None), Asset.project_id.is_distinct_from(ObjectDefinition.project_id))),
    ).exists()
    return has_assets, unavailable


def _conditions(db, user_id, organization_id, filters):
    scope = _scope(db, user_id, organization_id, filters.workspace_id, filters.project_id)
    model = ObjectDefinition
    clauses = [model.organization_id == organization_id]
    if scope["workspace_id"] is not None:
        clauses.append(or_(model.workspace_id.is_(None), model.workspace_id == scope["workspace_id"]))
        clauses.append(or_(model.project_id.is_(None), model.project_id == scope["project_id"]))
    if filters.scope == "organization":
        clauses.append(model.workspace_id.is_(None))
    elif filters.scope == "workspace":
        clauses.extend((model.workspace_id.is_not(None), model.project_id.is_(None)))
    elif filters.scope == "project":
        clauses.append(model.project_id.is_not(None))
    if filters.status != "all":
        clauses.append(model.status == filters.status)
    if filters.q:
        safe = filters.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{safe}%"
        clauses.append(or_(model.name.ilike(pattern, escape="\\"), model.description.ilike(pattern, escape="\\")))
    if filters.category:
        clauses.append(func.lower(model.category) == filters.category.lower())
    if filters.object_type:
        clauses.append(func.lower(model.object_type) == filters.object_type.lower())
    if filters.tags:
        clauses.append(model.tags.contains(sorted({tag.lower() for tag in filters.tags})))
    if filters.geometry_type:
        clauses.append(model.geometry["type"].astext == filters.geometry_type.lower())
    if filters.plan_compatible is not None:
        clauses.append(model.plan_compatible == filters.plan_compatible)
    if filters.min_width_m is not None:
        clauses.append(model.width_m >= filters.min_width_m)
    if filters.max_width_m is not None:
        clauses.append(model.width_m <= filters.max_width_m)
    has_assets, unavailable = _asset_filters()
    if filters.asset_availability == "none":
        clauses.append(~has_assets)
    elif filters.asset_availability == "available":
        clauses.extend((has_assets, ~unavailable))
    elif filters.asset_availability == "unavailable":
        clauses.append(unavailable)
    return clauses, has_assets, unavailable


def list_definitions(db, user_id, organization_id, filters):
    clauses, has_assets, unavailable = _conditions(db, user_id, organization_id, filters)
    query = select(ObjectDefinition, has_assets, unavailable).where(*clauses).order_by(
        func.lower(ObjectDefinition.name), ObjectDefinition.id,
    ).offset(filters.offset).limit(filters.limit + 1)
    rows = db.execute(query).all()
    return {"items": [_summary(row, "unavailable" if bad else "available" if has else "none")
                      for row, has, bad in rows[:filters.limit]], "offset": filters.offset, "limit": filters.limit,
            "has_more": len(rows) > filters.limit}


def catalog_facets(db, user_id, organization_id, filters):
    clauses, _, _ = _conditions(db, user_id, organization_id, filters)
    model = ObjectDefinition
    categories = select(func.lower(model.category).label("value")).where(*clauses).subquery()
    tags = select(func.unnest(model.tags).label("value")).where(*clauses).subquery()

    def counts(table):
        value = table.c.value
        query = select(value, func.count().label("count")).group_by(value).order_by(value)
        return [dict(row._mapping) for row in db.execute(query.offset(filters.offset).limit(filters.limit))]

    return {"categories": counts(categories), "tags": counts(tags)}


def scene_definition(db, user_id, project, definition_id):
    row = _definition(db, user_id, project.organization_id, definition_id, lock=True)
    if (row.workspace_id is not None and row.workspace_id != project.workspace_id or
        row.project_id is not None and row.project_id != project.id):
        _missing()
    return row


def placement_candidates(db, user_id, project, names, categories):
    """Bound catalog work per search term and reuse the public discovery scope and asset rules."""
    filters = CatalogFilters(project_id=project.id)
    clauses, has_assets, unavailable = _conditions(db, user_id, project.organization_id, filters)
    model = ObjectDefinition
    matches = {}
    for field, terms in ((model.name, names), (model.category, categories), (model.object_type, categories)):
        if not terms:
            continue
        key = func.lower(func.btrim(field))
        ranked = select(model.id, func.row_number().over(
            partition_by=key, order_by=(func.lower(model.name), model.id),
        ).label("rank")).where(*clauses, key.in_(terms), ~unavailable).subquery()
        rows = db.execute(select(model, has_assets).join(ranked, ranked.c.id == model.id).where(ranked.c.rank <= 24))
        for row, has in rows:
            try:
                matches[row.id] = DefinitionSummary.model_validate(_summary(row, "available" if has else "none"))
            except (ValidationError, KeyError, TypeError):
                continue
    return list(matches.values())


def placement_versions(db, user_id, project, definition_ids):
    if not definition_ids:
        return {}
    clauses, _, unavailable = _conditions(db, user_id, project.organization_id, CatalogFilters(project_id=project.id))
    return dict(db.execute(select(ObjectDefinition.id, ObjectDefinition.version).where(
        *clauses, ObjectDefinition.id.in_(definition_ids), ~unavailable,
    )).all())


def instantiate(db, user_id, scene_id, definition_id, data, *, commit=True, entity_id=None):
    scene = _get_scene(db, scene_id, user_id, write=True, lock=True)
    project = require_project_access(db, user_id, scene.project_id, write=True)
    row = scene_definition(db, user_id, project, definition_id)
    if scene.current_revision != data.base_revision:
        raise HTTPException(409, detail={"code": "scene_revision_conflict", "current_revision": scene.current_revision,
                                          "message": "Reload the current scene."})
    _check_version(row, data.definition_version)
    if row.status != "active":
        raise HTTPException(409, detail={"code": "definition_archived", "message": "Archived definitions cannot be placed."})

    transform = _merged_transform(_default_transform(row), data.transform)
    dimensions = _stored_dimensions(data.dimensions if data.dimensions is not None else _dimensions(row))
    properties = {"geometry": row.geometry}
    if data.metadata is not None:
        properties["instance_metadata"] = data.metadata
    parent_world = _world(db, scene.id, SpatialObject, data.parent_object_id)
    validation = _geometry_validation(row.id, _prototype(dimensions, transform, properties), parent_world)
    _require_valid(validation)
    assets, asset_issues = _assets(db, row, _links(db, row.id), lock=True)
    _require_valid(_combined_validation(validation, asset_issues), status=409)
    bounds = validation["world_bounds"]
    limits = (float(scene.width_m), float(scene.depth_m), float(scene.height_m))
    if any(single_min < -DEFAULT_TOLERANCE.linear(single_min) or
           single_max > limit + DEFAULT_TOLERANCE.linear(single_max, limit)
           for single_min, single_max, limit in zip(bounds["minimum_m"], bounds["maximum_m"], limits)):
        validation = _combined_validation(validation, [
            _issue("outside_scene_bounds", "The placed geometry extends outside the scene bounds.",
                   "transform", severity="warning"),
        ])

    scope = {"layer_id": data.layer_id} if "layer_id" in data.model_fields_set else {}
    name = data.name or row.name
    if any(ord(character) < 32 for character in name):
        raise HTTPException(422, detail="Object name cannot contain control characters.")
    instance = SpatialObjectCreate(
        name=name, object_type=row.object_type, category=row.category, zone_id=data.zone_id,
        parent_object_id=data.parent_object_id, status=data.status, transform=transform, dimensions=dimensions,
        appearance=data.appearance if data.appearance is not None else row.appearance,
        material_refs=data.material_refs if data.material_refs is not None else row.material_refs,
        properties=properties, **scope,
    )
    try:
        result = SpatialService.create_object(db, scene.id, instance, user_id, commit=False, entity_id=entity_id,
                                              library_origin=(row.id, row.version))
        for asset in assets:
            link = SpatialAssetAttach(asset_id=asset["asset_id"], role=asset["role"])
            SpatialService.attach_asset(db, result["id"], link, user_id, commit=False)
        committed = scene.current_revision
        if commit:
            _commit(db)
    except Exception:
        if commit:
            db.rollback()
        raise
    return {"object": result, "scene_revision": committed, "validation": validation}
