"""One authorized scene revision, projected into a synchronized 2D/3D pair."""

from pydantic import ValidationError

from app.schemas.spatial_sync import Footprint2D
from app.services.spatial import _get_scene
from app.services.spatial_geometry import _geometry_data
from app.services.spatial_graph import GraphRead
from app.services.spatial_runtime import SceneCompilation
from app.spatial.projection import project_xy
from app.spatial.tolerance import DEFAULT_TOLERANCE
from app.spatial.types import GeometryError, UnsupportedGeometryError


_SHARED_NODE_FIELDS = (
    "type", "id", "parent", "transform_parent", "children", "order_index", "zone_id", "layer_id", "layer_order_index",
    "visible", "locked", "effective_visible", "effective_locked", "authored_dimensions",
    "library_definition_id", "library_definition_version",
)


def _identity(node):
    return node["type"], node["id"]


def _issue(code, message, *, node=None, field=None, representation="pair", severity="error"):
    return {
        "code": code, "message": message, "node": {"type": node["type"], "id": node["id"]} if node else None,
        "field": field, "representation": representation, "severity": severity,
    }


def _world_origin(matrix):
    return tuple(row[3] for row in matrix[:3]) if matrix is not None else None


def _xy_bounds(world_bounds):
    if world_bounds is None:
        return None
    minimum, maximum = world_bounds["minimum_m"], world_bounds["maximum_m"]
    dimensions = world_bounds["dimensions_m"]
    return {
        "min_x_m": minimum[0], "min_y_m": minimum[1], "max_x_m": maximum[0], "max_y_m": maximum[1],
        "width_m": dimensions["width_m"], "depth_m": dimensions["depth_m"],
    }


def _revision_check(supplied, current):
    if supplied is None:
        status = "not_supplied"
    elif supplied == current:
        status = "current"
    else:
        status = "stale" if supplied < current else "ahead"
    return {"supplied_revision": supplied, "status": status}


def project_plan(compilation, runtime):
    """Reuse the canonical footprints and world frames resolved during compilation."""
    nodes, issues = [], []
    for node in runtime["nodes"]:
        identity = _identity(node)
        source = compilation.footprints.get(identity)
        world, dimensions = node["world_matrix"], node["authored_dimensions"]
        footprint = None
        if source is not None and world is not None:
            try:
                height = dimensions["height_m"] if dimensions is not None else None
                projected = project_xy(source, world, height_m=height)
                footprint = Footprint2D.model_validate(_geometry_data(projected)).model_dump()
                compilation.projected_footprints[identity] = projected
            except UnsupportedGeometryError as exc:
                issues.append(_issue("plan_projection_unsupported", str(exc), node=node, field="footprint",
                                     representation="plan", severity="warning"))
            except GeometryError as exc:
                issues.append(_issue("invalid_plan_projection", str(exc), node=node, field="footprint", representation="plan"))
            except (ValidationError, OverflowError):
                issues.append(_issue("invalid_plan_projection", "The projected footprint has non-finite or invalid measurements.",
                                     node=node, field="footprint", representation="plan"))
        nodes.append({
            **{field: node[field] for field in _SHARED_NODE_FIELDS},
            "world_origin_m": _world_origin(world), "footprint": footprint,
            "renderable": footprint is not None and node["renderable"],
        })

    return {
        "scene_id": runtime["scene_id"], "scene_revision": runtime["scene_revision"],
        "coordinate_system": runtime["coordinates"].system, "length_unit": runtime["coordinates"].length_unit,
        "display_unit": runtime["display_unit"], "meters_per_display_unit": runtime["meters_per_display_unit"],
        "default_layer_id": runtime["default_layer_id"], "bounds": _xy_bounds(runtime["bounds"]),
        "roots": runtime["roots"], "nodes": nodes,
    }, issues


def _matching_bounds(footprint, geometry):
    bounds = _xy_bounds(geometry["world_bounds"])
    return all(DEFAULT_TOLERANCE.equal(footprint["bounds"][field], value) for field, value in bounds.items())


def validate_pair(plan, runtime):
    """Check the generated projections without accepting client geometry as input."""
    issues = []
    coordinates = runtime["coordinates"]
    coordinates = coordinates.model_dump() if hasattr(coordinates, "model_dump") else coordinates
    for field, expected, code in (
        ("scene_id", runtime["scene_id"], "scene_mismatch"),
        ("scene_revision", runtime["scene_revision"], "revision_mismatch"),
        ("coordinate_system", coordinates["system"], "coordinate_mismatch"),
        ("length_unit", coordinates["length_unit"], "unit_mismatch"),
        ("display_unit", runtime["display_unit"], "unit_mismatch"),
        ("meters_per_display_unit", runtime["meters_per_display_unit"], "unit_mismatch"),
        ("default_layer_id", runtime["default_layer_id"], "layer_mismatch"),
        ("bounds", _xy_bounds(runtime["bounds"]), "scene_bounds_mismatch"),
        ("roots", runtime["roots"], "hierarchy_mismatch"),
    ):
        if plan[field] != expected:
            issues.append(_issue(code, f"The projections have different {field}.", field=field))

    planar = {_identity(node): node for node in plan["nodes"]}
    spatial = {_identity(node): node for node in runtime["nodes"]}
    if len(planar) != len(plan["nodes"]) or len(spatial) != len(runtime["nodes"]):
        issues.append(_issue("duplicate_node", "A projection contains repeated node references.", field="nodes"))
    for identity, node in planar.items():
        if identity not in spatial:
            issues.append(_issue("missing_runtime_node", "The 3D projection is missing a 2D node.", node=node))
    if list(planar) != list(spatial):
        issues.append(_issue("node_order_mismatch", "The projections have different ordered node references.", field="nodes"))

    for identity, node in spatial.items():
        outline = planar.get(identity)
        if outline is None:
            issues.append(_issue("missing_plan_node", "The 2D projection is missing a 3D node.", node=node))
            continue
        for field in _SHARED_NODE_FIELDS[2:]:
            if outline[field] != node[field]:
                issues.append(_issue("node_metadata_mismatch", f"The projections have different {field}.", node=node, field=field))
        origin = _world_origin(node["world_matrix"])
        plan_origin = outline["world_origin_m"]
        if (tuple(plan_origin) if plan_origin is not None else None) != origin:
            issues.append(_issue("origin_mismatch", "The 2D origin differs from the 3D world transform.",
                                 node=node, field="world_origin_m"))
        if outline["footprint"] is not None and node["geometry"] is None:
            issues.append(_issue("missing_runtime_geometry", "The projected footprint has no matching runtime geometry.",
                                 node=node, field="geometry"))
        elif outline["footprint"] is not None:
            if not _matching_bounds(outline["footprint"], node["geometry"]):
                issues.append(_issue("projection_bounds_mismatch", "The 2D bounds differ from the XY extent of the 3D geometry.",
                                     node=node, field="footprint.bounds"))
            if outline["renderable"] != node["renderable"]:
                issues.append(_issue("visibility_mismatch", "The projections disagree on whether a compatible node can be drawn.",
                                     node=node, field="renderable"))
    return issues


def compile_pair(compilation, *, plan_revision=None, runtime_revision=None):
    runtime = compilation.compile()
    plan, issues = project_plan(compilation, runtime)
    mismatches = validate_pair(plan, runtime)
    affected = {_identity(issue["node"]) for issue in mismatches if issue["node"] is not None}
    if mismatches:
        all_nodes_affected = any(issue["node"] is None for issue in mismatches)
        for node in plan["nodes"]:
            if all_nodes_affected or _identity(node) in affected:
                node["renderable"] = False
    issues = [*({**issue, "representation": "runtime"} for issue in runtime["validation"]["issues"]), *issues, *mismatches]
    status = "invalid" if any(issue["severity"] == "error" for issue in issues) else (
        "partial" if issues else "synchronized")
    revision = runtime["scene_revision"]
    return {
        "scene_id": runtime["scene_id"], "scene_revision": revision,
        "freshness": {"plan": _revision_check(plan_revision, revision), "runtime": _revision_check(runtime_revision, revision)},
        "validation": {"status": status, "issues": issues},
        "plan": plan, "runtime": runtime,
    }


def synchronize_scene(db, scene_id, user_id, *, plan_revision=None, runtime_revision=None):
    scene = _get_scene(db, scene_id, user_id, lock="read")
    compilation = SceneCompilation(GraphRead(db, scene))
    return compile_pair(compilation, plan_revision=plan_revision, runtime_revision=runtime_revision)
