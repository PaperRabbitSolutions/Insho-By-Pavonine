import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.event import Event
from app.models.project import Project
from app.models.spatial import SpatialScene, SpatialSceneRevision
from app.spatial_units import from_meters, to_meters


def make_project(db_session, identity, name="Spatial project"):
    project = Project(
        organization_id=identity["organization"].id,
        workspace_id=identity["workspace"].id,
        name=name,
        status="draft",
    )
    db_session.add(project)
    db_session.commit()
    return project


def create_scene(client, project_id, **overrides):
    body = {
        "name": "Main venue",
        "scene_type": "venue",
        "display_unit": "ft",
        "dimensions": {"width_m": 30, "depth_m": 20, "height_m": 8},
    }
    body.update(overrides)
    response = client.post(f"/api/v1/spatial/projects/{project_id}/scenes", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def create_object(client, scene_id, **overrides):
    body = {
        "name": "Stage",
        "object_type": "stage",
        "category": "stage",
        "dimensions": {"width_m": 8, "depth_m": 4, "height_m": 1},
    }
    body.update(overrides)
    response = client.post(f"/api/v1/spatial/scenes/{scene_id}/objects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_scene_persists_with_canonical_bounds_and_revision(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)

    scene = create_scene(client, project.id)

    stored = db_session.get(SpatialScene, uuid.UUID(scene["id"]))
    assert stored.project_id == project.id
    assert stored.width_m == 30
    assert stored.display_unit == "ft"
    assert stored.current_revision == 1
    assert Decimal(client.get(f"/api/v1/spatial/scenes/{scene['id']}").json()["dimensions"]["depth_m"]) == Decimal("20")


def test_scene_event_must_belong_to_same_authorized_project(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    first_project = make_project(db_session, identity, "First")
    second_project = make_project(db_session, identity, "Second")
    event = Event(organization_id=identity["organization"].id, project_id=second_project.id, name="Other event")
    db_session.add(event)
    db_session.commit()

    response = client.post(
        f"/api/v1/spatial/projects/{first_project.id}/scenes",
        json={"name": "Bad link", "event_id": str(event.id), "dimensions": {"width_m": 10, "depth_m": 10, "height_m": 5}},
    )
    assert response.status_code == 404


def test_zone_and_object_hierarchy_is_persisted_and_cycles_are_rejected(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    scene = create_scene(client, project.id)
    scene_id = scene["id"]

    parent_zone = client.post(f"/api/v1/spatial/scenes/{scene_id}/zones", json={"name": "Reception", "category": "reception"}).json()
    child_zone_response = client.post(
        f"/api/v1/spatial/scenes/{scene_id}/zones",
        json={"name": "Dining", "category": "dining", "parent_zone_id": parent_zone["id"], "dimensions": {"width_m": 12, "depth_m": 8, "height_m": 0.1}},
    )
    assert child_zone_response.status_code == 201, child_zone_response.text
    child_zone = child_zone_response.json()
    assert child_zone["transform"]["position"]["z"] == "0.000000"
    assert client.patch(f"/api/v1/spatial/zones/{parent_zone['id']}", json={"parent_zone_id": child_zone["id"]}).status_code == 409

    parent_object = create_object(client, scene_id, zone_id=child_zone["id"])
    child_object = create_object(client, scene_id, name="Backdrop", parent_object_id=parent_object["id"])
    assert client.patch(f"/api/v1/spatial/objects/{parent_object['id']}", json={"parent_object_id": child_object["id"]}).status_code == 409
    assert client.delete(f"/api/v1/spatial/zones/{child_zone['id']}").status_code == 409
    assert client.delete(f"/api/v1/spatial/objects/{parent_object['id']}").status_code == 409
    assert client.get(f"/api/v1/spatial/scenes/{scene_id}/hierarchy").status_code == 200
    hierarchy = client.get(f"/api/v1/spatial/scenes/{scene_id}/hierarchy").json()
    dining = next(node for node in hierarchy["children"] if node["id"] == parent_zone["id"])["children"][0]
    assert dining["id"] == child_zone["id"]
    assert dining["children"][0]["id"] == parent_object["id"]
    assert dining["children"][0]["children"][0]["id"] == child_object["id"]


def test_parent_zone_and_object_cannot_cross_scenes(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    scene_a = create_scene(client, project.id, name="A")
    scene_b = create_scene(client, project.id, name="B")
    zone_a = client.post(f"/api/v1/spatial/scenes/{scene_a['id']}/zones", json={"name": "A zone", "category": "custom"}).json()
    zone_b = client.post(f"/api/v1/spatial/scenes/{scene_b['id']}/zones", json={"name": "B zone", "category": "custom"}).json()
    assert client.patch(f"/api/v1/spatial/zones/{zone_b['id']}", json={"parent_zone_id": zone_a["id"]}).status_code == 404

    from app.models.spatial import SpatialZone
    db_session.add(SpatialZone(scene_id=uuid.UUID(scene_b["id"]), parent_zone_id=uuid.UUID(zone_a["id"]), name="Invalid direct row", category="custom"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    object_a = create_object(client, scene_a["id"], name="A object")
    object_b = create_object(client, scene_b["id"], name="B object")
    assert client.patch(f"/api/v1/spatial/objects/{object_b['id']}", json={"parent_object_id": object_a["id"]}).status_code == 404


def test_transform_scale_semantics_and_validation(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    scene = create_scene(client, project.id)
    obj = create_object(client, scene["id"], transform={"position": {"x": 1.25, "y": -2, "z": 0.4}, "rotation_deg": {"x": 0, "y": 0, "z": 90}, "scale": {"x": 2, "y": 1, "z": 0.5}})
    assert obj["transform"]["rotation_deg"]["z"] == "90.000000"
    assert obj["dimensions"] == {"width_m": "8.000000", "depth_m": "4.000000", "height_m": "1.000000"}
    assert {key: Decimal(value) for key, value in obj["effective_dimensions_m"].items()} == {
        "width_m": Decimal("16"), "depth_m": Decimal("4"), "height_m": Decimal("0.5")
    }

    bad = client.post(
        f"/api/v1/spatial/scenes/{scene['id']}/objects",
        json={"name": "Bad scale", "dimensions": {"width_m": 1, "depth_m": 1, "height_m": 1}, "transform": {"scale": {"x": 0, "y": 1, "z": 1}}},
    )
    assert bad.status_code == 422


def test_scene_deletion_is_safe_and_revisions_audit_updates(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    scene = create_scene(client, project.id)
    obj = create_object(client, scene["id"])
    assert client.delete(f"/api/v1/spatial/scenes/{scene['id']}").status_code == 409
    updated = client.patch(f"/api/v1/spatial/objects/{obj['id']}", json={"transform": {"position": {"x": 3, "y": 0, "z": 0}}})
    assert updated.status_code == 200, updated.text
    assert Decimal(updated.json()["transform"]["position"]["x"]) == Decimal("3")
    revisions = client.get(f"/api/v1/spatial/scenes/{scene['id']}/revisions")
    assert revisions.status_code == 200, revisions.text
    numbers = [entry["revision_number"] for entry in revisions.json()]
    assert numbers == [3, 2, 1]
    assert revisions.json()[0]["entity_type"] == "object"
    assert db_session.query(SpatialSceneRevision).filter_by(scene_id=uuid.UUID(scene["id"])).count() == 3

    empty_scene = create_scene(client, project.id, name="Empty scene")
    deleted = client.delete(f"/api/v1/spatial/scenes/{empty_scene['id']}")
    assert deleted.status_code == 204
    deleted_row = db_session.get(SpatialScene, uuid.UUID(empty_scene["id"]))
    assert deleted_row.status == "deleted"
    assert deleted_row.current_revision == 2
    assert client.get(f"/api/v1/spatial/scenes/{empty_scene['id']}").status_code == 404


def test_invalid_or_cross_project_asset_reference_is_hidden(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity, "Spatial project")
    other_project = make_project(db_session, identity, "Other project")
    scene = create_scene(client, project.id)
    obj = create_object(client, scene["id"])

    nonexistent = client.post(f"/api/v1/spatial/objects/{obj['id']}/assets", json={"asset_id": str(uuid.uuid4()), "role": "model"})
    assert nonexistent.status_code == 404

    from app.models.asset import Asset
    other_asset = Asset(
        organization_id=identity["organization"].id,
        workspace_id=identity["workspace"].id,
        project_id=other_project.id,
        original_filename="model.glb",
        display_name="Model",
        storage_key=uuid.uuid4().hex,
        mime_type="model/gltf-binary",
        file_extension=".glb",
        file_size=1,
        category="design",
    )
    db_session.add(other_asset)
    db_session.commit()
    cross_project = client.post(f"/api/v1/spatial/objects/{obj['id']}/assets", json={"asset_id": str(other_asset.id), "role": "model"})
    assert cross_project.status_code == 404


def test_existing_same_project_asset_can_be_linked_without_copying_it(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    scene = create_scene(client, project.id)
    obj = create_object(client, scene["id"])

    from app.models.asset import Asset
    asset = Asset(
        organization_id=identity["organization"].id,
        workspace_id=identity["workspace"].id,
        project_id=project.id,
        original_filename="reference.png",
        display_name="Reference",
        storage_key=uuid.uuid4().hex,
        mime_type="image/png",
        file_extension=".png",
        file_size=20,
        category="image",
    )
    db_session.add(asset)
    db_session.commit()

    attached = client.post(f"/api/v1/spatial/objects/{obj['id']}/assets", json={"asset_id": str(asset.id), "role": "reference"})
    assert attached.status_code == 201, attached.text
    assert attached.json()["asset_id"] == str(asset.id)
    assert attached.json()["asset_metadata"]["mime_type"] == "image/png"
    duplicate = client.post(f"/api/v1/spatial/objects/{obj['id']}/assets", json={"asset_id": str(asset.id), "role": "reference"})
    assert duplicate.status_code == 409
    listed = client.get(f"/api/v1/spatial/objects/{obj['id']}/assets")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    detached = client.delete(f"/api/v1/spatial/objects/{obj['id']}/assets/{attached.json()['id']}")
    assert detached.status_code == 204
    assert db_session.get(Asset, asset.id) is not None


def test_typed_relationships_are_persisted_and_duplicate_edges_conflict(client, db_session, identity_factory):
    identity = identity_factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db_session, identity)
    scene = create_scene(client, project.id)
    first = create_object(client, scene["id"], name="Table")
    second = create_object(client, scene["id"], name="Chair")
    payload = {"source_object_id": first["id"], "target_object_id": second["id"], "relationship_type": "supports", "metadata": {"note": "table/chair association"}}

    edge = client.post(f"/api/v1/spatial/scenes/{scene['id']}/relationships", json=payload)
    assert edge.status_code == 201, edge.text
    assert edge.json()["relationship_type"] == "supports"
    assert client.get(f"/api/v1/spatial/scenes/{scene['id']}/relationships").json() == [edge.json()]
    duplicate = client.post(f"/api/v1/spatial/scenes/{scene['id']}/relationships", json=payload)
    assert duplicate.status_code == 409


def test_cross_tenant_scene_id_is_not_discoverable(client, db_session, identity_factory):
    identity_a = identity_factory()
    identity_b = identity_factory()
    project_b = make_project(db_session, identity_b)
    client.headers["Authorization"] = "Bearer " + identity_b["token"]
    scene = create_scene(client, project_b.id)
    client.headers["Authorization"] = "Bearer " + identity_a["token"]
    assert client.get(f"/api/v1/spatial/scenes/{scene['id']}").status_code == 404
    assert client.patch(f"/api/v1/spatial/scenes/{scene['id']}", json={"name": "intrusion"}).status_code == 404


def test_unit_conversion_is_exact_and_display_only():
    assert to_meters("12", "in") == Decimal("0.3048")
    assert from_meters("1", "ft") == Decimal("3.280839895013123359580052493")
