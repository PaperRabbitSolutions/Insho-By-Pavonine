import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.design_artifact import DesignArtifact
from app.models.design_audit import DesignAudit
from app.models.design_version import DesignBranch
from app.schemas.design_audit import Dimension, DimensionResult
from app.services import design_audit, design_snapshot
from app.services.ai_orchestration import AIProviderError, ProviderResult, ai_execution_service
from test_assets import auth
from test_design_versioning import production, versions
from test_phase9_spatial import create_object, make_project
from test_presentation import concept, present, ref, sourced
from test_production import request
from test_production_procurement import sourcing
from test_production_quantification import calculate, patch_object, quantity_request
from test_project_memory_history import make_memory


def audit(versions, version, status=201, **options):
    return versions.call("POST", "/design-audits", status, json={"design_version_id": version["id"], **options})


def findings(run):
    assert len(run["results"]) == 18, run.get("warnings")
    return {part["dimension"]: part for part in run["results"]}


def position(x, y=5, z=1):
    return {"position": {"x": x, "y": y, "z": z}}


def test_all_dimensions_honestly_report_missing_evidence(versions):
    request(versions.api.client, "PATCH", f"/api/v1/projects/{versions.project.id}", json={"guest_count": 120})
    version = versions.save()
    result = audit(versions, version)
    assert result["status"] == "completed_with_warnings"
    assert result["snapshot_id"] == version["snapshot_id"]
    assert result["summary"]["evaluated"] == 0 and result["summary"]["not_evaluated"] == 18
    assert list(findings(result)) == list(Dimension.__args__)
    assert all(part["state"] == "not_evaluated" and part["score"] is None and part["confidence"] == "inconclusive"
               for part in result["results"])
    assert all(part["explanation"] and part["evidence"] and part["recommendations"] for part in result["results"])
    guest = findings(result)["guest_experience"]
    assert any(row["source"]["field"] == "guest_count" and row["value"] == {"guest_count": 120} for row in guest["evidence"])
    safety = findings(result)["risk_safety_flags"]
    assert "professionally_verified_site_specific_safety_package" in safety["missing_inputs"]
    assert all(value["review_required"] == "professional" for value in safety["recommendations"])
    with pytest.raises(ValidationError):
        DimensionResult.model_validate({**safety, "state": "evaluated", "score": 3, "confidence": "high"})
    path = f"/design-audits/{result['id']}"
    assert versions.call("GET", path + "/dimensions/risk_safety_flags") == safety
    assert versions.call("GET", path + "/dimensions") == result["results"]
    assert versions.call("GET", path + "/evidence", params={"dimension": "risk_safety_flags"}) == safety["evidence"]
    assert versions.call("GET", path + "/recommendations", params={"dimension": "risk_safety_flags"}) == safety["recommendations"]
    versions.call("GET", path + "/dimensions/pretty", 422)
    versions.call("POST", path + "/retry", 409)
    versions.call("PATCH", path, 405, json={"status": "completed"})
    [row] = versions.call("GET", "/design-audits", params={"version_id": version["id"], "limit": 1})
    assert row["id"] == result["id"] and "results" not in row
    events = versions.call("GET", "/design-activity", params={"version_id": version["id"]})
    assert {row["action"] for row in events} >= {"design_audit_started", "design_audit_completed_with_warnings"}


def test_frozen_spatial_evidence_and_comparison_across_versions(versions):
    stage = create_object(versions.api.client, versions.api.scene_id, transform=position(29))
    safety_memory = make_memory(versions, "safety_constraints", verification="verified", source_reference="Engineer note ENG-7",
                                content={"constraint": "Verify anchorage and site exposure with the engineer."})
    version = versions.save(selection={"scene_ids": [versions.api.scene_id], "memory_item_ids": [safety_memory["id"]]})
    first = audit(versions, version)
    parts = findings(first)
    assert parts["scale_proportion"]["score"] == 1 and parts["spatial_feasibility"]["score"] == 1
    assert parts["furniture_decor"]["score"] is None
    issue = parts["scale_proportion"]["findings"][0]
    assert issue["code"] == "venue_boundary_exceeded" and issue["source_finding_id"]
    assert any(row["value"] and (row["value"].get("measurement") or {}).get("measured") == 3
               for row in parts["scale_proportion"]["evidence"])
    assert stage["id"] in {target["id"] for row in parts["scale_proportion"]["recommendations"] for target in row["targets"]}
    safety = parts["risk_safety_flags"]
    assert safety["score"] is None and safety["state"] == "not_evaluated" and safety["severity"] == "high"
    assert any(value["source"]["id"] == safety_memory["id"] and value["value"]["source_reference"] == "Engineer note ENG-7"
               for value in safety["evidence"])
    assert all(item["review_required"] == "professional" for item in safety["recommendations"])

    patch_object(versions.api, stage, transform=position(12))
    second_version = versions.save(version["id"], selection={"scene_ids": [versions.api.scene_id]})
    second = audit(versions, second_version)
    assert findings(second)["scale_proportion"]["score"] == 3
    assert versions.call("GET", f"/design-audits/{first['id']}") == first
    repeated = audit(versions, version)
    assert repeated["id"] != first["id"] and repeated["input_fingerprint"] == first["input_fingerprint"]
    assert repeated["results"] == first["results"]
    comparison = versions.call("GET", "/design-audits/compare", params={
        "from_evaluation_id": first["id"], "to_evaluation_id": second["id"],
    })
    indexed = {row["dimension"]: row for row in comparison["dimensions"]}
    assert indexed["scale_proportion"]["comparable"] and indexed["scale_proportion"]["from_score"] == 1
    assert indexed["scale_proportion"]["to_score"] == 3 and not indexed["risk_safety_flags"]["comparable"]


def test_accessibility_uses_only_captured_authored_rules_with_real_targets(versions):
    api = versions.api
    shape = {"width_m": 2, "depth_m": 2, "height_m": 1}
    a = create_object(api.client, api.scene_id, name="Seat", category="furniture", dimensions=shape, transform=position(5))
    b = create_object(api.client, api.scene_id, name="Barrier", dimensions=shape, transform=position(8))
    request(api.client, "PATCH", f"/api/v1/projects/{versions.project.id}", json={"constraints": {"spatial_validation": {"clearances": [
        {"id": "access-route", "minimum_m": 2, "subject": {"object_id": a["id"]}, "against": {"object_id": b["id"]}},
        {"id": "empty-target", "minimum_m": 2, "subject": {"category": "absent"}, "against": {"object_id": b["id"]}},
    ]}}})
    selected = versions.save(selection={"scene_ids": [api.scene_id]}, refresh_context=True)
    missing = findings(audit(versions, selected, accessibility_clearance_ids=["empty-target"]))["accessibility"]
    assert missing["state"] == "not_evaluated" and not missing["findings"]
    failed = findings(audit(versions, selected, accessibility_clearance_ids=["access-route"]))["accessibility"]
    assert failed["score"] == 1 and failed["findings"][0]["code"] == "clearance_below_minimum"
    measured = next(item["value"]["measurement"] for item in failed["evidence"] if item["value"].get("measurement"))
    assert measured == {"measured": 1.0, "expected": 2.0, "shortfall": 1.0, "unit": "m"}
    assert failed["recommendations"][0]["review_required"] == "professional"
    audit(versions, selected, 422, accessibility_clearance_ids=["duplicate", "duplicate"])
    request(api.client, "PATCH", f"/api/v1/projects/{versions.project.id}", json={"constraints": {"spatial_validation": {"clearances": [
        {"id": "empty-target", "minimum_m": 2, "subject": {"category": "absent"}, "against": {"object_id": b["id"]}},
    ]}}})
    selected = versions.save(selected["id"], selection={"scene_ids": [api.scene_id]}, refresh_context=True)
    layout = findings(audit(versions, selected))["spatial_feasibility"]
    assert layout["state"] == "not_evaluated" and not layout["findings"]


def test_budget_boq_and_led_reuse_pinned_phase10_evidence(versions, sourcing, present):
    api = versions.api
    led = create_object(api.client, api.scene_id, name="LED wall", object_type="led_wall", transform=position(12),
                        dimensions={"width_m": 2, "depth_m": 0.5, "height_m": 1})
    bom, *_ = sourcing.make_bom()
    item = api.item("LED wall", spatial_object_id=led["id"])
    pixels = calculate(api, quantity_request(item, "led_surface", face="xz", pixel_pitch={"value": "10", "unit": "mm"}))[0]
    sheet = present.create(sections={"led_visual_preview": sourced(ref("led_quantity", pixels)),
                                     "production_confidence": sourced(ref("production_record", api.record))})
    budget = sourcing.call("POST", "/budgets", 201, params=versions.event_params, json={
        "expected_version": 0, "currency": "INR", "amount": "70.00", "bom_ids": [bom["id"]],
    })
    sourcing.call("POST", f"/budgets/{budget['id']}/approve", json={"expected_version": 1})
    material = make_memory(versions, "production_constraints", verification="verified", source_reference="Supplier material declaration",
                           content={"sustainability": {"reuse_required": True, "reuse_verified": False}})
    design = versions.save(selection={"scene_ids": [api.scene_id], "bom_ids": [bom["id"]], "budget_ids": [budget["id"]],
                                      "memory_item_ids": [material["id"]],
                                      "presentation": {"id": sheet["id"], "revision": sheet["revision"]}})
    parts = findings(audit(versions, design))
    assert parts["budget_efficiency"]["score"] == 1
    assert parts["budget_efficiency"]["evidence"][0]["value"]["estimated_variance"] == "10.00"
    assert parts["technical_completeness"]["score"] == 2
    assert any(item["source"]["kind"] == "technical_specification" for item in parts["technical_completeness"]["evidence"])
    assert parts["production_complexity"]["score"] == 1
    assert parts["led_suitability"]["score"] == 3 and parts["led_suitability"]["evidence"][0]["value"]["total_pixels"] == 20000
    assert parts["sustainability"]["score"] == 1


def test_active_part2_forks_keep_their_legacy_budget_basis(versions, sourcing, db_session):
    bom, *_ = sourcing.make_bom()
    budget = sourcing.call("POST", "/budgets", 201, params=versions.event_params, json={
        "expected_version": 0, "currency": "INR", "amount": "100", "bom_ids": [bom["id"]],
    })
    design = versions.save(selection={"bom_ids": [bom["id"]], "budget_ids": [budget["id"]]})
    frame = design["state"]
    del frame["budgets"][budget["id"]]["basis_bom_ids"]
    for line in frame["boms"][bom["id"]]["lines"].values():
        del line["issues"]
        del line["dependencies"]
        del line["category"]
    branch = versions.fork(design)
    assert db_session.get(DesignBranch, UUID(branch["id"])).live_basis == design_snapshot.digest(frame)
    promoted = versions.promote(branch, {"id": branch["current_version_id"]}, design)
    assert promoted["snapshot_id"] == design["snapshot_id"]


def _design_artifacts(versions, db_session):
    artifacts = []
    for module, content in (("style", {"creative_direction": "An indigo arrival sequence", "photography_direction": "Eye-level entry view"}),
                            ("vision", {"concept_summary": "An indigo entry frames the stage", "views": [{"name": "Entry"}]})):
        artifacts.append(DesignArtifact(project_id=versions.project.id, event_id=versions.event.id, event_scope_key=str(versions.event.id),
                         module=module, version=1, content=content, status="approved", source="user_edited",
                         context_fingerprint="fixture", created_by_user_id=versions.owner["user"].id))
    db_session.add_all(artifacts)
    db_session.commit()
    return [str(row.id) for row in artifacts]


class MockModel:
    def __init__(self):
        self.action = "unavailable"

    async def execute(self, model, request, timeout):
        body = json.loads(request.input)
        assert set(body) == {"creativity", "photography_social_impact"}
        if self.action == "unavailable":
            raise AIProviderError("provider_unavailable", "Fixture provider unavailable", retryable=True)
        creative = body["creativity"]
        return ProviderResult(output=json.dumps({"assessments": [
            {"dimension": "creativity", "assessment": "mixed", "evidence_ids": [creative["intent"][0]["id"], creative["implementation"][0]["id"]]},
            {"dimension": "photography_social_impact", "assessment": "supported", "evidence_ids": [str(uuid4()), str(uuid4())]},
            {"dimension": "risk_safety_flags", "assessment": "supported", "evidence_ids": [creative["intent"][0]["id"], creative["implementation"][0]["id"]]},
        ]}))


def test_routed_ai_fallback_and_evidence_normalization(versions, db_session, monkeypatch):
    design = versions.save(selection={"artifact_ids": _design_artifacts(versions, db_session)})
    for key, value in {"AI_OPENAI_ENABLED": "true", "OPENAI_API_KEY": "fixture", "AI_OPENAI_MODELS": "fixture-model",
                       "AI_OPENAI_CAPABILITIES": "text,structured_output", "AI_ANTHROPIC_ENABLED": "false", "AI_MAX_RETRIES": "0"}.items():
        monkeypatch.setenv(key, value)
    stub = MockModel()
    monkeypatch.setitem(ai_execution_service.adapters, "openai", stub)
    first = audit(versions, design, ai_assisted=True)
    assert first["retryable"] and first["status"] == "completed_with_warnings" and first["ai_execution_id"]
    assert findings(first)["creativity"]["score"] is None
    stub.action = "valid_and_invalid"
    retry = versions.call("POST", f"/design-audits/{first['id']}/retry", 201)
    assert retry["retry_of_id"] == first["id"] and retry["design_version_id"] == design["id"]
    assert retry["provider"] == "openai" and retry["model"] == "fixture-model"
    parts = findings(retry)
    assert parts["creativity"]["score"] == 2 and parts["creativity"]["confidence"] == "low"
    assert parts["creativity"]["findings"][0]["origin"] == "ai_inferred"
    assert parts["photography_social_impact"]["score"] is None and parts["risk_safety_flags"]["score"] is None
    assert retry["warnings"][0]["code"] == "ai_assessments_incomplete"
    assert versions.call("GET", f"/design-audits/{first['id']}") == first


def test_permissions_and_cross_project_version_evidence(versions, db_session, identity_factory):
    root = versions.save()
    run = audit(versions, root)
    path = f"/design-audits/{run['id']}"
    member = identity_factory(role="member", organization=versions.owner["organization"])
    versions.call("GET", path, headers=auth(member))
    versions.call("POST", "/design-audits", 403, headers=auth(member), json={"design_version_id": root["id"]})
    outsider = identity_factory()
    versions.call("GET", path, 404, headers=auth(outsider))
    versions.call("GET", path, 401, headers={"Authorization": ""})
    sibling = make_project(db_session, identity_factory(organization=versions.owner["organization"]))
    sibling_prefix = f"/api/v1/projects/{sibling.id}/design-audits"
    request(versions.api.client, "POST", sibling_prefix, 404, json={"design_version_id": root["id"]})
    request(versions.api.client, "GET", sibling_prefix + f"/{run['id']}/evidence", 404)
    request(versions.api.client, "GET", sibling_prefix, 404, params={"version_id": root["id"]})


def test_terminal_failure_is_recorded_and_retry_creates_new_run(versions, monkeypatch):
    selected = versions.save()
    solve = design_audit._evaluate_deterministic

    def unavailable(*args):
        raise RuntimeError("internal fixture error")

    monkeypatch.setattr(design_audit, "_evaluate_deterministic", unavailable)
    failed = audit(versions, selected)
    assert failed["status"] == "failed" and failed["retryable"] and failed["summary"] is None
    assert "internal fixture error" not in str(failed)
    versions.call("GET", f"/design-audits/{failed['id']}/dimensions", 409)
    monkeypatch.setattr(design_audit, "_evaluate_deterministic", solve)
    retried = versions.call("POST", f"/design-audits/{failed['id']}/retry", 201)
    assert retried["retry_of_id"] == failed["id"] and retried["input_fingerprint"] == failed["input_fingerprint"]


def test_approval_and_asset_evidence_are_attributed_to_the_audit_run(versions, present, concept, db_session):
    dna, asset_id, sections = concept
    sheet = present.create(purpose="concept", sections=sections)
    pin = {"id": sheet["id"], "revision": sheet["revision"]}
    version = versions.save(selection={"design_dna_id": str(dna.id), "presentation": pin})
    versions.call("PUT", f"/design-versions/{version['id']}/review", json=pin)
    check = present.checkpoint(sheet)
    present.review(sheet, check)
    first = audit(versions, version)
    present.review(sheet, check, "decision", decision="approved")
    second = audit(versions, version)

    def checkpoint_state(run):
        return next(item["value"]["status"] for item in findings(run)["technical_completeness"]["evidence"]
                    if item["source"]["id"] == check["id"])

    path = f"/design-audits/{first['id']}"
    assert checkpoint_state(versions.call("GET", path)) == "pending" and checkpoint_state(second) == "approved"
    assert first["input_fingerprint"] != second["input_fingerprint"]
    request(versions.api.client, "DELETE", f"/api/v1/assets/{asset_id}")
    latest = versions.call("GET", path)
    asset = next(item for item in findings(latest)["photography_social_impact"]["evidence"] if item["source"]["id"] == asset_id)
    assert asset["available"] is False and asset["value"] is None
    persisted = db_session.get(DesignAudit, UUID(first["id"]))
    captured = next(part for part in persisted.results if part["dimension"] == "photography_social_impact")
    assert any(item["source"]["id"] == asset_id and item["value"]["mime_type"] == "image/jpeg" for item in captured["evidence"])


def test_interrupted_audit_is_readable_and_retryable(versions, db_session):
    version = versions.save()
    running = DesignAudit(id=uuid4(), organization_id=versions.project.organization_id, project_id=versions.project.id,
                          event_id=versions.event.id, design_version_id=UUID(version["id"]), snapshot_id=UUID(version["snapshot_id"]),
                          initiated_by_user_id=versions.owner["user"].id, request_id=uuid4(), status="running",
                          configuration_version="28.1-v1", options={"ai_assisted": False, "accessibility_clearance_ids": []},
                          input_fingerprint="0" * 64, results=[], warnings=[], retryable=False, evidence_sources=[],
                          started_at=datetime.now(timezone.utc) - timedelta(minutes=11))
    db_session.add(running)
    db_session.commit()
    history = versions.call("GET", "/design-audits", params={"status": "interrupted"})
    assert len(history) == 1 and history[0]["retryable"]
    retry = versions.call("POST", f"/design-audits/{running.id}/retry", 201)
    assert retry["retry_of_id"] == str(running.id)
