from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.asset import Asset
from app.models.spatial import SpatialLayer, SpatialObject
from app.spatial.math3d import decompose_transform, transform_matrix
from app.spatial.types import Transform3, Vec3
from test_phase9_spatial import create_object, create_scene, make_project


def ref(row_id, kind="object"):
    return {"type": kind, "id": str(row_id)}


def node_of(graph, entity_id):
    return next(node for node in graph["nodes"] if node["id"] == str(entity_id))


class SceneGraphClient:
    def __init__(self, http, scene):
        self.client = http
        self.scene = scene
        self.path = f"/api/v1/spatial/scenes/{scene}"
        self.graph_path = self.path + "/graph"

    def read(self):
        response = self.client.get(self.graph_path)
        assert response.status_code == 200, response.text
        return response.json()

    def change(self, *operations, expected=200, base_revision=None):
        if base_revision is None:
            base_revision = self.read()["scene_revision"]
        response = self.client.patch(self.graph_path, json={"base_revision": base_revision, "operations": operations})
        assert response.status_code == expected, response.text
        return response.json()

    def geometry(self, entity_id):
        response = self.client.get(self.path + f"/objects/{entity_id}/geometry")
        assert response.status_code == 200, response.text
        return response.json()

    def object(self, name, **kwargs):
        return create_object(self.client, self.scene, name=name, **kwargs)["id"]

    def zone(self, name, **kwargs):
        response = self.client.post(self.path + "/zones", json={"name": name, "category": "hall", **kwargs})
        assert response.status_code == 201, response.text
        return response.json()["id"]


@pytest.fixture
def graph_client(client, db_session, identity_factory):
    owner = identity_factory()
    project = make_project(db_session, owner)
    client.headers["Authorization"] = "Bearer " + owner["token"]
    return SceneGraphClient(client, create_scene(client, project.id)["id"]), owner, project


def transform(x=0, y=0, z=2, rotation=0, scale=None):
    return {"position": {"x": x, "y": y, "z": z}, "rotation_deg": {"z": rotation}, "scale": scale or {}}


def matrix_close(actual, expected, abs_tol=1e-6):
    for actual_row, expected_row in zip(actual, expected):
        assert actual_row == pytest.approx(expected_row, abs=abs_tol)


def test_group_layer_relationships_and_persistence(graph_client, db_session):
    g, owner, project = graph_client
    hall = g.zone("Main Hall", transform=transform(3, 2))
    stage = g.object("Stage", zone_id=hall, transform=transform(8, 5, rotation=25), properties={"fabric": "linen"})
    light = g.object("Lighting", zone_id=hall, transform=transform(13, 3))
    sibling = g.object("Arch", zone_id=hall)
    layer, group = str(uuid4()), str(uuid4())
    old_geometry = g.geometry(stage)
    result = g.change(
        {"kind": "create_layer", "id": layer, "name": "Structures", "metadata": {"team": "Production"}},
        {"kind": "assign_layer", "nodes": [ref(stage)], "layer_id": layer},
        {"kind": "create_group", "id": group, "group": {"name": "Production", "properties": {"lead": "Designer"}},
         "members": [ref(light), ref(stage)]},
    )
    graph = g.read()
    assert graph["scene_revision"] == result["scene_revision"]
    assert graph["project_id"] == str(project.id)
    assert graph["roots"] == [ref(hall, "zone")]
    assert node_of(graph, hall)["children"] == [ref(group, "group"), ref(sibling)]
    assert node_of(graph, group)["children"] == [ref(stage), ref(light)]
    assert node_of(graph, stage)["metadata"] == {"fabric": "linen"}
    assert graph["layers"][0]["members"] == [ref(stage)]
    relations = g.client.get(g.graph_path + f"/nodes/object/{stage}").json()
    assert relations["parent"] == ref(group, "group")
    assert relations["ancestors"] == [ref(group, "group"), ref(hall, "zone")]
    assert relations["siblings"] == [ref(light)]
    relations = g.client.get(g.graph_path + f"/nodes/zone/{hall}").json()
    assert relations["descendants"] == [ref(group, "group"), ref(stage), ref(light), ref(sibling)]
    assert g.geometry(stage)["transform_matrix"] == old_geometry["transform_matrix"]
    assert g.geometry(stage)["vertices"] == old_geometry["vertices"]
    legacy = g.client.get(g.path + "/hierarchy").json()
    assert legacy["children"][0]["children"][0]["node_type"] == "group"
    snapshot = g.client.get(g.path + "/editor").json()
    assert snapshot["geometry"][group].keys() == {"matrix"}
    assert next(row for row in snapshot["objects"] if row["id"] == group)["dimensions"] is None
    findings = g.client.post(g.path + "/validate", json={}).json()
    assert group not in [row["object_id"] for row in findings["boundary_checks"]]
    g.change({"kind": "update_group", "id": group, "name": "Main production"})
    g.change({"kind": "delete_group", "id": group}, expected=409)
    g.change({"kind": "ungroup", "id": group})
    assert g.geometry(stage)["transform_matrix"] == old_geometry["transform_matrix"]
    assert g.read()["layers"][0]["members"] == [ref(stage)]
    from conftest import TestingSessionLocal
    with TestingSessionLocal() as independent:
        stored = independent.get(SpatialObject, UUID(stage))
        assert stored.zone_id == UUID(hall) and stored.layer_id == UUID(layer) and stored.parent_object_id is None
        assert independent.get(SpatialObject, UUID(group)) is None
        assert independent.get(SpatialLayer, UUID(layer)).name == "Structures"


def test_nested_group_transforms_and_duplicate_reuses_assets(graph_client, db_session):
    g, owner, project = graph_client
    a, b, outside = g.object("Left", transform=transform(4, 4)), g.object("Right", transform=transform(8, 4)), g.object("Other")
    asset = Asset(organization_id=owner["organization"].id, project_id=project.id, original_filename="texture.png",
                  storage_key=uuid4().hex, display_name="Texture", mime_type="image/png", file_extension="png", file_size=10)
    db_session.add(asset)
    db_session.commit()
    assert g.client.post(f"/api/v1/spatial/objects/{a}/assets", json={"asset_id": str(asset.id), "role": "texture"}).status_code == 201
    for target in (b, outside):
        assert g.client.post(g.path + "/relationships", json={"source_object_id": a, "target_object_id": target,
                             "relationship_type": "adjacent"}).status_code == 201
    inner, outer = str(uuid4()), str(uuid4())
    g.change(
        {"kind": "create_group", "id": inner, "group": {"name": "Pair"}, "members": [ref(a), ref(b)]},
        {"kind": "create_group", "id": outer, "group": {"name": "Outer"}, "members": [ref(inner, "group")]},
        {"kind": "update_group", "id": outer, "transform": transform(2, 3, 1, 90)},
    )
    moved = g.geometry(a)
    result = g.change({"kind": "duplicate_group", "id": outer})
    new_group, new_inner, new_a, new_b = result["results"][0]["ids"]
    assert len({new_group, new_inner, new_a, new_b, outer, inner, a, b}) == 8
    matrix_close(g.geometry(new_a)["transform_matrix"], moved["transform_matrix"])
    graph = g.read()
    assert node_of(graph, new_a)["assets"][0]["asset_id"] == str(asset.id)
    assert any(edge["source_object_id"] == new_a and edge["target_object_id"] == new_b for edge in graph["relationships"])
    assert not any(edge["source_object_id"] == new_a and edge["target_object_id"] == outside for edge in graph["relationships"])
    g.change({"kind": "ungroup", "id": outer}, {"kind": "ungroup", "id": inner})
    matrix_close(g.geometry(a)["transform_matrix"], moved["transform_matrix"])


def test_reparent_preserves_world_and_rejects_invalid_or_sheared_placements(graph_client):
    g, _, _ = graph_client
    a = g.object("Parent", transform=transform(12, 15, rotation=37))
    child = g.object("Child", transform=transform(12, 16, 2, 24))
    other = g.object("Sheared frame", transform=transform(1, 1, rotation=12, scale={"x": 2, "y": 1, "z": 1}))
    before = g.geometry(child)
    g.change({"kind": "reparent", "node": ref(child), "parent": ref(a)})
    matrix_close(g.geometry(child)["transform_matrix"], before["transform_matrix"])
    cycle = g.change({"kind": "reparent", "node": ref(a), "parent": ref(child)}, expected=409)
    assert "cycle" in cycle["detail"]
    g.change({"kind": "reparent", "node": ref(a), "parent": ref(a)}, expected=409)
    g.change({"kind": "reparent", "node": ref(a), "parent": ref(uuid4())}, expected=404)
    prior = g.read()
    err = g.change(
        {"kind": "create_layer", "name": "Will roll back"},
        {"kind": "reparent", "node": ref(child), "parent": ref(other)},
        expected=409,
    )
    assert "shear" in err["detail"]
    assert g.read() == prior
    hall = g.zone("Hall", transform=transform(20, 10, rotation=20))
    zone = g.zone("Inner hall", transform=transform(3, 1, rotation=5))
    g.change({"kind": "reparent", "node": ref(zone, "zone"), "parent": ref(child)}, expected=422)
    old_matrix = g.client.get(g.path + "/editor").json()["geometry"][zone]["matrix"]
    g.change({"kind": "reparent", "node": ref(zone, "zone"), "parent": ref(hall, "zone")},
             {"kind": "reparent", "node": ref(child), "parent": ref(zone, "zone")})
    matrix_close(g.client.get(g.path + "/editor").json()["geometry"][zone]["matrix"], old_matrix)
    matrix_close(g.geometry(child)["transform_matrix"], before["transform_matrix"])
    assert node_of(g.read(), child)["zone_id"] == zone
    g.change({"kind": "reparent", "node": ref(child), "parent": None})
    assert node_of(g.read(), child)["parent"] is None
    matrix_close(g.geometry(child)["transform_matrix"], before["transform_matrix"])


def test_ordering_layers_and_stale_batch(graph_client):
    g, _, _ = graph_client
    a, b = g.object("A"), g.object("B")
    g.change({"kind": "create_layer", "name": "   "}, expected=422)
    layer1, layer2 = str(uuid4()), str(uuid4())
    g.change({"kind": "create_layer", "id": layer1, "name": "Furniture"},
             {"kind": "create_layer", "id": layer2, "name": "Stage"})
    initial = g.read()
    g.change({"kind": "reorder", "node": ref(layer2, "layer"), "before": ref(layer1, "layer")},
             {"kind": "reorder", "node": ref(b), "before": ref(a)},
             {"kind": "update_layer", "id": layer2, "name": "Scenic", "metadata": {"color": "red"}},
             {"kind": "assign_layer", "nodes": [ref(b), ref(a)], "layer_id": layer2})
    updated = g.read()
    assert [row["id"] for row in updated["layers"]] == [layer2, layer1]
    assert updated["layers"][0]["name"] == "Scenic" and updated["layers"][0]["metadata"] == {"color": "red"}
    assert updated["layers"][0]["members"] == [ref(b), ref(a)]
    assert updated["roots"] == [ref(b), ref(a)]
    assert node_of(updated, a)["order_index"] == node_of(initial, a)["order_index"]
    assert g.client.delete(g.path).status_code == 409
    g.change({"kind": "delete_layer", "id": layer2}, expected=409)
    g.change({"kind": "assign_layer", "nodes": [ref(a)], "layer_id": layer1},
             {"kind": "assign_layer", "nodes": [ref(b)], "layer_id": None},
             {"kind": "delete_layer", "id": layer2})
    g.change({"kind": "reorder", "node": ref(a), "before": ref(b)}, expected=409,
             base_revision=updated["scene_revision"])
    c = g.object("C")
    assert g.read()["roots"] == [ref(b), ref(a), ref(c)]


def test_effective_state_and_lock_enforcement_on_all_mutation_paths(graph_client):
    g, _, _ = graph_client
    hall, layer, group = g.zone("Hall"), str(uuid4()), str(uuid4())
    a, b = g.object("A", zone_id=hall), g.object("B", zone_id=hall)
    outside = g.object("Unassigned")
    g.change(
        {"kind": "create_layer", "id": layer, "name": "Backdrop"},
        {"kind": "assign_layer", "nodes": [ref(a)], "layer_id": layer},
        {"kind": "create_group", "id": group, "group": {"name": "Grouped"}, "members": [ref(a), ref(b)]},
        {"kind": "set_visibility", "nodes": [ref(layer, "layer"), ref(group, "group")], "visible": False},
    )
    graph = g.read()
    assert node_of(graph, a)["visible"] and not node_of(graph, a)["effective_visible"]
    g.change({"kind": "set_visibility", "nodes": [ref(group, "group")]})
    assert node_of(g.read(), b)["effective_visible"]
    assert not node_of(g.read(), a)["effective_visible"]
    g.change({"kind": "set_visibility", "nodes": [ref(hall, "zone")], "visible": False})
    assert not node_of(g.read(), b)["effective_visible"]
    g.change({"kind": "set_visibility", "nodes": [ref(hall, "zone"), ref(layer, "layer")], "visible": True})
    assert node_of(g.read(), a)["effective_visible"]
    g.change({"kind": "set_locked", "nodes": [ref(layer, "layer")], "locked": True})
    graph = g.read()
    assert node_of(graph, a)["effective_locked"] and not node_of(graph, a)["locked"]
    assert not node_of(graph, group)["effective_locked"]
    assert g.client.patch(f"/api/v1/spatial/objects/{a}", json={"name": "Locked"}).status_code == 409
    assert g.client.delete(f"/api/v1/spatial/objects/{a}").status_code == 409
    assert g.client.post(f"/api/v1/spatial/objects/{a}/assets", json={"asset_id": str(uuid4())}).status_code == 409
    assert g.client.post(g.path + "/relationships", json={"source_object_id": outside, "target_object_id": a,
                         "relationship_type": "near"}).status_code == 409
    assert g.client.patch(f"/api/v1/spatial/objects/{group}", json={"transform": transform(1, 1)}).status_code == 409
    g.change({"kind": "assign_layer", "nodes": [ref(outside)], "layer_id": layer}, expected=409)
    g.change({"kind": "ungroup", "id": group}, expected=409)
    g.change({"kind": "set_visibility", "nodes": [ref(outside), ref(a)], "visible": False}, expected=409)
    assert g.read() == graph
    assert g.client.put(g.path + f"/editor/changes/{uuid4()}", json={
        "base_revision": graph["scene_revision"],
        "operations": [{"kind": "update_object", "id": a, "data": {"name": "Bypass"}}],
    }).status_code == 409
    g.change({"kind": "set_locked", "nodes": [ref(layer, "layer")], "locked": False},
             {"kind": "set_locked", "nodes": [ref(group, "group"), ref(hall, "zone")], "locked": True})
    assert node_of(g.read(), b)["effective_locked"]
    assert g.client.post(g.path + "/objects", json={"name": "Blocked", "zone_id": hall,
                         "dimensions": {"width_m": 1, "depth_m": 1, "height_m": 1}}).status_code == 409
    g.change({"kind": "set_locked", "nodes": [ref(hall, "zone"), ref(group, "group")], "locked": False})
    assert not node_of(g.read(), b)["effective_locked"]


def test_scene_project_and_tenant_scope(graph_client, identity_factory, db_session, unauthenticated_client):
    g, owner, project = graph_client
    a = g.object("Local")
    other_project = make_project(db_session, owner, "Other project")
    other = SceneGraphClient(g.client, create_scene(g.client, other_project.id)["id"])
    external = other.object("Other project node")
    layer = other.change({"kind": "create_layer", "name": "Other layer"})["results"][0]["ids"][0]
    g.change({"kind": "reparent", "node": ref(a), "parent": ref(external)}, expected=404)
    g.change({"kind": "assign_layer", "nodes": [ref(a)], "layer_id": layer}, expected=404)
    sibling_scene = SceneGraphClient(g.client, create_scene(g.client, project.id, name="Second scene")["id"])
    same_project = sibling_scene.object("Second scene node")
    g.change({"kind": "create_group", "group": {"name": "Bad"}, "members": [ref(a), ref(same_project)]}, expected=404)
    with pytest.raises(IntegrityError):
        obj = db_session.get(SpatialObject, UUID(a))
        obj.layer_id = UUID(layer)
        obj.layer_order_index = 1024
        db_session.flush()
    db_session.rollback()

    assert unauthenticated_client.get(g.graph_path).status_code == 401
    member = identity_factory(role="member", organization=owner["organization"])
    g.client.headers["Authorization"] = "Bearer " + member["token"]
    assert node_of(g.read(), a)
    g.change({"kind": "set_locked", "nodes": [ref(a)], "locked": True}, expected=403)
    outsider = identity_factory()
    g.client.headers["Authorization"] = "Bearer " + outsider["token"]
    assert g.client.get(g.graph_path).status_code == 404
    g.change({"kind": "create_layer", "name": "Bad"}, base_revision=1, expected=404)
    outsider_scene = SceneGraphClient(g.client, create_scene(g.client, make_project(db_session, outsider).id)["id"])
    foreign = outsider_scene.object("Other tenant object")
    g.client.headers["Authorization"] = "Bearer " + owner["token"]
    g.change({"kind": "reparent", "node": ref(a), "parent": ref(foreign)}, expected=404)
    assert g.client.get(g.graph_path + f"/nodes/object/{foreign}").status_code == 404


def test_layer_members_order_independently_across_parents(graph_client):
    g, _, _ = graph_client
    parent, peer = g.object("Parent"), g.object("Peer")
    child = g.object("Child", parent_object_id=parent)
    layer = g.change({"kind": "create_layer", "name": "Lighting"})["results"][0]["ids"][0]
    g.change({"kind": "assign_layer", "nodes": [ref(peer), ref(child)], "layer_id": layer})
    prior = g.read()
    g.change({"kind": "reorder", "node": ref(child), "before": ref(peer)}, expected=409)
    g.change({"kind": "reorder", "scope": "layer_members", "node": ref(child), "before": ref(peer)})
    state = g.read()
    assert state["layers"][0]["members"] == [ref(child), ref(peer)]
    assert state["roots"] == prior["roots"]
    assert node_of(state, child)["parent"] == ref(parent)
    assert node_of(state, child)["order_index"] == node_of(prior, child)["order_index"]
    unchanged = g.change({"kind": "reorder", "scope": "layer_members", "node": ref(child), "before": ref(peer)})
    assert unchanged["scene_revision"] == state["scene_revision"]


def test_concurrent_batches_have_one_winner(graph_client, db_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi.testclient import TestClient

    from app.db.database import get_db
    from app.main import app
    from conftest import TestingSessionLocal

    g, owner, _ = graph_client
    revision = g.read()["scene_revision"]
    # The shared HTTP fixture keeps its read transaction open across requests.
    db_session.rollback()
    token = owner["token"]
    gate = Barrier(2)
    original = app.dependency_overrides[get_db]

    def separate_session():
        with TestingSessionLocal() as session:
            yield session

    def write(name):
        with TestClient(app, headers={"Authorization": "Bearer " + token}) as client:
            gate.wait(timeout=5)
            return client.patch(g.graph_path, json={"base_revision": revision,
                                "operations": [{"kind": "create_layer", "name": name}]}).status_code

    app.dependency_overrides[get_db] = separate_session
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert sorted(workers.map(write, ["One", "Two"])) == [200, 409]
    finally:
        app.dependency_overrides[get_db] = original
    assert len(g.read()["layers"]) == 1


@pytest.mark.parametrize("pitch", [34, -34, 90, -90])
def test_canonical_transform_decomposition(pitch):
    original = transform_matrix(Transform3(Vec3(4, 5, 6), Vec3(45, pitch, -37), Vec3(2, 3, 4)))
    matrix_close(transform_matrix(decompose_transform(original)), original, abs_tol=1e-10)


def test_default_layer_new_objects_and_legacy_editor_restore(graph_client, db_session, identity_factory):
    g, _, _ = graph_client
    existing = g.object("Before default")
    assert g.read()["default_layer_id"] is None
    first, second = str(uuid4()), str(uuid4())
    g.change({"kind": "create_layer", "id": first, "name": "Furniture"},
             {"kind": "create_layer", "id": second, "name": "A/V", "visible": False},
             {"kind": "set_default_layer", "layer_id": first})
    assert g.read()["default_layer_id"] == first
    inherited = g.object("Default assignment")
    unassigned = g.object("Explicitly unassigned", layer_id=None)
    assigned = g.object("Explicitly assigned", layer_id=second)
    graph = g.read()
    assert node_of(graph, existing)["layer_id"] is None
    assert node_of(graph, inherited)["layer_id"] == first
    assert node_of(graph, unassigned)["layer_id"] is None
    assert node_of(graph, assigned)["layer_id"] == second
    assert not node_of(graph, assigned)["effective_visible"]
    g.change({"kind": "delete_layer", "id": first}, expected=409)
    g.change({"kind": "set_default_layer", "layer_id": second})
    assert [layer["id"] for layer in g.read()["layers"] if layer["is_default"]] == [second]

    restored = {"kind": "create_object", "id": existing, "data": {
        "name": "Before default", "dimensions": {"width_m": 1, "depth_m": 1, "height_m": 1},
    }}
    revision = g.read()["scene_revision"]
    deleted = g.client.put(g.path + f"/editor/changes/{uuid4()}", json={
        "base_revision": revision, "operations": [
            {"kind": "delete_object", "id": existing},
            {"kind": "delete_object", "id": inherited},
        ],
    })
    assert deleted.status_code == 200, deleted.text
    recovered = g.client.put(g.path + f"/editor/changes/{uuid4()}", json={
        "base_revision": deleted.json()["committed_revision"],
        "operations": [restored, {**restored, "id": inherited}],
    })
    assert recovered.status_code == 200, recovered.text
    assert node_of(g.read(), existing)["layer_id"] is None
    assert node_of(g.read(), inherited)["layer_id"] == first

    g.change({"kind": "set_locked", "nodes": [ref(second, "layer")], "locked": True})
    denied = g.client.post(g.path + "/objects", json=restored["data"])
    assert denied.status_code == 409
    assert g.object("Opt out of locked default", layer_id=None)
    g.change({"kind": "set_default_layer", "layer_id": None}, expected=409)

    outsider = identity_factory()
    auth = g.client.headers["Authorization"]
    g.client.headers["Authorization"] = "Bearer " + outsider["token"]
    outsider_project = make_project(db_session, outsider)
    other_scene = SceneGraphClient(g.client, create_scene(g.client, outsider_project.id)["id"])
    other_layer = other_scene.change({"kind": "create_layer", "name": "Other"})["results"][0]["ids"][0]
    g.client.headers["Authorization"] = auth
    g.change({"kind": "set_default_layer", "layer_id": other_layer}, expected=404)
    g.change({"kind": "set_locked", "nodes": [ref(second, "layer")], "locked": False},
             {"kind": "set_default_layer", "layer_id": None})
    assert g.read()["default_layer_id"] is None
    no_default = g.object("No default now")
    assert node_of(g.read(), no_default)["layer_id"] is None


def test_project_and_event_deletion_cannot_bypass_graph_locks(graph_client, db_session):
    from app.models.event import Event

    g, owner, project = graph_client
    event = Event(organization_id=owner["organization"].id, project_id=project.id, name="Reception")
    db_session.add(event)
    db_session.commit()
    scoped = SceneGraphClient(g.client, create_scene(g.client, project.id, event_id=str(event.id))["id"])
    stage = scoped.object("Stage")
    layer = scoped.change({"kind": "create_layer", "name": "Stage layer"})["results"][0]["ids"][0]
    scoped.change({"kind": "assign_layer", "nodes": [ref(stage)], "layer_id": layer},
                  {"kind": "set_locked", "nodes": [ref(layer, "layer")], "locked": True})
    event_path = f"/api/v1/events/{event.id}"
    project_path = f"/api/v1/projects/{project.id}"
    assert scoped.client.delete(event_path).status_code == 409
    assert scoped.client.delete(project_path).status_code == 409
    assert node_of(scoped.read(), stage)["effective_locked"]
    scoped.change({"kind": "set_locked", "nodes": [ref(layer, "layer")], "locked": False})
    assert scoped.client.delete(event_path).status_code == 204
    assert scoped.client.get(scoped.graph_path).status_code == 404
    assert g.client.delete(project_path).status_code == 204


def test_database_rejects_cycles_and_locked_scene_cascade(graph_client, db_session):
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    g, _, project = graph_client
    root = g.object("Parent")
    child = g.object("Child", parent_object_id=root)
    zone = g.zone("Hall")
    subzone = g.zone("Side hall", parent_zone_id=zone)
    g.change({"kind": "set_locked", "nodes": [ref(child)], "locked": True})
    layers = [str(uuid4()), str(uuid4())]
    g.change(*({"kind": "create_layer", "id": layer, "name": layer} for layer in layers))
    # create_all provides the latest tables. Install the exact migration triggers
    # within this test transaction and roll them back on exit.
    conn = db_session.connection()
    migration = import_module("migrations.versions.d37b196ac241_phase9_part4_graph_invariants")
    try:
        with Operations.context(MigrationContext.configure(conn)):
            migration._install_graph_guards()
        cases = [
            ("UPDATE spatial_layers SET is_default = true WHERE id IN (:first, :second)",
             {"first": layers[0], "second": layers[1]}, "uq_spatial_layers_scene_default"),
            ("UPDATE spatial_objects SET parent_object_id = :parent WHERE id = :id",
             {"id": root, "parent": child}, "ck_spatial_objects_parent_acyclic"),
            ("UPDATE spatial_zones SET parent_zone_id = :parent WHERE id = :id",
             {"id": zone, "parent": subzone}, "ck_spatial_zones_parent_acyclic"),
            ("DELETE FROM projects WHERE id = :id",
             {"id": project.id}, "ck_spatial_scene_delete_locked"),
        ]
        for sql, params, constraint in cases:
            with pytest.raises(IntegrityError) as error, conn.begin_nested():
                conn.execute(text(sql), params)
            assert error.value.orig.diag.constraint_name == constraint
    finally:
        db_session.rollback()
