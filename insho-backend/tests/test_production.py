from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.event import Event
from app.models.production import ProductionItem, ProductionMeasurement, ProductionRecord
from app.models.spatial import SpatialScene
from app.models.spatial_design_review import SpatialDesignReview
from app.schemas.production import ItemCreate
from app.services import production as production_service
from app.services.production_units import normalize
from conftest import TestingSessionLocal
from test_object_library import LibraryClient
from test_phase9_spatial import create_object, create_scene, make_project


def request(client, method, path, status=200, **kwargs):
    response = client.request(method, path, **kwargs)
    assert response.status_code == status, response.text
    return response.json() if response.content else None


class ProductionAPI:
    def __init__(self, client, scene):
        self.client = client
        self.scene_id = scene["id"]
        self.base = f"/api/v1/production/scenes/{self.scene_id}/records"
        self.record = request(client, "POST", self.base, 201, json={"name": "Build list"})
        self.path = self.base + "/" + self.record["id"]
        self.version = self.record["version"]

    def get(self):
        return request(self.client, "GET", self.path)

    def write(self, method, suffix, status=200, **payload):
        result = request(self.client, method, self.path + suffix, status,
                         json={"expected_version": self.version, **payload})
        if status < 300:
            self.version = result["record_version"] if "record_version" in result else result["version"]
        return result

    def delete(self, suffix, status=200):
        result = request(self.client, "DELETE", self.path + suffix, status, params={"expected_version": self.version})
        if status == 200:
            self.version = result["record_version"]

    def item(self, name="Deck", status=201, **payload):
        return self.write("POST", "/items", status, name=name, **payload).get("id")

    def measure(self, item_id, kind, source=None, status=200, **payload):
        return self.write("PUT", f"/items/{item_id}/measurements/{kind}", status,
                          source=source or {"type": "user_entered"}, **payload)

    def sources(self, revision, *, action="review-sources", status=200):
        return self.write("POST", f"/{action}", status, scene_revision=revision)


@pytest.fixture
def production(client, db_session, identity_factory):
    owner = identity_factory()
    client.headers["Authorization"] = "Bearer " + owner["token"]
    project = make_project(db_session, owner)
    event = Event(organization_id=owner["organization"].id, project_id=project.id, name="Main event")
    db_session.add(event)
    db_session.commit()
    scene = create_scene(client, project.id, event_id=str(event.id))
    return ProductionAPI(client, scene), owner, project, event


def item_view(api, item_id):
    return next(item for item in api.get()["items"] if item["id"] == item_id)


def measurements(api, item_id):
    return {row["kind"]: row for row in item_view(api, item_id)["measurements"]}


def test_record_lifecycle_and_work_breakdown(production, db_session):
    api, owner, project, event = production
    assert (api.record["project_id"], api.record["event_id"], api.record["scene_id"]) == (
        str(project.id), str(event.id), api.scene_id,
    )
    assert api.record["created_by_user_id"] == str(owner["user"].id)
    assert api.record["scene_revision"] == 1
    assert request(api.client, "GET", api.base, params={"limit": 1}) == [api.record]
    api.write("PATCH", "", name="Scene build")
    material = api.write("POST", "/materials", 201, name="Birch plywood")["id"]
    api.write("PUT", f"/materials/{material}", name="Birch", description="Exterior grade")
    assembly = api.item("Stage", kind="assembly")
    item = api.item(parent_item_id=assembly, material_id=material)
    api.item("Invalid parent", status=422, parent_item_id=item)
    api.write("PUT", f"/items/{item}", name="Deck panels", material_id=material)
    spec = api.write("POST", f"/items/{item}/specifications", 201, name="Finish", content="Seal all edges.")["id"]
    api.measure(item, "count", value=12, unit="each")
    detail = api.get()
    assert detail["name"] == "Scene build" and not detail["stale"]
    assert detail["materials"] == [{"id": material, "name": "Birch", "description": "Exterior grade"}]
    stored = db_session.get(ProductionItem, UUID(item))
    assert (stored.parent_item_id, stored.material_id, stored.scene_id) == (UUID(assembly), UUID(material), UUID(api.scene_id))
    assert item_view(api, item)["specifications"][0]["id"] == spec
    assert measurements(api, item)["count"]["value"] == "12.000000"
    api.delete(f"/items/{assembly}", 409)
    api.delete(f"/materials/{material}", 409)
    assert api.get()["version"] == api.version
    api.delete(f"/items/{item}")
    api.delete(f"/items/{assembly}")
    api.delete(f"/materials/{material}")
    api.delete("", 204)
    assert db_session.get(ProductionRecord, UUID(api.record["id"])) is None


@pytest.mark.parametrize("kind,value,unit,expected", [
    ("length", "1", "ft", "0.304800"),
    ("width", "1", "in", "0.025400"),
    ("height", "48", "mm", "0.048000"),
    ("area", "10000", "cm2", "1.000000"),
    ("area", "1", "ft2", "0.092903"),
    ("volume", "1000000", "cm3", "1.000000"),
    ("volume", "1", "ft3", "0.028317"),
    ("weight", "1", "lb", "0.453592"),
    ("weight", "450", "g", "0.450000"),
    ("count", "0", "each", "0.000000"),
    ("length", "0.0000005", "m", "0.000001"),
])
def test_canonical_conversions(kind, value, unit, expected):
    assert normalize(kind, Decimal(value), unit) == Decimal(expected)


@pytest.mark.parametrize("kind,value,unit", [
    ("length", "0", "m"), ("length", "-1", "m"), ("area", "1", "m"), ("volume", "1", "m2"),
    ("width", "1", "kg"), ("height", "0.0000001", "m"), ("count", "1.2", "each"), ("count", "3", "kg"),
    ("weight", "1", "oz"), ("weight", "NaN", "kg"), ("weight", "Infinity", "kg"),
    ("length", "1000000000000000000", "m"),
])
def test_invalid_measurements(kind, value, unit):
    with pytest.raises(ValueError):
        normalize(kind, Decimal(value), unit)


def test_validation_and_optimistic_conflicts(production):
    api, _, _, _ = production
    item_id = api.item()
    path = f"/items/{item_id}/measurements"
    api.measure(item_id, "count", value="0.1", unit="each", status=422)
    api.measure(item_id, "weight", value="NaN", unit="kg", status=422)
    api.measure(item_id, "width", value=True, unit="m", status=422)
    api.measure(item_id, "width", value="1", status=422)
    api.measure(item_id, "width", source={"type": "spatial_geometry", "target": "scene", "scene_revision": 1},
                value=10, unit="m", status=422)
    api.write("PUT", f"{path}/speed", 422, value="3", unit="m", source={"type": "user_entered"})
    api.write("POST", "/items", 422, name="   ")
    api.write("POST", f"/items/{item_id}/specifications", 422, name="Width", content="bad\x00data")
    api.write("POST", "/items", 422, name="Invalid", project_id=str(uuid4()))
    assert measurements(api, item_id) == {}
    with_version = {"name": "Too late", "expected_version": api.version - 1}
    conflict = request(api.client, "POST", api.path + "/items", 409, json=with_version)
    assert conflict["detail"]["current_version"] == api.version


def test_concurrent_draft_writes_commit_once(production):
    api, owner, _, _ = production
    scene_id, record_id, user_id = UUID(api.scene_id), UUID(api.record["id"]), owner["user"].id
    start = Barrier(2, timeout=5)

    def create_item(name):
        with TestingSessionLocal() as session:
            start.wait()
            try:
                production_service.write_item(session, scene_id, record_id, user_id,
                                              ItemCreate(name=name, expected_version=api.version))
                return 201
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(create_item, ("Panel A", "Panel B"))) == [201, 409]
    detail = api.get()
    assert detail["version"] == api.version + 1
    assert len(detail["items"]) == 1


def test_geometry_invalidation_manual_protection_and_immutable_approval(production):
    api, _, _, _ = production
    obj = create_object(api.client, api.scene_id, transform={
        "scale": {"x": 2, "y": 1, "z": 3}, "rotation_deg": {"x": 0, "y": 0, "z": 90},
    })
    item_id = api.item(spatial_object_id=obj["id"])
    assert api.get()["scene_stale"]
    source = {"type": "spatial_geometry", "target": "object", "scene_revision": 2}
    for kind in ("length", "width", "height", "area", "volume"):
        api.measure(item_id, kind, source=source)
    api.measure(item_id, "weight", source=source, status=422)
    api.measure(item_id, "width", value="200", unit="cm")
    assert api.measure(item_id, "width", source=source, status=409)["detail"]["code"] == "production_manual_measurement"
    assert {kind: (row["value"], row["unit"]) for kind, row in measurements(api, item_id).items()} == {
        "length": ("16.000000", "m"), "width": ("2.000000", "m"), "height": ("3.000000", "m"),
        "area": ("64.000000", "m2"), "volume": ("192.000000", "m3"),
    }
    assert measurements(api, item_id)["volume"]["source"]["target_id"] == obj["id"]
    api.sources(2)
    assert not api.get()["stale"]
    request(api.client, "PATCH", f"/api/v1/spatial/objects/{obj['id']}",
            json={"dimensions": {"width_m": 6, "depth_m": 4, "height_m": 1}})
    assert api.get()["stale"]
    stale = measurements(api, item_id)
    assert stale["volume"]["stale"] and not stale["width"]["stale"]
    assert stale["volume"]["value"] == "192.000000"
    api.sources(3, status=409)
    api.sources(3, action="approve", status=409)
    api.measure(item_id, "volume", source=source, status=409)
    for kind in ("length", "height", "area", "volume"):
        api.measure(item_id, kind, source={**source, "scene_revision": 3})
    api.sources(3)
    api.sources(3, action="approve")
    assert api.get()["status"] == "approved"
    api.measure(item_id, "width", value=3, unit="m", status=409)
    api.delete("", 409)

    request(api.client, "DELETE", f"/api/v1/spatial/objects/{obj['id']}", 204)
    item = item_view(api, item_id)
    assert item["spatial_object_id"] is None and item["source"]["spatial_object_id"] == obj["id"]
    assert "spatial_object_missing" in item["stale_reasons"]
    assert measurements(api, item_id)["volume"]["value"] == "144.000000"
    assert measurements(api, item_id)["width"]["value"] == "2.000000"
    request(api.client, "DELETE", f"/api/v1/spatial/scenes/{api.scene_id}", 204)
    assert "scene_deleted" in api.get()["stale_reasons"]


def test_linked_zone_library_and_specification_versions(production):
    api, owner, _, _ = production
    lib = LibraryClient(api.client, owner["organization"].id, api.scene_id)
    definition = lib.create()
    zone = request(api.client, "POST", f"/api/v1/spatial/scenes/{api.scene_id}/zones", 201,
                   json={"name": "Stage area", "category": "stage",
                         "dimensions": {"width_m": 8, "depth_m": 4, "height_m": 1}})["id"]
    placed = lib.place(definition, zone_id=zone)["object"]
    item_id = api.item(spatial_object_id=placed["id"])
    item = item_view(api, item_id)
    assert item["spatial_zone_id"] == zone
    assert item["library_definition_id"] == definition["id"]
    assert item["source"]["placed_library_definition_version"] == 1
    api.measure(item_id, "area", source={"type": "spatial_geometry", "target": "zone", "scene_revision": 3})
    assert measurements(api, item_id)["area"]["value"] == "32.000000"
    library_source = {"type": "object_library", "definition_version": 1}
    api.measure(item_id, "width", source=library_source)
    assert measurements(api, item_id)["width"]["value"] == "4.000000"
    spec_path = f"/items/{item_id}/specifications"
    spec_id = api.write("POST", spec_path, 201, name="Mass", content="Mass 12 kg")["id"]
    spec_source = {"type": "technical_specification", "specification_id": spec_id, "specification_version": 1}
    api.measure(item_id, "weight", source=spec_source, value=12, unit="kg")
    api.delete(f"{spec_path}/{spec_id}", 409)
    api.sources(3)
    api.write("PUT", f"{spec_path}/{spec_id}", name="Mass", content="Mass 15 kg")
    lib.patch(definition["id"], 1, dimensions={"width_m": 5, "depth_m": 2, "height_m": 1})
    assert set(item_view(api, item_id)["stale_reasons"]) == {"library_definition_changed", "measurement_source_changed"}
    api.measure(item_id, "weight", source=spec_source, value=15, unit="kg", status=409)
    api.measure(item_id, "width", source=library_source, status=409)
    api.measure(item_id, "weight", source={**spec_source, "specification_version": 2}, value=15, unit="kg")
    api.measure(item_id, "width", source={**library_source, "definition_version": 2})
    assert "library_definition_changed" in item_view(api, item_id)["stale_reasons"]
    api.sources(3)
    assert not api.get()["stale"]
    request(api.client, "PATCH", f"/api/v1/spatial/objects/{placed['id']}", json={"zone_id": None})
    assert "object_zone_changed" in item_view(api, item_id)["stale_reasons"]


def test_reviewed_ai_provenance_and_stale_review(production, db_session):
    api, _, _, _ = production
    recommendation, receipt = uuid4(), uuid4()
    review = SpatialDesignReview(
        scene_id=UUID(api.scene_id), recommendation_id=recommendation, review_id=receipt, status="validated",
        base_revision=1, context_version="context", source={}, action={}, outcome={}, policy_fingerprint="policy",
    )
    db_session.add(review)
    db_session.commit()
    item_id = api.item()
    source = {"type": "ai_suggestion", "recommendation_id": str(recommendation), "review_id": str(receipt)}
    api.measure(item_id, "count", source={**source, "recommendation_id": str(uuid4())}, value=3, unit="each", status=404)
    api.measure(item_id, "count", source={**source, "review_id": str(uuid4())}, value=3, unit="each", status=409)
    api.measure(item_id, "count", source=source, value=3, unit="each")
    measured = measurements(api, item_id)["count"]
    assert measured["source"] == {**source, "scene_revision": 1} and not measured["stale"]
    review.review_id = uuid4()
    db_session.commit()
    assert measurements(api, item_id)["count"]["stale"]
    api.sources(1, action="approve", status=409)


def test_authentication_scope_and_foreign_keys(production, db_session, identity_factory):
    api, owner, project, event = production
    other_scene = create_scene(api.client, project.id)
    unrelated = ProductionAPI(api.client, other_scene)
    unrelated_obj = create_object(api.client, other_scene["id"])
    foreign_material = unrelated.write("POST", "/materials", 201, name="Steel")["id"]
    other_item = unrelated.item(kind="assembly")
    unrelated_spec = unrelated.write("POST", f"/items/{other_item}/specifications", 201, name="Spec", content="Text")["id"]
    api.item(status=404, spatial_object_id=unrelated_obj["id"])
    api.item(status=404, material_id=foreign_material)
    api.item(status=404, parent_item_id=other_item)
    local_item = api.item()
    api.measure(local_item, "weight", source={"type": "technical_specification", "specification_id": unrelated_spec,
                "specification_version": 1}, value=1, unit="kg", status=404)
    request(api.client, "GET", unrelated.base + "/" + api.record["id"], 404)
    with pytest.raises(IntegrityError):
        db_session.add(ProductionItem(scene_id=UUID(api.scene_id), record_id=UUID(api.record["id"]),
                                      spatial_object_id=UUID(unrelated_obj["id"]), name="Bad FK", kind="component"))
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.add(ProductionMeasurement(item_id=UUID(local_item), scene_id=UUID(api.scene_id), kind="count",
                                             value=Decimal("1.5"), source={}, source_type="user_entered"))
        db_session.commit()
    db_session.rollback()

    outsider = identity_factory()
    outside_project = make_project(db_session, outsider)
    api.client.headers["Authorization"] = "Bearer " + outsider["token"]
    outside_scene = create_scene(api.client, outside_project.id)
    outside_library = LibraryClient(api.client, outsider["organization"].id, outside_scene["id"]).create()
    request(api.client, "GET", api.path, 404)
    api.item(status=404)
    request(api.client, "POST", api.base, 404, json={"name": "Foreign"})
    api.client.headers.pop("Authorization")
    request(api.client, "GET", api.path, 401)

    viewer = identity_factory(role="member", organization=owner["organization"])
    api.client.headers["Authorization"] = "Bearer " + viewer["token"]
    assert api.get()["id"] == api.record["id"]
    api.item(status=403)
    designer = identity_factory(role="designer", organization=owner["organization"])
    api.client.headers["Authorization"] = "Bearer " + designer["token"]
    api.item("Designer component")
    api.item(status=404, library_definition_id=outside_library["id"])
    api.sources(1, action="approve", status=403)

    event.project_id = outside_project.id
    db_session.commit()
    request(api.client, "GET", api.path, 404)
    assert db_session.get(SpatialScene, UUID(api.scene_id)) is not None
