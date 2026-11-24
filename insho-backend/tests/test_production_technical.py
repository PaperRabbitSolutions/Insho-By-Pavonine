from uuid import uuid4

from test_phase9_spatial import create_object
from test_production import production, request
from test_production_quantification import current, patch_object, revision


def technical_api(api, **fields):
    return {"scene_revision": revision(api), **fields}


def specification(api, path):
    return request(api.client, "GET", api.path + path)


def test_geometry_suggestion_review_and_approved_revision_history(production, db_session, identity_factory):
    api, owner, _, _ = production
    obj = create_object(api.client, api.scene_id)
    material = api.write("POST", "/materials", 201, name="Birch", description="12 mm panel")["id"]
    item = api.item(spatial_object_id=obj["id"], material_id=material)
    specs = f"/items/{item}/specifications"
    technical = technical_api(api, source="spatial_geometry", target="object", finish="Matte laminate",
                              fabrication_notes="Seal panel edges.", installation_requirements="Position per layout.",
                              parameters=[{"name": "Manufacturer reference", "value": "Panel 680"}])
    spec = api.write("POST", specs, 201, name="Stage deck", content="Deck detail", technical=technical)["id"]
    route = specs + "/" + spec
    row = specification(api, route)
    assert row["status"] == "suggestion" and not row["stale"]
    assert row["technical"]["dimensions"] == {"width": {"value": "8.000000", "unit": "m"},
                                              "length": {"value": "4.000000", "unit": "m"},
                                              "height": {"value": "1.000000", "unit": "m"}}
    assert row["technical"]["material"]["id"] == material
    assert row["technical"]["source"]["target_id"] == obj["id"]
    assert row["technical"]["parameter_source"] == "user_entered"
    assert row["technical"]["fabrication_notes"] == "Seal panel edges."
    assert row["technical"]["finish"] == "Matte laminate"
    assert row["version"] == 1
    designer = identity_factory(role="designer", organization=owner["organization"])
    api.client.headers["Authorization"] = "Bearer " + designer["token"]
    api.write("POST", route + "/approve", 403, scene_revision=revision(api))
    api.client.headers["Authorization"] = "Bearer " + owner["token"]
    api.write("POST", route + "/approve", scene_revision=revision(api))
    approved = specification(api, route)
    assert approved["status"] == "approved" and approved["approved_at"]
    assert approved["approved_by_user_id"] == str(owner["user"].id)
    api.write("PUT", route, 409, name="Rewrite", content="Cannot edit approved sheet")
    api.delete(route, 409)
    api.delete(f"/items/{item}", 409)
    api.delete("", 409)

    patch_object(api, obj, dimensions={"width_m": 9, "depth_m": 4, "height_m": 1})
    assert specification(api, route)["stale_reasons"] == ["spatial_source_changed"]
    assert api.get()["stale"]
    assert api.sources(revision(api), status=409)["detail"]["code"] == "production_sources_stale"
    new_id = api.write("POST", specs, 201, name="Deck revision", content="Revised sheet", supersedes_id=spec,
                       technical={**technical, "scene_revision": revision(api)})["id"]
    new_route = specs + "/" + new_id
    new = specification(api, new_route)
    assert new["status"] == "suggestion" and new["version"] == 2 and new["supersedes_id"] == spec
    assert new["technical"]["dimensions"]["width"]["value"] == "9.000000"
    api.write("POST", specs, 409, name="Second revision", content="Same predecessor", supersedes_id=spec,
              technical={**technical, "scene_revision": revision(api)})
    old = specification(api, route)
    assert old["status"] == "approved" and old["superseded_by_id"] == new_id and old["stale"]
    assert old["technical"]["dimensions"]["width"]["value"] == "8.000000"
    api.write("POST", new_route + "/approve", scene_revision=revision(api))
    api.sources(revision(api))
    api.sources(revision(api), action="approve")
    assert all(row["status"] == "approved" for row in specification(api, specs))
    assert current(api) == []


def test_manual_specs_validate_units_and_refresh_material_sources(production):
    api, _, _, _ = production
    material = api.write("POST", "/materials", 201, name="Aluminium")["id"]
    item = api.item(material_id=material)
    specs = f"/items/{item}/specifications"
    field = technical_api(api, dimensions={"length": {"value": "1", "unit": "ft"}},
                          installation_requirements="Align to survey marks.",
                          parameters=[{"name": "Supplied fitting", "value": "A12"}])
    api.write("POST", specs, 422, name="Bad unit", content="Equipment",
              technical={**field, "dimensions": {"length": {"value": 12, "unit": "kg"}}})
    api.write("POST", specs, 422, name="Bad parameter", content="Equipment",
              technical={**field, "parameters": [{"name": "Gauge", "value": "14"},
                                                 {"name": " gauge ", "value": "16"}]})
    api.write("POST", specs, 422, name="Bad source", content="Equipment",
              technical={**field, "source": "spatial_geometry", "target": "scene"})
    api.write("POST", specs, 422, name="Invalid dimension", content="Equipment",
              technical={**field, "dimensions": {"length": {"value": "-1", "unit": "m"}}})
    assert specification(api, specs) == []
    saved = api.write("POST", specs, 201, name="Supplied shop drawing", content="Reference V1", technical=field)["id"]
    route = specs + "/" + saved
    row = specification(api, route)
    assert row["technical"]["dimensions"]["length"] == {"value": "0.304800", "unit": "m"}
    assert row["status"] == "draft" and row["technical"]["source"]["type"] == "user_entered"
    assert row["technical"]["source"]["target_id"] is None
    api.write("PUT", f"/materials/{material}", name="Anodized aluminium")
    assert specification(api, route)["stale_reasons"] == ["material_changed"]
    api.write("POST", route + "/approve", 409, scene_revision=revision(api))
    api.write("PUT", route, name="Supplied shop drawing", content="Reference V2", technical=field)
    assert specification(api, route)["version"] == 2
    assert not specification(api, route)["stale"]
    api.write("PUT", route, name="Shop drawing", content="Reference V2")
    assert specification(api, route)["technical"]["dimensions"]["length"]["value"] == "0.304800"
    assert specification(api, route)["version"] == 3


def test_legacy_spec_and_cross_item_predecessor(production):
    api, _, _, _ = production
    first, second = api.item("First"), api.item("Second")
    old = api.write("POST", f"/items/{first}/specifications", 201, name="Legacy", content="Legacy text")["id"]
    path = f"/items/{first}/specifications/{old}"
    row = specification(api, path)
    assert row["technical"] is None and row["status"] == "draft"
    api.write("POST", path + "/approve", scene_revision=revision(api))
    api.write("POST", f"/items/{second}/specifications", 404, name="Cross item", content="Legacy", supersedes_id=old)
    request(api.client, "GET", api.path + f"/items/{second}/specifications/{old}", 404)
    request(api.client, "GET", api.path + f"/items/{uuid4()}/specifications/{old}", 404)


def test_installation_freshness_and_tenant_boundary(production, identity_factory):
    api, _, _, _ = production
    obj = create_object(api.client, api.scene_id)
    item = api.item(spatial_object_id=obj["id"])
    specs = f"/items/{item}/specifications"
    spec = api.write("POST", specs, 201, name="Location detail", content="Surveyed attachment positions",
                     technical=technical_api(api, installation_requirements="Follow the recorded placement."))["id"]
    route = specs + "/" + spec
    patch_object(api, obj, transform={"position": {"x": 2, "y": 1, "z": 0}})
    assert specification(api, route)["stale_reasons"] == ["spatial_source_changed"]
    outsider = identity_factory()
    api.client.headers["Authorization"] = "Bearer " + outsider["token"]
    request(api.client, "GET", api.path + route, 404)
    api.write("PUT", route, 404, name="Foreign", content="No access")
