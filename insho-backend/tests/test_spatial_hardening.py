"""Cross-part workflow and regressions discovered during the Phase 9 completion audit."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.models.asset import Asset
from app.models.event import Event
from app.models.object_library import ObjectDefinition
from app.models.spatial import SpatialObject
from app.spatial.alignment import align, distribute_equal_spacing
from app.spatial.geometry2d import Polygon2, Segment2, geometry_area, geometry_bounds, geometry_center, intersection, segment_intersections
from app.spatial.types import Vec2
from test_object_library import library
from test_phase9_spatial import create_scene
from test_spatial_design_integration import Designer, object_action, zone_action
from test_spatial_graph import SceneGraphClient, graph_client, ref, transform


DIMS = {"width_m": 2, "depth_m": 2, "height_m": 1}


def test_polygon_area_center_and_overlap_preserve_precision_far_from_origin():
    origin = 99_000_000
    def shape(x, y):
        return Polygon2(tuple(Vec2(x + dx, y + dy) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))))

    left, right = shape(origin, origin), shape(origin + 0.5, origin + 0.5)
    assert geometry_area(left) == pytest.approx(1)
    assert geometry_center(left) == Vec2(origin + 0.5, origin + 0.5)
    result = intersection(left, right)
    assert result.classification == "partial_overlap" and result.overlap_area_m2 == pytest.approx(0.25)


def test_segment_endpoint_tolerance_is_in_meters():
    long = Segment2(Vec2(0, 0), Vec2(1000, 0))
    outside = Segment2(Vec2(1000.0005, -1), Vec2(1000.0005, 1))
    touching = Segment2(Vec2(1000.0000001, -1), Vec2(1000.0000001, 1))
    assert segment_intersections(long, outside) == ()
    assert segment_intersections(long, touching) == (Vec2(1000, 0),)


@pytest.mark.parametrize("mode,axis,side", [
    ("left", "x", "min"), ("right", "x", "max"), ("bottom", "y", "min"), ("top", "y", "max"),
])
def test_asymmetric_footprints_align_using_the_selected_edges(mode, axis, side):
    outlines = {
        "wedge": Polygon2((Vec2(2, 1), Vec2(6, 1), Vec2(2, 4))),
        "opposite": Polygon2((Vec2(12, 11), Vec2(8, 13), Vec2(12, 13))),
    }
    centers = {key: geometry_center(outline) for key, outline in outlines.items()}
    def get_edge(outline):
        return getattr(geometry_bounds(outline), f"{side}_{axis}")
    positions = align(outlines, mode)
    target = (min if side == "min" else max)(map(get_edge, outlines.values()))
    opposite_axis = "y" if axis == "x" else "x"
    for key, point in positions.items():
        translated_edge = get_edge(outlines[key]) + getattr(point, axis) - getattr(centers[key], axis)
        assert translated_edge == pytest.approx(target)
        assert getattr(point, opposite_axis) == getattr(centers[key], opposite_axis)


@pytest.mark.parametrize("axis", ["x", "y"])
def test_asymmetric_spacing_preserves_endpoint_positions_and_equal_edge_gaps(axis):
    offsets = (2, 9, 23)
    outlines = {str(i): Polygon2((Vec2(offset, offset), Vec2(offset + 4, offset), Vec2(offset, offset + 2)))
                for i, offset in enumerate(offsets)}
    positions = distribute_equal_spacing(outlines, axis)
    assert positions["0"] == geometry_center(outlines["0"])
    last = geometry_center(outlines["2"])
    assert (positions["2"].x, positions["2"].y) == pytest.approx((last.x, last.y))
    edges = []
    for key, point in positions.items():
        delta = getattr(point, axis) - getattr(geometry_center(outlines[key]), axis)
        bounds = geometry_bounds(outlines[key])
        edges.append((getattr(bounds, "min_" + axis) + delta, getattr(bounds, "max_" + axis) + delta))
    assert edges[1][0] - edges[0][1] == pytest.approx(edges[2][0] - edges[1][1])


def test_invalid_runtime_shape_never_publishes_a_drawable_plan_node(graph_client):
    g, _, _ = graph_client
    good = g.object("Measurable", transform=transform(8, 8), dimensions=DIMS)
    bad = g.object("Degenerate segment", transform=transform(12, 8), dimensions=DIMS, properties={"geometry": {
        "type": "line", "start": {"x_m": 0, "y_m": 0}, "end": {"x_m": 0, "y_m": 0},
    }})
    payload = g.client.get(g.path + "/synchronization")
    assert payload.status_code == 200, payload.text
    paired = payload.json()
    assert paired["validation"]["status"] == "invalid"
    assert {issue["node"]["id"] for issue in paired["validation"]["issues"] if issue["code"] == "invalid_geometry"} == {bad}
    for key in ("plan", "runtime"):
        assert {node["id"] for node in paired[key]["nodes"] if node["renderable"]} == {good}
    broken_plan = next(node for node in paired["plan"]["nodes"] if node["id"] == bad)
    assert broken_plan["footprint"] is None


def test_legacy_geometry_can_read_valid_branches_of_a_scene_with_bad_transform(graph_client, db_session):
    g, _, _ = graph_client
    good = g.object("Good", transform=transform(8, 8), dimensions=DIMS)
    invalid = g.object("Invalid parent", transform=transform(14, 8), dimensions=DIMS)
    dependent = g.object("Dependent", parent_object_id=invalid, transform=transform(2, 0, 0), dimensions=DIMS)
    row = db_session.get(SpatialObject, UUID(invalid))
    row.position_x_m = Decimal("NaN")
    db_session.commit()

    assert g.geometry(good)["center"]["x_m"] == 8
    geometry = g.client.get(g.path + f"/objects/{dependent}/geometry")
    assert geometry.status_code == 422 and "transform" in geometry.json()["detail"]
    result = g.client.post(g.path + "/validate", json={})
    assert result.status_code == 200, result.text
    report = result.json()
    assert report["status"] == "fail"
    errors = {item["object_id"]: item["reason"] for item in report["invalid_geometries"]}
    assert set(errors) == {invalid, dependent} and "parent" in errors[dependent].lower()
    assert next(item for item in report["boundary_checks"] if item["object_id"] == good)["status"] == "inside"
    snapped = g.client.post(g.path + "/snap", json={"position": {"x_m": 8.01, "y_m": 8}, "anchor_tolerance_m": 0.05})
    assert snapped.status_code == 200 and snapped.json()["target_id"] == good


def test_storage_invalid_inputs_are_rejected_without_revision_changes(graph_client):
    g, _, project = graph_client
    revision = g.read()["scene_revision"]
    response = g.client.patch(g.path, content='{"metadata":{"measurement":1e400}}',
                              headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and "finite JSON" in response.text
    response = g.client.patch(g.path, json={"metadata": {"designation": "invalid\x00value"}})
    assert response.status_code == 422
    response = g.client.post(g.path + "/objects", json={"name": "   ", "dimensions": DIMS})
    assert response.status_code == 422
    response = g.client.post(f"/api/v1/spatial/projects/{project.id}/scenes", json={"name": "   ", "dimensions": DIMS})
    assert response.status_code == 422
    g.change({"kind": "create_layer", "name": "Details", "metadata": {"key\x00": True}}, expected=422,
             base_revision=revision)
    assert g.read()["scene_revision"] == revision


def test_extreme_finite_grid_spacing_fails_as_validation_error(graph_client):
    g, _, _ = graph_client
    result = g.client.post(g.path + "/snap", json={
        "position": {"x_m": 30, "y_m": 20}, "grid_spacing_m": 1e-310, "use_anchors": False,
    })
    assert result.status_code == 422 and "Grid index" in result.json()["detail"]


def test_invalid_catalog_transform_has_structured_preview_and_read_errors(library, db_session):
    lib, _, _ = library
    definition = lib.create()
    row = db_session.get(ObjectDefinition, UUID(definition["id"]))
    row.default_transform = {"scale": {"z": 0}}
    db_session.commit()
    before = lib.g.read()["scene_revision"]
    detail = lib.get(definition["id"], expected=422)["detail"]
    placement = lib.place(definition, expected=422)["detail"]
    assert placement == detail
    assert detail["issues"][0]["code"] == "invalid_spatial_defaults"
    assert lib.g.read()["scene_revision"] == before


def test_deep_hierarchy_has_a_flat_graph_fallback(graph_client, db_session):
    g, _, _ = graph_client
    ids = [uuid4() for _ in range(260)]
    db_session.add_all([
        SpatialObject(id=ident, scene_id=UUID(g.scene), name=f"Group {index}", category="group", object_type="group",
                      node_kind="group", order_index=index, parent_object_id=ids[index - 1] if index else None)
        for index, ident in enumerate(ids)
    ])
    db_session.commit()
    nested = g.client.get(g.path + "/hierarchy")
    assert nested.status_code == 409 and nested.json()["detail"]["code"] == "nested_hierarchy_unavailable"
    graph = g.read()
    assert len(graph["nodes"]) == len(ids) and graph["roots"] == [ref(ids[0], "group")]
    assert graph["nodes"][-1]["parent"] == ref(ids[-2], "group")


def test_event_to_catalog_review_to_synchronized_scene(graph_client, db_session, monkeypatch):
    g, owner, project = graph_client
    http = g.client
    event = Event(project_id=project.id, organization_id=owner["organization"].id, name="Garden reception")
    db_session.add(event)
    db_session.commit()
    assert http.get(f"/api/v1/projects/{project.id}").status_code == 200
    assert http.get(f"/api/v1/events/{event.id}").status_code == 200
    scene = create_scene(http, project.id, event_id=str(event.id), name="Reception layout")
    g = SceneGraphClient(http, scene["id"])
    asset = Asset(organization_id=owner["organization"].id, project_id=project.id, event_id=event.id,
                  original_filename="entry.jpg", display_name="Reference", mime_type="image/jpeg",
                  file_extension="jpg", storage_key=uuid4().hex, file_size=512)
    reusable = Asset(organization_id=owner["organization"].id, project_id=project.id,
                     original_filename="catalog.jpg", display_name="Catalog reference", mime_type="image/jpeg",
                     file_extension="jpg", storage_key=uuid4().hex, file_size=512)
    db_session.add_all([asset, reusable])
    db_session.commit()
    d = Designer(g, owner, project, monkeypatch)
    d.generate("plan", expected=404, reference_asset_ids=[str(asset.id)])
    d.generate("plan", event_id=str(event.id), reference_asset_ids=[str(asset.id)])
    d.generate("elements", event_id=str(event.id))
    definition = d.lib.create(name="Seasonal entry floral", category="floral", project_id=str(project.id), dimensions=DIMS,
                              assets=[{"asset_id": str(reusable.id), "role": "preview"}])
    assert d.lib.read(q="seasonal", project_id=str(project.id))["items"][0]["id"] == definition["id"]
    entry = g.zone("Entry", dimensions={"width_m": 10, "depth_m": 10, "height_m": 2}, transform=transform(8, 8))
    designer_object = g.object("Welcome desk", dimensions=DIMS, zone_id=entry, transform=transform(4, 8))
    layer = d.g.change({"kind": "create_layer", "name": "Arrival"})["results"][0]["ids"][0]
    d.g.change({"kind": "assign_layer", "nodes": [ref(designer_object)], "layer_id": layer})
    existing = g.read()
    plan, base = d.rec("plan")
    zone_review = d.act(plan, "validate", **base, action=zone_action(20, 12))
    rec, snap = d.rec("elements")
    assert rec["candidates"][0]["definition"]["id"] == definition["id"]
    preview = d.act(rec, "validate", **snap, action={**object_action(definition, entry), "layer_id": layer})
    assert preview["status"] == "validated"
    assert g.read() == existing
    receipt = d.act(rec, "apply", review_id=preview["review_id"])
    applied = receipt["entity_id"]
    stale = d.act(plan, "apply", expected=409, review_id=zone_review["review_id"])
    assert stale["detail"]["code"] == "scene_revision_conflict"

    sync = http.get(g.path + "/synchronization", params={"plan_revision": snap["base_revision"],
                                                         "runtime_revision": snap["base_revision"]}).json()
    assert sync["scene_revision"] == receipt["scene_revision"] and sync["validation"]["status"] == "synchronized"
    assert {item["status"] for item in sync["freshness"].values()} == {"stale"}
    for key in ("plan", "runtime"):
        node = next(node for node in sync[key]["nodes"] if node["id"] == applied)
        assert node["layer_id"] == layer and node["zone_id"] == entry
        assert node["library_definition_id"] == definition["id"] and node["renderable"]
    links = http.get(f"/api/v1/spatial/objects/{applied}/assets").json()
    assert links[0]["asset_id"] == str(reusable.id)
    alignment = http.post(g.path + "/align", json={"object_ids": [applied, designer_object], "mode": "center_y"}).json()
    assert alignment["persisted"] is False and all(position["y_m"] == pytest.approx(8) for position in alignment["positions"])
    validated = http.post(g.path + "/validation-report", json={"plan_revision": receipt["scene_revision"],
                                                               "runtime_revision": receipt["scene_revision"]}).json()
    assert validated["status"] == "valid" and validated["scene_revision"] == receipt["scene_revision"]
    assert g.read()["scene_revision"] == receipt["scene_revision"]
