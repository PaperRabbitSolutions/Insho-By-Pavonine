"""Check catalog provenance without replacing pinned instance geometry."""

from collections import defaultdict

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import or_, select

from app.models.asset import Asset
from app.models.object_library import ObjectDefinition, ObjectDefinitionAsset
from app.schemas.object_library import CatalogGeometry
from app.schemas.spatial import SpatialTransform
from app.services.object_library import _asset_available, _dimensions, _geometry_validation, _prototype
from app.services.spatial import asset_matches_scene


def _default_issues(definition):
    try:
        transform = SpatialTransform.model_validate(definition.default_transform)
        geometry = CatalogGeometry.model_validate(definition.geometry)
        spatial = _prototype(_dimensions(definition), transform, {"geometry": geometry.model_dump(exclude_none=True)})
    except (ValidationError, HTTPException):
        return [{"code": "invalid_spatial_defaults", "message": "Stored catalog defaults do not satisfy the spatial schema.",
                 "field": "defaults", "severity": "error"}]
    return _geometry_validation(definition.id, spatial)["issues"]


def object_library(ctx, run):
    resolution = "Review the catalog reference and its asset availability, then revalidate the scene."
    definition_ids = {row.library_definition_id for row in ctx.objects.values() if row.library_definition_id is not None}
    definitions = {}
    if definition_ids:
        definitions = {row.id: row for row in ctx.db.scalars(select(ObjectDefinition).where(
            ObjectDefinition.id.in_(definition_ids),
            ObjectDefinition.organization_id == ctx.project.organization_id,
            or_(ObjectDefinition.workspace_id.is_(None), ObjectDefinition.workspace_id == ctx.project.workspace_id),
            or_(ObjectDefinition.project_id.is_(None), ObjectDefinition.project_id == ctx.project.id),
        ))}
    current_ids = {row.library_definition_id for row in ctx.objects.values()
                   if row.library_definition_id in definitions
                   and row.library_definition_version == definitions[row.library_definition_id].version}
    links = defaultdict(list)
    if current_ids:
        for link in ctx.db.scalars(select(ObjectDefinitionAsset).where(ObjectDefinitionAsset.definition_id.in_(current_ids))):
            links[link.definition_id].append(link)

    scene_links = ctx.graph.assets
    asset_ids = {link["asset_id"] for object_links in scene_links.values() for link in object_links}
    asset_ids.update(link.asset_id for definition_links in links.values() for link in definition_links)
    assets = {}
    if asset_ids:
        assets = {row.id: row for row in ctx.db.scalars(select(Asset).where(
            Asset.id.in_(asset_ids), Asset.organization_id == ctx.project.organization_id,
        ))}
    for object_id, object_links in scene_links.items():
        ref = ("object", object_id) if object_id in ctx.objects else ("group", object_id)
        for link in object_links:
            if ref not in ctx.by_ref:
                run.finding("asset_owner_unavailable", "Asset link owner unavailable",
                            "An asset link points to a node absent from this scene.", resolution,
                            qualifier=str(link["id"]), incomplete=True)
                continue
            asset = assets.get(link["asset_id"])
            if (asset is None or asset.status != "active" or asset.deleted_at is not None
                    or not asset_matches_scene(asset, ctx.project, ctx.scene)):
                run.finding("instance_asset_unavailable", "Instance asset unavailable",
                            "An attached asset is unavailable within this scene's scope.", resolution, (ref,),
                            qualifier=str(link["id"]), metadata={"field": "assets", "role": link["role"]})
    definition_checks = {}
    for object_id, row in ctx.objects.items():
        ref = ("object", object_id)
        definition_id, version = row.library_definition_id, row.library_definition_version
        if definition_id is None and version is None:
            continue
        definition = definitions.get(definition_id)
        if definition is None:
            run.finding("definition_unavailable", "Definition unavailable",
                        "The instance's definition could not be resolved within this scene's scope.",
                        resolution, (ref,), incomplete=True)
            continue
        if version is None or version < 1 or version > definition.version:
            run.finding("invalid_definition_version", "Invalid definition version",
                        "The instance's pinned version falls outside the catalog version range.",
                        resolution, (ref,), metadata={"definition_id": definition.id, "pinned_version": version,
                                                      "current_version": definition.version}, incomplete=True)
            continue
        if definition.status == "archived":
            run.finding("definition_archived", "Catalog definition archived",
                        "The instance remains usable with its stored geometry. The definition is archived.",
                        "Review the catalog entry if the layout needs a new placement of this item.", (ref,),
                        severity="info", metadata={"definition_id": definition.id, "pinned_version": version})
        if version != definition.version:
            continue
        if definition.id not in definition_checks:
            definition_checks[definition.id] = _default_issues(definition)
        for issue in definition_checks[definition.id]:
            run.finding("definition_" + issue["code"], "Catalog spatial defaults",
                        issue["message"], resolution, (ref,), severity=issue["severity"],
                        qualifier=issue["field"], metadata={"definition_id": definition.id, "field": issue["field"]})
        for link in links[definition.id]:
            if not _asset_available(definition, assets.get(link.asset_id)):
                run.finding("definition_asset_unavailable", "Definition asset unavailable",
                            "An asset linked to the current definition is unavailable in its catalog scope.",
                            resolution, (ref,), qualifier=f"{link.asset_id}/{link.role}",
                            metadata={"definition_id": definition.id, "field": "assets", "role": link.role})
