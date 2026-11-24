from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models.spatial import SpatialObject, SpatialScene
from app.schemas.spatial_sync import SynchronizedScene
from app.services import spatial_sync
from app.services.spatial_graph import GraphRead
from app.services.spatial_runtime import SceneCompilation
from test_phase9_spatial import create_scene, make_project
from test_spatial_graph import SceneGraphClient, ref, transform


@pytest.fixture
def sync_scene(client, db_session, identity_factory):
    owner = identity_factory()
    project = make_project(db_session, owner, "Synchronized project")
    client.headers["Authorization"] = "Bearer " + owner["token"]
    scene = create_scene(client, project.id)
    return SceneGraphClient(client, scene["id"]), owner, project


def request_pair(g, **params):
    return g.client.get(g.path + "/synchronization", params=params)


def pair(g, **params):
    response = request_pair(g, **params)
    assert response.status_code == 200, response.text
    return response.json()


def node(data, view, entity_id):
    return next(entity for entity in data[view]["nodes"] if entity["id"] == entity_id)


def editor_change(g, revision, *ops):
    return g.client.put(g.path + f"/editor/changes/{uuid4()}", json={
        "base_revision": revision,
        "operations": [{"kind": kind, "id": oid, "data": data} for kind, oid, data in ops],
    })


def assert_xy_bounds_match(plan, runtime):
    bounds = plan["footprint"]["bounds"]
    world = runtime["geometry"]["world_bounds"]
    assert [bounds["min_x_m"], bounds["min_y_m"]] == pytest.approx(world["minimum_m"][:2])
    assert [bounds["max_x_m"], bounds["max_y_m"]] == pytest.approx(world["maximum_m"][:2])


def test_pair_is_authorized_revision_consistent_and_deterministic(sync_scene, identity_factory, unauthenticated_client):
    g, owner, project = sync_scene
    revisions = g.client.get(g.path + "/revisions").json()
    first, second = request_pair(g), request_pair(g)
    assert first.status_code == 200, first.text
    assert first.content == second.content
    assert first.headers["cache-control"] == "private, no-store"
    assert g.client.get(g.path + "/editor").headers["cache-control"] == "private, no-store"
    data = first.json()
    assert data["runtime"]["project_id"] == str(project.id)
    assert data["scene_id"] == g.scene
    assert data["plan"]["scene_revision"] == data["runtime"]["scene_revision"] == data["scene_revision"] == 1
    assert data["plan"]["bounds"] == {"min_x_m": 0, "min_y_m": 0, "max_x_m": 30, "max_y_m": 20,
                                      "width_m": 30, "depth_m": 20}
    assert data["validation"] == {"status": "synchronized", "issues": []}
    assert data["freshness"]["plan"]["status"] == data["freshness"]["runtime"]["status"] == "not_supplied"
    assert data["plan"]["length_unit"] == data["runtime"]["coordinates"]["length_unit"] == "m"
    assert data["plan"]["coordinate_system"] == data["runtime"]["coordinates"]["system"] == "right_handed_z_up"
    assert data["plan"]["display_unit"] == data["runtime"]["display_unit"] == "ft"
    assert data["plan"]["meters_per_display_unit"] == data["runtime"]["meters_per_display_unit"] == 0.3048
    assert g.client.get(g.path + "/revisions").json() == revisions

    assert request_pair(g, plan_revision=0).status_code == 422
    assert unauthenticated_client.get(g.path + "/synchronization").status_code == 401
    assert g.client.get(f"/api/v1/spatial/scenes/{uuid4()}/synchronization").status_code == 404
    member = identity_factory(role="member", organization=owner["organization"])
    g.client.headers["Authorization"] = "Bearer " + member["token"]
    assert request_pair(g).content == first.content
    stranger = identity_factory()
    g.client.headers["Authorization"] = "Bearer " + stranger["token"]
    assert request_pair(g, plan_revision=1, runtime_revision=1).status_code == 404


def test_legacy_editor_changes_recompile_both_views_and_check_freshness(sync_scene):
    g, _, _ = sync_scene
    oid = str(uuid4())
    dimensions = {"width_m": 4, "depth_m": 2, "height_m": 3}
    created = editor_change(g, 1, ("create_object", oid, {"name": "Stage", "dimensions": dimensions,
                                                        "transform": transform(5, 5, 2)}))
    assert created.status_code == 200, created.text
    data = pair(g, plan_revision=1, runtime_revision=1)
    revision = data["scene_revision"]
    assert revision == created.json()["committed_revision"]
    assert data["freshness"] == {"plan": {"supplied_revision": 1, "status": "stale"},
                                 "runtime": {"supplied_revision": 1, "status": "stale"}}
    assert node(data, "plan", oid)["world_origin_m"] == [5, 5, 2]
    assert_xy_bounds_match(node(data, "plan", oid), node(data, "runtime", oid))
    assert data["validation"]["status"] == "synchronized"
    assert node(data, "plan", oid)["authored_dimensions"] == dimensions
    assert node(data, "plan", oid)["footprint"]["vertices"] == created.json()["snapshot"]["geometry"][oid]["vertices"]

    rejected = editor_change(g, 1, ("update_object", oid, {"transform": {"position": {"x": 20}}}))
    assert rejected.status_code == 409
    assert pair(g)["scene_revision"] == revision
    resized = {**dimensions, "width_m": 8}
    changed = editor_change(g, revision, ("update_object", oid, {
        "transform": {"position": {"x": 10, "y": 8}, "rotation_deg": {"z": 90}}, "dimensions": resized,
    }))
    assert changed.status_code == 200, changed.text
    latest_revision = changed.json()["committed_revision"]
    data = pair(g, plan_revision=revision, runtime_revision=latest_revision)
    assert data["freshness"]["plan"]["status"] == "stale"
    assert data["freshness"]["runtime"]["status"] == "current"
    assert data["plan"]["scene_revision"] == data["runtime"]["scene_revision"] == latest_revision
    plan, runtime = node(data, "plan", oid), node(data, "runtime", oid)
    assert plan["world_origin_m"] == [10, 8, 2]
    assert runtime["local_transform"]["rotation_deg"] == [0, 0, 90]
    assert plan["authored_dimensions"] == runtime["authored_dimensions"] == resized
    assert plan["footprint"]["bounds"]["width_m"] == pytest.approx(2)
    assert plan["footprint"]["bounds"]["depth_m"] == pytest.approx(8)
    assert_xy_bounds_match(plan, runtime)
    canonical = g.client.get(f"/api/v1/spatial/objects/{oid}").json()
    assert Decimal(canonical["transform"]["position"]["z"]) == 2
    assert Decimal(canonical["dimensions"]["height_m"]) == 3

    deleted = editor_change(g, latest_revision, ("delete_object", oid, {}))
    assert deleted.status_code == 200, deleted.text
    current = pair(g, plan_revision=latest_revision, runtime_revision=latest_revision + 500)
    assert current["freshness"]["plan"]["status"] == "stale"
    assert current["freshness"]["runtime"]["status"] == "ahead"
    assert not current["plan"]["nodes"] and not current["runtime"]["nodes"]
    assert current["validation"]["status"] == "synchronized"


def test_graph_mutations_propagate_through_both_views(sync_scene):
    g, _, _ = sync_scene
    zone = g.zone("Hall", transform=transform(2, 3, 1, 90), dimensions={"width_m": 6, "depth_m": 4, "height_m": 1})
    parent, nested, layer = (str(uuid4()) for _ in range(3))
    g.change(
        {"kind": "create_group", "id": parent, "group": {"name": "Parent", "zone_id": zone,
                                                         "transform": transform(10, 8, 4, 90, {"x": 2, "y": 3, "z": 1})}},
        {"kind": "create_group", "id": nested, "group": {"name": "Nested", "parent_object_id": parent,
                                                         "transform": transform(1, 2, 1)}},
        {"kind": "create_layer", "id": layer, "name": "Rigging"},
    )
    first = g.object("Rig A", parent_object_id=nested, zone_id=zone, transform=transform(1, 0, 1, 30))
    second = g.object("Rig B", parent_object_id=nested, transform=transform(0, 0, 0))
    assert request_pair(g).content == request_pair(g).content
    initial = pair(g)
    assert initial["validation"]["status"] == "synchronized"
    assert [entity["id"] for entity in initial["plan"]["nodes"]] == [zone, parent, nested, first, second]
    plan = node(initial, "plan", first)
    assert plan["parent"] == plan["transform_parent"] == ref(nested, "group")
    assert node(initial, "plan", parent)["parent"] == ref(zone, "zone")
    assert node(initial, "plan", parent)["transform_parent"] is None
    assert plan["zone_id"] == zone
    assert plan["world_origin_m"] == pytest.approx([4, 12, 6])
    assert_xy_bounds_match(plan, node(initial, "runtime", first))
    assert node(initial, "plan", zone)["footprint"]["bounds"]["width_m"] == pytest.approx(4)

    g.change(
        {"kind": "update_group", "id": parent, "transform": transform(9, 7, 5, 45, {"x": 2, "y": 3, "z": 2})},
        {"kind": "assign_layer", "nodes": [ref(first)], "layer_id": layer},
        {"kind": "reorder", "node": ref(second), "before": ref(first)},
        {"kind": "set_visibility", "nodes": [ref(layer, "layer")], "visible": False},
        {"kind": "set_locked", "nodes": [ref(parent, "group")], "locked": True},
    )
    latest = pair(g, plan_revision=initial["scene_revision"], runtime_revision=initial["scene_revision"])
    assert latest["validation"]["status"] == "synchronized"
    assert [entity["id"] for entity in latest["plan"]["nodes"]] == [zone, parent, nested, second, first]
    plan, runtime = node(latest, "plan", first), node(latest, "runtime", first)
    assert plan["world_origin_m"] == pytest.approx([9 - 2 ** .5, 7 + 5 * 2 ** .5, 9])
    assert_xy_bounds_match(plan, runtime)
    assert plan["footprint"] and runtime["geometry"]
    assert plan["visible"] and runtime["visible"]
    assert not plan["effective_visible"] and not runtime["effective_visible"]
    assert not plan["renderable"] and not runtime["renderable"]
    assert plan["effective_locked"] and runtime["effective_locked"]
    assert plan["layer_id"] == runtime["layer_id"] == layer

    assert g.client.patch(f"/api/v1/spatial/objects/{first}", json={"name": "Blocked"}).status_code == 409
    g.change({"kind": "set_locked", "nodes": [ref(parent, "group")], "locked": False},
             {"kind": "set_visibility", "nodes": [ref(layer, "layer")], "visible": True},
             {"kind": "reparent", "node": ref(second), "parent": ref(zone, "zone")})
    final = pair(g)
    assert node(final, "plan", first)["renderable"]
    assert node(final, "plan", second)["parent"] == ref(zone, "zone")
    assert node(final, "plan", second)["transform_parent"] is None
    assert node(final, "plan", second)["world_origin_m"] == pytest.approx(node(latest, "plan", second)["world_origin_m"])


def test_projection_support_distinguishes_exact_shapes_from_unavailable_outlines(sync_scene):
    g, _, _ = sync_scene
    def points(entries):
        return [{"x_m": x, "y_m": y} for x, y in entries]
    tilt = {"position": {"x": 4, "y": 8, "z": 3}, "rotation_deg": {"x": 45}}
    rect = g.zone("Tilted zone", transform=tilt, dimensions={"width_m": 4, "depth_m": 2, "height_m": 1})
    zone_plane = g.zone("Polygon surface", transform=tilt, footprint=points([(0, 0), (2, 0), (0, 1)]))
    convex = g.object("Convex prism", transform=tilt, properties={"geometry": {
        "type": "polygon", "vertices": points([(0, 0), (2, 0), (0, 1)])}})
    concave = g.object("Concave prism", transform=tilt, properties={"geometry": {
        "type": "polygon", "vertices": points([(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)])}})
    circle = g.object("Round venue", transform=transform(9, 9, 2), properties={"geometry": {"type": "circle", "radius_m": 1}})
    ellipse = g.object("Affine cylinder", transform=transform(9, 9, 2, scale={"x": 2, "y": 1, "z": 1}),
                       properties={"geometry": {"type": "circle", "radius_m": 1}})
    point = g.object("Point", properties={"geometry_type": "point"})
    line = g.object("Line", transform=tilt, properties={"geometry_type": "line"})
    partial = pair(g)
    assert partial["validation"]["status"] == "partial"
    assert {issue["node"]["id"] for issue in partial["validation"]["issues"]} == {concave, ellipse}
    for entity in (concave, ellipse):
        assert node(partial, "plan", entity)["footprint"] is None
        assert not node(partial, "plan", entity)["renderable"]
        assert node(partial, "runtime", entity)["renderable"]
    for entity in (rect, zone_plane, convex, circle, point, line):
        outline, runtime = node(partial, "plan", entity), node(partial, "runtime", entity)
        assert outline["renderable"] and runtime["renderable"]
        assert_xy_bounds_match(outline, runtime)
    assert node(partial, "plan", circle)["footprint"]["radius_m"] == 1

    unknown = g.object("Unknown", properties={"geometry_type": "fake_type"})
    invalid = g.object("Invalid", properties={"geometry": {"type": "circle", "radius_m": 99}})
    diagnostic = pair(g)
    assert diagnostic["validation"]["status"] == "invalid"
    codes = {issue["node"]["id"]: issue["code"] for issue in diagnostic["validation"]["issues"]}
    assert codes[unknown] == "unsupported_geometry"
    assert codes[invalid] == "invalid_geometry"
    for entity in (invalid, unknown):
        assert node(diagnostic, "plan", entity)["footprint"] is None
        assert node(diagnostic, "runtime", entity)["geometry"] is None


def test_pair_validation_detects_metadata_and_projection_drift(sync_scene):
    g, _, _ = sync_scene
    first, second = g.object("First"), g.object("Second")
    data = pair(g)
    plan = deepcopy(data["plan"])
    runtime = data["runtime"]
    assert not spatial_sync.validate_pair(plan, runtime)
    plan["coordinate_system"] = "y_up"
    plan["length_unit"] = "cm"
    plan["scene_revision"] = plan["scene_revision"] + 1
    plan["nodes"].pop(1)
    plan["nodes"][0]["world_origin_m"][1] += 3
    plan["nodes"][0]["footprint"]["bounds"]["min_x_m"] -= 5
    plan["nodes"][0]["renderable"] = False
    plan["nodes"][0]["layer_id"] = str(uuid4())
    issues = spatial_sync.validate_pair(plan, runtime)
    assert {issue["code"] for issue in issues} == {
        "coordinate_mismatch", "unit_mismatch", "revision_mismatch", "node_order_mismatch",
        "origin_mismatch", "projection_bounds_mismatch", "visibility_mismatch", "node_metadata_mismatch",
        "missing_plan_node",
    }
    assert any(issue["node"] == ref(first) and issue["field"] == "layer_id" for issue in issues)
    assert any(issue["node"] == ref(second) and issue["code"] == "missing_plan_node" for issue in issues)


def test_malformed_canonical_data_keeps_node_identity_and_diagnostics(sync_scene, db_session):
    g, _, _ = sync_scene
    missing, invalid_dim, invalid_transform = (g.object(name) for name in ("Missing", "Dimensions", "Transform"))
    scene = db_session.get(SpatialScene, UUID(g.scene))
    with db_session.no_autoflush:
        db_session.get(SpatialObject, UUID(missing)).parent_object_id = uuid4()
        db_session.get(SpatialObject, UUID(invalid_dim)).height_m = Decimal("NaN")
        db_session.get(SpatialObject, UUID(invalid_transform)).scale_y = Decimal("0")
        scene.coordinate_system = "bad_coordinate_system"
        scene.display_unit = "yards"
        data = SynchronizedScene.model_validate(spatial_sync.compile_pair(
            SceneCompilation(GraphRead(db_session, scene)))).model_dump(mode="json")
    assert data["validation"]["status"] == "invalid"
    codes = {issue["code"] for issue in data["validation"]["issues"]}
    assert {"missing_parent", "invalid_dimensions", "invalid_transform", "invalid_coordinate_system",
            "unsupported_display_unit"} <= codes
    assert len(data["plan"]["nodes"]) == len(data["runtime"]["nodes"]) == 3
    assert all(not entity["renderable"] for entity in data["plan"]["nodes"] + data["runtime"]["nodes"])
    assert data["plan"]["meters_per_display_unit"] is None
    db_session.rollback()
    assert pair(g)["validation"]["status"] == "synchronized"


def test_paired_read_holds_scene_lock_until_both_views_are_compiled(sync_scene, db_session, monkeypatch):
    from fastapi.testclient import TestClient

    from app.db.database import get_db
    from app.main import app
    from conftest import TestingSessionLocal

    g, _, _ = sync_scene
    oid = g.object("Stage")
    revision = g.read()["scene_revision"]
    auth = g.client.headers["Authorization"]
    db_session.rollback()
    plan_started, release = Event(), Event()
    original_project = spatial_sync.project_plan
    override = app.dependency_overrides[get_db]

    def isolated_session():
        with TestingSessionLocal() as session:
            yield session

    def pause_plan(compilation, runtime):
        plan_started.set()
        assert release.wait(timeout=6), "Test did not release paired compilation"
        return original_project(compilation, runtime)

    def read():
        with TestClient(app, headers={"Authorization": auth}) as client:
            return client.get(g.path + "/synchronization")

    monkeypatch.setattr(spatial_sync, "project_plan", pause_plan)
    app.dependency_overrides[get_db] = isolated_session
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            request = workers.submit(read)
            try:
                assert plan_started.wait(timeout=6)
                with TestingSessionLocal() as probe, pytest.raises(OperationalError) as blocked:
                    probe.execute(select(SpatialScene.id).where(SpatialScene.id == UUID(g.scene)).with_for_update(nowait=True))
                assert blocked.value.orig.sqlstate == "55P03"
            finally:
                release.set()
            response = request.result(timeout=6)
    finally:
        app.dependency_overrides[get_db] = override
    assert response.status_code == 200, response.text
    assert response.json()["scene_revision"] == revision
    assert response.json()["plan"]["scene_revision"] == response.json()["runtime"]["scene_revision"]
    assert g.client.patch(f"/api/v1/spatial/objects/{oid}", json={"transform": {"position": {"x": 2}}}).status_code == 200
    new_data = pair(g, plan_revision=revision, runtime_revision=revision)
    assert new_data["scene_revision"] > revision
    assert new_data["freshness"]["plan"]["status"] == new_data["freshness"]["runtime"]["status"] == "stale"
    assert node(new_data, "plan", oid)["world_origin_m"][0] == 2
