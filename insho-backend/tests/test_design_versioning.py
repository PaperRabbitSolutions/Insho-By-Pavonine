from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.asset import Asset
from app.models.design_artifact import DesignArtifact, DesignElement
from app.models.design_version import DesignSnapshot
from app.models.event import Event
from app.schemas.design_version import VersionWrite
from app.services import design_version
from app.services.asset_lifecycle import cleanup_deleted_assets
from conftest import TestingSessionLocal
from test_assets import auth, upload_jpeg
from test_phase9_spatial import create_object, create_scene, make_project
from test_presentation import concept, present
from test_production import production, request
from test_production_procurement import sourcing
from test_production_quantification import patch_object


class Versions:
    def __init__(self, production):
        self.api, self.owner, self.project, self.event = production
        self.path = f"/api/v1/projects/{self.project.id}"
        self.event_params = {"event_id": str(self.event.id)}

    def call(self, method, suffix, status=200, **kwargs):
        return request(self.api.client, method, self.path + suffix, status, **kwargs)

    def save(self, expected=None, status=201, *, selection=None, branch=None, headers=None, **fields):
        path = "/design-versions" if branch is None else f"/design-branches/{branch['id']}/versions"
        return self.call("POST", path, status, params=self.event_params if branch is None else None, headers=headers,
                         json={"expected_head_id": expected, "selection": selection or {}, "summary": "Stage scheme",
                               "reason": "Review stage arrangement", **fields})

    def read(self, row):
        return self.call("GET", f"/design-versions/{row['id']}")

    def current(self):
        return self.call("GET", "/design-versions/current", params=self.event_params)

    def fork(self, source, main=None, status=201, **fields):
        return self.call("POST", "/design-branches", status, json={
            "source_version_id": source["id"], "expected_mainline_id": (main or source)["id"],
            "name": "Alternate stage", "reason": "Explore a separate stage", **fields,
        })

    def promote(self, branch, head, main, status=201):
        return self.call("POST", f"/design-branches/{branch['id']}/promote", status, json={
            "expected_head_id": head["id"], "expected_mainline_id": main["id"], "mode": "whole_state",
            "summary": "Publish alternate stage", "reason": "Team chose the alternate state",
        })


@pytest.fixture
def versions(production):
    return Versions(production)


def test_revisions_compare_and_restore_authored_spatial_state(versions):
    api = versions.api
    stage = create_object(api.client, api.scene_id)
    initial = versions.save(selection={"scene_ids": [api.scene_id]})
    assert initial["number"] == 1 and initial["parent_id"] is None and initial["status"] == "draft"
    patch_object(api, stage, dimensions={"width_m": 6, "depth_m": 4, "height_m": 1})
    request_id = str(uuid4())
    changed = versions.save(initial["id"], selection={"scene_ids": [api.scene_id]}, headers={"X-Request-ID": request_id})
    assert changed["number"] == 2 and changed["parent_id"] == initial["id"]
    assert versions.current()["id"] == changed["id"]
    assert versions.read(initial)["state"]["scenes"][api.scene_id]["objects"][stage["id"]]["width_m"] == "8.000000"
    comparison = versions.call("GET", "/design-versions/compare", params={
        "from_version_id": initial["id"], "to_version_id": changed["id"],
    })
    object_change = next(item for item in comparison["changes"] if item["id"] == stage["id"])
    assert object_change["change"] == "updated" and "width_m" in object_change["fields"]
    assert api.scene_id in comparison["affected_source_ids"]
    audit = versions.call("GET", "/design-activity", params={"version_id": changed["id"]})[0]
    assert audit["request_id"] == request_id and audit["actor_id"] == str(versions.owner["user"].id)
    assert audit["details"]["previous_version_id"] == initial["id"] and audit["reason"]

    restored = versions.call("POST", "/design-versions/restore", 201, json={
        "source_version_id": initial["id"], "expected_head_id": changed["id"],
        "summary": "Restore earlier proportions", "reason": "Use the original deck dimensions",
    })
    assert restored["number"] == 3 and restored["parent_id"] == changed["id"]
    assert restored["based_on_id"] == initial["id"] and restored["snapshot_id"] == initial["snapshot_id"]
    assert restored["state"] == initial["state"]
    canonical = request(api.client, "GET", f"/api/v1/spatial/objects/{stage['id']}")
    assert float(canonical["dimensions"]["width_m"]) == 6
    assert [row["number"] for row in versions.call("GET", "/design-versions", params=versions.event_params)] == [3, 2, 1]


def test_branch_isolation_reviewed_promotion_and_archive(versions, identity_factory):
    api = versions.api
    create_object(api.client, api.scene_id)
    other_scene = create_scene(api.client, versions.project.id, event_id=str(versions.event.id))
    create_object(api.client, other_scene["id"], name="Alternate stage")
    base = versions.save(selection={"scene_ids": [api.scene_id]})
    branch, sibling = versions.fork(base), versions.fork(base, name="Second option")
    branch_head = versions.save(branch["current_version_id"], selection={"scene_ids": [other_scene["id"]]}, branch=branch)
    assert branch_head["branch_id"] == branch["id"]
    assert versions.current()["id"] == base["id"]
    assert list(versions.read({"id": sibling["current_version_id"]})["state"]["scenes"]) == [api.scene_id]
    compared = versions.call("GET", f"/design-branches/{branch['id']}/compare")
    assert {item["id"] for item in compared["changes"] if item["kind"] == "scene"} == {other_scene["id"], api.scene_id}
    assert {item["id"] for item in versions.call("GET", "/design-branches", params=versions.event_params)} == {branch["id"], sibling["id"]}
    child = versions.fork(branch_head, base, name="Nested option")
    assert child["parent_branch_id"] == branch["id"] and child["base_mainline_id"] == base["id"]

    designer = identity_factory(role="designer", organization=versions.owner["organization"])
    api.client.headers.update(auth(designer))
    versions.promote(branch, branch_head, base, status=403)
    api.client.headers.update(auth(versions.owner))
    versions.promote(branch, {"id": branch["current_version_id"]}, base, status=409)
    promoted = versions.promote(branch, branch_head, base)
    assert promoted["parent_id"] == base["id"] and promoted["based_on_id"] == branch_head["id"]
    assert promoted["snapshot_id"] == branch_head["snapshot_id"] and promoted["branch_id"] is None
    assert versions.current()["id"] == promoted["id"] and versions.read(base)["state"]["scenes"].keys() == {api.scene_id}
    assert versions.promote(sibling, {"id": sibling["current_version_id"]}, promoted, status=409)["detail"]["code"] == "design_mainline_diverged"
    assert versions.promote(child, {"id": child["current_version_id"]}, promoted, status=409)["detail"]["code"] == "design_mainline_diverged"
    suffix = f"/design-branches/{branch['id']}"
    assert versions.call("GET", suffix)["status"] == "promoted"
    assert len(versions.call("GET", suffix + "/versions")) == 2
    archived = versions.call("POST", suffix + "/archive", json={"expected_head_id": branch_head["id"], "reason": "Option recorded"})
    assert archived["status"] == "archived"
    versions.save(branch_head["id"], status=409, branch=branch)
    assert {row["action"] for row in versions.call("GET", "/design-activity", params={"branch_id": branch["id"]})} == {
        "branch_created", "version_created", "branch_promoted", "branch_archived",
    }


def test_promotion_detects_live_dependency_change_without_a_new_mainline(versions):
    api = versions.api
    stage = create_object(api.client, api.scene_id)
    base = versions.save(selection={"scene_ids": [api.scene_id]})
    branch = versions.fork(base)
    patch_object(api, stage, dimensions={"width_m": 6, "depth_m": 4, "height_m": 1})
    error = versions.promote(branch, {"id": branch["current_version_id"]}, base, status=409)
    assert error["detail"]["code"] == "design_dependencies_diverged"
    assert versions.current()["id"] == base["id"]


def test_branch_restore_reuses_a_pinned_design_artifact_state(versions, db_session):
    artifact = DesignArtifact(project_id=versions.project.id, event_id=versions.event.id, event_scope_key=str(versions.event.id),
                              module="elements", version=1, content={"elements": []}, status="approved", source="user_edited",
                              context_fingerprint="fixture", created_by_user_id=versions.owner["user"].id)
    db_session.add(artifact)
    db_session.flush()
    element = DesignElement(artifact_id=artifact.id, project_id=versions.project.id, event_id=versions.event.id,
                            name="Deck", category="stage", source="user_confirmed", details={"finish": "matte"})
    db_session.add(element)
    db_session.commit()
    key, element_id = str(artifact.id), str(element.id)
    main = versions.save(selection={"artifact_ids": [key]})
    assert main["state"]["artifacts"][key]["elements"][element_id]["details"]["finish"] == "matte"
    branch = versions.fork(main)
    removed = versions.save(branch["current_version_id"], branch=branch, selection={"artifact_ids": []})
    restored = versions.call("POST", f"/design-branches/{branch['id']}/restore", 201, json={
        "source_version_id": branch["current_version_id"], "expected_head_id": removed["id"],
        "summary": "Use the original elements", "reason": "The original finish is still preferred",
    })
    assert restored["parent_id"] == removed["id"] and restored["branch_id"] == branch["id"]
    assert restored["snapshot_id"] == main["snapshot_id"] and versions.current()["id"] == main["id"]


def test_presentation_decisions_approve_only_the_bound_design_revision(versions, present, concept):
    dna, _, sections = concept
    sheet = present.create(purpose="concept", sections=sections)
    pin = {"id": sheet["id"], "revision": sheet["revision"]}
    original = versions.save(selection={"design_dna_id": str(dna.id), "presentation": pin})
    url = f"/design-versions/{original['id']}/review"
    versions.call("PUT", url, 409, json={**pin, "revision": 2})
    assert versions.call("PUT", url, json=pin)["review"] == pin
    subsequent = versions.save(original["id"])
    assert subsequent["review"] is None
    versions.call("PUT", f"/design-versions/{subsequent['id']}/review", 409, json=pin)
    checkpoint = present.checkpoint(sheet)
    present.review(sheet, checkpoint)
    assert versions.read(original)["status"] == "pending"
    decision = present.review(sheet, checkpoint, "decision", decision="approved", feedback="Signed brief")
    assert versions.read(original)["status"] == "approved" and versions.current()["status"] == "draft"
    versions.call("PATCH", f"/design-versions/{original['id']}", 405, json={"summary": "Overwrite approved state"})
    source = versions.call("GET", "/design-activity", params={"version_id": original["id"]})
    assert {row["action"] for row in source} == {"version_created", "review_bound", "checkpoint_pending", "checkpoint_approved"}
    rejected_link = {"project_id": str(versions.project.id), "event_id": str(versions.event.id), "category": "rejected",
                     "title": "Review decision", "content": {"note": "Confirmed client decision"}, "decision_id": decision["id"]}
    request(versions.api.client, "POST", "/api/v1/project-memory/items", 409, json=rejected_link)
    bad = request(versions.api.client, "POST", "/api/v1/project-memory/items", 409,
                  json={**rejected_link, "category": "approved", "linked_version_id": subsequent["id"]})
    assert bad["detail"]["code"] == "memory_version_decision_mismatch"
    linked = request(versions.api.client, "POST", "/api/v1/project-memory/items", 201,
                     json={**rejected_link, "category": "approved", "verification": "verified"})
    assert linked["linked_version_id"] == original["id"] and linked["decision_id"] == decision["id"]


def test_scope_guards_reject_foreign_data_before_saving(versions, db_session, identity_factory, monkeypatch, tmp_path):
    api = versions.api
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "version-assets"))
    outsider = identity_factory()
    foreign_project = make_project(db_session, outsider)
    asset = upload_jpeg(api.client, outsider, str(foreign_project.id))["asset_id"]
    versions.save(status=404, selection={"asset_ids": [asset]})
    assert db_session.scalar(select(func.count()).select_from(DesignSnapshot).where(DesignSnapshot.project_id == versions.project.id)) == 0
    base = versions.save()
    assert base["number"] == 1
    branch = versions.fork(base)
    versions.call("GET", f"/design-versions/{base['id']}", 401, headers={"Authorization": ""})
    versions.call("GET", f"/design-branches/{branch['id']}", 404, headers=auth(outsider))
    sibling = make_project(db_session, identity_factory(organization=versions.owner["organization"]))
    request(api.client, "GET", f"/api/v1/projects/{sibling.id}/design-versions/{base['id']}", 404)
    request(api.client, "GET", f"/api/v1/projects/{sibling.id}/design-activity", 404, params={"version_id": base["id"]})
    other = Event(organization_id=versions.project.organization_id, project_id=versions.project.id, name="Other event")
    db_session.add(other)
    db_session.commit()
    other_scene = create_scene(api.client, versions.project.id, event_id=str(other.id))
    versions.save(base["id"], status=404, selection={"scene_ids": [other_scene["id"]]})
    assert versions.current()["id"] == base["id"]
    request(api.client, "DELETE", f"/api/v1/events/{versions.event.id}", 409)
    request(api.client, "DELETE", f"/api/v1/projects/{versions.project.id}", 409)


def test_snapshots_reuse_bom_budget_and_managed_asset_history(versions, sourcing, db_session, monkeypatch, tmp_path):
    api = versions.api
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "version-assets"))
    asset_id = upload_jpeg(api.client, versions.owner, str(versions.project.id))["asset_id"]
    bom, *_ = sourcing.make_bom()
    budget = sourcing.call("POST", "/budgets", 201, params=versions.event_params,
                          json={"expected_version": 0, "amount": "200", "currency": "INR", "bom_ids": [bom["id"]]})
    captured = versions.save(selection={"scene_ids": [api.scene_id], "bom_ids": [bom["id"]], "budget_ids": [budget["id"]],
                                         "asset_ids": [asset_id]})
    assert captured["state"]["budgets"][budget["id"]]["summary"]["estimated"]["total"] == "80.00"
    assert captured["state"]["boms"][bom["id"]]["lines"][bom["lines"][0]["id"]]["total"] == "80.00"
    assert "storage_key" not in captured["state"]["assets"][asset_id]
    eligible = cleanup_deleted_assets(db=db_session)["eligible"]
    request(api.client, "DELETE", f"/api/v1/assets/{asset_id}")
    asset = db_session.get(Asset, UUID(asset_id))
    asset.deleted_at = datetime.now(timezone.utc) - timedelta(days=40)
    db_session.commit()
    history = versions.read(captured)
    assert history["unavailable_asset_ids"] == [asset_id] and history["state"]["assets"] == {}
    assert cleanup_deleted_assets(db=db_session)["eligible"] == eligible
    replacement = versions.save(captured["id"], selection={"asset_ids": []})
    error = versions.call("POST", "/design-versions/restore", 409, json={
        "expected_head_id": replacement["id"], "source_version_id": captured["id"],
        "summary": "Restore earlier state", "reason": "Check asset scope",
    })
    assert error["detail"]["code"] == "design_snapshot_assets_unavailable"


def test_concurrent_design_revision_expects_one_head(versions):
    root = versions.save()
    project_id, event_id, actor_id = versions.project.id, versions.event.id, versions.owner["user"].id
    barrier = Barrier(2)

    def append(summary):
        with TestingSessionLocal() as db:
            barrier.wait(timeout=5)
            try:
                result = design_version.write(db, project_id, event_id, actor_id,
                    VersionWrite(expected_head_id=UUID(root["id"]), summary=summary, reason="Race fixture"))
                return result["number"]
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(append, ("First option", "Second option"))) == [2, 409]
    assert [row["number"] for row in versions.call("GET", "/design-versions", params=versions.event_params)] == [2, 1]
