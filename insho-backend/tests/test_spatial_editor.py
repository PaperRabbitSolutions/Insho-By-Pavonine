import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from uuid import uuid4

import pytest
import uvicorn

from app.db.database import get_db
from app.main import app
from app.models.asset import Asset
from app.models.design_artifact import DesignArtifact
from app.models.event import Event
from test_phase9_spatial import create_scene, make_project


def change(client, scene, operations, revision=1, action=None):
    action = action or uuid4()
    return client.put(f"/api/v1/spatial/scenes/{scene}/editor/changes/{action}",
                      json={"base_revision": revision, "operations": operations})


def op(kind, entity_id, data=None):
    return {"kind": kind, "id": str(entity_id), "data": data or {}}


def box(name="Stage", x=7):
    return {"name": name, "category": "stage", "dimensions": {"width_m": 4, "depth_m": 2, "height_m": 1},
            "transform": {"position": {"x": x, "y": 7, "z": 0.5}}}


def scene_for(client, db, factory):
    identity = factory()
    client.headers["Authorization"] = "Bearer " + identity["token"]
    project = make_project(db, identity)
    return identity, project, create_scene(client, project.id)["id"]


def test_editor_atomic_changes_retry_and_conflict(client, db_session, identity_factory):
    _, project, scene = scene_for(client, db_session, identity_factory)
    a, b, action = uuid4(), uuid4(), uuid4()
    writes = [op("create_object", a, box()), op("create_object", b, {**box("Chair"), "parent_object_id": str(a)})]
    initial = change(client, scene, writes, action=action)
    assert initial.status_code == 200, initial.text
    receipt = initial.json()
    assert receipt["committed_revision"] == 4
    assert {row["id"] for row in receipt["snapshot"]["objects"]} == {str(a), str(b)}
    assert receipt["snapshot"]["geometry"][str(b)]["matrix"][0][3] == 14
    assert change(client, scene, writes, action=action).json()["committed_revision"] == 4
    assert change(client, scene, [op("update_object", a, {"name": "Stale"})]).status_code == 409
    invalid = change(client, scene, [
        op("update_object", a, {"name": "Will roll back"}), op("update_object", b, {"dimensions": {"width_m": -1}}),
    ], revision=4)
    assert invalid.status_code == 422, invalid.text
    bad_transform = change(client, scene, [op("update_object", b, {"transform": {"scale": {"x": 0}}})], revision=4)
    assert bad_transform.status_code == 422
    state = client.get(f"/api/v1/spatial/scenes/{scene}/editor").json()
    assert state["scene"]["current_revision"] == 4
    assert next(row for row in state["objects"] if row["id"] == str(a))["name"] == "Stage"
    assert client.get(f"/workspace/projects/{project.id}/spatial/{scene}").status_code == 200


def test_editor_scope_read_only_and_memberships(client, db_session, identity_factory, unauthenticated_client):
    owner, project, scene = scene_for(client, db_session, identity_factory)
    member = identity_factory(role="member", organization=owner["organization"])
    stranger = identity_factory()
    path = f"/api/v1/spatial/scenes/{scene}/editor"
    assert unauthenticated_client.get(path).status_code == 401
    client.headers["Authorization"] = "Bearer " + member["token"]
    assert client.get(path).json()["can_edit"] is False
    assert change(client, scene, [op("create_object", uuid4(), box())]).status_code == 403
    assert {org["id"] for org in client.get("/api/v1/organizations/mine").json()} == {str(owner["organization"].id)}
    client.headers["Authorization"] = "Bearer " + stranger["token"]
    assert client.get(path).status_code == 404
    assert change(client, scene, [op("create_object", uuid4(), box())]).status_code == 404
    assert {org["id"] for org in client.get("/api/v1/organizations/mine").json()} == {str(stranger["organization"].id)}


def test_editor_rejects_cross_scene_mutation_and_restores_identity(client, db_session, identity_factory):
    _, project, scene = scene_for(client, db_session, identity_factory)
    other = create_scene(client, project.id)["id"]
    obj, zone = uuid4(), uuid4()
    zone_data = {"name": "Stage area", "category": "stage_area", "dimensions": box()["dimensions"]}
    object_data = {**box(), "zone_id": str(zone)}
    creates = [op("create_zone", zone, zone_data), op("create_object", obj, object_data)]
    assert change(client, scene, creates).status_code == 200
    assert change(client, other, [op("update_object", obj, {"name": "Wrong scene"})]).status_code == 404
    removed = change(client, scene, [op("delete_object", obj), op("delete_zone", zone)], revision=4)
    assert removed.status_code == 200, removed.text
    assert not removed.json()["snapshot"]["objects"]
    assert not removed.json()["snapshot"]["zones"]
    restored = change(client, scene, creates, revision=7)
    assert restored.status_code == 200, restored.text
    assert restored.json()["snapshot"]["objects"][0]["id"] == str(obj)


def test_editor_projection_zones_and_snap_match_part2(client, db_session, identity_factory):
    _, _, scene = scene_for(client, db_session, identity_factory)
    obj, zone = uuid4(), uuid4()
    shape = box()
    shape["transform"]["rotation_deg"] = {"z": 45}
    result = change(client, scene, [op("create_object", obj, shape), op("create_zone", zone, {
        "name": "Zone", "category": "custom", "dimensions": shape["dimensions"],
        "transform": {"position": {"x": 10, "y": 10}, "rotation_deg": {"z": 90}},
    })])
    assert result.status_code == 200, result.text
    state = result.json()["snapshot"]
    prefix = f"/api/v1/spatial/scenes/{scene}"
    authoritative = client.get(f"{prefix}/objects/{obj}/geometry").json()
    geom = state["geometry"][str(obj)]
    assert geom["vertices"] == authoritative["vertices"]
    assert geom["bounds"] == authoritative["bounds"]
    assert state["geometry"][str(zone)]["bounds"]["width_m"] == pytest.approx(2)
    assert state["meters_per_unit"]["ft"] == 0.3048
    corner = next(anchor for anchor in geom["anchors"] if anchor["kind"].startswith("corner"))
    point = {key: corner[key] for key in ("x_m", "y_m")}
    result = client.post(prefix + "/snap", json={"position": point, "anchor_tolerance_m": 0.001}).json()
    assert result["target_id"] == str(obj) and result["point"] == point
    tilted = change(client, scene, [op("update_object", obj, {"transform": {"rotation_deg": {"x": 90, "z": 0}}})], revision=4)
    assert tilted.status_code == 200, tilted.text
    geometry = tilted.json()["snapshot"]["geometry"][str(obj)]
    assert geometry["bounds"]["depth_m"] == pytest.approx(1)
    assert geometry["upright"] is False


def test_editor_asset_and_relationship_restore(client, db_session, identity_factory):
    identity, project, scene = scene_for(client, db_session, identity_factory)
    asset = Asset(organization_id=identity["organization"].id, project_id=project.id,
                  original_filename="plan.pdf", display_name="Design reference", storage_key=uuid4().hex,
                  mime_type="application/pdf", file_extension="pdf", file_size=25)
    db_session.add(asset)
    db_session.commit()
    first, second, link, edge = (uuid4() for _ in range(4))
    attach = {**op("attach_asset", link, {"asset_id": str(asset.id)}), "object_id": str(first)}
    connect = op("create_relationship", edge, {"source_object_id": str(first),
                                              "target_object_id": str(second), "relationship_type": "near"})
    create = op("create_object", first, box())
    result = change(client, scene, [create, op("create_object", second, box("Other")), attach, connect])
    assert result.status_code == 200, result.text
    removed = change(client, scene, [op("delete_object", first)], revision=6)
    assert removed.status_code == 200, removed.text
    assert not removed.json()["snapshot"]["assets"]
    assert not removed.json()["snapshot"]["relationships"]
    restored = change(client, scene, [create, attach, connect], revision=8)
    assert restored.status_code == 200, restored.text
    assert restored.json()["snapshot"]["assets"][0]["id"] == str(link)
    assert restored.json()["snapshot"]["relationships"][0]["id"] == str(edge)


@pytest.mark.skipif(not os.environ.get("INSHO_CHROME"), reason="Set INSHO_CHROME to run the real browser workflow")
def test_editor_real_browser(db_session, identity_factory):
    email, password = f"browser-{uuid4()}@example.test", "Insho browser test password"
    identity = identity_factory(email=email, password=password)
    project = make_project(db_session, identity, "Browser workflow project")
    event = Event(organization_id=identity["organization"].id, project_id=project.id, name="Reception")
    db_session.add(event)
    db_session.flush()
    db_session.add_all([
        DesignArtifact(project_id=project.id, event_id=event.id, event_scope_key=str(event.id), module="plan", version=1,
                       content={"summary": "Reception layout reference", "zones": [{"name": "Arrival", "purpose": "Guest arrival"}]},
                       source="user_edited", context_fingerprint="seed", created_by_user_id=identity["user"].id),
        Asset(organization_id=identity["organization"].id, project_id=project.id, original_filename="plan.pdf",
              display_name="Reception reference", storage_key=uuid4().hex, mime_type="application/pdf",
              file_extension="pdf", file_size=25),
    ])
    db_session.commit()

    # Serve the same FastAPI app using independent sessions on the test database.
    from conftest import TestingSessionLocal

    def test_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", ws="none"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        result = subprocess.run(
            ["node", str(Path(__file__).parent / "browser" / "editor.mjs")],
            env={**os.environ, "INSHO_URL": f"http://127.0.0.1:{port}", "INSHO_TEST_EMAIL": email,
                 "INSHO_TEST_PASSWORD": password}, capture_output=True, text=True, timeout=100,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        app.dependency_overrides.pop(get_db, None)
