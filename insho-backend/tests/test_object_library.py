from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.asset import Asset
from app.models.object_library import ObjectDefinition
from app.models.spatial import SpatialObject, SpatialObjectAsset
from app.services.asset_lifecycle import cleanup_deleted_assets
from app.services.spatial import SpatialService
from app.services.storage import get_storage_provider
from test_phase9_spatial import create_scene, make_project
from test_spatial_graph import SceneGraphClient, ref, transform


DIMS = {"width_m": 4, "depth_m": 2, "height_m": 1}


class LibraryClient:
    def __init__(self, client, org, scene):
        self.client = client
        self.org = str(org)
        self.path = f"/api/v1/object-library/organizations/{self.org}"
        self.g = SceneGraphClient(client, scene)

    def body(self, **overrides):
        return {"name": "Main stage", "category": "Scenic", "object_type": "stage", "dimensions": DIMS, **overrides}

    def create(self, expected=201, **overrides):
        response = self.client.post(self.path + "/definitions", json=self.body(**overrides))
        assert response.status_code == expected, response.text
        return response.json()

    def get(self, oid, expected=200):
        response = self.client.get(self.path + f"/definitions/{oid}")
        assert response.status_code == expected, response.text
        return response.json()

    def patch(self, oid, version, expected=200, **changes):
        response = self.client.patch(self.path + f"/definitions/{oid}", json={"expected_version": version, **changes})
        assert response.status_code == expected, response.text
        return response.json()

    def read(self, **filters):
        response = self.client.get(self.path + "/definitions", params=filters)
        assert response.status_code == 200, response.text
        return response.json()

    def place(self, definition, expected=201, version=None, revision=None, graph=None, **overrides):
        target = graph if graph is not None else self.g
        if revision is None:
            revision = target.read()["scene_revision"]
        body = {"definition_version": version if version is not None else definition["version"],
                "base_revision": revision, **overrides}
        response = self.client.post(target.path + f"/objects/from-library/{definition['id']}", json=body)
        assert response.status_code == expected, response.text
        return response.json()


@pytest.fixture
def library(client, db_session, identity_factory):
    owner = identity_factory()
    project = make_project(db_session, owner, "Library project")
    client.headers["Authorization"] = "Bearer " + owner["token"]
    scene = create_scene(client, project.id)
    return LibraryClient(client, owner["organization"].id, scene["id"]), owner, project


def upload(lib, monkeypatch, tmp_path, **scope):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "private-assets"))
    response = lib.client.post("/api/v1/uploads", data={"organization_id": lib.org, **scope},
                               files={"file": ("preview.jpg", b"\xff\xd8\xffobject-preview\xff\xd9", "image/jpeg")})
    assert response.status_code == 201, response.text
    return response.json()["asset_id"]


def test_catalog_create_update_duplicate_archive_and_restore(library, db_session):
    lib, _, _ = library
    item = lib.create(name="  Custom Design  ", category="Unlisted category",
                      description="Reusable object", tags=["Outdoor", "outdoor", "Stage"],
                      metadata={"internal_label": 10}, default_transform=transform(2, 3, 4, 45))
    oid = item["id"]
    assert item["version"] == 1 and item["name"] == "Custom Design"
    assert item["category"] == "Unlisted category" and item["scope"] == "organization"
    assert item["tags"] == ["outdoor", "stage"]
    assert item["length_unit"] == "m" and item["asset_availability"] == "none"
    assert item["metadata"] == {"internal_label": 10}
    assert item["validation"]["status"] == "valid"
    assert lib.get(oid) == item
    updated = lib.patch(oid, 1, default_transform={"position": {"x": "5.00000055"}})
    assert updated["version"] == 2 and updated["id"] == oid
    assert updated["default_transform"]["position"] == {"x": "5.000001", "y": "3.000000", "z": "4.000000"}
    assert lib.patch(oid, 1, expected=409, name="Stale")["detail"]["current_version"] == 2
    dup = lib.client.post(lib.path + f"/definitions/{oid}/duplicate", json={"expected_version": 2})
    assert dup.status_code == 201, dup.text
    assert dup.json()["id"] != oid and dup.json()["version"] == 1
    assert dup.json()["name"] == "Custom Design copy"

    archived = lib.client.delete(lib.path + f"/definitions/{oid}", params={"expected_version": 2})
    assert archived.status_code == 200, archived.text
    assert archived.json()["version"] == 3 and archived.json()["status"] == "archived"
    assert {row["id"] for row in lib.read()["items"]} == {dup.json()["id"]}
    assert lib.read(status="archived")["items"][0]["id"] == oid
    assert db_session.get(ObjectDefinition, UUID(oid)) is not None
    assert lib.place(archived.json(), expected=409)["detail"]["code"] == "definition_archived"
    assert lib.patch(oid, 3, status="active")["version"] == 4


def test_catalog_discovery_facets_and_project_filters(library, db_session, identity_factory):
    lib, owner, project = library
    other_workspace = identity_factory(organization=owner["organization"])
    other = make_project(db_session, other_workspace, "Separate workspace")
    a = lib.create(name="Alpha 100% display", category="Scenic", tags=["Lighting", "reusable"])
    b = lib.create(name="Beta display", workspace_id=str(owner["workspace"].id), category="Scenic", tags=["Lighting"])
    c = lib.create(name="Gamma rental", project_id=str(project.id), category="Rental", tags=["reusable"],
                   geometry={"type": "circle", "radius_m": 1},
                   default_transform={"scale": {"x": 2, "y": 1, "z": 1}})
    d = lib.create(name="Delta private", project_id=str(other.id), tags=["private"])
    assert c["validation"]["status"] == "valid_with_warnings" and not c["plan_compatible"]
    assert c["scope"] == "project" and c["workspace_id"] == str(owner["workspace"].id)
    assert b["scope"] == "workspace"
    assert [row["id"] for row in lib.read(q="100%")["items"]] == [a["id"]]
    assert [row["id"] for row in lib.read(category="SCENIC", tags=["lighting", "reusable"])["items"]] == [a["id"]]
    assert [row["id"] for row in lib.read(plan_compatible=False, geometry_type="circle")["items"]] == [c["id"]]
    assert {row["id"] for row in lib.read(project_id=str(project.id))["items"]} == {a["id"], b["id"], c["id"]}
    assert {row["id"] for row in lib.read(workspace_id=str(owner["workspace"].id))["items"]} == {a["id"], b["id"]}
    assert {row["id"] for row in lib.read(scope="project")["items"]} == {c["id"], d["id"]}
    assert not lib.read(min_width_m="4.1")["items"]
    assert len(lib.read(min_width_m=4, max_width_m=4, object_type="STAGE")["items"]) == 4
    page = lib.read(limit=1, offset=1)
    assert page["items"][0]["id"] == b["id"] and page["has_more"]
    assert lib.client.get(lib.path + "/definitions", params={"min_width_m": 5, "max_width_m": 4}).status_code == 422
    response = lib.client.get(lib.path + "/facets", params={"project_id": str(project.id)})
    assert response.status_code == 200, response.text
    assert response.json()["categories"] == [{"value": "rental", "count": 1}, {"value": "scenic", "count": 2}]
    assert response.json()["tags"] == [{"value": "lighting", "count": 2}, {"value": "reusable", "count": 2}]


def test_catalog_rejects_invalid_dimensions_units_shapes_and_metadata(library):
    lib, _, _ = library
    cases = [
        {"name": "  "}, {"category": ""}, {"dimensions": {"width_m": 3, "depth_m": 1}},
        {"dimensions": {**DIMS, "height_m": 0}}, {"dimensions": {**DIMS, "height_m": "NaN"}},
        {"default_transform": {"scale": {"x": 0}}}, {"default_transform": {"position": {"z": "Infinity"}}},
        {"length_unit": "cm"}, {"metadata": {"null_char": "\x00"}},
        {"metadata": {"large": "x" * 65536}}, {"transform": {"position": {"x": 1}}},
        {"geometry": {"type": "point", "radius_m": 2}},
    ]
    for params in cases:
        lib.create(expected=422, **params)
    err = lib.create(expected=422, geometry={"type": "imported_mesh"})
    assert err["detail"]["issues"][0]["code"] == "unsupported_geometry"
    err = lib.create(expected=422, geometry={"type": "circle", "radius_m": 10})
    assert err["detail"]["issues"][0]["code"] == "invalid_geometry"
    assert lib.create(expected=422, geometry={"type": "polygon"})["detail"]["status"] == "invalid"
    err = lib.create(expected=422, assets=[{"asset_id": str(uuid4()), "role": "preview"}])
    assert err["detail"]["issues"][0]["code"] == "asset_unavailable"
    assert not lib.read()["items"]


def test_assets_reuse_phase6_lifecycle_and_keep_referenced_file(library, db_session, monkeypatch, tmp_path):
    lib, _, project = library
    asset_id = upload(lib, monkeypatch, tmp_path, project_id=str(project.id))
    ref_data = {"asset_id": asset_id, "role": "preview"}
    bad = lib.create(expected=422, assets=[ref_data])
    assert bad["detail"]["issues"][0]["code"] == "asset_unavailable"
    item = lib.create(project_id=str(project.id), assets=[ref_data])
    assert item["assets"] == [{**ref_data, "available": True}]
    assert lib.read(asset_availability="available")["items"][0]["id"] == item["id"]
    assert lib.client.get(f"/api/v1/assets/{asset_id}/download").status_code == 200

    asset = db_session.get(Asset, UUID(asset_id))
    key = asset.storage_key
    assert lib.client.delete(f"/api/v1/assets/{asset_id}").status_code == 200
    asset.deleted_at = datetime.now(timezone.utc) - timedelta(days=40)
    db_session.commit()
    unavailable = lib.get(item["id"])
    assert unavailable["asset_availability"] == "unavailable"
    assert unavailable["validation"]["issues"][0]["code"] == "asset_unavailable"
    assert lib.read(asset_availability="unavailable")["items"][0]["id"] == item["id"]
    assert lib.place(item, expected=409)["detail"]["issues"][0]["code"] == "asset_unavailable"
    provider = get_storage_provider()
    cleanup = cleanup_deleted_assets(confirm_permanent_delete=True, db=db_session, provider=provider)
    assert cleanup["removed"] == 0 and provider.exists(key)
    assert lib.client.post(f"/api/v1/assets/{asset_id}/restore").status_code == 200
    assert lib.get(item["id"])["asset_availability"] == "available"
    swapped = lib.patch(item["id"], 1, assets=[])
    assert swapped["version"] == 2 and swapped["asset_availability"] == "none"


def test_instantiation_pins_version_and_exports_to_graph_runtime_and_sync(library, db_session, monkeypatch, tmp_path):
    lib, _, project = library
    asset_id = upload(lib, monkeypatch, tmp_path)
    source = lib.create(assets=[{"asset_id": asset_id, "role": "reference"}],
                        metadata={"catalog_rank": 7}, default_transform=transform(5, 6, 2, 30),
                        appearance={"finish": "matte"}, material_refs=[{"label": "Wood"}])
    g = lib.g
    zone = g.zone("Main Hall")
    layer, group = str(uuid4()), str(uuid4())
    g.change({"kind": "create_layer", "id": layer, "name": "Scenic"},
             {"kind": "set_default_layer", "layer_id": layer},
             {"kind": "create_group", "id": group, "group": {
                 "name": "Stage Field", "zone_id": zone, "transform": transform(2, 4, 1, 90),
             }})
    placed = lib.place(source, parent_object_id=group, zone_id=zone, transform={"position": {"x": 3}},
                       appearance={"finish": "satin"}, metadata={"design_note": "Left"})
    obj, oid = placed["object"], placed["object"]["id"]
    assert obj["library_definition_id"] == source["id"] and obj["library_definition_version"] == 1
    assert obj["layer_id"] == layer and obj["parent_object_id"] == group and obj["zone_id"] == zone
    assert obj["transform"]["position"] == {"x": "3.000000", "y": "6.000000", "z": "2.000000"}
    assert obj["appearance"] == {"finish": "satin"} and obj["material_refs"] == [{"label": "Wood"}]
    assert obj["properties"] == {"geometry": {"type": "box"}, "instance_metadata": {"design_note": "Left"}}
    links = g.client.get(f"/api/v1/spatial/objects/{oid}/assets").json()
    assert links[0]["asset_id"] == asset_id
    assert g.read()["scene_revision"] == placed["scene_revision"]
    assert next(row for row in g.read()["nodes"] if row["id"] == oid)["library_definition_id"] == source["id"]

    runtime = g.client.get(g.path + "/runtime-3d").json()
    compiled = next(row for row in runtime["nodes"] if row["id"] == oid)
    assert compiled["geometry"]["kind"] == "box" and compiled["renderable"]
    assert compiled["library_definition_version"] == 1
    assert [row[3] for row in compiled["world_matrix"][:3]] == pytest.approx([-4, 7, 3])
    response = g.client.get(g.path + "/synchronization")
    assert response.status_code == 200, response.text
    pair = response.json()
    assert next(row for row in pair["plan"]["nodes"] if row["id"] == oid)["library_definition_id"] == source["id"]
    assert lib.get(source["id"]) == source

    current = lib.patch(source["id"], 1, dimensions={**DIMS, "width_m": 8}, appearance={"finish": "gloss"})
    assert lib.place(source, expected=409)["detail"]["code"] == "definition_version_conflict"
    assert lib.place(current, expected=409, revision=1)["detail"]["code"] == "scene_revision_conflict"
    newer = lib.place(current, transform={"position": {"x": 12}}, layer_id=None)["object"]
    assert newer["id"] != oid and newer["layer_id"] is None and newer["library_definition_version"] == 2
    assert Decimal(newer["dimensions"]["width_m"]) == 8 and newer["appearance"] == {"finish": "gloss"}
    original = g.client.get(f"/api/v1/spatial/objects/{oid}").json()
    assert original == obj


def test_placement_overrides_fail_atomically_and_preserve_canonical_edits(library, db_session, monkeypatch, tmp_path):
    lib, _, _ = library
    aid = upload(lib, monkeypatch, tmp_path)
    source = lib.create(geometry={"type": "circle", "radius_m": 1},
                        assets=[{"asset_id": aid, "role": "preview"}, {"asset_id": aid, "role": "reference"}])
    revision = lib.g.read()["scene_revision"]
    assert lib.place(source, expected=422, dimensions={**DIMS, "depth_m": 1})["detail"]["status"] == "invalid"
    lib.place(source, expected=422, geometry={"type": "box"})

    original = SpatialService.attach_asset
    def fail_last(db, object_id, data, user_id, **kwargs):
        if data.role == "reference":
            raise HTTPException(503, detail="Storage dependency failed")
        return original(db, object_id, data, user_id, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(SpatialService, "attach_asset", staticmethod(fail_last))
        lib.place(source, expected=503)
    assert lib.g.read()["scene_revision"] == revision
    assert not lib.g.read()["nodes"]
    assert db_session.scalar(select(SpatialObjectAsset.id).where(SpatialObjectAsset.scene_id == UUID(lib.g.scene))) is None

    locked_layer = str(uuid4())
    lib.g.change({"kind": "create_layer", "id": locked_layer, "name": "Locked default"},
                 {"kind": "set_default_layer", "layer_id": locked_layer},
                 {"kind": "set_locked", "nodes": [ref(locked_layer, "layer")], "locked": True})
    locked_revision = lib.g.read()["scene_revision"]
    lib.place(source, expected=409)
    assert lib.g.read()["scene_revision"] == locked_revision and not lib.g.read()["nodes"]
    placed = lib.place(source, layer_id=None)["object"]
    api = f"/api/v1/spatial/objects/{placed['id']}"
    bad = lib.client.patch(api, json={"dimensions": {**DIMS, "depth_m": 1}})
    assert bad.status_code == 422, bad.text
    assert lib.client.get(api).json() == placed
    allowed = lib.client.patch(api, json={"properties": {"instance_metadata": {"finish": "oak"}}, "transform": {"position": {"x": 10}}})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["properties"]["geometry"]["type"] == "circle"
    assert lib.get(source["id"]) == source


def test_catalog_access_and_scene_scope(library, db_session, identity_factory, unauthenticated_client, monkeypatch, tmp_path):
    lib, owner, project = library
    root = lib.create()
    scoped = lib.create(project_id=str(project.id))
    other = identity_factory(organization=owner["organization"])
    other_project = make_project(db_session, other, "Different workspace")
    second = SceneGraphClient(lib.client, create_scene(lib.client, other_project.id)["id"])
    lib.place(scoped, graph=second, expected=404)
    assert lib.place(root, graph=second)["object"]["library_definition_id"] == root["id"]

    reader = identity_factory(role="member", organization=owner["organization"])
    owner_auth = lib.client.headers["Authorization"]
    lib.client.headers["Authorization"] = "Bearer " + reader["token"]
    assert lib.get(root["id"])["id"] == root["id"]
    lib.create(expected=403)
    lib.patch(root["id"], 1, expected=403, name="Unauthorized")
    assert lib.client.delete(lib.path + f"/definitions/{root['id']}", params={"expected_version": 1}).status_code == 403
    lib.place(root, expected=403)
    outsider = identity_factory()
    lib.client.headers["Authorization"] = "Bearer " + outsider["token"]
    outsider_lib = LibraryClient(lib.client, outsider["organization"].id, "")
    foreign_asset = upload(outsider_lib, monkeypatch, tmp_path)
    lib.get(root["id"], expected=404)
    assert lib.client.get(lib.path + "/definitions").status_code == 404
    assert lib.client.get(lib.path + "/facets").status_code == 404
    lib.place(root, expected=404, revision=1)
    assert unauthenticated_client.get(lib.path + "/definitions").status_code == 401
    lib.client.headers["Authorization"] = owner_auth
    assert lib.create(expected=422, assets=[{"asset_id": foreign_asset, "role": "reference"}])["detail"]["issues"][0]["code"] == "asset_unavailable"
    bad_provenance = lib.client.post(lib.g.path + "/objects", json={
        "name": "Foreign provenance", "dimensions": DIMS, "library_definition_id": root["id"],
    })
    assert bad_provenance.status_code == 422


def test_duplication_and_editor_restore_keep_library_provenance(library):
    lib, _, _ = library
    source = lib.create()
    g = lib.g
    group = str(uuid4())
    g.change({"kind": "create_group", "id": group, "group": {"name": "Group"}})
    placed = lib.place(source, parent_object_id=group)["object"]
    result = g.change({"kind": "duplicate_group", "id": group})
    copied_oid = result["results"][0]["ids"][1]
    copy = lib.client.get(f"/api/v1/spatial/objects/{copied_oid}").json()
    assert copy["library_definition_id"] == source["id"] and copy["library_definition_version"] == 1
    rev = g.read()["scene_revision"]
    deleted = lib.client.put(g.path + f"/editor/changes/{uuid4()}", json={"base_revision": rev, "operations": [
        {"kind": "delete_object", "id": placed["id"]},
    ]})
    assert deleted.status_code == 200, deleted.text
    restored = lib.client.put(g.path + f"/editor/changes/{uuid4()}", json={
        "base_revision": deleted.json()["committed_revision"], "operations": [
            {"kind": "create_object", "id": placed["id"], "data": {
                "name": placed["name"], "dimensions": placed["dimensions"], "transform": placed["transform"],
                "properties": placed["properties"], "parent_object_id": group,
            }},
        ],
    })
    assert restored.status_code == 200, restored.text
    assert lib.client.get(f"/api/v1/spatial/objects/{placed['id']}").json()["library_definition_id"] == source["id"]
