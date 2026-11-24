import math
import uuid
from decimal import Decimal

import pytest

from app.models.project import Project
from app.spatial.alignment import align, distribute_equal_spacing
from app.spatial.geometry2d import (
    Circle2, Polygon2, Rectangle2, Segment2, contains, distance_between,
    geometry_area, geometry_bounds, geometry_center, geometry_perimeter,
    intersection, point_location, point_segment_distance, segment_intersections,
)
from app.spatial.geometry3d import (
    Parallelepiped3, clearance, distance_between as box_distance,
    intersects as box_intersection,
)
from app.spatial.math3d import (
    compose_transforms, inverse_matrix, transform_matrix, transform_point,
    transform_vector, world_to_local,
)
from app.spatial.operations import duplicate_geometry, measure, move, resize, rotate
from app.spatial.snapping import (
    Grid2, SnapAnchor, grid_index, nearest_grid_point, snap_point, visible_grid_points,
)
from app.spatial.tolerance import DEFAULT_TOLERANCE, Tolerance
from app.spatial.types import GeometryError, Transform3, Vec2, Vec3
from app.spatial.validation import (
    GeometryItem, broad_phase_pairs, clearance_result, detect_overlaps,
    evaluate_boundary, evaluate_constraint, scene_boundary,
)
from app.spatial_units import (
    convert_area, convert_coordinates, convert_dimensions, convert_distance,
    convert_volume, from_meters, to_meters,
)


def near(actual, expected, abs_tol=1e-8):
    assert actual == pytest.approx(expected, abs=abs_tol)


def test_unit_conversions_are_decimal_precise_and_dimension_aware():
    assert to_meters("12", "in") == Decimal("0.3048")
    assert from_meters("1", "ft") == Decimal("3.280839895013123359580052493")
    assert convert_distance("1", "ft", "in") == Decimal("12")
    assert convert_coordinates(["1", "2", "3"], "ft", "m") == tuple(map(Decimal, ("0.3048", "0.6096", "0.9144")))
    assert convert_dimensions({"width": "1", "depth": "2"}, "m", "cm") == {"width": Decimal("100"), "depth": Decimal("200")}
    assert convert_area("1", "m", "cm") == Decimal("10000")
    assert convert_volume("1", "m", "cm") == Decimal("1000000")
    with pytest.raises(ValueError):
        convert_distance(1, "yard", "m")
    with pytest.raises(ValueError):
        to_meters(float("nan"), "m")


def test_vector_operations_rotation_and_zero_validation():
    from app.spatial.vectors import (
        add, angle_between, cross2, cross3, divide, dot, length, normalize,
        perpendicular, rotate2, subtract,
    )
    assert add(Vec2(1, 2), Vec2(3, 4)) == Vec2(4, 6)
    assert subtract(Vec3(2, 2, 2), Vec3(1, 2, 3)) == Vec3(1, 0, -1)
    assert dot(Vec2(1, 2), Vec2(3, 4)) == 11
    assert cross2(Vec2(1, 0), Vec2(0, 1)) == 1
    assert cross3(Vec3(1, 0, 0), Vec3(0, 1, 0)) == Vec3(0, 0, 1)
    assert length(Vec3(3, 4, 0)) == 5
    normalized = normalize(Vec2(3, 4))
    near(normalized.x, 0.6)
    near(normalized.y, 0.8)
    assert divide(Vec2(2, 4), 2) == Vec2(1, 2)
    assert angle_between(Vec2(1, 0), Vec2(0, 1), degrees=True) == 90
    assert perpendicular(Vec2(1, 0)) == Vec2(0, 1)
    near(rotate2(Vec2(1, 0), 90).y, 1)
    with pytest.raises(GeometryError):
        divide(Vec2(1, 2), 0)
    with pytest.raises(GeometryError):
        normalize(Vec3(0, 0, 0))


def test_transform_composition_inversion_and_vector_semantics():
    parent = transform_matrix(Transform3(Vec3(10, 0, 0), Vec3(0, 0, 90), Vec3(1, 1, 1)))
    child = transform_matrix(Transform3(Vec3(2, 0, 0)))
    world = compose_transforms(parent, child)
    point = transform_point(world, Vec3(0, 0, 0))
    assert point.x == pytest.approx(10)
    assert point.y == pytest.approx(2)
    assert point.z == pytest.approx(0)
    assert world_to_local(point, world).x == pytest.approx(0)
    vector = transform_vector(parent, Vec3(1, 0, 0))
    assert vector.x == pytest.approx(0, abs=1e-12)
    assert vector.y == pytest.approx(1)
    matrix = transform_matrix(Transform3(Vec3(1, 2, 3), Vec3(15, 30, 45), Vec3(2, 0.5, 3)))
    inverse = inverse_matrix(matrix)
    sample = Vec3(1.25, -2.5, 0.75)
    restored = transform_point(inverse, transform_point(matrix, sample))
    assert restored.x == pytest.approx(sample.x)
    assert restored.y == pytest.approx(sample.y)
    assert restored.z == pytest.approx(sample.z)
    with pytest.raises(GeometryError):
        inverse_matrix(((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 1)))


def test_rectangles_polygons_and_bounds_include_rotation_and_measurements():
    rotated = Rectangle2(Vec2(2, 3), 2, 1, 45)
    bounds = geometry_bounds(rotated)
    near(bounds.width, 3 / math.sqrt(2))
    near(bounds.height, 3 / math.sqrt(2))
    assert bounds.center == Vec2(2, 3)
    assert rotated.width * rotated.height == 2
    shape = Polygon2(tuple(Vec2(*point) for point in [(0, 0), (4, 0), (4, 2), (0, 2)]))
    assert geometry_area(shape) == 8
    assert geometry_perimeter(shape) == 12
    assert geometry_center(shape) == Vec2(2, 1)
    assert point_location(Vec2(2, 1), shape) == "inside"
    assert point_location(Vec2(4, 1), shape) == "boundary"
    assert contains(shape, Vec2(4, 1)) is True
    assert measure(rotated)["area_m2"] == 2
    assert measure(rotated)["bounding_radius_m"] == pytest.approx(math.sqrt(1.25))
    with pytest.raises(GeometryError):
        Rectangle2(Vec2(0, 0), 0, 1)
    with pytest.raises(GeometryError):
        Polygon2((Vec2(0, 0), Vec2(1, 1), Vec2(0, 0)))
    with pytest.raises(GeometryError):
        Polygon2(tuple(Vec2(*point) for point in [(0, 0), (2, 2), (0, 2), (2, 0)]))


def test_intersections_touching_containment_circle_and_concave_polygon_area():
    left = Rectangle2(Vec2(0, 0), 2, 2)
    overlap = Rectangle2(Vec2(1, 0), 2, 2)
    result = intersection(left, overlap)
    assert result.intersects and result.classification == "partial_overlap"
    assert result.overlap_area_m2 == pytest.approx(2)
    touching = intersection(left, Rectangle2(Vec2(2, 0), 2, 2))
    assert touching.intersects and touching.classification == "touching"
    assert touching.overlap_area_m2 == pytest.approx(0)
    assert intersection(Rectangle2(Vec2(0, 0), 4, 4), left).classification == "fully_contained"
    rotated = intersection(Rectangle2(Vec2(0, 0), 2, 1, 45), Rectangle2(Vec2(1, 0), 2, 1, -45))
    assert rotated.intersects and rotated.overlap_area_m2 > 0
    c1, c2 = Circle2(Vec2(0, 0), 1), Circle2(Vec2(1, 0), 1)
    circles = intersection(c1, c2)
    assert circles.classification == "partial_overlap"
    near(circles.overlap_area_m2, 2 * math.pi / 3 - math.sqrt(3) / 2)
    tangent = intersection(c1, Circle2(Vec2(2, 0), 1))
    assert tangent.classification == "touching"
    half_circle = intersection(c1, Rectangle2(Vec2(1, 0), 2, 2))
    near(half_circle.overlap_area_m2, math.pi / 2)
    concave = Polygon2(tuple(Vec2(*point) for point in [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]))
    l_overlap = intersection(concave, Rectangle2(Vec2(1, 1), 1, 1))
    near(l_overlap.overlap_area_m2, 0.75)
    near_contact = intersection(Rectangle2(Vec2(0, 0), 1, 1), Rectangle2(Vec2(1.0000005, 0), 1, 1))
    assert near_contact.intersects and near_contact.classification == "touching"


def test_lines_distances_clearance_and_boundary_classification():
    horizontal = Segment2(Vec2(0, 0), Vec2(2, 0))
    vertical = Segment2(Vec2(1, -1), Vec2(1, 1))
    assert len(segment_intersections(horizontal, vertical)) == 1
    assert not intersection(Segment2(Vec2(0, 0), Vec2(1, 0)), Segment2(Vec2(0, 1), Vec2(1, 1))).intersects
    collinear = segment_intersections(horizontal, Segment2(Vec2(1, 0), Vec2(3, 0)))
    assert collinear == (Vec2(1, 0), Vec2(2, 0))
    assert point_segment_distance(Vec2(1, 2), horizontal) == 2
    assert distance_between(Rectangle2(Vec2(0, 0), 2, 2), Rectangle2(Vec2(4, 0), 2, 2)) == 2
    circle_gap = distance_between(Circle2(Vec2(0, 0), 1), Circle2(Vec2(3, 0), 1))
    assert circle_gap == 1
    scene = scene_boundary(10, 10, 5)
    inside = GeometryItem("inside", "box", Rectangle2(Vec2(5, 5), 2, 2),
                          Parallelepiped3.from_center_dimensions(Vec3(5, 5, 1), Vec3(2, 2, 2)))
    partial = GeometryItem("partial", "box", Rectangle2(Vec2(0.5, 5), 2, 2),
                           Parallelepiped3.from_center_dimensions(Vec3(0.5, 5, 1), Vec3(2, 2, 2)))
    outside = GeometryItem("outside", "box", Rectangle2(Vec2(-5, 5), 2, 2),
                           Parallelepiped3.from_center_dimensions(Vec3(-5, 5, 1), Vec3(2, 2, 2)))
    assert evaluate_boundary(inside, scene).status == "inside"
    assert evaluate_boundary(partial, scene).status == "partial"
    assert evaluate_boundary(outside, scene).status == "outside"
    assert clearance_result(inside, partial, 3)["clearance_met"] is False
    assert clearance_result(inside, partial, 0)["clearance_met"] is True


def test_three_dimensional_sat_bounds_and_distance_cover_rotation_and_elevation():
    first = Parallelepiped3.from_center_dimensions(Vec3(0, 0, 0), Vec3(2, 2, 2))
    contained = Parallelepiped3.from_center_dimensions(Vec3(0, 0, 0), Vec3(1, 1, 1))
    assert box_intersection(first, contained).classification == "fully_contained"
    touching = Parallelepiped3.from_center_dimensions(Vec3(2, 0, 0), Vec3(2, 2, 2))
    assert box_intersection(first, touching).classification == "touching"
    separated = Parallelepiped3.from_center_dimensions(Vec3(3, 0, 0), Vec3(2, 2, 2))
    near(box_distance(first, separated), 1)
    rotated_matrix = transform_matrix(Transform3(rotation_deg=Vec3(0, 0, 45)))
    rotated = Parallelepiped3.from_center_dimensions(Vec3(2, 0, 0), Vec3(2, 1, 1), rotated_matrix)
    result = box_intersection(first, rotated)
    assert result.calculation_method == "15-axis separating axis test"
    assert result.intersects
    result = clearance(first, separated, 1)
    assert result["clearance_met"] is True
    high = Parallelepiped3.from_center_dimensions(Vec3(0, 0, 3), Vec3(2, 2, 2))
    assert not box_intersection(first, high).intersects


def test_geometry_operations_grid_snapping_alignment_and_sweep_broadphase():
    rect = Rectangle2(Vec2(0, 0), 2, 2)
    assert move(rect, Vec2(2, 1)).center == Vec2(2, 1)
    assert rotate(rect, 90).rotation_deg == 90
    assert resize(rect, 2).width == 4
    assert duplicate_geometry(rect, Vec2(3, 0)).center == Vec2(3, 0)
    assert grid_index(0.125, 0.25) == 1
    assert grid_index(-0.125, 0.25) == -1
    assert nearest_grid_point(Vec2(-0.125, 0.125), Grid2(0.25)) == Vec2(-0.25, 0.25)
    points = visible_grid_points(geometry_bounds(Rectangle2(Vec2(0, 0), 1, 1)), Grid2(0.5))
    assert len(points) == 9
    with pytest.raises(GeometryError):
        visible_grid_points(geometry_bounds(Rectangle2(Vec2(0, 0), 100, 100)), Grid2(0.001), max_points=10)
    anchors = [SnapAnchor("b", "center", Vec2(2, 2)), SnapAnchor("a", "center", Vec2(2, 2))]
    snapped = snap_point(Vec2(2.01, 2), anchors=anchors, anchor_tolerance_m=0.02)
    assert snapped.snapped and snapped.target_id == "a"
    items = {"a": Rectangle2(Vec2(0, 0), 2, 2), "b": Rectangle2(Vec2(4, 1), 2, 2)}
    left = align(items, "left")
    assert left["a"].x == left["b"].x
    distributed = distribute_equal_spacing({
        "a": Rectangle2(Vec2(0, 0), 1, 1),
        "b": Rectangle2(Vec2(3, 0), 2, 1),
        "c": Rectangle2(Vec2(8, 0), 1, 1),
    })
    assert distributed["a"].x < distributed["b"].x < distributed["c"].x
    items3d = [
        GeometryItem("a", "box", Rectangle2(Vec2(0, 0), 2, 2), Parallelepiped3.from_center_dimensions(Vec3(0,0,0),Vec3(2,2,2))),
        GeometryItem("b", "box", Rectangle2(Vec2(1, 0), 2, 2), Parallelepiped3.from_center_dimensions(Vec3(1,0,0),Vec3(2,2,2))),
        GeometryItem("c", "box", Rectangle2(Vec2(20, 0), 2, 2), Parallelepiped3.from_center_dimensions(Vec3(20,0,0),Vec3(2,2,2))),
    ]
    assert broad_phase_pairs(items3d) == ((0, 1),)
    overlaps = detect_overlaps(items3d)
    assert len(overlaps) == 1 and overlaps[0]["classification"] == "partial_overlap"


def test_constraint_results_are_structured_and_unsupported_is_unknown():
    boundary = scene_boundary(10, 10, 5)
    subject = GeometryItem("a", "box", Rectangle2(Vec2(2, 2), 1, 1),
                          Parallelepiped3.from_center_dimensions(Vec3(2, 2, 1), Vec3(1, 1, 1)))
    related = GeometryItem("b", "box", Rectangle2(Vec2(5, 2), 1, 1),
                           Parallelepiped3.from_center_dimensions(Vec3(5, 2, 1), Vec3(1, 1, 1)))
    objects = {"a": subject, "b": related}
    result = evaluate_constraint({"id": "inside", "type": "object_inside_scene", "subject_object_id": "a"},
                                 objects=objects, scene_boundary_geometry=boundary)
    assert result["status"] == "pass"
    distance_result = evaluate_constraint({"id": "clearance", "type": "minimum_distance", "subject_object_id": "a", "related_object_id": "b", "minimum_distance_m": 2}, objects=objects)
    assert distance_result["status"] == "pass"
    unknown = evaluate_constraint({"type": "fire_code", "subject_object_id": "a"}, objects=objects)
    assert unknown["status"] == "unknown"
    assert unknown["calculated_value"] is None


def make_project(db_session, identity):
    project = Project(organization_id=identity["organization"].id,
                      workspace_id=identity["workspace"].id,
                      name="Geometry integration project", status="draft")
    db_session.add(project)
    db_session.commit()
    return project


def make_scene(client, project_id):
    response = client.post(f"/api/v1/spatial/projects/{project_id}/scenes", json={
        "name": "Geometry scene", "dimensions": {"width_m": 20, "depth_m": 20, "height_m": 8},
    })
    assert response.status_code == 201, response.text
    return response.json()


def make_object(client, scene_id, name, transform, dimensions=None, **kwargs):
    response = client.post(f"/api/v1/spatial/scenes/{scene_id}/objects", json={
        "name": name, "object_type": "furniture", "category": "furniture",
        "transform": transform,
        "dimensions": dimensions or {"width_m": 2, "depth_m": 2, "height_m": 1},
        **kwargs,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_geometry_api_uses_persisted_objects_world_transforms_and_constraints(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    scene = make_scene(client, project.id)
    parent = make_object(client, scene["id"], "Parent", {
        "position": {"x": 5, "y": 5, "z": 1},
        "rotation_deg": {"x": 0, "y": 0, "z": 90},
    })
    child = make_object(client, scene["id"], "Child", {
        "position": {"x": 2, "y": 0, "z": 0},
    }, {"width_m": 1, "depth_m": 1, "height_m": 1}, parent_object_id=parent["id"])
    geometry = client.get(f"/api/v1/spatial/scenes/{scene['id']}/objects/{child['id']}/geometry")
    assert geometry.status_code == 200, geometry.text
    assert geometry.json()["center"] == {"x_m": pytest.approx(5), "y_m": pytest.approx(7)}
    assert geometry.json()["scene_revision"] == 3

    second = make_object(client, scene["id"], "Nearby", {
        "position": {"x": 6.5, "y": 5, "z": 1},
    })
    validation = client.post(f"/api/v1/spatial/scenes/{scene['id']}/validate", json={
        "constraints": [
            {"id": "inside", "type": "object_inside_scene", "subject_object_id": child["id"]},
            {"id": "distance", "type": "minimum_distance", "subject_object_id": child["id"], "related_object_id": second["id"], "minimum_distance_m": 1},
            {"id": "unknown", "type": "unimplemented_rule", "subject_object_id": child["id"]},
        ],
    })
    assert validation.status_code == 200, validation.text
    result = validation.json()
    assert result["constraints"][0]["status"] == "pass"
    assert result["constraints"][1]["status"] == "fail"
    assert result["constraints"][2]["status"] == "unknown"
    assert any(item["classification"] == "partial_overlap" for item in result["overlaps"])
    assert result["status"] == "fail"

    snap = client.post(f"/api/v1/spatial/scenes/{scene['id']}/snap", json={
        "position": {"x_m": 0.12, "y_m": 0.62}, "grid_spacing_m": 0.25,
    })
    assert snap.status_code == 200, snap.text
    assert snap.json()["point"] == {"x_m": pytest.approx(0), "y_m": pytest.approx(0.5)}
    align_result = client.post(f"/api/v1/spatial/scenes/{scene['id']}/align", json={
        "object_ids": [parent["id"], second["id"]], "mode": "center_x",
    })
    assert align_result.status_code == 200, align_result.text
    assert align_result.json()["persisted"] is False
    unchanged = client.get(f"/api/v1/spatial/objects/{second['id']}").json()
    assert Decimal(unchanged["transform"]["position"]["x"]) == Decimal("6.500000")
    clearance_response = client.post(f"/api/v1/spatial/scenes/{scene['id']}/clearance", json={
        "first_object_id": child["id"], "second_object_id": second["id"], "requested_clearance_m": 0.25,
    })
    assert clearance_response.status_code == 200, clearance_response.text
    assert clearance_response.json()["clearance_met"] is True


def test_geometry_api_marks_invalid_shapes_and_unmeasured_zones_honestly(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    scene = make_scene(client, project.id)
    malformed = make_object(client, scene["id"], "Malformed circle", {
        "position": {"x": 5, "y": 5, "z": 1},
    }, properties={"geometry": {"type": "circle", "radius_m": -1}})
    response = client.get(f"/api/v1/spatial/scenes/{scene['id']}/objects/{malformed['id']}/geometry")
    assert response.status_code == 422
    report = client.post(f"/api/v1/spatial/scenes/{scene['id']}/validate", json={})
    assert report.status_code == 200
    assert report.json()["invalid_geometries"][0]["status"] == "invalid"
    assert report.json()["status"] == "fail"

    missing_dimensions = client.post(f"/api/v1/spatial/scenes/{scene['id']}/objects", json={"name": "No dimensions"})
    assert missing_dimensions.status_code == 422


def test_geometry_api_boundary_results_and_tenant_protection(client, db_session, identity_factory):
    owner = identity_factory()
    outsider = identity_factory()
    client.headers["Authorization"] = "Bearer " + owner["token"]
    project = make_project(db_session, owner)
    scene = make_scene(client, project.id)
    obj = make_object(client, scene["id"], "At boundary", {"position": {"x": 0.5, "y": 5, "z": 1}})
    report = client.post(f"/api/v1/spatial/scenes/{scene['id']}/validate", json={})
    assert report.status_code == 200
    assert report.json()["boundary_checks"][0]["status"] == "partial"
    assert report.json()["status"] == "fail"
    client.headers["Authorization"] = "Bearer " + outsider["token"]
    assert client.get(f"/api/v1/spatial/scenes/{scene['id']}/objects/{obj['id']}/geometry").status_code == 404
    assert client.post(f"/api/v1/spatial/scenes/{scene['id']}/validate", json={}).status_code == 404
