from decimal import Decimal
import math
from uuid import UUID, uuid4

import pytest

from app.models.asset import Asset
from app.models.spatial import SpatialObject, SpatialScene, SpatialZone
from app.schemas.spatial_runtime import CompiledScene3D
from app.services.spatial_graph import GraphRead
from app.services.spatial_runtime import SceneCompilation
from app.spatial.geometry2d import Circle2, Polygon2, Rectangle2, Segment2
from app.spatial.math3d import IDENTITY, resolve_world_matrices, transform_matrix, transform_point
from app.spatial.runtime3d import CYLINDER_SEGMENTS, compile_geometry
from app.spatial.types import GeometryError, Transform3, Vec2, Vec3
from app.spatial.vectors import cross3, dot, subtract
from test_phase9_spatial import create_scene, make_project
from test_spatial_graph import SceneGraphClient, matrix_close, ref, transform


@pytest.fixture
def runtime_scene(client, db_session, identity_factory):
    owner = identity_factory()
    project = make_project(db_session, owner, "Runtime 3D project")
    client.headers["Authorization"] = "Bearer " + owner["token"]
    scene = create_scene(client, project.id, metadata={"source": "test workspace"})
    return SceneGraphClient(client, scene["id"]), owner, project


def runtime(graph):
    response = graph.client.get(graph.path + "/runtime-3d")
    assert response.status_code == 200, response.text
    return response.json()


def by_id(data):
    return {node["id"]: node for node in data["nodes"]}


def indexed_triangles(mesh):
    return [tuple(mesh.vertices[i] for i in mesh.indices[start:start + 3])
            for start in range(0, len(mesh.indices), 3)]


def test_empty_scene_is_versioned_deterministic_authorized_and_read_only(
    runtime_scene, db_session, identity_factory, unauthenticated_client,
):
    g, owner, project = runtime_scene
    before = g.client.get(g.path + "/revisions").json()
    first = g.client.get(g.path + "/runtime-3d")
    second = g.client.get(g.path + "/runtime-3d")
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "private, no-store"
    assert first.content == second.content
    data = first.json()
    assert data["project_id"] == str(project.id)
    assert data["schema_version"] == 1
    assert data["coordinates"]["system"] == "right_handed_z_up"
    assert data["coordinates"]["up_axis"] == "Z"
    assert data["coordinates"]["ground_plane"] == "XY"
    assert data["display_unit"] == "ft"
    assert data["meters_per_display_unit"] == 0.3048
    assert data["metadata"] == {"source": "test workspace"}
    assert data["bounds"]["minimum_m"] == [0, 0, 0]
    assert data["bounds"]["maximum_m"] == [30, 20, 8]
    assert not data["nodes"] and not data["roots"] and not data["layers"]
    assert data["validation"] == {"status": "valid", "issues": []}
    assert g.client.get(g.path + "/revisions").json() == before
    assert unauthenticated_client.get(g.path + "/runtime-3d").status_code == 401
    assert g.client.get(f"/api/v1/spatial/scenes/{uuid4()}/runtime-3d").status_code == 404

    member = identity_factory(role="member", organization=owner["organization"])
    g.client.headers["Authorization"] = "Bearer " + member["token"]
    assert g.client.get(g.path + "/runtime-3d").content == first.content
    outsider = identity_factory()
    g.client.headers["Authorization"] = "Bearer " + outsider["token"]
    assert g.client.get(g.path + "/runtime-3d").status_code == 404


def test_runtime_reuses_graph_order_visibility_and_distinct_transform_parents(runtime_scene, db_session):
    g, owner, project = runtime_scene
    zone_dims = {"width_m": 12, "depth_m": 6, "height_m": 1}
    hall = g.zone("Hall", transform=transform(20, 10, 1, 90), dimensions=zone_dims)
    subzone = g.zone("Stage zone", parent_zone_id=hall, transform=transform(2, 1, 0), dimensions=zone_dims)
    layer, outer, inner = (str(uuid4()) for _ in range(3))
    g.change(
        {"kind": "create_layer", "id": layer, "name": "Rigging", "visible": False},
        {"kind": "set_default_layer", "layer_id": layer},
        {"kind": "create_group", "id": outer, "group": {
            "name": "Outer", "zone_id": subzone, "transform": transform(10, 11, 2, 90, {"x": 2, "y": 3, "z": 1}),
        }},
        {"kind": "create_group", "id": inner, "group": {
            "name": "Inner", "parent_object_id": outer, "transform": transform(1, 2, 1),
        }},
    )
    first = g.object("Box", parent_object_id=inner, zone_id=subzone, layer_id=None, transform=transform(1, 0, 1, 30))
    second = g.object("Light", parent_object_id=inner, zone_id=subzone, transform=transform(0, 1, 1))
    own_hidden = g.object("Local hidden", layer_id=None, status="hidden")
    inherited = g.object("Hidden from parent", parent_object_id=own_hidden, layer_id=None)
    g.change({"kind": "reorder", "node": ref(second), "before": ref(first)},
             {"kind": "set_locked", "nodes": [ref(outer, "group")], "locked": True})

    asset = Asset(organization_id=owner["organization"].id, project_id=project.id,
                  original_filename="model.glb", display_name="Reference model",
                  storage_key=uuid4().hex, mime_type="model/gltf-binary", file_extension="glb", file_size=256)
    db_session.add(asset)
    db_session.commit()
    link = g.client.post(f"/api/v1/spatial/objects/{inherited}/assets", json={"asset_id": str(asset.id), "role": "model"})
    assert link.status_code == 201, link.text

    original_object = g.client.get(f"/api/v1/spatial/objects/{first}").json()
    first_response = g.client.get(g.path + "/runtime-3d")
    assert first_response.status_code == 200, first_response.text
    assert g.client.get(g.path + "/runtime-3d").content == first_response.content
    data, graph = first_response.json(), g.read()
    nodes = by_id(data)
    assert data["scene_revision"] == graph["scene_revision"]
    assert data["roots"] == graph["roots"] == [ref(hall, "zone"), ref(own_hidden)]
    assert data["layers"] == graph["layers"] and data["default_layer_id"] == layer
    assert [node["id"] for node in data["nodes"]] == [hall, subzone, outer, inner, second, first, own_hidden, inherited]
    assert nodes[subzone]["transform_parent"] == ref(hall, "zone")
    assert [row[3] for row in nodes[subzone]["world_matrix"][:3]] == pytest.approx([19, 12, 1])
    assert nodes[outer]["parent"] == ref(subzone, "zone")
    assert nodes[outer]["transform_parent"] is None
    assert nodes[outer]["geometry"] is None and not nodes[outer]["renderable"]
    assert nodes[first]["parent"] == nodes[first]["transform_parent"] == ref(inner, "group")
    # The first box is in the group's frame, even though its assigned zone has a different transform.
    assert [row[3] for row in nodes[first]["world_matrix"][:3]] == pytest.approx([4, 15, 4])
    assert nodes[first]["local_transform"]["position_m"] == [1, 0, 1]
    assert nodes[first]["local_transform"]["rotation_deg"] == [0, 0, 30]
    assert nodes[outer]["local_transform"]["scale"] == [2, 3, 1]
    matrix_close(nodes[first]["world_matrix"], g.geometry(first)["transform_matrix"])
    part2_bounds = g.geometry(first)["bounds_3d"]
    assert nodes[first]["geometry"]["world_bounds"]["minimum_m"] == pytest.approx(
        [part2_bounds["min_x_m"], part2_bounds["min_y_m"], part2_bounds["min_z_m"]])
    assert nodes[first]["renderable"] and nodes[first]["effective_locked"] and not nodes[first]["locked"]
    assert nodes[second]["visible"] and not nodes[second]["effective_visible"] and not nodes[second]["renderable"]
    assert nodes[second]["geometry"]
    assert not nodes[own_hidden]["visible"] and not nodes[own_hidden]["renderable"]
    assert nodes[inherited]["visible"] and not nodes[inherited]["effective_visible"] and nodes[inherited]["geometry"]
    assert nodes[inherited]["assets"] == [{"id": link.json()["id"], "asset_id": str(asset.id), "role": "model"}]
    assert g.client.get(f"/api/v1/spatial/objects/{first}").json() == original_object


def test_supported_canonical_geometry_and_invalid_data_share_one_response(runtime_scene):
    g, _, _ = runtime_scene
    footprint = [{"x_m": x, "y_m": y} for x, y in [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]]
    measured_zone = g.zone("Polygon venue", footprint=footprint,
                           dimensions={"width_m": 2, "depth_m": 2, "height_m": 3})
    plane = g.zone("Plane zone", footprint=footprint)
    unspecified = g.zone("Unmeasured zone")
    polygon = g.object("Polygon", properties={"geometry": {"type": "polygon", "vertices": footprint}})
    circle = g.object("Circle", properties={"geometry": {"type": "circle", "radius_m": 1}},
                      transform={"position": {"x": 5, "y": 5, "z": 3}, "rotation_deg": {"x": 90},
                                 "scale": {"x": 2, "y": 3, "z": 4}})
    line = g.object("Line", properties={"geometry_type": "line_segment"})
    point = g.object("Point", properties={"geometry_type": "point"}, transform=transform(9, 2, 4))
    unknown = g.object("Unsupported asset mesh", properties={"geometry_type": "mesh"})
    invalid_line = g.object("Invalid line", properties={"geometry": {"type": "line",
                                         "start": {"x_m": 0, "y_m": 0}, "end": {"x_m": 0, "y_m": 0}}})
    crossed = g.object("Invalid polygon", properties={"geometry": {"type": "polygon",
                             "vertices": [{"x_m": x, "y_m": y} for x, y in [(0, 0), (1, 1), (0, 1), (1, 0)]]}})
    data = runtime(g)
    nodes = by_id(data)
    expected = {measured_zone: "polygon_prism", plane: "polygon_surface", polygon: "polygon_prism",
                circle: "cylinder", line: "line_segment", point: "point"}
    for row, kind in expected.items():
        assert nodes[row]["geometry"]["kind"] == kind and nodes[row]["renderable"]
    assert len(nodes[plane]["geometry"]["indices"]) == 12
    assert len(nodes[circle]["geometry"]["vertices_m"]) == CYLINDER_SEGMENTS * 2
    assert nodes[circle]["geometry"]["world_bounds"]["dimensions_m"] == pytest.approx(
        {"width_m": 4, "depth_m": 4, "height_m": 6})
    assert nodes[line]["geometry"]["mode"] == "lines"
    assert nodes[line]["geometry"]["world_bounds"]["dimensions_m"]["height_m"] == 0
    assert nodes[point]["geometry"]["world_bounds"]["minimum_m"] == [9, 2, 4]
    assert nodes[point]["geometry"]["mode"] == "points"

    assert data["validation"]["status"] == "invalid"
    issues = {issue["node"]["id"]: issue["code"] for issue in data["validation"]["issues"]}
    assert issues == {unspecified: "unmeasured_zone", unknown: "unsupported_geometry",
                      crossed: "invalid_geometry", invalid_line: "invalid_geometry"}
    for row in issues:
        assert nodes[row]["geometry"] is None and not nodes[row]["renderable"]

    previous_revision = data["scene_revision"]
    changed_outline = [{"x_m": 0, "y_m": 0}, {"x_m": 3, "y_m": 0}, {"x_m": 0, "y_m": 3}]
    edited = g.client.patch(f"/api/v1/spatial/zones/{plane}", json={"footprint": changed_outline})
    assert edited.status_code == 200, edited.text
    latest = runtime(g)
    assert latest["scene_revision"] > previous_revision
    assert by_id(latest)[plane]["geometry"]["vertices_m"] == [[0, 0, 0], [3, 0, 0], [0, 3, 0]]


def test_compiler_diagnoses_bad_references_and_numbers_without_rewriting_them(runtime_scene, db_session):
    g, _, _ = runtime_scene
    bad = g.object("Bad transform")
    child = g.object("Child", parent_object_id=bad)
    no_parent = g.object("Orphan")
    bad_zone = g.zone("Partial dimensions")
    no_layer = g.object("Bad assignments")
    good = g.object("Valid")
    loop1, loop2 = g.object("Cycle 1"), g.object("Cycle 2")
    scene = db_session.get(SpatialScene, UUID(g.scene))
    # Simulate data an upstream importer could pass to the compiler. These changes
    # stay in memory because the live PostgreSQL schema rejects broken references.
    with db_session.no_autoflush:
        db_session.get(SpatialObject, UUID(bad)).scale_x = Decimal("0")
        db_session.get(SpatialObject, UUID(no_parent)).parent_object_id = uuid4()
        db_session.get(SpatialObject, UUID(no_layer)).zone_id = uuid4()
        db_session.get(SpatialObject, UUID(no_layer)).layer_id = uuid4()
        db_session.get(SpatialObject, UUID(loop1)).parent_object_id = UUID(loop2)
        db_session.get(SpatialObject, UUID(loop2)).parent_object_id = UUID(loop1)
        db_session.get(SpatialObject, UUID(good)).position_z_m = Decimal("NaN")
        db_session.get(SpatialZone, UUID(bad_zone)).width_m = Decimal("10")
        scene.coordinate_system = "unknown_z_up"
        scene.height_m = Decimal("Infinity")
        payload = SceneCompilation(GraphRead(db_session, scene)).compile()
        result = CompiledScene3D.model_validate(payload).model_dump(mode="json")
    assert result["validation"]["status"] == "invalid"
    found = {(issue["node"]["id"] if issue["node"] else None, issue["code"], issue["field"])
             for issue in result["validation"]["issues"]}
    assert (None, "invalid_coordinate_system", "coordinate_system") in found
    assert (None, "invalid_scene_dimensions", "dimensions") in found
    assert (bad, "invalid_transform", "transform") in found
    assert (good, "invalid_transform", "transform") in found
    assert (child, "parent_transform_unavailable", "parent_object_id") in found
    assert (no_parent, "missing_parent", "parent_object_id") in found
    assert (no_layer, "missing_reference", "zone_id") in found
    assert (no_layer, "missing_reference", "layer_id") in found
    assert (bad_zone, "invalid_dimensions", "dimensions") in found
    assert (loop1, "parent_cycle", "parent_object_id") in found
    assert (loop2, "parent_cycle", "parent_object_id") in found
    assert result["bounds"] is None and result["dimensions"] is None
    assert len(result["nodes"]) == 8
    assert all(node["world_matrix"] is None and not node["renderable"] for node in result["nodes"])
    db_session.rollback()
    assert runtime(g)["validation"]["status"] == "valid_with_warnings"


def test_measured_invalid_geometry_dimensions_return_finite_error_payload(runtime_scene, db_session):
    g, _, _ = runtime_scene
    oid = g.object("Imported")
    row = db_session.get(SpatialObject, UUID(oid))
    row.height_m = Decimal("NaN")
    row.appearance = {"test": float("inf")}
    response = g.client.get(g.path + "/runtime-3d")
    assert response.status_code == 200, response.text
    data = response.json()
    item = by_id(data)[oid]
    assert item["authored_dimensions"] is None and item["geometry"] is None
    assert item["appearance"] == {"test": None}
    assert {issue["code"] for issue in data["validation"]["issues"]} == {"invalid_dimensions", "non_finite_extension_data"}
    db_session.rollback()


def test_box_mesh_outward_winding_and_exact_bounds():
    world = transform_matrix(Transform3(Vec3(9, 0, 3), Vec3(0, 15, 90), Vec3(2, 1, 3)))
    mesh = compile_geometry(Rectangle2(Vec2(0, 0), 4, 6), 2, world)
    assert mesh.kind == "box"
    assert len(mesh.vertices) == 8 and len(mesh.indices) == 36
    for a, b, c in indexed_triangles(mesh):
        assert dot(cross3(subtract(b, a), subtract(c, a)), a) > 0
    assert mesh.local_bounds.dimensions == Vec3(4, 6, 2)
    mapped = [transform_point(world, vertex) for vertex in mesh.vertices]
    assert mesh.world_bounds.max_z == max(point.z for point in mapped)
    assert mesh.world_bounds.min_x == min(point.x for point in mapped)


def test_concave_mesh_caps_and_extrusion_are_wound_consistently():
    outline = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    polygon = Polygon2(tuple(Vec2(*p) for p in reversed(outline)))
    surface = compile_geometry(polygon, None, IDENTITY)
    solid = compile_geometry(polygon, 4, IDENTITY)
    assert surface == compile_geometry(polygon, None, IDENTITY)
    assert len(surface.indices) == 12 and len(solid.indices) == 60
    surface_normals = [cross3(subtract(b, a), subtract(c, a)).z for a, b, c in indexed_triangles(surface)]
    assert all(normal > 0 for normal in surface_normals)
    assert sum(surface_normals) / 2 == pytest.approx(3)
    assert solid.local_bounds.min_z == -2 and solid.local_bounds.max_z == 2
    for a, b, c in indexed_triangles(solid):
        if a.z == b.z == c.z:
            assert a.z * cross3(subtract(b, a), subtract(c, a)).z > 0


def test_affine_cylinder_bounds_enclose_mesh_and_capture_exact_smooth_extent():
    world = transform_matrix(Transform3(Vec3(10, 15, 3), Vec3(25, 35, 45), Vec3(2, .5, 3)))
    mesh = compile_geometry(Circle2(Vec2(0, 0), 2), 8, world)
    assert len(mesh.indices) == 3 * (4 * CYLINDER_SEGMENTS - 4)
    assert mesh.local_bounds.dimensions == Vec3(4, 4, 8)
    for i, (low, high) in enumerate(zip(
        (mesh.world_bounds.min_x, mesh.world_bounds.min_y, mesh.world_bounds.min_z),
        (mesh.world_bounds.max_x, mesh.world_bounds.max_y, mesh.world_bounds.max_z),
    )):
        w = world[i]
        extent = math.hypot(w[0] * 2, w[1] * 2) + abs(w[2]) * 4
        assert low == pytest.approx(w[3] - extent)
        assert high == pytest.approx(w[3] + extent)
        axis = ("x", "y", "z")[i]
        assert all(low - 1e-9 <= getattr(transform_point(world, v), axis) <= high + 1e-9 for v in mesh.vertices)


def test_world_resolver_continues_independent_branches_and_rejects_overflow():
    issues = []
    huge = transform_matrix(Transform3(scale=Vec3(1e200, 1e200, 1e200)))
    parents = {"missing": "absent", "child": "missing", "a": "b", "b": "a",
               "good": None, "huge": None, "overflow": "huge", "tiny": None, "underflow": "tiny"}
    local = {node: huge if node in {"huge", "overflow"} else IDENTITY for node in parents}
    tiny = transform_matrix(Transform3(scale=Vec3(1e-200, 1e-200, 1e-200)))
    local.update(tiny=tiny, underflow=tiny)
    result = resolve_world_matrices(local, parents, report=lambda node, code, message: issues.append((node, code)))
    assert set(result) == {"good", "huge", "tiny"}
    assert dict(issues) == {"missing": "missing_parent", "child": "parent_transform_unavailable",
                            "a": "parent_cycle", "b": "parent_cycle", "overflow": "invalid_world_transform",
                            "underflow": "invalid_world_transform"}
    with pytest.raises(GeometryError):
        resolve_world_matrices(local, parents)
    with pytest.raises(GeometryError):
        compile_geometry(Segment2(Vec2(0, 0), Vec2(0, 0)), 1, IDENTITY)
