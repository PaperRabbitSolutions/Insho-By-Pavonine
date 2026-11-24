from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.production import ProductionQuantity
from app.models.spatial_design_review import SpatialDesignReview
from test_phase9_spatial import create_object, create_scene, make_project
from test_production import ProductionAPI, production, request
from test_spatial_graph import SceneGraphClient, ref


def revision(api):
    return request(api.client, "GET", f"/api/v1/spatial/scenes/{api.scene_id}")["current_revision"]


def quantity_request(item, method, target="object", **parameters):
    return {"item_id": item, "target": target, "rule": {"method": method, **parameters}}


def calculate(api, *calculations, status=200):
    response = api.write("POST", "/quantities/calculate", status, scene_revision=revision(api),
                         calculations=list(calculations))
    return response["quantities"] if status == 200 else response


def current(api, **params):
    return request(api.client, "GET", api.path + "/quantities", params=params)


def patch_object(api, obj, **payload):
    return request(api.client, "PATCH", f"/api/v1/spatial/objects/{obj['id']}", json=payload)


def new_object(api, **overrides):
    obj = create_object(api.client, api.scene_id, **overrides)
    return obj, api.item(spatial_object_id=obj["id"])


def test_world_geometry_rules_and_allowance(production):
    api, _, project, event = production
    obj, item = new_object(api, transform={"scale": {"x": 2, "y": 1, "z": 3},
                                            "rotation_deg": {"x": 0, "y": 0, "z": 90}})
    generated = calculate(
        api, quantity_request(item, "plan_area", allowance_percent="10.00"),
        quantity_request(item, "surface_area", face="xz"), quantity_request(item, "linear", axis="x"),
        quantity_request(item, "solid_volume"), quantity_request(item, "object_count"),
    )
    assert {row["method"]: (row["value"], row["unit"]) for row in generated} == {
        "plan_area": ("70.400000", "m2"), "surface_area": ("48.000000", "m2"),
        "linear": ("16.000000", "m"), "solid_volume": ("192.000000", "m3"), "object_count": ("1.000000", "each"),
    }
    area = generated[0]
    assert area["outputs"]["base_value"] == "64.000000"
    assert area["parameters"]["allowance_percent"] == "10"
    assert area["inputs"]["length_unit"] == "m"
    assert area["source"] == {"target": "object", "target_id": obj["id"]}
    assert area["item_id"] == item and area["project_id"] == str(project.id) and area["event_id"] == str(event.id)
    assert "allowance_percent / 100" in area["formula"]
    assert area["version"] == area["engine_version"] == 1 and area["scene_revision"] == revision(api)
    assert area["status"] == "calculated" and not area["stale"] and area["calculated_at"]
    assert request(api.client, "GET", api.path + f"/quantities/{area['id']}") == area

    path = f"/api/v1/production/projects/{project.id}/quantities"
    assert {row["id"] for row in request(api.client, "GET", path, params={"event_id": str(event.id)})} == {
        row["id"] for row in generated
    }
    assert len(request(api.client, "GET", path, params={"limit": 2, "offset": 1})) == 2


def test_zone_scene_line_and_curved_solid(production):
    api, _, _, _ = production
    zone = request(api.client, "POST", f"/api/v1/spatial/scenes/{api.scene_id}/zones", 201, json={
        "name": "Flooring", "category": "flooring",
        "footprint": [{"x_m": 0, "y_m": 0}, {"x_m": 3, "y_m": 0}, {"x_m": 0, "y_m": 4}],
    })
    flooring = api.item(spatial_zone_id=zone["id"])
    _, border = new_object(api, properties={"geometry": {
        "type": "line", "start": {"x_m": -1, "y_m": 0}, "end": {"x_m": 2, "y_m": 0},
    }}, transform={"scale": {"x": 2, "y": 3, "z": 1}})
    _, column = new_object(api, dimensions={"width_m": 2, "depth_m": 2, "height_m": 3},
                           properties={"geometry": {"type": "circle", "radius_m": 1}},
                           transform={"scale": {"x": 2, "y": 1, "z": 1}})
    rows = calculate(api, quantity_request(flooring, "plan_area", "zone"),
                     quantity_request(flooring, "solid_volume", "zone"),
                     quantity_request(border, "linear", axis="path"), quantity_request(column, "solid_volume"),
                     quantity_request(flooring, "solid_volume", "scene"))
    assert [row["value"] for row in rows] == ["6.000000", None, "6.000000", "18.849556", "4800.000000"]
    assert rows[1]["status"] == "invalid"
    assert "closed" in rows[1]["errors"][0]["message"]


def test_led_pitch_resolution_and_recipe_counts(production):
    api, _, _, _ = production
    _, wall = new_object(api, name="LED panel", dimensions={"width_m": 2, "depth_m": ".15", "height_m": 1})
    _, floral = new_object(api, name="Centerpiece", category="floral")
    led = quantity_request(wall, "led_surface", face="xz", pixel_pitch={"value": "1.5625", "unit": "mm"},
                           resolution={"width_px": 1280, "height_px": 640})
    stems = quantity_request(floral, "floral_stems", recipe_reference="Centerpiece recipe R3",
                             stems_per_arrangement=23, allowance_percent="5")
    pixels, flowers = calculate(api, led, stems)
    assert pixels["value"] == "2.000000"
    assert pixels["outputs"] == {"width_m": "2.000000", "height_m": "1.000000", "pixel_pitch_m": "0.0015625",
                                "width_px": 1280, "height_px": 640, "total_pixels": 819200, "base_value": "2.000000"}
    assert flowers["value"] == "25.000000" and flowers["outputs"]["base_value"] == "23.000000"
    assert flowers["parameters"]["recipe_reference"] == "Centerpiece recipe R3"
    impossible = {**led, "rule": {**led["rule"], "resolution": {"width_px": 1920, "height_px": 640}}}
    invalid = calculate(api, impossible)[0]
    assert invalid["status"] == "invalid" and invalid["version"] == 2 and invalid["value"] is None
    assert "must fit" in invalid["errors"][0]["message"]
    inferred = {**led, "rule": {key: val for key, val in led["rule"].items() if key != "resolution"}}
    assert calculate(api, inferred)[0]["outputs"]["total_pixels"] == 819200

    invalid_unit = {**led, "rule": {**led["rule"], "pixel_pitch": {"value": 1, "unit": "kg"}}}
    calculate(api, invalid_unit, status=422)
    calculate(api, quantity_request(floral, "floral_stems", stems_per_arrangement=23), status=422)
    calculate(api, quantity_request(floral, "object_count", allowance_percent="-2"), status=422)


def test_unique_lines_retries_and_atomic_scope_validation(production):
    api, _, _, _ = production
    obj, item = new_object(api)
    duplicate_item = api.item(spatial_object_id=obj["id"])
    line = quantity_request(item, "plan_area", allowance_percent="10.00")
    calculate(api, line, line, status=422)
    conflict = calculate(api, line, quantity_request(duplicate_item, "plan_area"), status=409)
    assert conflict["detail"]["code"] == "duplicate_quantity_source"
    calculate(api, line, quantity_request(str(uuid4()), "linear", axis="x"), status=404)
    assert current(api) == []
    original = calculate(api, line)[0]
    version = api.version
    result = calculate(api, quantity_request(item, "plan_area", allowance_percent="10"))[0]
    assert result["id"] == original["id"] and api.version == version
    assert calculate(api, quantity_request(duplicate_item, "plan_area"), status=409)["detail"]["code"] == "quantity_source_already_used"
    api.write("POST", "/quantities/calculate", 409, scene_revision=revision(api) - 1, calculations=[line])
    api.write("POST", "/quantities/calculate", 409, expected_version=api.version - 1,
              scene_revision=revision(api), calculations=[line])
    assert len(current(api, history=True)) == 1


def test_targeted_staleness_recalculation_and_approval_history(production):
    api, owner, _, _ = production
    obj, item = new_object(api)
    area, count = calculate(api, quantity_request(item, "plan_area"), quantity_request(item, "object_count"))
    create_object(api.client, api.scene_id, name="Unrelated prop")
    patch_object(api, obj, transform={"position": {"x": 2, "y": 3, "z": 0}})
    assert not any(row["stale"] for row in current(api))
    no_change = api.write("POST", f"/quantities/{area['id']}/recalculate", scene_revision=revision(api))["quantities"][0]
    assert no_change["id"] == area["id"] and no_change["scene_revision"] < no_change["current_scene_revision"]
    patch_object(api, obj, dimensions={"width_m": 6, "depth_m": 4, "height_m": 1})
    views = {row["method"]: row for row in current(api)}
    assert views["plan_area"]["stale"] and not views["object_count"]["stale"]
    api.sources(revision(api))
    failure = api.sources(revision(api), action="approve", status=409)
    assert failure["detail"]["code"] == "production_quantities_unready"
    changed = api.write("POST", f"/quantities/{area['id']}/recalculate", scene_revision=revision(api))["quantities"][0]
    assert changed["value"] == "24.000000" and changed["version"] == 2 and not changed["stale"]
    old = request(api.client, "GET", api.path + f"/quantities/{area['id']}")
    assert old["value"] == "32.000000" and old["stale"] and not old["latest"]
    assert len(current(api, history=True)) == 3
    api.write("POST", f"/quantities/{area['id']}/recalculate", 409, scene_revision=revision(api))
    api.sources(revision(api), action="approve")
    approved = {row["id"]: row for row in current(api, history=True)}
    assert approved[changed["id"]]["approved"] and approved[count["id"]]["approved"]
    assert approved[changed["id"]]["approved_by_user_id"] == str(owner["user"].id)
    assert not approved[area["id"]]["approved"]
    request(api.client, "DELETE", f"/api/v1/spatial/objects/{obj['id']}", 204)
    read = request(api.client, "GET", api.path + f"/quantities/{changed['id']}")
    assert read["stale"] and read["approved"] and read["value"] == "24.000000"
    api.write("POST", f"/quantities/{changed['id']}/recalculate", 409, scene_revision=revision(api))


def test_parent_scale_invalidates_descendant_quantities(production):
    api, _, _, _ = production
    obj, item = new_object(api)
    graph = SceneGraphClient(api.client, api.scene_id)
    group = str(uuid4())
    graph.change({"kind": "create_group", "id": group, "group": {"name": "Frame"}, "members": [ref(obj["id"])]})
    takeoff = calculate(api, quantity_request(item, "linear", axis="x"))[0]
    assert takeoff["value"] == "8.000000"
    graph.change({"kind": "update_group", "id": group, "transform": {"scale": {"x": 2, "y": 1, "z": 1}}})
    assert current(api)[0]["stale"]
    updated = api.write("POST", f"/quantities/{takeoff['id']}/recalculate", scene_revision=revision(api))["quantities"][0]
    assert updated["value"] == "16.000000"


def test_source_trace_from_applied_design_review(production, db_session):
    api, _, _, event = production
    recommendation, element, review_id = uuid4(), uuid4(), uuid4()
    obj, item = new_object(api, properties={"design_intelligence": {"recommendation_id": str(recommendation)}})
    source = {"artifact_id": str(uuid4()), "artifact_version": 2, "element_id": str(element), "module": "elements",
              "event_id": str(event.id), "path": "items[0]"}
    db_session.add(SpatialDesignReview(
        scene_id=UUID(api.scene_id), recommendation_id=recommendation, review_id=review_id, status="applied",
        base_revision=1, context_version="example", source=source, action={}, policy_fingerprint="policy",
        outcome={"entity_type": "object", "entity_id": obj["id"], "scene_revision": 2},
    ))
    db_session.commit()
    measured = calculate(api, quantity_request(item, "plan_area"))[0]
    assert measured["source"]["design"] == {**source, "recommendation_id": str(recommendation), "review_id": str(review_id)}


def test_quantity_db_uniqueness_and_scope(production, db_session):
    api, _, _, _ = production
    _, item = new_object(api)
    row = calculate(api, quantity_request(item, "object_count"))[0]
    original = db_session.get(ProductionQuantity, UUID(row["id"]))
    values = {column.key: getattr(original, column.key) for column in ProductionQuantity.__table__.columns
              if column.key not in {"id", "calculated_at"}}
    db_session.add(ProductionQuantity(**values))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.add(ProductionQuantity(**{**values, "record_id": uuid4(), "value": Decimal(1)}))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_scope_and_roles_for_analysis(production, db_session, identity_factory):
    api, owner, project, event = production
    _, item = new_object(api)
    row = calculate(api, quantity_request(item, "object_count"))[0]
    path = f"/api/v1/production/projects/{project.id}/quantities"
    another_project = make_project(db_session, owner)
    another_scene = create_scene(api.client, another_project.id)
    other = ProductionAPI(api.client, another_scene)
    foreign_item = other.item()
    request(api.client, "GET", f"/api/v1/production/projects/{another_project.id}/quantities", 404,
            params={"event_id": str(event.id)})
    calculate(api, quantity_request(foreign_item, "linear", axis="x"), status=404)
    request(api.client, "GET", other.path + "/quantities/" + row["id"], 404)
    outsider = identity_factory()
    api.client.headers["Authorization"] = "Bearer " + outsider["token"]
    request(api.client, "GET", path, 404)
    request(api.client, "GET", api.path + "/quantities/" + row["id"], 404)
    request(api.client, "POST", api.path + "/quantities/calculate", 404, json={
        "expected_version": api.version, "scene_revision": 2, "calculations": [quantity_request(item, "object_count")],
    })
    member = identity_factory(role="member", organization=owner["organization"])
    api.client.headers["Authorization"] = "Bearer " + member["token"]
    assert request(api.client, "GET", path)[0]["id"] == row["id"]
    calculate(api, quantity_request(item, "linear", axis="x"), status=403)
    api.client.headers.pop("Authorization")
    request(api.client, "GET", path, 401)
