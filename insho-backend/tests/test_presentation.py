from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.models.ai_execution import AIExecution
from app.models.asset import Asset
from app.models.design_artifact import DesignArtifact, DesignArtifactAsset
from app.models.design_dna import DesignDNA
from app.models.event import Event
from app.models.presentation import Presentation, PresentationAsset
from app.models.project_memory_item import ProjectMemoryItem
from app.schemas.presentation import PresentationUpdate
from app.services import presentation
from app.services.asset_lifecycle import cleanup_deleted_assets
from app.services.presentation_validation import EMPHASIS, section_order
from conftest import TestingSessionLocal
from test_assets import auth, upload_jpeg
from test_phase9_spatial import create_object, create_scene, make_project
from test_production import production, request
from test_production_bom import generate, manual
from test_production_integration import technical_sheet, transition
from test_production_procurement import sourcing
from test_production_quantification import calculate, patch_object, quantity_request


def ref(kind, row, **fields):
    return {"kind": kind, "id": str(row["id"] if isinstance(row, dict) else row.id), **fields}


def sourced(*refs, **fields):
    return {"sources": list(refs), **fields}


def by_section(result):
    return {section["section"]: section for section in result["sections"]}


class PresentAPI:
    def __init__(self, client, owner, project, event):
        self.client, self.owner, self.project, self.event = client, owner, project, event
        self.path = f"/api/v1/projects/{project.id}/presentations"

    def call(self, method, suffix="", status=200, **kwargs):
        return request(self.client, method, self.path + suffix, status, **kwargs)

    def create(self, status=201, **fields):
        return self.call("POST", status=status, json={"title": "Client presentation", "client_context": "luxury",
                                                      "event_id": str(self.event.id), **fields})

    def revise(self, row, status=200, **fields):
        return self.call("PUT", "/" + row["id"], status, json={"expected_revision": row["revision"], **row["sheet"], **fields})

    def checkpoint(self, row, title="Client signoff", status=201):
        return self.call("POST", f"/{row['id']}/approvals", status, json={"revision": row["revision"], "title": title})

    def review(self, row, checkpoint, action="request", status=200, **fields):
        path = f"/{row['id']}/approvals/{checkpoint['id']}/{action}"
        return self.call("POST", path, status, json={"revision": row["revision"], **fields})


@pytest.fixture
def present(production, monkeypatch, tmp_path):
    api, owner, project, event = production
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "presentation-assets"))
    return PresentAPI(api.client, owner, project, event)


@pytest.fixture
def concept(present, db_session):
    dna = DesignDNA(project_id=present.project.id, palette={"primary": "Indigo"})
    db_session.add(dna)
    db_session.commit()
    asset_id = upload_jpeg(present.client, present.owner, str(present.project.id))["asset_id"]
    sections = {"concept_story": {"text": "An indigo arrival with a clear focal stage."},
                "design_dna": sourced(ref("design_dna", dna)), "moodboard": {"asset_ids": [asset_id]},
                "hero_visual": {"asset_ids": [asset_id]}}
    return dna, asset_id, sections


@pytest.mark.parametrize("context", sorted(EMPHASIS))
def test_client_context_orders_all_sections_once(context):
    order = section_order(context)
    assert order[:3] == list(EMPHASIS[context])
    assert len(order) == len(set(order)) == 12


def test_edit_link_unlink_validation_and_asset_retention(present, concept, db_session):
    _, asset_id, sections = concept
    first = present.create(purpose="concept", client_context="bride_family", sections={
        "led_visual_preview": {"applicability": "not_applicable"},
    })
    path = "/" + first["id"]
    assert first["revision"] == 1 and first["approval_status"] == "draft"
    assert first["project_id"] == str(present.project.id) and first["event_id"] == str(present.event.id)
    assert first["workspace_id"] == str(present.owner["workspace"].id)
    assert present.call("GET", path + "/validation") == first["validation"]
    incomplete = by_section(first["validation"])
    assert incomplete["concept_story"]["status"] == "missing" and incomplete["design_dna"]["status"] == "unavailable"
    assert incomplete["led_visual_preview"]["status"] == "not_applicable"
    blocked = present.checkpoint(first)
    assert present.review(first, blocked, status=409)["detail"]["code"] == "presentation_incomplete"
    present.revise(first, status=422, sections={"design_dna": {"applicability": "not_applicable"}})
    present.revise(first, status=422, sections={"concept_story": {"text": "  "}})
    second = present.revise(first, sections=sections)
    assert second["revision"] == 2 and second["validation"]["complete"]
    assert by_section(second)["spatial_plan"]["requirement"] == "optional"
    assert present.revise(second)["revision"] == 2
    assert present.revise(first, status=409, sections=sections)["detail"]["current_revision"] == 2
    assert present.call("GET", path + "/sections") == second["sections"]
    assert present.call("GET", params={"event_id": first["event_id"], "limit": 1})[0]["revision"] == 2
    third = present.revise(second, sections={**sections, "hero_visual": {}})
    assert by_section(third)["hero_visual"]["assets"] == [] and not third["validation"]["complete"]
    assert by_section(present.call("GET", path, params={"revision": 2}))["hero_visual"]["assets"][0]["id"] == asset_id
    assert [item["revision"] for item in present.call("GET", path + "/revisions", params={"limit": 2})] == [3, 2]
    assert db_session.get(PresentationAsset, (UUID(first["id"]), 2, UUID(asset_id))) is not None
    eligible = cleanup_deleted_assets(db=db_session)["eligible"]

    request(present.client, "DELETE", f"/api/v1/assets/{asset_id}")
    asset = db_session.get(Asset, UUID(asset_id))
    asset.deleted_at = datetime.now(timezone.utc) - timedelta(days=40)
    db_session.commit()
    assert cleanup_deleted_assets(db=db_session)["eligible"] == eligible
    unavailable = by_section(present.call("GET", path, params={"revision": 2}))["hero_visual"]["assets"][0]
    assert unavailable["status"] == "unavailable" and unavailable["metadata"] is None
    request(present.client, "GET", f"/api/v1/assets/{asset_id}/download", 404)


def test_revision_bound_approval_decisions_and_history(present, concept, identity_factory):
    dna, _, sections = concept
    first = present.create(purpose="concept", sections=sections)
    path = "/" + first["id"]
    a, b = present.checkpoint(first), present.checkpoint(first, title="Technical summary")
    present.checkpoint(first, title="client SIGNOFF", status=409)
    present.review(first, a, "decision", status=409, decision="approved")
    assert present.review(first, a, comment="Review the selected visual")["status"] == "pending"
    assert present.call("GET", path)["approval_status"] == "pending"
    present.checkpoint(first, title="Late checkpoint", status=409)
    approved = present.review(first, a, "decision", decision="approved", feedback="Approved by the review lead")
    assert approved["decided_by_user_id"] == str(present.owner["user"].id) and approved["decided_at"]
    assert approved["requested_by_user_id"] == str(present.owner["user"].id) and approved["request_comment"]
    assert present.call("GET", path)["approval_status"] == "pending"
    present.review(first, b)
    designer = identity_factory(role="designer", organization=present.owner["organization"])
    present.client.headers.update(auth(designer))
    present.review(first, b, "decision", 403, decision="approved")
    present.client.headers.update(auth(present.owner))
    present.review(first, b, "decision", decision="approved")
    assert present.call("GET", path)["approval_status"] == "approved"
    present.review(first, b, "decision", 409, decision="rejected")

    second = present.revise(first, title="Client presentation revised")
    c = present.checkpoint(second)
    present.review(second, c)
    third = present.revise(second, title="Client presentation, third revision")
    present.review(second, c, "decision", 409, decision="approved")
    assert present.review(third, c, "decision", 409, decision="approved")["detail"]["code"] == "presentation_checkpoint_revision_mismatch"
    d = present.checkpoint(third)
    present.review(third, d)
    requested = present.review(third, d, "decision", decision="revision_requested", feedback="Change the framing.")
    assert requested["status"] == "revision_requested"
    fourth = present.revise(third, title="Fourth revision")
    e = present.checkpoint(fourth)
    present.review(fourth, e)
    present.review(fourth, e, "decision", decision="rejected", feedback="Client changed direction.")
    assert present.call("GET", path)["approval_status"] == "rejected"
    historical = present.call("GET", path, params={"revision": first["revision"]})
    assert historical["approval_status"] == "approved" and historical["current_revision"] == 4
    assert historical["title"] == first["title"] and dna.palette == {"primary": "Indigo"}
    assert {row["status"] for row in present.call("GET", path + "/approvals")} == {
        "approved", "pending", "revision_requested", "rejected",
    }
    assert [row["id"] for row in present.call("GET", path + "/approvals", params={"revision": 2})] == [c["id"]]
    request(present.client, "PATCH", f"/api/v1/design-dna/{dna.id}", json={"palette": {"primary": "Amber"}})
    approved_history = present.call("GET", path, params={"revision": 1})
    dna_evidence = by_section(approved_history)["design_dna"]["sources"][0]
    assert approved_history["approval_status"] == "approved" and not approved_history["validation"]["complete"]
    assert dna_evidence["status"] == "changed" and dna_evidence["data"]["palette"] == {"primary": "Indigo"}
    refreshed = present.revise(fourth)
    assert refreshed["revision"] == 5 and refreshed["approval_status"] == "draft"
    assert by_section(refreshed)["design_dna"]["sources"][0]["data"]["palette"] == {"primary": "Amber"}
    request(present.client, "DELETE", f"/api/v1/events/{present.event.id}", 409)
    request(present.client, "DELETE", f"/api/v1/projects/{present.project.id}", 409)


def test_full_production_review_uses_existing_sources(present, concept, sourcing, db_session):
    dna, asset_id, _ = concept
    api = sourcing.api
    stage = create_object(api.client, api.scene_id)
    led = create_object(api.client, api.scene_id, name="LED wall", object_type="led_wall",
                        dimensions={"width_m": 2, "depth_m": 0.5, "height_m": 1})
    definition = sourcing.parts.material(finish="Matte")
    material = api.write("POST", "/materials", 201, name="Stage maple finish", definition_id=definition["id"])
    item = api.item("Stage", spatial_object_id=stage["id"], material_id=material["id"])
    screen = api.item("LED wall", spatial_object_id=led["id"])
    area, pixels = calculate(api, quantity_request(item, "plan_area"), quantity_request(
        screen, "led_surface", face="xz", pixel_pitch={"value": "10", "unit": "mm"},
    ))
    assert pixels["status"] == "calculated"
    _, sheet_id = technical_sheet(api, item, area)
    technical_sheet(api, screen, pixels)
    bom = sourcing.parts.approve(sourcing.parts.revise(generate(area, spec=sheet_id), manual(item_id=screen)))
    checkpoint = request(api.client, "GET", api.path + "/workflow")
    checkpoint = transition(api, checkpoint, "design_approved", evidence_reference="Client concept notes")
    checkpoint = transition(api, checkpoint, "technical_review")
    transition(api, checkpoint, "production_ready")
    sourcing.procurement(bom)
    params = {"event_id": str(sourcing.event.id)}
    budget = sourcing.call("POST", "/budgets", 201, params=params, json={
        "expected_version": 0, "currency": "INR", "amount": "200", "bom_ids": [bom["id"]],
    })
    budget = sourcing.call("POST", f"/budgets/{budget['id']}/approve", json={"expected_version": 1})

    owner_id = present.owner["user"].id
    run = AIExecution(id=uuid4(), request_id=uuid4(), organization_id=present.project.organization_id,
                      workspace_id=present.project.workspace_id, project_id=present.project.id, user_id=owner_id,
                      task="design_vision", feature="insho_vision", provider="fixture", model="fixture-image",
                      status="completed", output="private model output", metadata_json={"private": "provider configuration"})
    db_session.add(run)
    db_session.flush()

    def artifact(module, content, execution_id=None):
        row = DesignArtifact(project_id=present.project.id, event_id=present.event.id, event_scope_key=str(present.event.id),
                             module=module, version=1, status="approved", source="user_edited", content=content,
                             context_fingerprint="fixture", created_by_user_id=owner_id, ai_execution_id=execution_id)
        db_session.add(row)
        db_session.flush()
        return row

    mood = artifact("mood", {"keywords": ["Indigo"], "atmosphere": "Soft entry lighting"})
    vision = artifact("vision", {"concept_summary": "A stage framed in indigo light"}, run.id)
    plan = artifact("plan", {"guest_flow": ["Enter through gallery", "Proceed to the stage"]})
    db_session.add_all([
        DesignArtifactAsset(artifact_id=mood.id, asset_id=UUID(asset_id), role="reference"),
        DesignArtifactAsset(artifact_id=vision.id, asset_id=UUID(asset_id), role="generated"),
    ])
    memory = ProjectMemoryItem(project_id=present.project.id, event_id=present.event.id, kind="preference",
                               title="Approved indigo finish", content={"internal_contact": "Private context"})
    db_session.add(memory)
    db_session.commit()

    main = present.create(purpose="production_review", client_context="government")
    alt = present.create(title="Alternative entry scheme", alternative_of_id=main["id"],
                         sections={"hero_visual": {"asset_ids": [asset_id]}})
    assert present.call("GET", f"/{main['id']}/alternatives")[0]["id"] == alt["id"]
    present.create(status=409, title="Nested option", alternative_of_id=alt["id"])
    scene_ref = ref("spatial_scene", {"id": api.scene_id})
    sections = {
        "concept_story": sourced(ref("design_dna", dna), ref("design_artifact", vision), ref("project_memory_item", memory)),
        "design_dna": sourced(ref("design_dna", dna)), "moodboard": sourced(ref("design_artifact", mood)),
        "hero_visual": sourced(ref("design_artifact", vision)), "spatial_plan": sourced(scene_ref),
        "views_3d": sourced(scene_ref, ref("design_artifact", vision)), "journey": sourced(ref("design_artifact", plan)),
        "material_palette": sourced(ref("material_assignment", material)),
        "led_visual_preview": sourced(ref("led_quantity", pixels), asset_ids=[asset_id]),
        "budget_summary": sourced(ref("budget", budget)),
        "production_confidence": sourced(ref("production_record", api.record)),
        "alternatives": sourced(ref("alternative", alt, version=alt["revision"]), applicability="required"),
    }
    incomplete = present.revise(main, sections={**sections, "led_visual_preview": {"applicability": "not_applicable"}})
    assert by_section(incomplete)["led_visual_preview"]["requirement"] == "required" and not incomplete["validation"]["complete"]
    current = present.revise(incomplete, sections=sections)
    assert current["validation"]["complete"], current["validation"]
    assert current["section_order"][:3] == ["production_confidence", "spatial_plan", "budget_summary"]
    part = by_section(current)
    assert set(section["status"] for section in part.values()) == {"complete"}
    assert part["moodboard"]["assets"][0]["role"] == "reference"
    assert part["hero_visual"]["assets"][0]["role"] == "generated"
    assert part["hero_visual"]["sources"][0]["data"]["generation"]["model"] == "fixture-image"
    assert part["spatial_plan"]["sources"][0]["version"] == part["views_3d"]["sources"][0]["version"]
    assert part["journey"]["sources"][0]["data"]["content"]["guest_flow"][0] == "Enter through gallery"
    assert part["material_palette"]["sources"][0]["data"]["finish"] == "Matte"
    assert part["led_visual_preview"]["sources"][0]["data"]["source"]["target_id"] == led["id"]
    assert part["budget_summary"]["sources"][0]["data"]["summary"] == budget["summary"]
    assert part["production_confidence"]["sources"][0]["data"]["procurement_status_counts"] == {"requested": 2}
    assert all(value not in str(current) for value in ("private model output", "provider configuration", "Private context", "storage_key"))
    updated_alt = present.revise(alt, title="Alternative version two")
    assert updated_alt["revision"] == 2
    reopened = present.call("GET", f"/{main['id']}")
    assert by_section(reopened)["alternatives"]["sources"][0]["data"]["title"] == alt["title"]

    review = present.checkpoint(current)
    present.review(current, review)
    patch_object(api, stage, dimensions={"width_m": 6, "depth_m": 4, "height_m": 1})
    assert present.review(current, review, "decision", 409, decision="approved")["detail"]["code"] == "presentation_incomplete"
    stale = present.call("GET", f"/{main['id']}")
    assert by_section(stale)["spatial_plan"]["sources"][0]["status"] == "changed"
    assert by_section(stale)["production_confidence"]["sources"][0]["status"] == "changed"
    assert not stale["validation"]["complete"]


def test_scope_permissions_and_other_project_event_references(present, concept, db_session, identity_factory):
    _, asset_id, sections = concept
    root = present.create(purpose="concept", sections=sections)
    suffix = "/" + root["id"]
    pending = present.checkpoint(root)
    present.review(root, pending)
    present.call("GET", suffix, 401, headers={"Authorization": ""})
    outsider = identity_factory()
    member = identity_factory(role="member", organization=present.owner["organization"])
    for identity, code in ((member, 403), (outsider, 404)):
        present.client.headers.update(auth(identity))
        present.call("GET", suffix, status=200 if identity is member else 404)
        present.revise(root, status=code, title="Unauthorized edit")
        present.review(root, pending, "decision", code, decision="approved")
    outsider_project = make_project(db_session, outsider)
    other_asset = upload_jpeg(present.client, outsider, str(outsider_project.id))["asset_id"]
    present.client.headers.update(auth(present.owner))
    present.create(status=422, approval_status="approved")
    present.create(status=404, sections={"hero_visual": {"asset_ids": [other_asset]}})
    assert db_session.scalar(select(Presentation.id).where(Presentation.project_id == present.project.id)) == UUID(root["id"])
    request(present.client, "GET", f"/api/v1/assets/{other_asset}/download", 404)

    sibling_identity = identity_factory(organization=present.owner["organization"])
    sibling = make_project(db_session, sibling_identity)
    sibling_scene = create_scene(present.client, sibling.id)
    sibling_event = Event(name="Different event", project_id=present.project.id, organization_id=present.project.organization_id)
    db_session.add(sibling_event)
    db_session.commit()
    other_scene = create_scene(present.client, present.project.id, event_id=str(sibling_event.id))
    for scene in (sibling_scene, other_scene):
        present.create(status=404, sections={"spatial_plan": sourced(ref("spatial_scene", scene))})
    request(present.client, "GET", f"/api/v1/projects/{sibling.id}/presentations/{root['id']}", 404)
    present.create(status=404, alternative_of_id=root["id"], event_id=str(sibling_event.id))
    other = present.create(purpose="concept", sections=sections)
    present.review(other, pending, "decision", 404, decision="approved")
    assert present.client.get(f"/api/v1/assets/{asset_id}/download").status_code == 200
    request(present.client, "POST", f"/api/v1/assets/{asset_id}/move", json={"event_id": str(sibling_event.id)})
    moved = by_section(present.call("GET", suffix))["hero_visual"]["assets"][0]
    assert moved["status"] == "unavailable" and moved["metadata"] is None
    present.review(root, pending, "decision", 409, decision="approved")


def test_concurrent_writes_compete_on_one_revision(present):
    root = present.create()
    barrier = Barrier(2)
    project_id, user_id, presentation_id = present.project.id, present.owner["user"].id, UUID(root["id"])

    def update(title):
        with TestingSessionLocal() as db:
            barrier.wait(timeout=5)
            try:
                result = presentation.revise(db, project_id, presentation_id, user_id,
                                             PresentationUpdate(expected_revision=1, title=title, client_context="corporate"))
                return result["revision"]
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ("Option A", "Option B"))) == [2, 409]
    assert [item["revision"] for item in present.call("GET", f"/{root['id']}/revisions")] == [2, 1]
