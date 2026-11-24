"""Rules over the shared scene compilation and deterministic geometry library."""

import math

from app.schemas.spatial_validation import ClearanceZone
from app.spatial.geometry2d import contains, geometry_bounds, intersection, minimum_distance
from app.spatial.geometry3d import AABB3, aabb_intersects
from app.spatial.tolerance import DEFAULT_TOLERANCE
from app.spatial.types import GeometryError
from app.spatial.validation import clearance_result, evaluate_boundary, iter_aabb_pairs, object_intersection


_GEOMETRY_CODES = {"invalid_scene_dimensions", "invalid_dimensions", "unsupported_geometry", "invalid_geometry", "unmeasured_zone"}
_TRANSFORM_CODES = {"invalid_transform", "invalid_world_transform", "parent_transform_unavailable", "invalid_coordinate_system"}
_HIERARCHY_CODES = {"hierarchy_unreachable", "missing_parent", "parent_cycle", "missing_reference", "effective_state_unavailable"}
_CLEARANCE_RESOLUTION = "Move the relevant objects or revise the configured minimum separation."


def _compiled_issues(ctx, run, codes, resolution):
    for issue in ctx.compiled["runtime"]["validation"]["issues"]:
        if issue["code"] not in codes:
            continue
        identity = issue["node"]
        refs = ((identity["type"], identity["id"]),) if identity is not None else ()
        code, field = issue["code"], issue["field"]
        run.finding(code, code.replace("_", " ").capitalize(), issue["message"], resolution, refs,
                    severity=issue["severity"], qualifier=field or "", metadata={"field": field},
                    incomplete=issue["severity"] == "error")


def geometry(ctx, run):
    _compiled_issues(ctx, run, _GEOMETRY_CODES, "Repair the authored dimensions or footprint, then revalidate the scene.")


def transforms(ctx, run):
    _compiled_issues(ctx, run, _TRANSFORM_CODES, "Correct the local transform or its parent frame, then revalidate the scene.")


def hierarchy(ctx, run):
    resolution = "Repair the node's references or ordering in the canonical scene graph."
    _compiled_issues(ctx, run, _HIERARCHY_CODES, resolution)
    for node in ctx.nodes.values():
        ref = (node["type"], node["id"])
        row = ctx.by_ref[ref]
        if row.order_index < 0:
            run.finding("invalid_order", "Invalid sibling order", "Sibling order must be nonnegative.", resolution, (ref,))
        if ref[0] not in {"group", "object"}:
            continue
        if row.node_kind not in {"object", "group"} or (
            row.node_kind == "group" and any(value is not None for value in (row.width_m, row.depth_m, row.height_m))
        ):
            run.finding("invalid_group_structure", "Invalid object or group structure",
                        "The node kind and physical dimensions conflict with the graph contract.", resolution, (ref,),
                        incomplete=True)
        if ((row.layer_id is None) != (row.layer_order_index is None)
                or (row.layer_order_index is not None and row.layer_order_index < 0)):
            run.finding("invalid_layer_order", "Invalid layer order",
                        "Layer membership requires a nonnegative layer order index.", resolution, (ref,))
    for edge in ctx.relationships:
        refs = tuple(("object", node_id) if node_id in ctx.objects else ("group", node_id)
                     for node_id in (edge.source_object_id, edge.target_object_id))
        if refs[0] == refs[1] or any(ref not in ctx.by_ref for ref in refs):
            run.finding("invalid_relationship", "Invalid object relationship",
                        "A relationship must join distinct nodes in this scene.", resolution, refs,
                        qualifier=str(edge.id), incomplete=True)


def venue_boundary(ctx, run):
    resolution = "Adjust the placement or dimensions, or correct the measured venue dimensions."
    if ctx.venue is None:
        run.finding("venue_boundary_unavailable", "Venue boundary unavailable",
                    "Measured venue dimensions are required to check the scene boundary.",
                    "Supply valid venue dimensions and run validation again.", severity="warning", incomplete=True)
        return
    venue = ctx.venue.aabb
    refs = [("object", object_id) for object_id in ctx.objects]
    refs.extend(("zone", zone_id) for zone_id in ctx.boundary_zone_ids)
    for ref in refs:
        bounds = ctx.bounds.get(ref)
        if bounds is None:
            run.complete = False
            continue
        extents = (
            (venue.min_x, bounds.min_x, "min_x", 1), (venue.max_x, bounds.max_x, "max_x", -1),
            (venue.min_y, bounds.min_y, "min_y", 1), (venue.max_y, bounds.max_y, "max_y", -1),
            (venue.min_z, bounds.min_z, "min_z", 1), (venue.max_z, bounds.max_z, "max_z", -1),
        )
        excursions = {axis: (limit - value) * sign for limit, value, axis, sign in extents
                      if (limit - value) * sign > DEFAULT_TOLERANCE.linear(limit, value)}
        if not excursions:
            continue
        classification = "exceeds"
        if not aabb_intersects(venue, bounds):
            classification = "outside"
        elif ref[0] == "object":
            try:
                result = evaluate_boundary(ctx.item(ref[1]), ctx.venue)
                if result.status in {"partial", "outside"}:
                    classification = result.status
            except (GeometryError, OverflowError):
                pass  # The exact world bounds already prove the containment failure.
        run.finding("venue_boundary_exceeded", f"{ref[0].capitalize()} exceeds venue bounds",
                    "The measured geometry extends beyond the venue envelope.", resolution, (ref,),
                    measurement={"measured": max(excursions.values()), "expected": 0, "unit": "m"},
                    metadata={"classification": classification, "excursions_m": excursions})


def _check_zone(ctx, run, ref, zone_id):
    target = ("zone", zone_id)
    refs = (ref, target)
    resolution = "Adjust the placement or revise the assigned zone's measured footprint."
    outline, boundary = ctx.projected.get(ref), ctx.projected.get(target)
    if outline is None or boundary is None:
        run.finding("zone_containment_unavailable", "Zone containment unavailable",
                    "The assigned zone or its member has no usable XY footprint.", resolution, refs, incomplete=True)
        return
    try:
        if contains(boundary, outline):
            return
        overlap = intersection(boundary, outline)
    except (GeometryError, OverflowError):
        run.finding("zone_containment_unavailable", "Zone containment unavailable",
                    "The current footprints cannot be compared reliably.", resolution, refs, incomplete=True)
        return
    run.finding("outside_assigned_zone", "Geometry extends outside its zone",
                "The projected geometry is not fully inside the assigned zone.", resolution, refs,
                metadata={"classification": "partial" if overlap.intersects else "outside"})


def zone_containment(ctx, run):
    for object_id in ctx.objects:
        zone_id = ctx.zones_by_object[object_id]
        if zone_id is not None:
            _check_zone(ctx, run, ("object", object_id), zone_id)
        elif ctx.config.require_zone_assignment:
            run.finding("zone_assignment_required", "Zone assignment required",
                        "No zone is assigned directly or through an ancestor.",
                        "Assign the object or its group to a measured zone.", (("object", object_id),))
    for ref, row in ctx.by_ref.items():
        if ref[0] == "zone" and row.parent_zone_id is not None:
            _check_zone(ctx, run, ref, row.parent_zone_id)


def zone_overlap(ctx, run):
    resolution = "Move or resize one of the zones, or update the exclusion in the validation policy."
    for pair in sorted({pair.key() for pair in ctx.config.exclusive_zone_pairs or ()}):
        refs = tuple(("zone", zone_id) for zone_id in pair)
        outlines = [ctx.projected.get(ref) for ref in refs]
        try:
            result = intersection(*outlines) if all(outline is not None for outline in outlines) else None
        except (GeometryError, OverflowError):
            result = None
        if result is None:
            run.finding("zone_overlap_unavailable", "Zone comparison unavailable",
                        "An exclusive zone pair does not have two measurable footprints in this scene.",
                        resolution, refs, qualifier="/".join(map(str, pair)), incomplete=True)
        elif result.intersects and result.classification != "touching":
            measure = {"measured": result.overlap_area_m2, "expected": 0, "unit": "m²"}
            run.finding("exclusive_zones_overlap", "Exclusive zones overlap",
                        "The configured exclusive zones share space in the floor plan.", resolution, refs,
                        measurement=measure, metadata={"classification": result.classification})


def collision(ctx, run):
    options = ctx.config.collision
    allowed = {pair.key() for pair in options.allowed_pairs or ()}
    allowed.update(tuple(sorted((edge.source_object_id, edge.target_object_id)))
                   for edge in ctx.relationships if edge.relationship_type == "allows_overlap")
    ids = []
    for object_id, row in ctx.objects.items():
        ref = ("object", object_id)
        if isinstance(row.properties, dict) and row.properties.get("collision_policy") == "ignore":
            continue
        if not options.include_hidden and not ctx.nodes[ref]["effective_visible"]:
            continue
        if ref in ctx.bounds:
            ids.append(object_id)
        else:
            run.complete = False
    resolution = "Adjust the placement or record the intended overlap in the scene policy."
    for first, second in ctx.pairs(ids):
        if (first, second) in allowed or (options.ignore_ancestor_pairs and ctx.are_related(first, second)):
            continue
        refs = (("object", first), ("object", second))
        try:
            result = object_intersection(ctx.item(first), ctx.item(second))
        except (GeometryError, OverflowError) as exc:
            result = {"intersects": None, "calculation_method": str(exc)}
        if result["intersects"] is None:
            run.finding("collision_unavailable", "Collision check unavailable", result["calculation_method"],
                        "Review this geometry pair in 3D or use supported collision shapes.", refs, incomplete=True)
        elif result["intersects"] and (options.report_touching or result["classification"] != "touching"):
            area, volume = result["overlap_area_m2"], result["overlap_volume_m3"]
            unit, value = ("m³", volume) if volume is not None else ("m²", area)
            measure = {"measured": value, "expected": 0, "unit": unit} if value is not None and math.isfinite(value) else None
            touching = result["classification"] == "touching"
            run.finding("objects_touch" if touching else "objects_overlap", "Objects touch" if touching else "Objects overlap",
                        "The objects' measured geometry intersects.", resolution, refs,
                        severity="info" if touching else None, measurement=measure,
                        metadata={"classification": result["classification"], "method": result["calculation_method"]})


def _matching(ctx, selector):
    if selector.zone_id is not None and ("zone", selector.zone_id) not in ctx.by_ref:
        return set()
    return {object_id for object_id, row in ctx.objects.items()
            if (selector.object_id is None or selector.object_id == object_id)
            and (selector.zone_id is None or selector.zone_id == ctx.zones_by_object[object_id])
            and (selector.category is None or selector.category == row.category)}


def _unavailable_clearance_target(run, requirement):
    run.finding("clearance_target_unavailable", "Clearance target unavailable",
                "A configured object or zone is unavailable in this scene.",
                "Update the clearance selector to use entities in this scene.",
                qualifier=requirement.id, metadata={"requirement_id": requirement.id}, incomplete=True)


def _clearance_measurement(run, requirement, refs, measured, method):
    minimum = requirement.minimum_m
    run.finding("clearance_below_minimum", "Clearance below minimum",
                "Surface-to-surface separation is below the configured minimum.", _CLEARANCE_RESOLUTION, refs,
                qualifier=requirement.id, metadata={"requirement_id": requirement.id, "method": method},
                measurement={"measured": measured, "expected": minimum, "shortfall": minimum - measured, "unit": "m"})


def _planar_bounds(outline):
    box = geometry_bounds(outline)
    return AABB3(box.min_x, box.min_y, 0, box.max_x, box.max_y, 0)


def _zone_clearance(ctx, run, requirement, subjects):
    zone = ("zone", requirement.against.zone_footprint_id)
    if zone not in ctx.by_ref:
        _unavailable_clearance_target(run, requirement)
        return
    boundary = ctx.projected.get(zone)
    if boundary is None:
        run.finding("clearance_geometry_unavailable", "Clearance geometry unavailable",
                    "The configured access or exclusion zone has no usable XY footprint.", _CLEARANCE_RESOLUTION, (zone,),
                    qualifier=requirement.id, metadata={"requirement_id": requirement.id}, incomplete=True)
        return
    limit = requirement.minimum_m
    bounds = _planar_bounds(boundary)
    for object_id in sorted(subjects):
        ref = ("object", object_id)
        outline = ctx.projected.get(ref)
        try:
            if outline is None:
                raise GeometryError("The selected object has no usable XY footprint.")
            if not aabb_intersects(bounds, _planar_bounds(outline), margin_m=limit):
                continue
            measured = minimum_distance(boundary, outline)
            if not math.isfinite(measured):
                raise GeometryError("The distance calculation exceeded the supported coordinate range.")
        except (GeometryError, OverflowError) as exc:
            run.finding("clearance_unavailable", "Clearance check unavailable", str(exc), _CLEARANCE_RESOLUTION, (ref, zone),
                        qualifier=requirement.id, metadata={"requirement_id": requirement.id}, incomplete=True)
            continue
        if measured + DEFAULT_TOLERANCE.linear(measured, limit) < limit:
            _clearance_measurement(run, requirement, (ref, zone), measured, "2D distance to zone footprint")


def clearance(ctx, run):
    for requirement in ctx.config.clearances or ():
        zone_target = isinstance(requirement.against, ClearanceZone)
        selectors = (requirement.subject,) if zone_target else (requirement.subject, requirement.against)
        unavailable_target = any(
            (selector.object_id is not None and selector.object_id not in ctx.objects)
            or (selector.zone_id is not None and ("zone", selector.zone_id) not in ctx.by_ref)
            for selector in selectors
        )
        if unavailable_target:
            _unavailable_clearance_target(run, requirement)
            continue
        subject = _matching(ctx, requirement.subject)
        if zone_target:
            _zone_clearance(ctx, run, requirement, subject)
            continue
        against = _matching(ctx, requirement.against)
        metadata = {"requirement_id": requirement.id}
        if not subject or not against or len(subject | against) < 2:
            continue
        invalid = [object_id for object_id in sorted(subject | against) if ("object", object_id) not in ctx.bounds]
        for object_id in invalid:
            run.finding("clearance_geometry_unavailable", "Clearance geometry unavailable",
                        "A selected object has no measurable geometry.", _CLEARANCE_RESOLUTION, (("object", object_id),),
                        qualifier=requirement.id, metadata=metadata, incomplete=True)
        ids = sorted((subject | against).difference(invalid))
        for first, second in ctx.pairs(ids, margin=requirement.minimum_m):
            if not ((first in subject and second in against) or (second in subject and first in against)):
                continue
            refs = (("object", first), ("object", second))
            try:
                result = clearance_result(ctx.item(first), ctx.item(second), requirement.minimum_m)
                if not math.isfinite(result["distance_m"]):
                    raise GeometryError("The distance calculation exceeded the supported coordinate range.")
            except (GeometryError, OverflowError) as exc:
                run.finding("clearance_unavailable", "Clearance check unavailable", str(exc), _CLEARANCE_RESOLUTION, refs,
                            qualifier=requirement.id, metadata=metadata, incomplete=True)
                continue
            if not result["clearance_met"]:
                _clearance_measurement(run, requirement, refs, result["distance_m"], result["calculation_method"])


def consistency(ctx, run):
    resolution = "Reload both representations from the authorized scene synchronization endpoint."
    excluded = _GEOMETRY_CODES | _TRANSFORM_CODES | _HIERARCHY_CODES
    for issue in ctx.compiled["validation"]["issues"]:
        if issue["representation"] == "runtime" and issue["code"] in excluded:
            continue
        identity = issue["node"]
        refs = ((identity["type"], identity["id"]),) if identity else ()
        field, source = issue["field"], issue["representation"]
        run.finding(issue["code"], issue["code"].replace("_", " ").capitalize(), issue["message"], resolution, refs,
                    severity=issue["severity"], qualifier=f"{source}/{field}",
                    metadata={"field": field, "representation": source},
                    incomplete=issue["severity"] == "error" or issue["code"] == "plan_projection_unsupported")
    for source, freshness in ctx.compiled["freshness"].items():
        if freshness["status"] in {"stale", "ahead"}:
            run.finding("representation_" + freshness["status"], "Representation revision mismatch",
                        f"The supplied {source} revision differs from the canonical scene revision.", resolution,
                        qualifier=source, metadata={"representation": source, "freshness": freshness["status"]},
                        severity="warning" if freshness["status"] == "stale" else "error",
                        measurement={"measured": freshness["supplied_revision"], "expected": ctx.scene.current_revision,
                                     "unit": "revision"})
