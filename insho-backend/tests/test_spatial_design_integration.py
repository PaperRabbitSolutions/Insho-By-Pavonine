"""Phase 8 sources, scoped recommendation reviews, and actual canonical application."""

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.models.asset import Asset
from app.models.design_artifact import DesignArtifact
from app.models.event import Event
from app.models.object_library import ObjectDefinition
from app.services import spatial_design
from app.services.ai_orchestration import AIProviderError
from test_object_library import LibraryClient
from test_phase8_design_intelligence import DesignMockAdapter, MOCK_OUTPUTS, configure_mock_provider
from test_phase9_spatial import create_scene, make_project
from test_spatial_graph import SceneGraphClient, graph_client, ref, transform


DIMS = {"width_m": 4, "depth_m": 3, "height_m": 1}


class Designer:
    def __init__(self, g, owner, project, monkeypatch):
        self.g, self.owner, self.project = g, owner, project
        self.http = g.client
        self.path = g.path + "/design-integration"
        self.artifacts = f"/api/v1/design-intelligence/projects/{project.id}"
        self.lib = LibraryClient(self.http, owner["organization"].id, g.scene)
        self.monkeypatch = monkeypatch
        configure_mock_provider(monkeypatch, DesignMockAdapter())

    def generate(self, module, expected=201, **body):
        result = self.http.post(f"{self.artifacts}/{module}/generate", json=body)
        assert result.status_code == expected, result.text
        return result.json()

    def read(self, suffix="", expected=200, **params):
        response = self.http.get(self.path + suffix, params=params)
        assert response.status_code == expected, response.text
        return response.json()

    def rec(self, source="plan"):
        page = self.read("/recommendations", source=source)
        assert page["total"] == 1, page
        return page["items"][0], {k: page[key] for k, key in (("base_revision", "scene_revision"), ("context_version", "context_version"))}

    def act(self, rec, operation, expected=200, **body):
        response = self.http.post(f"{self.path}/recommendations/{rec['id']}/{operation}", json=body)
        assert response.status_code == expected, response.text
        return response.json()

    def element(self):
        zone = self.g.zone("Entry", transform=transform(8, 8), dimensions={"width_m": 8, "depth_m": 8, "height_m": 1})
        artifact = self.generate("elements")
        definition = self.lib.create(name=MOCK_OUTPUTS["elements"]["elements"][0]["name"], category="floral", dimensions=DIMS)
        return zone, artifact, definition


@pytest.fixture
def designer(graph_client, monkeypatch):
    return Designer(*graph_client, monkeypatch)


def entries(payload):
    return {m["module"]: m for m in payload["modules"]}


def zone_action(x=8, y=8):
    return {"kind": "create_zone", "dimensions": DIMS, "transform": transform(x, y)}


def object_action(definition, zone, x=8, y=8):
    return {"kind": "place_object", "definition_id": definition["id"], "definition_version": definition["version"],
            "zone_id": zone, "transform": transform(x, y)}


def test_context_normalizes_all_sources_and_tracks_partial_failure(designer, db_session):
    d = designer
    empty = d.read()
    assert empty["status"] == "unavailable" and {m["status"] for m in empty["modules"]} == {"missing"}
    assert d.read("/recommendations")["items"] == []
    untrusted = d.http.post("/api/v1/ai/execute", json={
        "project_id": str(d.project.id), "task": "design_mood", "feature": "insho_mood", "input": "Test source marker",
        "metadata": {"provenance": {"source": "design_intelligence", "event_scope": str(UUID(int=0))}},
    })
    assert untrusted.status_code == 200, untrusted.text
    assert entries(d.read())["mood"]["status"] == "missing"

    class FailedProvider:
        async def execute(self, *args):
            raise AIProviderError("provider_unavailable", "Provider unavailable", status_code=503)

    configure_mock_provider(d.monkeypatch, FailedProvider())
    failed = d.generate("mood", expected=503)["detail"]
    mood = entries(d.read())["mood"]
    assert mood["status"] == "failed" and mood["latest_execution_id"] == failed["execution_id"]
    assert mood["artifact"] is None

    configure_mock_provider(d.monkeypatch, DesignMockAdapter())
    outputs = {module: d.generate(module) for module in MOCK_OUTPUTS}
    bundle = d.read()
    assert bundle == d.read() and bundle["status"] == "available"
    for module, entry in entries(bundle).items():
        assert entry["status"] == "available" and entry["artifact"]["artifact_id"] == outputs[module]["id"]
        assert entry["artifact"]["execution_id"] == outputs[module]["ai_execution_id"]
        assert all(entry["normalized"][key] == val for key, val in outputs[module]["content"].items())
    assert entries(bundle)["vision"]["normalized"]["views"][0]["image_generation_status"] == "not_requested"
    before = d.g.read()
    d.generate("mood")
    refreshed = d.read()
    assert refreshed["context_version"] != bundle["context_version"]
    assert entries(refreshed)["style"]["status"] == "stale"
    assert d.g.read() == before

    artifact = db_session.get(DesignArtifact, UUID(outputs["plan"]["id"]))
    artifact.content = {"summary": "Saved partial plan.", "zones": [{"name": "Too little information"}], "unsupported": "field"}
    db_session.commit()
    partial = entries(d.read())["plan"]
    assert partial["status"] == "partial" and partial["normalized"] == {"summary": "Saved partial plan."}
    assert "invalid_field:unsupported" in partial["issues"]


def test_plan_zone_resolution_measured_preview_and_retry(designer):
    d = designer
    base = d.generate("plan")
    rec, snap = d.rec()
    assert rec["status"] == "proposed" and rec["source"]["artifact_id"] == base["id"]
    assert rec["intent"]["zone"]["dimensions"] is None
    assert rec["intent"]["adjacencies"][0]["to_zone_ids"] == []
    action = zone_action()
    incomplete = deepcopy(action)
    del incomplete["transform"]["position"]["z"]
    d.act(rec, "validate", 422, **snap, action=incomplete)
    d.act(rec, "validate", 422, **snap, action={"kind": "delete_zone"})
    outside = d.act(rec, "validate", **snap, action=zone_action(x=29))
    assert outside["status"] == "blocked" and outside["review_id"] is None
    assert any(f["code"] == "venue_boundary_exceeded" and f["entities"][0]["type"] == "zone"
               for f in outside["validation"]["findings"])

    before = d.g.read()
    preview = d.act(rec, "validate", **snap, action=action)
    assert preview["status"] == "validated" and preview["validation"]["complete"]
    assert d.g.read() == before
    reviewed = d.read(f"/recommendations/{rec['id']}")
    assert reviewed["status"] == "validated" and reviewed["review"]["review_id"] == preview["review_id"]
    result = d.act(rec, "apply", review_id=preview["review_id"])
    assert result["entity_type"] == "zone" and result["entity_id"] == preview["proposed_entity_id"]
    assert result["scene_revision"] == snap["base_revision"] + 1
    saved = d.http.get(f"/api/v1/spatial/zones/{result['entity_id']}").json()
    assert saved["name"] == "Dining" and saved["metadata"]["design_intelligence"]["source"]["artifact_id"] == base["id"]
    assert d.act(rec, "apply", review_id=preview["review_id"]) == result
    assert len(d.g.read()["nodes"]) == 1
    d.act(rec, "reject", 409, **snap)

    d.generate("plan")
    page = d.read("/recommendations", status="resolved")
    resolved, = page["items"]
    assert resolved["zone_matches"][0]["id"] == result["entity_id"]
    d.act(resolved, "validate", 409, base_revision=page["scene_revision"], context_version=page["context_version"],
          action=action)


def test_element_catalog_ranking_review_and_warning_approval(designer):
    d = designer
    zone = d.g.zone("Entry", transform=transform(8, 8), dimensions={"width_m": 12, "depth_m": 12, "height_m": 2})
    blocker = d.g.object("Designer table", zone_id=zone, dimensions=DIMS, transform=transform(8, 8),
                         properties={"designer_note": "leave as is"})
    d.generate("mood")
    d.generate("style")
    source = d.generate("elements")
    name = MOCK_OUTPUTS["elements"]["elements"][0]["name"]
    true_match = d.lib.create(name=name, category="floral", dimensions=DIMS)
    weak = d.lib.create(name="Arrangement", category="floral", dimensions=DIMS, tags=["linen"])
    d.lib.create(name="Unrelated", category="furniture", object_type="chair")
    recommendation, snap = d.rec("elements")
    assert recommendation["source"]["artifact_id"] == source["id"] and recommendation["source"]["element_id"]
    candidates = recommendation["candidates"]
    assert [m["definition"]["id"] for m in candidates] == [true_match["id"], weak["id"]]
    assert candidates[0]["reasons"] == ["name", "category"] and "style_tags" in candidates[1]["reasons"]
    assert d.read("/recommendations", source="elements")["items"][0] == recommendation
    missing = object_action(true_match, None)
    d.act(recommendation, "validate", 422, **snap, action=missing)
    preview = d.act(recommendation, "validate", **snap, action=object_action(true_match, zone))
    assert preview["status"] == "validated_with_warnings" and preview["requires_warning_acceptance"]
    overlaps = [f for f in preview["validation"]["findings"] if f["code"] == "objects_overlap"]
    assert {r["id"] for r in overlaps[0]["entities"]} == {blocker, preview["proposed_entity_id"]}
    denied = d.act(recommendation, "apply", 409, review_id=preview["review_id"])
    assert denied["detail"]["code"] == "warning_acceptance_required"
    assert d.g.read()["scene_revision"] == snap["base_revision"]
    result = d.act(recommendation, "apply", review_id=preview["review_id"], accept_warnings=True)
    created = d.http.get(f"/api/v1/spatial/objects/{result['entity_id']}").json()
    assert created["library_definition_id"] == true_match["id"]
    assert created["properties"]["instance_metadata"]["design_intelligence"]["recommendation_id"] == recommendation["id"]
    assert d.http.get(f"/api/v1/spatial/objects/{blocker}").json()["properties"]["designer_note"] == "leave as is"
    assert d.lib.get(true_match["id"])["version"] == 1
    synced = d.http.get(d.g.path + "/synchronization").json()
    assert synced["scene_revision"] == result["scene_revision"]
    assert result["entity_id"] in {node["id"] for node in synced["runtime"]["nodes"]}


def test_rejected_superseded_and_stale_scene_reviews(designer):
    d = designer
    d.generate("plan")
    rec, snap = d.rec()
    preview = d.act(rec, "validate", **snap, action=zone_action())
    d.g.object("New designer object", transform=transform(15, 15), dimensions=DIMS)
    stale = d.act(rec, "apply", 409, review_id=preview["review_id"])
    assert stale["detail"]["code"] == "scene_revision_conflict"
    assert d.read(f"/recommendations/{rec['id']}")["status"] == "stale"
    reread, new_snap = d.rec()
    assert reread["id"] == rec["id"]
    rejected = d.act(rec, "reject", **new_snap, reason="Existing plan uses a different dining concept.")
    assert rejected["status"] == "rejected" and rejected["rejection_reason"].startswith("Existing plan")
    assert d.act(rec, "reject", **new_snap) == rejected
    d.act(rec, "validate", 409, **new_snap, action=zone_action())
    d.generate("plan")
    page = d.read("/recommendations", limit=1, offset=1)
    assert page["total"] == 2 and len(page["items"]) == 1
    assert d.read(f"/recommendations/{rec['id']}")["status"] == "rejected"


def test_live_catalog_version_locked_zone_and_invalid_definition(designer, db_session):
    d = designer
    zone, _, definition = d.element()
    rec, snap = d.rec("elements")
    d.g.change({"kind": "set_locked", "nodes": [ref(zone, "zone")], "locked": True})
    rec, snap = d.rec("elements")
    d.act(rec, "validate", 409, **snap, action=object_action(definition, zone))
    d.g.change({"kind": "set_locked", "nodes": [ref(zone, "zone")], "locked": False})
    rec, snap = d.rec("elements")
    valid = d.act(rec, "validate", **snap, action=object_action(definition, zone))
    updated = d.lib.patch(definition["id"], definition["version"], tags=["floral"])
    assert d.read(f"/recommendations/{rec['id']}")["status"] == "stale"
    stale = d.act(rec, "apply", 409, review_id=valid["review_id"])
    assert stale["detail"]["code"] == "definition_version_conflict"
    catalog = db_session.get(ObjectDefinition, UUID(updated["id"]))
    catalog.geometry = {"type": "polygon", "vertices": [{"x_m": 0, "y_m": 0}] * 3}
    db_session.commit()
    invalid = d.act(rec, "validate", 422, **snap, action=object_action(updated, zone))
    assert invalid["detail"]["issues"][0]["code"] == "invalid_geometry"
    assert d.g.read()["scene_revision"] == snap["base_revision"]


def test_blocking_clearance_and_unresolved_element(designer):
    d = designer
    policy = {
        "rules": {"clearance": {"severity": "error"}},
        "clearances": [{"id": "floral-service", "minimum_m": 0.75,
                        "subject": {"category": "floral"}, "against": {"category": "furniture"}}],
    }
    saved = d.http.patch(f"/api/v1/projects/{d.project.id}", json={"constraints": {"spatial_validation": policy}})
    assert saved.status_code == 200, saved.text
    d.generate("elements")
    unresolved, _ = d.rec("elements")
    assert unresolved["status"] == "needs_resolution" and unresolved["candidates"] == []
    assert set(unresolved["prerequisites"]) == {"choose_target_zone", "resolve_catalog_definition"}
    zone, _, definition = d.element()
    d.g.object("Service table", category="furniture", transform=transform(12, 8), dimensions=DIMS)
    rec, snap = d.rec("elements")
    preview = d.act(rec, "validate", **snap, action=object_action(definition, zone))
    assert preview["status"] == "blocked" and not preview["review_id"]
    violation, = [f for f in preview["validation"]["findings"] if f["code"] == "clearance_below_minimum"]
    assert violation["blocking"] and violation["measurement"]["measured"] == pytest.approx(0)
    assert d.g.read()["scene_revision"] == snap["base_revision"]
    blocked = d.act(rec, "apply", 409, review_id=str(uuid4()))
    assert blocked["detail"]["code"] == "review_required"


def test_artifact_refresh_and_atomic_application_failure(designer, monkeypatch):
    d = designer
    d.generate("plan")
    rec, snap = d.rec()
    old = d.act(rec, "validate", **snap, action=zone_action())
    d.generate("plan")
    denied = d.act(rec, "apply", 409, review_id=old["review_id"])
    assert denied["detail"]["code"] == "design_context_changed"
    assert d.read(f"/recommendations/{rec['id']}")["status"] == "stale"

    page = d.read("/recommendations", status="proposed")
    rec, = page["items"]
    snap = {"base_revision": page["scene_revision"], "context_version": page["context_version"]}
    review = d.act(rec, "validate", **snap, action=zone_action())
    original = spatial_design._commit

    def fail_commit(db):
        raise HTTPException(409, detail="Simulated persistence failure")

    monkeypatch.setattr(spatial_design, "_commit", fail_commit)
    d.act(rec, "apply", 409, review_id=review["review_id"])
    assert d.g.read()["scene_revision"] == snap["base_revision"] and not d.g.read()["nodes"]
    assert d.read(f"/recommendations/{rec['id']}")["status"] == "validated"
    monkeypatch.setattr(spatial_design, "_commit", original)
    assert d.act(rec, "apply", review_id=review["review_id"])["entity_id"] == review["proposed_entity_id"]


def test_event_context_reference_and_catalog_tenant_isolation(designer, db_session, identity_factory):
    d = designer
    event = Event(organization_id=d.owner["organization"].id, project_id=d.project.id, name="Event")
    parallel = Event(organization_id=d.owner["organization"].id, project_id=d.project.id, name="Separate event")
    db_session.add_all([event, parallel])
    db_session.commit()
    base = d.generate("mood")
    scene = create_scene(d.http, d.project.id, event_id=str(event.id))
    sibling = create_scene(d.http, d.project.id, event_id=str(parallel.id))
    event_scene = Designer(SceneGraphClient(d.http, scene["id"]), d.owner, d.project, d.monkeypatch)
    asset = Asset(organization_id=d.owner["organization"].id, project_id=d.project.id, event_id=event.id,
                  original_filename="reference.png", display_name="Event reference", mime_type="image/png",
                  file_extension="png", storage_key=uuid4().hex, file_size=12)
    db_session.add(asset)
    db_session.commit()
    vision = event_scene.generate("vision", event_id=str(event.id), reference_asset_ids=[str(asset.id)])
    event_data = entries(event_scene.read())
    assert event_data["mood"]["artifact"]["artifact_id"] == base["id"]
    assert event_data["vision"]["artifact"]["artifact_id"] == vision["id"]
    assert event_data["vision"]["references"][0]["asset_id"] == str(asset.id)
    sibling_context = d.http.get(f"/api/v1/spatial/scenes/{sibling['id']}/design-integration").json()
    assert entries(sibling_context)["vision"]["status"] == "missing"
    asset.event_id = parallel.id
    db_session.commit()
    vision_state = entries(event_scene.read())["vision"]
    assert vision_state["status"] == "stale" and vision_state["references"] == []
    assert vision_state["unavailable_reference_count"] == 1 and str(asset.id) not in str(vision_state)

    zone, _, supported = d.element()
    rec, snap = d.rec("elements")
    preview = d.act(rec, "validate", **snap, action=object_action(supported, zone))
    owner_auth = d.http.headers["Authorization"]
    other = identity_factory()
    other_project = make_project(db_session, other)
    d.http.headers["Authorization"] = "Bearer " + other["token"]
    other_scene = create_scene(d.http, other_project.id)
    other_lib = LibraryClient(d.http, other["organization"].id, other_scene["id"])
    foreign = other_lib.create(name=supported["name"], category="floral")
    assert d.read(expected=404)["detail"] == "Resource not found"
    d.read(f"/recommendations/{rec['id']}", expected=404)
    d.act(rec, "validate", 404, **snap, action=object_action(supported, zone))
    d.act(rec, "apply", 404, review_id=preview["review_id"])
    d.act(rec, "reject", 404, **snap)
    assert d.generate("mood", expected=404)["detail"] == "Resource not found"

    d.http.headers["Authorization"] = owner_auth
    assert foreign["id"] not in str(d.rec("elements")[0]["candidates"])
    d.act(rec, "validate", 404, **snap, action=object_action(foreign, zone))
    d.act(rec, "validate", 404, **snap, action=object_action(supported, str(uuid4())))
    member = identity_factory(role="member", organization=d.owner["organization"])
    d.http.headers["Authorization"] = "Bearer " + member["token"]
    d.read()
    d.act(rec, "apply", 403, review_id=preview["review_id"])
    del d.http.headers["Authorization"]
    d.read(expected=401)
    d.http.headers["Authorization"] = owner_auth


def test_many_elements_reuse_context_and_batch_catalog_queries(designer, monkeypatch):
    import json

    from sqlalchemy import event

    from app.services.ai_orchestration import ProviderResult
    from app.services.context import ContextService
    from conftest import test_engine

    class ManyElements:
        async def execute(self, model, request, timeout):
            element = MOCK_OUTPUTS["elements"]["elements"][0]
            return ProviderResult(output=json.dumps({"elements": [
                {**element, "name": f"Arrangement {i}"} for i in range(30)
            ]}))

    d = designer
    configure_mock_provider(monkeypatch, ManyElements())
    d.generate("elements")
    d.lib.create(name="Arrangement 1", category="floral")
    d.lib.create(name="Floral backdrop", category="floral")
    context_calls = 0
    catalog_queries = 0
    original = ContextService.get_project_context

    def counted_context(*args):
        nonlocal context_calls
        context_calls += 1
        return original(*args)

    def count_catalog(conn, cursor, statement, params, execution_context, executemany):
        nonlocal catalog_queries
        if "row_number()" in statement and "spatial_object_definitions" in statement:
            catalog_queries += 1

    monkeypatch.setattr(ContextService, "get_project_context", staticmethod(counted_context))
    event.listen(test_engine, "before_cursor_execute", count_catalog)
    try:
        page = d.read("/recommendations", source="elements")
    finally:
        event.remove(test_engine, "before_cursor_execute", count_catalog)
    assert page["total"] == 30
    assert context_calls == 1 and catalog_queries == 3
    assert all(item["candidates"] for item in page["items"])


def test_vision_links_only_matching_published_element_labels(designer, monkeypatch):
    import json

    from app.services.ai_orchestration import ProviderResult

    class NamedElement(DesignMockAdapter):
        async def execute(self, model, request, timeout):
            if request.task != "design_elements":
                return await super().execute(model, request, timeout)
            return ProviderResult(output=json.dumps({"elements": [
                {**MOCK_OUTPUTS["elements"]["elements"][0], "name": "Entry floral"},
            ]}))

    d = designer
    configure_mock_provider(monkeypatch, NamedElement())
    d.generate("vision")
    d.generate("elements")
    vision = entries(d.read())["vision"]["normalized"]
    observation, = vision["element_links"]
    assert observation["category"] == "floral" and observation["basis"] == "exact_element_name"
    assert observation["element_id"] and "dimensions" not in observation and "confidence" not in observation


def test_unmeasured_zone_blocks_incomplete_preview(designer):
    d = designer
    zone = d.g.zone("Entry")
    d.generate("elements")
    definition = d.lib.create(name="Floral setup", category="floral", dimensions=DIMS)
    rec, snap = d.rec("elements")
    response = d.act(rec, "validate", **snap, action=object_action(definition, zone))
    assert response["status"] == "blocked" and not response["validation"]["complete"]
    assert any(f["code"] == "zone_containment_unavailable" for f in response["validation"]["findings"])
    assert {node["type"] for node in d.g.read()["nodes"]} == {"zone"}


def test_concurrent_apply_returns_one_canonical_receipt(designer, db_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi.testclient import TestClient

    from app.db.database import get_db
    from app.main import app
    from conftest import TestingSessionLocal

    d = designer
    d.generate("plan")
    rec, snap = d.rec()
    preview = d.act(rec, "validate", **snap, action=zone_action())
    auth = d.http.headers["Authorization"]
    db_session.rollback()
    original = app.dependency_overrides[get_db]
    barrier = Barrier(2)

    def isolated():
        with TestingSessionLocal() as session:
            yield session

    def apply():
        with TestClient(app, headers={"Authorization": auth}) as client:
            barrier.wait(timeout=5)
            response = client.post(f"{d.path}/recommendations/{rec['id']}/apply",
                                   json={"review_id": preview["review_id"]})
            assert response.status_code == 200, response.text
            return response.json()

    app.dependency_overrides[get_db] = isolated
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            calls = [executor.submit(apply) for _ in range(2)]
            first, second = (call.result(timeout=10) for call in calls)
    finally:
        app.dependency_overrides[get_db] = original
    assert first == second and first["scene_revision"] == snap["base_revision"] + 1
    assert len(d.g.read()["nodes"]) == 1
