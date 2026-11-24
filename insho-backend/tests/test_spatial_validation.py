"""Phase 9 Part 8 validation contract against canonical scene data."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.models.asset import Asset
from app.models.event import Event
from app.models.object_library import ObjectDefinition
from app.models.spatial import SpatialObject, SpatialScene
from app.services import spatial_validation_rules, spatial_sync
from test_object_library import library
from test_phase9_spatial import create_scene
from test_spatial_graph import graph_client, ref, transform


DIMS = {"width_m": 2, "depth_m": 2, "height_m": 1}


def report(g, **body):
    response = g.client.post(g.path + "/validation-report", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def findings(payload, code):
    return [finding for finding in payload["findings"] if finding["code"] == code]


def entities(finding):
    return {entity["id"] for entity in finding["entities"]}


def place(g, name, x, y, *, z=2, rotation=0, dimensions=DIMS, **options):
    return g.object(name, transform=transform(x, y, z, rotation), dimensions=dimensions, **options)


def pair(a, b):
    return {"first_id": a, "second_id": b}


def threshold(id_, subject, against, minimum):
    return {"id": id_, "subject": subject, "against": against, "minimum_m": minimum}


def test_report_security_revision_and_read_only(graph_client, db_session, identity_factory):
    g, owner, project = graph_client
    event = Event(organization_id=owner["organization"].id, project_id=project.id, name="Reception")
    db_session.add(event)
    db_session.commit()
    g = type(g)(g.client, create_scene(g.client, project.id, event_id=str(event.id))["id"])
    empty = report(g)
    assert empty["status"] == "valid" and empty["complete"]
    assert empty["summary"] == {"total": 0, "errors": 0, "warnings": 0, "info": 0, "blocking": 0}
    assert (empty["project_id"], empty["scene_id"], empty["event_id"]) == (str(project.id), g.scene, str(event.id))
    assert len(empty["rules_executed"]) == 10
    place(g, "A chair", 8, 8)
    after = report(g, plan_revision=empty["scene_revision"], runtime_revision=100)
    assert after["scene_revision"] == empty["scene_revision"] + 1 == g.read()["scene_revision"]
    assert after["status"] == "invalid" and after["complete"]
    assert findings(after, "representation_stale")[0]["measurement"] == {
        "measured": empty["scene_revision"], "expected": after["scene_revision"], "shortfall": None, "unit": "revision",
    }
    assert findings(after, "representation_ahead")[0]["blocking"]
    equal = report(g, plan_revision=after["scene_revision"], runtime_revision=after["scene_revision"])
    assert equal["status"] == "valid" and equal["scene_revision"] == after["scene_revision"]

    before = g.read()
    member = identity_factory(role="member", organization=owner["organization"])
    other = identity_factory()
    for token, expected in ((member["token"], 200), (other["token"], 404)):
        g.client.headers["Authorization"] = "Bearer " + token
        assert g.client.post(g.path + "/validation-report").status_code == expected
    del g.client.headers["Authorization"]
    assert g.client.post(g.path + "/validation-report").status_code == 401
    g.client.headers["Authorization"] = "Bearer " + owner["token"]
    assert g.read() == before


@pytest.mark.parametrize("config", [
    {"rules": {"typo": {"enabled": True}}},
    {"rules": {"collision": {"severity": "critical"}}},
    {"collision": {"report_touching": "false"}},
    {"exclusive_zone_pairs": [{"first_id": "invalid", "second_id": "invalid"}]},
    {"clearances": [threshold("bad", {}, {"category": "stage"}, 1)]},
    {"clearances": [threshold("bad", {"category": "stage"}, {"category": "other"}, 0)]},
    {"clearances": [threshold("bad", {"category": "stage"}, {"category": "other"}, "Infinity")]},
])
def test_invalid_configuration_is_rejected_at_report_and_write(graph_client, config):
    g, _, project = graph_client
    before = g.read()["scene_revision"]
    response = g.client.post(g.path + "/validation-report", json={"config": config})
    assert response.status_code == 422 and response.json()["detail"]
    scene = g.client.patch(g.path, json={"metadata": {"spatial_validation": config}})
    assert scene.status_code == 422 and scene.json()["detail"][0]["loc"][:2] == ["metadata", "spatial_validation"]
    update = g.client.patch(f"/api/v1/projects/{project.id}", json={"constraints": {"spatial_validation": config}})
    assert update.status_code == 422 and update.json()["detail"][0]["loc"][:2] == ["constraints", "spatial_validation"]
    assert g.read()["scene_revision"] == before


@pytest.mark.parametrize("value", ["NaN", "1e999"])
def test_nonfinite_json_threshold_returns_serializable_validation_error(graph_client, value):
    g, _, _ = graph_client
    raw = ('{"config":{"clearances":[{"id":"bad","subject":{"category":"chair"},'
           '"against":{"category":"stage"},"minimum_m":' + value + '}]}}')
    response = g.client.post(g.path + "/validation-report", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "minimum_m"


def test_collision_classification_filters_policy_and_determinism(graph_client):
    g, _, project = graph_client
    a = place(g, "Rotated structure", 6, 6, rotation=45)
    b = place(g, "Stage", 7.7, 6)
    c = place(g, "Touching seat", 9.7, 6)
    d = place(g, "Distant seat", 14, 6)
    place(g, "Marker", 6, 6, properties={"collision_policy": "ignore"})
    place(g, "Raised platform", 7.7, 6, z=4)
    hidden = place(g, "Hidden", 7.7, 6, status="hidden")
    child = place(g, "Mount", 0, 0, z=0, parent_object_id=d)
    baseline = report(g)
    overlap, = findings(baseline, "objects_overlap")
    assert entities(overlap) == {a, b}
    assert overlap["measurement"]["measured"] > 0 and overlap["measurement"]["unit"] == "m³"
    assert not overlap["blocking"] and not findings(baseline, "objects_touch")
    repeated = report(g)
    for field in ("findings", "summary", "configuration_fingerprint", "status", "scene_revision", "rules_executed"):
        assert repeated[field] == baseline[field]
    assert repeated["run_id"] != baseline["run_id"]

    project_config = {"rules": {"collision": {"severity": "error"}}, "collision": {"report_touching": True}}
    assert g.client.patch(f"/api/v1/projects/{project.id}", json={
        "constraints": {"spatial_validation": project_config, "brief": "retained"},
    }).status_code == 200
    assert g.client.patch(g.path, json={"metadata": {"spatial_validation": {
        "rules": {"collision": {"severity": "warning"}},
    }}}).status_code == 200
    overridden = report(g, config={"rules": {"collision": {"severity": "info"}}})
    assert entities(findings(overridden, "objects_touch")[0]) == {b, c}
    assert findings(overridden, "objects_overlap")[0]["id"] == overlap["id"]
    assert overridden["summary"]["info"] == 2 and overridden["summary"]["blocking"] == 0

    included = report(g, config={"collision": {"include_hidden": True, "ignore_ancestor_pairs": False}})
    actual = [entities(finding) for finding in findings(included, "objects_overlap")]
    assert {b, hidden} in actual and {d, child} in actual

    response = g.client.post(g.path + "/relationships", json={
        "source_object_id": b, "target_object_id": a, "relationship_type": "allows_overlap",
    })
    assert response.status_code == 201, response.text
    assert not findings(report(g), "objects_overlap")
    result = report(g, config={"collision": {"allowed_pairs": [pair(b, c), pair(c, b)]}})
    assert not findings(result, "objects_touch")
    assert report(g, config={"rules": {"collision": {"enabled": False}}})["status"] == "valid"
    filtered = report(g, category="collision", severity="warning", offset=1, limit=1)
    assert filtered["filtered_total"] == 1 and filtered["summary"]["total"] == 1 and filtered["findings"] == []


def test_venue_and_zone_containment_uses_transformed_shapes(graph_client):
    g, _, _ = graph_client
    main = g.zone("Dining", transform=transform(10, 10), dimensions={"width_m": 6, "depth_m": 6, "height_m": 1})
    nested = g.zone("Overflow", parent_zone_id=main, transform=transform(3, 0, 0), dimensions=DIMS)
    peer = g.zone("Service", transform=transform(12, 10), dimensions=DIMS)
    unmeasured = g.zone("Unmeasured")
    proper = place(g, "Inside", 9, 9, zone_id=main)
    spill = place(g, "Rotated", 29.2, 6, rotation=45)
    outside = place(g, "Outside", 34, 12)
    tall = place(g, "Ceiling", 12, 13, z=7.8)
    unknown = place(g, "Unmeasured assignment", 6, 12, zone_id=unmeasured)
    group = str(uuid4())
    g.change({"kind": "create_group", "id": group, "group": {"name": "Assembly", "zone_id": main,
              "transform": transform(12, 10, rotation=90, scale={"x": 2, "y": 1, "z": 1})}})
    inherited = place(g, "Inherited zone and transform", 2, 0, z=0, parent_object_id=group)

    result = report(g, config={"exclusive_zone_pairs": [pair(main, peer), pair(peer, main)]})
    boundary = {next(iter(entities(f))): f for f in findings(result, "venue_boundary_exceeded")}
    assert set(boundary) == {spill, outside, tall}
    assert boundary[spill]["metadata"]["classification"] == "partial"
    assert boundary[outside]["metadata"]["classification"] == "outside"
    assert boundary[tall]["metadata"]["excursions_m"]["max_z"] == pytest.approx(0.3)
    assert {frozenset(entities(f)) for f in findings(result, "outside_assigned_zone")} == {
        frozenset((inherited, main)), frozenset((nested, main)),
    }
    assert not any(proper in entities(f) for f in result["findings"])
    unavailable, = findings(result, "zone_containment_unavailable")
    assert entities(unavailable) == {unknown, unmeasured}
    exclusive, = findings(result, "exclusive_zones_overlap")
    assert entities(exclusive) == {main, peer} and exclusive["measurement"]["measured"] == pytest.approx(4)
    assert not result["complete"] and result["status"] == "invalid"
    assert spill in {next(iter(entities(f))) for f in findings(
        report(g, config={"require_zone_assignment": True}), "zone_assignment_required",
    )}


def test_clearance_configuration_surface_distance_and_deduplication(graph_client):
    g, _, _ = graph_client
    zone = g.zone("Exhibits", transform=transform(12, 10), dimensions={"width_m": 8, "depth_m": 6, "height_m": 1})
    rotated = place(g, "Exhibit", 10, 10, rotation=90, category="booth", zone_id=zone,
                    dimensions={"width_m": 2, "depth_m": 1, "height_m": 1})
    path = place(g, "Walkway proxy", 12.5, 10, category="walkway")
    first = {"zone_id": zone, "category": "booth"}
    other = {"object_id": path}
    required = [threshold("minimum", first, other, 1.25), threshold("optional", other, first, 1.1),
                threshold("exact", first, other, 1), threshold("sufficient", first, other, 0.9)]
    payload = report(g, category="clearance", config={"clearances": required, "rules": {"clearance": {"severity": "error"}}})
    measured = {finding["metadata"]["requirement_id"]: finding for finding in findings(payload, "clearance_below_minimum")}
    assert set(measured) == {"minimum", "optional"}
    first_violation = measured["minimum"]
    assert entities(first_violation) == {rotated, path} and first_violation["zone_ids"] == [zone]
    assert first_violation["blocking"] and payload["complete"]
    assert first_violation["measurement"] == {"measured": 1.0, "expected": 1.25, "shortfall": 0.25, "unit": "m"}
    assert first_violation["location"]["maximum_m"][0] == pytest.approx(13.5)
    assert payload["summary"]["blocking"] == 2 and payload["filtered_total"] == 2
    reversed_payload = report(g, config={"clearances": [threshold("minimum", other, first, 1.25)]})
    assert findings(reversed_payload, "clearance_below_minimum")[0]["id"] == first_violation["id"]
    unknown_id = str(uuid4())
    missing = report(g, config={"clearances": [threshold("missing", {"object_id": unknown_id}, first, 1)]})
    assert missing["status"] == "incomplete" and unknown_id not in str(missing)


def test_clearance_to_access_zone_measures_in_xy(graph_client):
    g, _, _ = graph_client
    access = g.zone("Access", transform=transform(10, 10), dimensions=DIMS)
    crossing = place(g, "High fixture", 10, 10, z=6, category="fixture")
    near = place(g, "Adjacent fixture", 12.5, 10, category="fixture")
    place(g, "Distant fixture", 20, 10, category="fixture")
    requirement = threshold("access", {"category": "fixture"}, {"zone_footprint_id": access}, 0.75)
    result = report(g, config={"clearances": [requirement]})
    observed = {next(iter(entities(f) - {access})): f["measurement"]["measured"]
                for f in findings(result, "clearance_below_minimum")}
    assert observed == {crossing: 0, near: 0.5}
    assert result["complete"] and result["summary"]["warnings"] == 2


def test_vertical_partial_overlap_is_not_full_containment(graph_client):
    g, _, _ = graph_client
    first = place(g, "Lower box", 10, 10)
    second = place(g, "Upper box", 10, 10, z=2.5)
    finding, = findings(report(g), "objects_overlap")
    assert entities(finding) == {first, second}
    assert finding["metadata"]["classification"] == "partial_overlap"
    assert finding["measurement"]["measured"] == pytest.approx(2)
    updated = g.client.patch(f"/api/v1/spatial/objects/{first}", json={"transform": {"rotation_deg": {"x": 30}}})
    assert updated.status_code == 200, updated.text
    tilted, = findings(report(g), "objects_overlap")
    assert entities(tilted) == {first, second} and "separating axis" in tilted["metadata"]["method"]


def test_malformed_geometry_transforms_and_graph_are_reported_without_aborting(graph_client, db_session, monkeypatch):
    g, _, _ = graph_client
    valid = place(g, "Valid", 20, 12)
    polygon = place(g, "Crossed polygon", 8, 8, properties={"geometry": {"type": "polygon", "vertices": [
        {"x_m": -1, "y_m": -1}, {"x_m": 1, "y_m": 1}, {"x_m": -1, "y_m": 1}, {"x_m": 1, "y_m": -1},
    ]}})
    degenerate = place(g, "Degenerate line", 6, 8, properties={"geometry": {
        "type": "line", "start": {"x_m": 0, "y_m": 0}, "end": {"x_m": 0, "y_m": 0},
    }})
    unsupported = place(g, "Unsupported", 4, 8, properties={"geometry": {"type": "imported_mesh"}})
    bad_dimensions = [place(g, f"Invalid dimension {value}", 4, 4) for value in (-1, 0, "NaN")]
    bad_position = place(g, "Nonfinite coordinates", 3, 3)
    bad_rotation = place(g, "Nonfinite rotation", 3, 5)
    bad_scale = place(g, "Collapsed transform", 3, 6)
    orphan = place(g, "Orphan", 4, 7)
    a = place(g, "Cycle A", 10, 10)
    b = place(g, "Cycle B", 10, 10)
    group = str(uuid4())
    g.change({"kind": "create_group", "id": group, "group": {"name": "Invalid group"}})
    huge = place(g, "Overflowed world frame", 0, 0, z=0, parent_object_id=group)

    # Simulate legacy rows that predate graph constraints without weakening the test database.
    from app.services import spatial_graph
    original = spatial_graph._scene_rows
    legacy = {UUID(key): value for key, value in {
        **{oid: {"width_m": Decimal(str(value))} for oid, value in zip(bad_dimensions, (-1, 0, "NaN"))},
        bad_position: {"position_x_m": Decimal("Infinity")},
        bad_rotation: {"rotation_z_deg": Decimal("NaN")},
        bad_scale: {"scale_y": Decimal("0")},
        orphan: {"parent_object_id": uuid4(), "layer_id": uuid4()},
        a: {"parent_object_id": UUID(b)}, b: {"parent_object_id": UUID(a)},
        group: {"width_m": Decimal("2"), "scale_x": Decimal("1e200")},
        huge: {"scale_x": Decimal("1e200")},
    }.items()}

    def legacy_rows(db, model, scene_id):
        rows = original(db, model, scene_id)
        if model is SpatialObject:
            for row in rows:
                for column, val in legacy.get(row.id, {}).items():
                    setattr(row, column, val)
        return rows

    monkeypatch.setattr(spatial_graph, "_scene_rows", legacy_rows)
    result = report(g)
    assert result["status"] == "invalid" and not result["complete"]
    assert {next(iter(entities(f))) for f in findings(result, "invalid_dimensions")} == set(bad_dimensions)
    assert {next(iter(entities(f))) for f in findings(result, "invalid_geometry")} == {polygon, degenerate}
    assert entities(findings(result, "unsupported_geometry")[0]) == {unsupported}
    assert {next(iter(entities(f))) for f in findings(result, "invalid_transform")} == {bad_position, bad_rotation, bad_scale}
    assert entities(findings(result, "invalid_world_transform")[0]) == {huge}
    assert {next(iter(entities(f))) for f in findings(result, "parent_cycle")} == {a, b}
    assert entities(findings(result, "missing_parent")[0]) == {orphan}
    assert findings(result, "missing_reference")[0]["metadata"]["field"] == "layer_id"
    assert entities(findings(result, "invalid_layer_order")[0]) == {orphan}
    assert entities(findings(result, "invalid_group_structure")[0]) == {group}
    assert not any(valid in entities(f) for f in result["findings"])


def test_unavailable_venue_and_unsupported_measurement(graph_client, db_session):
    g, _, _ = graph_client
    first = place(g, "Ellipse", 8, 8, properties={"geometry": {"type": "circle"}})
    response = g.client.patch(f"/api/v1/spatial/objects/{first}", json={"transform": {"scale": {"x": 2}}})
    assert response.status_code == 200, response.text
    second = place(g, "Nearby", 9, 8)
    req = threshold("tilt", {"object_id": first}, {"object_id": second}, 0.5)
    payload = report(g, config={"clearances": [req]})
    assert payload["status"] == "incomplete"
    assert entities(findings(payload, "plan_projection_unsupported")[0]) == {first}
    assert entities(findings(payload, "collision_unavailable")[0]) == {first, second}
    assert entities(findings(payload, "clearance_unavailable")[0]) == {first, second}

    scene = db_session.get(SpatialScene, UUID(g.scene))
    scene.width_m = Decimal("NaN")
    db_session.commit()
    missing = report(g)
    assert len(findings(missing, "venue_boundary_unavailable")) == 1
    assert not findings(missing, "venue_boundary_exceeded") and not missing["complete"]


def test_consistency_routes_shared_pair_diagnostics(graph_client, monkeypatch):
    g, _, _ = graph_client
    object_id = place(g, "Chair", 8, 8)
    original = spatial_sync.project_plan

    def mismatched_plan(*args, **kwargs):
        plan, issues = original(*args, **kwargs)
        plan["nodes"][0]["footprint"]["bounds"]["max_x_m"] += 1
        return plan, issues

    monkeypatch.setattr(spatial_sync, "project_plan", mismatched_plan)
    result = report(g)
    finding, = findings(result, "projection_bounds_mismatch")
    assert entities(finding) == {object_id} and finding["metadata"]["representation"] == "pair"
    assert result["status"] == "invalid" and not result["complete"]


def test_library_integrity_pins_defaults_and_keeps_tenants_isolated(library, db_session, identity_factory):
    lib, owner, project = library
    g = lib.g
    asset = Asset(organization_id=owner["organization"].id, project_id=project.id,
                  original_filename="model.png", display_name="Model", mime_type="image/png",
                  file_extension="png", storage_key=uuid4().hex, file_size=12)
    db_session.add(asset)
    db_session.commit()
    definition = lib.create(project_id=str(project.id), assets=[{"asset_id": str(asset.id), "role": "model"}])
    obj = lib.place(definition, transform=transform(8, 8))["object"]["id"]
    assert report(g)["status"] == "valid"
    rejected = g.client.patch(f"/api/v1/spatial/objects/{obj}", json={"properties": {"geometry": {"type": "circle", "radius_m": 30}}})
    assert rejected.status_code == 422

    catalog = db_session.get(ObjectDefinition, UUID(definition["id"]))
    catalog.geometry = {"type": "box", "radius_m": 500}
    asset.status = "deleted"
    db_session.commit()
    bad = report(g)
    assert {finding["code"] for finding in bad["findings"]} == {
        "definition_invalid_spatial_defaults", "definition_asset_unavailable", "instance_asset_unavailable",
    }
    assert all(entities(finding) == {obj} and finding["blocking"] for finding in bad["findings"])

    catalog.status = "archived"
    catalog.version += 1
    asset.status = "active"
    db_session.commit()
    pinned = report(g)
    assert [finding["code"] for finding in pinned["findings"]] == ["definition_archived"]
    assert pinned["findings"][0]["severity"] == "info" and pinned["complete"]
    other = identity_factory()
    # Bypass the API's scope check while retaining the valid foreign key.
    catalog.organization_id = other["organization"].id
    db_session.commit()
    isolated = report(g)
    assert entities(findings(isolated, "definition_unavailable")[0]) == {obj}
    assert definition["id"] not in str(isolated) and "Main stage" not in isolated["findings"][0]["explanation"]
    assert not isolated["complete"]


def test_sparse_scene_filters_most_pairs_before_exact_geometry(graph_client, db_session, monkeypatch):
    g, _, project = graph_client
    wide = create_scene(g.client, project.id, dimensions={"width_m": 65, "depth_m": 45, "height_m": 8})
    g = type(g)(g.client, wide["id"])
    rows = [
        SpatialObject(scene_id=UUID(g.scene), name=f"Seat {i}", object_type="chair", category="seating",
                      width_m=2, depth_m=2, height_m=1, position_x_m=4 if i == 1 else 3 + 3 * (i % 20),
                      position_y_m=3 + 3 * (i // 20), position_z_m=2, order_index=i)
        for i in range(240)
    ]
    db_session.add_all(rows)
    db_session.commit()
    counts = {"collision": 0, "clearance": 0}
    original_intersection = spatial_validation_rules.object_intersection
    original_clearance = spatial_validation_rules.clearance_result

    def counted_collision(*args):
        counts["collision"] += 1
        return original_intersection(*args)

    def counted_clearance(*args):
        counts["clearance"] += 1
        return original_clearance(*args)

    monkeypatch.setattr(spatial_validation_rules, "object_intersection", counted_collision)
    monkeypatch.setattr(spatial_validation_rules, "clearance_result", counted_clearance)
    seating = {"category": "seating"}
    result = report(g, config={"clearances": [threshold("seat-gaps", seating, seating, 0.5)]})
    assert result["complete"] and result["summary"]["total"] == 2
    assert counts == {"collision": 1, "clearance": 1}
    assert result["duration_ms"] > 0
